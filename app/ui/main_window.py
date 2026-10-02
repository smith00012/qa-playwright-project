from datetime import datetime
from pathlib import Path
import json
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk

from app.automation.flow_runner import FlowRunner
from app.automation.run_settings import NO_ENVIRONMENT, list_environments, load_run_settings
from app.config import DEFAULT_PATHS
from app.recorder.browser_recorder import BrowserRecorder
from app.reporting.test_case_report import TestCaseReportGenerator
from app.storage.case_matrix_repository import MatrixCase
from app.ui.case_matrix_panel import CaseMatrixPanel
from app.services.artifact_service import TestArtifactService
from app.ui.dialogs import ask_yes_no, ask_yes_no_cancel, show_error, show_info, show_warning


class MainWindow:

    WINDOW_TITLE = "Nexo QA | Plataforma de pruebas"
    WINDOW_SIZE = "1240x900"
    WARNING_TITLE = "Nexo QA"
    ERROR_TITLE = "No se pudo completar"
    SUCCESS_MESSAGE = "La prueba se guardó correctamente en el documento seleccionado."
    FLOW_FOLDER = DEFAULT_PATHS.flows_dir
    CREATE_FOLDER_OPTION = "Crear nueva carpeta..."
    CREATE_REPORT_OPTION = "Crear nuevo documento..."

    COLORS = {
        "background": "#f1f4f2",
        "header": "#163a3a",
        "header_text": "#f7faf8",
        "header_muted": "#bfd0cb",
        "muted": "#657773",
        "text": "#203332",
        "panel": "#ffffff",
        "field": "#f7faf8",
        "border": "#d9e3df",
        "accent": "#d77b62",
        "accent_dark": "#216b66",
        "danger": "#b94d58",
        "success": "#2e806b",
    }

    def __init__(self, root: tk.Tk) -> None:

        self.root = root

        self.root.title(self.WINDOW_TITLE)
        self.root.geometry(self.WINDOW_SIZE)
        self.root.minsize(1000, 720)
        self.root.configure(bg=self.COLORS["background"])
        self._configure_styles()

        DEFAULT_PATHS.ensure_data_directories()
        self.recorder: BrowserRecorder | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.latest_flow_path: Path | None = self._find_latest_flow()
        self.current_case_name = ""
        self.active_matrix_case: MatrixCase | None = None
        self.overwrite_matrix_case = False
        self._saved_flow_map: dict[str, Path] = {}
        self.report_folder = DEFAULT_PATHS.test_cases_dir
        self.report_name: str | None = None
        self.current_report_folder = self.report_folder
        self.current_report_name = "CasosDePrueba.docx"
        self._load_report_target()
        self.artifact_service = TestArtifactService(
            paths=DEFAULT_PATHS,
            log_callback=self._log,
        )

        self._build_ui()
        self.root.after(100, self._drain_logs)

    def _build_ui(self) -> None:

        header = tk.Frame(
            self.root,
            bg=self.COLORS["header"],
            height=104
        )
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        brand = tk.Frame(header, bg=self.COLORS["header"])
        brand.pack(anchor=tk.W, side=tk.LEFT, padx=30, pady=20)

        tk.Label(
            brand,
            text="N",
            font=("Segoe UI", 17, "bold"),
            fg="white",
            bg=self.COLORS["accent"],
            width=2,
            height=1,
            padx=4,
            pady=4,
        ).pack(side=tk.LEFT, padx=(0, 13))

        tk.Label(
            brand,
            text="NEXO QA",
            font=("Segoe UI", 20, "bold"),
            fg=self.COLORS["header_text"],
            bg=self.COLORS["header"]
        ).pack(side=tk.LEFT)

        tk.Label(
            header,
            text="PRUEBAS WEB  /  EVIDENCIAS  /  AUTOMATIZACIÓN",
            font=("Segoe UI", 9, "bold"),
            fg=self.COLORS["header_muted"],
            bg=self.COLORS["header"],
        ).pack(side=tk.RIGHT, padx=30)

        content = tk.Frame(
            self.root,
            bg=self.COLORS["background"]
        )
        content.pack(fill=tk.BOTH, expand=True, padx=28, pady=22)
        content.grid_columnconfigure(0, weight=6, minsize=560)
        content.grid_columnconfigure(1, weight=5, minsize=380)
        content.grid_rowconfigure(1, weight=1)

        intro = tk.Frame(content, bg=self.COLORS["background"])
        intro.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 17))

        tk.Label(
            intro,
            text="Nueva sesión",
            font=("Segoe UI", 17, "bold"),
            fg=self.COLORS["text"],
            bg=self.COLORS["background"]
        ).pack(anchor=tk.W)

        tk.Label(
            intro,
            text="Prepara el destino y registra un recorrido web con sus resultados y evidencias.",
            font=("Segoe UI", 10),
            fg=self.COLORS["muted"],
            bg=self.COLORS["background"]
        ).pack(anchor=tk.W, pady=(4, 0))

        workspace = tk.Frame(content, bg=self.COLORS["background"])
        workspace.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        sidebar = tk.Frame(content, bg=self.COLORS["background"])
        sidebar.grid(row=1, column=1, sticky="nsew")
        sidebar.grid_rowconfigure(0, weight=3)
        sidebar.grid_rowconfigure(1, weight=2)
        sidebar.grid_columnconfigure(0, weight=1)

        form = tk.Frame(
            workspace,
            bg=self.COLORS["panel"],
            highlightbackground=self.COLORS["border"],
            highlightthickness=1
        )
        form.pack(fill=tk.X)

        tk.Label(
            form,
            text="URL DEL SISTEMA",
            font=("Segoe UI", 9, "bold"),
            fg=self.COLORS["muted"],
            bg=self.COLORS["panel"]
        ).pack(anchor=tk.W, padx=22, pady=(19, 7))

        self.url_entry = tk.Entry(
            form,
            font=("Segoe UI", 11),
            fg=self.COLORS["text"],
            bg=self.COLORS["field"],
            relief=tk.FLAT,
            highlightbackground=self.COLORS["border"],
            highlightcolor=self.COLORS["accent"],
            highlightthickness=1
        )
        self.url_entry.pack(fill=tk.X, padx=22, ipady=9)

        case_name_header = tk.Frame(form, bg=self.COLORS["panel"])
        case_name_header.pack(fill=tk.X, padx=22, pady=(16, 7))
        tk.Label(
            case_name_header,
            text="NOMBRE DEL CASO DE PRUEBA",
            font=("Segoe UI", 9, "bold"),
            fg=self.COLORS["muted"],
            bg=self.COLORS["panel"]
        ).pack(side=tk.LEFT)
        tk.Button(
            case_name_header,
            text="CARGAR MATRIZ EXCEL",
            command=self.load_case_matrix,
            font=("Segoe UI", 9, "bold"),
            fg=self.COLORS["accent_dark"],
            bg="#eaf2ef",
            activeforeground="white",
            activebackground=self.COLORS["accent_dark"],
            relief=tk.FLAT,
            cursor="hand2",
            padx=13,
            pady=7,
        ).pack(side=tk.RIGHT)

        self.case_name_entry = tk.Entry(
            form,
            font=("Segoe UI", 11),
            fg=self.COLORS["text"],
            bg=self.COLORS["field"],
            relief=tk.FLAT,
            highlightbackground=self.COLORS["border"],
            highlightcolor=self.COLORS["accent"],
            highlightthickness=1
        )
        self.case_name_entry.pack(fill=tk.X, padx=22, ipady=9)

        self.case_matrix_panel = CaseMatrixPanel(
            form,
            colors=self.COLORS,
            on_case_selected=self._set_case_name_from_matrix,
        )

        self.report_fields = tk.Frame(form, bg=self.COLORS["panel"])
        self.report_fields.pack(fill=tk.X, padx=22, pady=(16, 0))

        folder_field = tk.Frame(self.report_fields, bg=self.COLORS["panel"])
        folder_field.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        tk.Label(
            folder_field,
            text="CARPETA DE RESULTADOS",
            font=("Segoe UI", 9, "bold"),
            fg=self.COLORS["muted"],
            bg=self.COLORS["panel"]
        ).pack(anchor=tk.W, pady=(0, 7))
        self.report_folder_entry = ttk.Combobox(
            folder_field,
            font=("Segoe UI", 11),
            values=self._report_folder_options(),
            postcommand=self._refresh_report_folders,
            state="readonly",
        )
        folder_name = self._selected_report_folder_name()
        self.report_folder_entry.set(folder_name)
        self.report_folder_entry.pack(fill=tk.X, ipady=9)
        self.report_folder_entry.bind(
            "<<ComboboxSelected>>",
            self._on_report_folder_selected,
        )
        self.new_report_folder_entry = ttk.Entry(
            folder_field,
            font=("Segoe UI", 11),
        )

        report_field = tk.Frame(self.report_fields, bg=self.COLORS["panel"])
        report_field.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        tk.Label(
            report_field,
            text="DOCUMENTO WORD",
            font=("Segoe UI", 9, "bold"),
            fg=self.COLORS["muted"],
            bg=self.COLORS["panel"]
        ).pack(anchor=tk.W, pady=(0, 7))
        initial_report_folder = (
            DEFAULT_PATHS.test_cases_dir / folder_name
        )
        self.report_name_entry = ttk.Combobox(
            report_field,
            font=("Segoe UI", 11),
            values=self._report_document_options(initial_report_folder),
            postcommand=self._refresh_report_documents,
            state="readonly",
        )
        existing_reports = self._report_document_names(initial_report_folder)
        if self.report_name in existing_reports:
            self.report_name_entry.set(self.report_name)
        else:
            self.report_name_entry.set(self.CREATE_REPORT_OPTION)
        self.report_name_entry.pack(fill=tk.X, ipady=9)
        self.report_name_entry.bind(
            "<<ComboboxSelected>>",
            self._on_report_document_selected,
        )
        self.new_report_name_entry = ttk.Entry(
            report_field,
            font=("Segoe UI", 11),
        )
        self.new_report_name_entry.insert(
            0,
            self.report_name or "CasosDePrueba.docx",
        )
        if self.report_name_entry.get() == self.CREATE_REPORT_OPTION:
            self.new_report_name_entry.pack(fill=tk.X, pady=(6, 0), ipady=7)

        self.reuse_report_target_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            form,
            text="Mantener esta carpeta y este Word para las siguientes pruebas",
            variable=self.reuse_report_target_var,
            font=("Segoe UI", 9),
            fg=self.COLORS["text"],
            bg=self.COLORS["panel"],
            activebackground=self.COLORS["panel"],
            selectcolor=self.COLORS["panel"],
            command=self._on_reuse_report_target_changed,
        ).pack(anchor=tk.W, padx=22, pady=(12, 0))

        actions = tk.Frame(form, bg=self.COLORS["panel"])
        actions.pack(fill=tk.X, padx=22, pady=20)

        self.start_button = tk.Button(
            actions,
            text="▶  INICIAR PRUEBA",
            command=self.start_recording,
            font=("Segoe UI", 10, "bold"),
            fg="white",
            bg=self.COLORS["accent_dark"],
            activeforeground="white",
            activebackground=self.COLORS["accent"],
            relief=tk.FLAT,
            cursor="hand2",
            padx=20,
            pady=11
        )
        self.start_button.pack(side=tk.LEFT)

        self.stop_button = tk.Button(
            actions,
            text="■  FINALIZAR PRUEBA",
            command=self.stop_recording,
            font=("Segoe UI", 10, "bold"),
            fg=self.COLORS["danger"],
            bg="#f8eeeb",
            activeforeground="white",
            activebackground=self.COLORS["danger"],
            relief=tk.FLAT,
            cursor="hand2",
            padx=20,
            pady=10,
            state=tk.DISABLED
        )
        self.stop_button.pack(side=tk.LEFT, padx=(12, 0))

        self.run_flow_button = tk.Button(
            actions,
            text="↻  EJECUTAR ÚLTIMO FLUJO",
            command=self.run_latest_flow,
            font=("Segoe UI", 10, "bold"),
            fg=self.COLORS["accent_dark"],
            bg=self.COLORS["panel"],
            activeforeground="white",
            activebackground=self.COLORS["accent_dark"],
            relief=tk.FLAT,
            cursor="hand2",
            padx=20,
            pady=10,
            state=tk.NORMAL if self.latest_flow_path else tk.DISABLED
        )
        self.run_flow_button.pack(side=tk.LEFT, padx=(12, 0))

        saved_flows_panel = tk.Frame(
            sidebar,
            bg=self.COLORS["panel"],
            highlightbackground=self.COLORS["border"],
            highlightthickness=1
        )
        saved_flows_panel.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            saved_flows_panel,
            text="FLUJOS GUARDADOS",
            font=("Segoe UI", 9, "bold"),
            fg=self.COLORS["muted"],
            bg=self.COLORS["panel"]
        ).pack(anchor=tk.W, padx=16, pady=(12, 8))

        flows_row = tk.Frame(saved_flows_panel, bg=self.COLORS["panel"])
        flows_row.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self.saved_flow_tree = ttk.Treeview(
            flows_row,
            show="tree",
            height=8,
            selectmode="browse",
        )
        self.saved_flow_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        flow_scrollbar = ttk.Scrollbar(flows_row, orient=tk.VERTICAL)
        flow_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.saved_flow_tree.configure(yscrollcommand=flow_scrollbar.set)
        flow_scrollbar.config(command=self.saved_flow_tree.yview)

        environment_row = tk.Frame(saved_flows_panel, bg=self.COLORS["panel"])
        environment_row.pack(fill=tk.X, padx=12, pady=(0, 10))

        tk.Label(
            environment_row,
            text="AMBIENTE",
            font=("Segoe UI", 8, "bold"),
            fg=self.COLORS["muted"],
            bg=self.COLORS["panel"]
        ).pack(side=tk.LEFT)

        self.environment_var = tk.StringVar(value=NO_ENVIRONMENT)
        self.environment_combo = ttk.Combobox(
            environment_row,
            textvariable=self.environment_var,
            state="readonly",
            width=18,
            postcommand=self._reload_environments,
        )
        self.environment_combo.pack(side=tk.LEFT, padx=(8, 10))
        self.environment_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._update_stop_guard_label()
        )

        self.stop_guard_label = tk.Label(
            environment_row,
            font=("Segoe UI", 8, "bold"),
            bg=self.COLORS["panel"]
        )
        self.stop_guard_label.pack(side=tk.LEFT)
        self._reload_environments()
        self._update_stop_guard_label()

        replay_button = tk.Button(
            saved_flows_panel,
            text="▶ REPRODUCIR FLUJO SELECCIONADO",
            command=self.run_selected_flow,
            font=("Segoe UI", 9, "bold"),
            fg="white",
            bg=self.COLORS["accent_dark"],
            activeforeground="white",
            activebackground="#185652",
            relief=tk.FLAT,
            cursor="hand2",
            padx=15,
            pady=9,
        )
        replay_button.pack(anchor=tk.W, padx=12, pady=(0, 12))

        status_bar = tk.Frame(
            content,
            bg=self.COLORS["background"]
        )
        status_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))

        self.status_indicator = tk.Canvas(
            status_bar,
            width=12,
            height=12,
            bg=self.COLORS["background"],
            highlightthickness=0
        )
        self.status_indicator.pack(side=tk.LEFT, padx=(2, 8))

        self.status_label = tk.Label(
            status_bar,
            text="Estado: En espera",
            font=("Segoe UI", 10, "bold"),
            fg=self.COLORS["muted"],
            bg=self.COLORS["background"]
        )
        self.status_label.pack(side=tk.LEFT)
        self._set_status("detenido")
        self._reload_saved_flows()

        log_panel = tk.Frame(
            sidebar,
            bg=self.COLORS["panel"],
            highlightbackground=self.COLORS["border"],
            highlightthickness=1
        )
        log_panel.pack(fill=tk.BOTH, expand=True, pady=(14, 0))

        tk.Label(
            log_panel,
            text="ACTIVIDAD DE LA SESIÓN",
            font=("Segoe UI", 9, "bold"),
            fg=self.COLORS["muted"],
            bg=self.COLORS["panel"]
        ).pack(anchor=tk.W, padx=16, pady=(12, 6))

        log_container = tk.Frame(log_panel, bg="#203332")
        log_container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        scrollbar = tk.Scrollbar(log_container)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text = tk.Text(
            log_container,
            height=7,
            bg="#203332",
            fg="#d8e6e0",
            insertbackground="#c9d8e6",
            font=("Cascadia Mono", 9),
            relief=tk.FLAT,
            wrap=tk.WORD,
            state=tk.DISABLED,
            yscrollcommand=scrollbar.set
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=8)
        scrollbar.config(command=self.log_text.yview)

    def _configure_styles(self) -> None:
        self.root.option_add("*Font", ("Segoe UI", 10))
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(
            "TCombobox",
            fieldbackground=self.COLORS["field"],
            background=self.COLORS["panel"],
            foreground=self.COLORS["text"],
            arrowcolor=self.COLORS["accent_dark"],
            bordercolor=self.COLORS["border"],
            padding=(9, 7),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.COLORS["field"])],
            foreground=[("disabled", self.COLORS["muted"])],
        )
        style.configure(
            "Treeview",
            background=self.COLORS["field"],
            fieldbackground=self.COLORS["field"],
            foreground=self.COLORS["text"],
            rowheight=29,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background="#eaf0ed",
            foreground=self.COLORS["muted"],
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
        )
        style.map(
            "Treeview",
            background=[("selected", self.COLORS["accent_dark"])],
            foreground=[("selected", "white")],
        )
        style.configure(
            "Vertical.TScrollbar",
            background="#d8e2de",
            troughcolor=self.COLORS["panel"],
            bordercolor=self.COLORS["panel"],
            arrowcolor=self.COLORS["muted"],
        )

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        self.log_queue.put(line)

    def _drain_logs(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break

            self._append_log(message)

        self.root.after(100, self._drain_logs)

    def _append_log(self, message: str) -> None:
        if not self.log_text.winfo_exists():
            return

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _recorder_log(self, message: str) -> None:
        self._log(f"[RECORDER] {message}")

    def _find_latest_flow(self) -> Path | None:
        flows = sorted(self.FLOW_FOLDER.glob("flow_*.json"))
        return flows[-1] if flows else None

    @staticmethod
    def resolve_flow_display_name(flow_file: Path, flow_data: dict | None = None) -> str:
        if isinstance(flow_data, dict):
            name = str(flow_data.get("name") or "").strip()
            if name:
                return name

        stem = flow_file.stem
        if stem.startswith("flow_"):
            stem = stem[5:]

        if re.fullmatch(r".+_\d{8}_\d{6}", stem):
            stem = re.sub(r"_\d{8}_\d{6}$", "", stem)

        if not stem or stem == "flow":
            return flow_file.stem

        return stem.replace("_", " ")

    def _reload_saved_flows(self) -> None:
        self.saved_flow_tree.delete(*self.saved_flow_tree.get_children())
        self._saved_flow_map = {}
        folder_items: dict[str, str] = {}
        document_items: dict[tuple[str, str], str] = {}

        flows = []
        for flow_file in self.FLOW_FOLDER.glob("flow_*.json"):
            try:
                with flow_file.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if not isinstance(data, dict):
                    data = {}
                display_name = self.resolve_flow_display_name(flow_file, data)
            except Exception:
                data = {}
                display_name = self.resolve_flow_display_name(flow_file)

            folder_name = str(data.get("report_folder") or "").strip()
            document_name = str(data.get("report_name") or "").strip()
            if not folder_name or not document_name:
                folder_name = "Sin carpeta asociada"
                document_name = "Sin documento asociado"
            flows.append((folder_name, document_name, display_name, flow_file))

        for index, (folder_name, document_name, display_name, flow_file) in enumerate(
            sorted(flows, key=lambda item: tuple(value.casefold() for value in item[:3]))
        ):
            if folder_name not in folder_items:
                folder_iid = f"folder_{len(folder_items)}"
                folder_items[folder_name] = folder_iid
                self.saved_flow_tree.insert(
                    "",
                    tk.END,
                    iid=folder_iid,
                    text=folder_name,
                    open=True,
                )

            document_key = (folder_name, document_name)
            if document_key not in document_items:
                document_iid = f"document_{len(document_items)}"
                document_items[document_key] = document_iid
                self.saved_flow_tree.insert(
                    folder_items[folder_name],
                    tk.END,
                    iid=document_iid,
                    text=document_name,
                    open=True,
                )

            flow_iid = f"flow_{index}"
            self.saved_flow_tree.insert(
                document_items[document_key],
                tk.END,
                iid=flow_iid,
                text=display_name,
            )
            self._saved_flow_map[flow_iid] = flow_file

    def _select_flow_path(self) -> Path | None:
        selected = self.saved_flow_tree.selection()
        if not selected:
            return None

        return self._saved_flow_map.get(selected[0])

    def _set_status(self, status: str) -> None:
        status_colors = {
            "detenido": self.COLORS["muted"],
            "grabando": self.COLORS["success"],
            "finalizado": self.COLORS["accent_dark"],
        }
        color = status_colors[status]
        self.status_indicator.delete("all")
        self.status_indicator.create_oval(1, 1, 11, 11, fill=color, outline=color)
        self.status_label.configure(fg=color)

    def _get_case_name(self) -> str | None:
        case_name = self.case_name_entry.get().strip()
        if not case_name:
            show_warning(
                self.root,
                self.WARNING_TITLE,
                "Debe ingresar un nombre para el caso de prueba."
            )
            return None

        self.current_case_name = case_name
        return case_name

    def load_case_matrix(self) -> None:
        self.case_matrix_panel.load(
            before_widget=self.report_fields,
            log_callback=self._log,
        )

    def _set_case_name_from_matrix(self, matrix_case: MatrixCase) -> None:
        self.case_name_entry.delete(0, tk.END)
        self.case_name_entry.insert(0, matrix_case.name)

    def _selected_matrix_case(self) -> MatrixCase | None:
        return self.case_matrix_panel.selected_case()

    def _advance_matrix_case(self) -> None:
        self.case_matrix_panel.advance(log_callback=self._log)

    def _confirm_matrix_case_repetition(self, matrix_case: MatrixCase | None) -> bool:
        self.overwrite_matrix_case = False
        if matrix_case is None:
            return True

        report_file = self.current_report_folder / self.current_report_name
        existing_case_id = TestCaseReportGenerator.find_matrix_case_id(
            report_file,
            matrix_case.matrix_key,
        )
        if existing_case_id is None:
            return True

        overwrite = ask_yes_no_cancel(
            self.root,
            "Caso ya registrado",
            f"'{matrix_case.name}' ya está registrado como {existing_case_id}.\n\n"
            "Sí: sobrescribir ese caso conservando su ID.\n"
            "No: agregar otra ejecución con un ID nuevo.\n"
            "Cancelar: no iniciar la prueba.",
            parent=self.root,
        )
        if overwrite is None:
            return False

        self.overwrite_matrix_case = overwrite
        return True

    def _get_obtained_result(self) -> str | None:
        if self.active_matrix_case is None:
            return None
        return self.case_matrix_panel.obtained_result()

    @staticmethod
    def _safe_report_name(value: str, extension: str = "") -> str | None:
        name = re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" ._")
        if not name:
            return None
        if extension and not name.lower().endswith(extension.lower()):
            name += extension
        return name

    def _selected_report_folder_name(self) -> str:
        if self.report_folder == DEFAULT_PATHS.test_cases_dir:
            return "Resultados"
        return self.report_folder.name

    def _report_folder_names(self) -> list[str]:
        names = sorted(
            folder.name
            for folder in DEFAULT_PATHS.test_cases_dir.iterdir()
            if folder.is_dir()
        )
        default_name = "Resultados"
        if default_name not in names:
            names.insert(0, default_name)
        selected_name = self._selected_report_folder_name()
        if selected_name not in names:
            names.insert(0, selected_name)
        return names

    def _report_folder_options(self) -> list[str]:
        return self._report_folder_names() + [self.CREATE_FOLDER_OPTION]

    @staticmethod
    def _report_document_names(folder: Path) -> list[str]:
        if not folder.is_dir():
            return []
        return sorted(
            document.name
            for document in folder.glob("*.docx")
            if document.is_file()
        )

    def _report_document_options(self, folder: Path) -> list[str]:
        return self._report_document_names(folder) + [self.CREATE_REPORT_OPTION]

    @staticmethod
    def _next_available_report_name(folder: Path, report_name: str) -> str:
        report_path = Path(report_name)
        suffix = report_path.suffix or ".docx"
        stem = report_path.stem
        candidate = f"{stem}_nuevo{suffix}"
        number = 2
        while (folder / candidate).exists():
            candidate = f"{stem}_nuevo_{number}{suffix}"
            number += 1
        return candidate

    @staticmethod
    def _requires_reuse_checkbox(
        folder_exists: bool,
        report_exists: bool,
        creating_new_folder: bool,
        creating_new_document: bool,
        maintain_target: bool,
    ) -> bool:
        if maintain_target:
            return False
        if creating_new_folder and folder_exists:
            return True
        if folder_exists and creating_new_document:
            return True
        return report_exists and not creating_new_document

    def _on_reuse_report_target_changed(self) -> None:
        locked = self.reuse_report_target_var.get()
        combo_state = "disabled" if locked else "readonly"
        entry_state = "disabled" if locked else "normal"
        self.report_folder_entry.configure(state=combo_state)
        self.report_name_entry.configure(state=combo_state)
        self.new_report_folder_entry.configure(state=entry_state)
        self.new_report_name_entry.configure(state=entry_state)

    def _refresh_report_folders(self) -> None:
        self.report_folder_entry.configure(values=self._report_folder_options())

    def _refresh_report_documents(self) -> None:
        folder_choice = self.report_folder_entry.get()
        if folder_choice == self.CREATE_FOLDER_OPTION:
            self.report_name_entry.configure(values=[self.CREATE_REPORT_OPTION])
            return
        folder_name = self._safe_report_name(folder_choice)
        if not folder_name:
            self.report_name_entry.configure(values=[self.CREATE_REPORT_OPTION])
            return
        folder = DEFAULT_PATHS.test_cases_dir / folder_name
        self.report_name_entry.configure(values=self._report_document_options(folder))

    def _on_report_folder_selected(self, _event=None) -> None:
        if self.report_folder_entry.get() == self.CREATE_FOLDER_OPTION:
            self.new_report_folder_entry.pack(fill=tk.X, pady=(6, 0), ipady=7)
            self.report_name_entry.configure(values=[self.CREATE_REPORT_OPTION])
            self.report_name_entry.set(self.CREATE_REPORT_OPTION)
            self._show_new_report_name_entry()
            return

        self.new_report_folder_entry.pack_forget()
        self._refresh_report_documents()
        documents = self._report_document_names(
            DEFAULT_PATHS.test_cases_dir / self.report_folder_entry.get()
        )
        if self.report_name_entry.get() not in documents:
            if documents:
                self.report_name_entry.set(documents[0])
                self._hide_new_report_name_entry()
            else:
                self.report_name_entry.set(self.CREATE_REPORT_OPTION)
                self._show_new_report_name_entry()

    def _on_report_document_selected(self, _event=None) -> None:
        if self.report_name_entry.get() == self.CREATE_REPORT_OPTION:
            self._show_new_report_name_entry()
        else:
            self._hide_new_report_name_entry()

    def _show_new_report_name_entry(self) -> None:
        if not self.new_report_name_entry.winfo_manager():
            self.new_report_name_entry.pack(fill=tk.X, pady=(6, 0), ipady=7)

    def _hide_new_report_name_entry(self) -> None:
        self.new_report_name_entry.pack_forget()

    def _load_report_target(self) -> None:
        target_file = DEFAULT_PATHS.report_target_file
        if not target_file.exists():
            return

        try:
            with target_file.open("r", encoding="utf-8") as handle:
                target = json.load(handle)

            folder_name = target.get("folder")
            report_name = target.get("report")
            if not isinstance(folder_name, str) or not isinstance(report_name, str):
                return
            if Path(folder_name).name != folder_name or folder_name in (".", ".."):
                return
            if Path(report_name).name != report_name or not report_name.lower().endswith(".docx"):
                return

            self.report_folder = DEFAULT_PATHS.test_cases_dir / folder_name
            self.report_name = report_name
        except (OSError, ValueError, TypeError):
            self._log("No se pudo cargar el destino Word guardado; se usará la carpeta predeterminada.")

    def _save_report_target(self, folder: Path, report_name: str) -> None:
        DEFAULT_PATHS.data_dir.mkdir(parents=True, exist_ok=True)
        target = {
            "folder": folder.name,
            "report": report_name,
        }
        with DEFAULT_PATHS.report_target_file.open("w", encoding="utf-8") as handle:
            json.dump(target, handle, ensure_ascii=False, indent=2)

    def _get_report_target(self) -> tuple[Path, str] | None:
        folder_choice = self.report_folder_entry.get()
        creating_new_folder = folder_choice == self.CREATE_FOLDER_OPTION
        if folder_choice == self.CREATE_FOLDER_OPTION:
            folder_name = self._safe_report_name(self.new_report_folder_entry.get())
            if not folder_name:
                show_warning(
                    self.root,
                    self.WARNING_TITLE,
                    "Ingrese un nombre válido para la nueva carpeta.",
                )
                return None
            requested_folder = DEFAULT_PATHS.test_cases_dir / folder_name
            folder_exists = requested_folder.exists()
            is_current_reused_folder = (
                self.reuse_report_target_var.get()
                and requested_folder == self.current_report_folder
            )
            if folder_exists and not is_current_reused_folder:
                show_warning(
                    self.root,
                    self.WARNING_TITLE,
                    "Esa carpeta ya existe. Marque 'Mantener esta carpeta y "
                    "este Word' para continuar allí, o seleccione otra carpeta.",
                )
                return None
        else:
            folder_name = self._safe_report_name(folder_choice)
            folder_path = DEFAULT_PATHS.test_cases_dir / folder_name if folder_name else None
            folder_exists = bool(folder_path and folder_path.is_dir())

        report_choice = self.report_name_entry.get()
        if report_choice == self.CREATE_REPORT_OPTION:
            report_name = self._safe_report_name(
                self.new_report_name_entry.get(),
                ".docx",
            )
        else:
            report_name = self._safe_report_name(report_choice, ".docx")
        if not folder_name or not report_name:
            show_warning(
                self.root,
                self.WARNING_TITLE,
                "Ingrese un nombre válido para la carpeta y el documento Word."
            )
            return None

        folder = DEFAULT_PATHS.test_cases_dir / folder_name
        report_path = folder / report_name
        creating_new_document = report_choice == self.CREATE_REPORT_OPTION
        maintain_target = self.reuse_report_target_var.get()

        is_current_reused_report = (
            maintain_target
            and report_path == self.current_report_folder / self.current_report_name
        )
        if (
            report_path.exists()
            and creating_new_document
            and not is_current_reused_report
        ):
            create_new = ask_yes_no(
                self.root,
                "Documento Word existente",
                f"Ya existe {report_path.name} en esta carpeta.\n\n"
                "¿Desea crear un documento nuevo?",
            )
            if create_new:
                report_name = self._next_available_report_name(folder, report_name)
                self.report_name_entry.set(self.CREATE_REPORT_OPTION)
                self.new_report_name_entry.delete(0, tk.END)
                self.new_report_name_entry.insert(0, report_name)
                self._show_new_report_name_entry()
                report_path = folder / report_name
            else:
                self.report_name_entry.set(report_name)
                self._hide_new_report_name_entry()
                creating_new_document = False

        if self._requires_reuse_checkbox(
            folder_exists,
            report_path.exists(),
            creating_new_folder,
            creating_new_document,
            maintain_target,
        ):
            show_warning(
                self.root,
                self.WARNING_TITLE,
                "Para usar una carpeta o un Word existente, o continuar con "
                "el Word nuevo que acaba de crear, marque 'Mantener esta "
                "carpeta y este Word'. No se modificó ningún archivo.",
            )
            return None

        self.report_folder = folder
        self.report_name = report_name
        self.current_report_folder = folder
        self.current_report_name = report_name
        self._save_report_target(folder, report_name)
        return folder, report_name

    def _reset_report_target_after_test(self) -> None:
        if self.reuse_report_target_var.get():
            return
        self.report_folder_entry.set("Resultados")
        self.report_name_entry.set(self.CREATE_REPORT_OPTION)
        self.new_report_folder_entry.delete(0, tk.END)
        self.new_report_name_entry.delete(0, tk.END)
        self.new_report_name_entry.insert(0, "CasosDePrueba.docx")
        self.new_report_folder_entry.pack_forget()
        self._show_new_report_name_entry()
        self.report_folder = DEFAULT_PATHS.test_cases_dir / "Resultados"
        self.report_name = "CasosDePrueba.docx"

    def start_recording(self) -> None:

        url = self.url_entry.get().strip()

        self._log("Solicitud de inicio de grabación.")

        if not url:

            self._log("Inicio cancelado: la URL está vacía.")

            show_warning(
                self.root,
                self.WARNING_TITLE,
                "Ingrese la URL del sistema."
            )

            return

        case_name = self._get_case_name()
        if case_name is None:
            return

        report_target = self._get_report_target()
        if report_target is None:
            self._log("Inicio cancelado: el destino del reporte no es válido.")
            return
        self.current_report_folder, self.current_report_name = report_target
        self.active_matrix_case = (
            self._selected_matrix_case()
            if self.case_matrix_panel.path is not None
            else None
        )
        if not self._confirm_matrix_case_repetition(self.active_matrix_case):
            self.active_matrix_case = None
            return

        execution_id = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        execution_folder = DEFAULT_PATHS.execution_dir(execution_id)

        self._log(f"URL: {url}")
        self._log(f"Carpeta de ejecución: {execution_folder}")
        self._log(f"Documento Word: {self.current_report_folder / self.current_report_name}")

        try:
            execution_folder.mkdir(parents=True, exist_ok=True)
            self.current_report_folder.mkdir(parents=True, exist_ok=True)

            self.recorder = BrowserRecorder(
                execution_folder,
                log_callback=self._recorder_log
            )

            self.recorder.start(url)

            self._log("BrowserRecorder iniciado correctamente.")

            self.start_button.config(
                state=tk.DISABLED
            )

            self.stop_button.config(
                state=tk.NORMAL
            )

            self.status_label.config(text="Estado: Grabando...")
            self._set_status("grabando")

        except Exception as error:

            self._log(f"Error iniciando grabación: {error}")
            show_error(self.root, self.ERROR_TITLE, str(error))

    def stop_recording(self) -> None:

        self._log("Solicitud de finalización de grabación.")
        self.stop_button.config(state=tk.DISABLED)

        try:

            if self.recorder is None:
                self._log("Finalización ignorada: no existe grabador activo.")
                return

            self.recorder.stop()

            self._log("BrowserRecorder finalizado y ejecución guardada.")
            flow_file = self.artifact_service.save_flow(
                self.recorder.execution_folder,
                flow_name=self.current_case_name,
                report_folder=self.current_report_folder,
                report_name=self.current_report_name,
            )
            if flow_file is not None:
                self.latest_flow_path = flow_file
                self.run_flow_button.config(state=tk.NORMAL)
                self._reload_saved_flows()

            _, qa_actions = self.artifact_service.generate_qa_actions(
                self.recorder.execution_folder
            )
            self.artifact_service.generate_test_case(
                self.recorder.execution_folder,
                qa_actions,
                case_name=self.current_case_name,
                report_folder=self.current_report_folder,
                report_name=self.current_report_name,
                matrix_case_key=(
                    self.active_matrix_case.matrix_key
                    if self.active_matrix_case
                    else None
                ),
                matrix_case_id=(
                    self.active_matrix_case.case_id
                    if self.active_matrix_case
                    else None
                ),
                expected_result=(
                    self.active_matrix_case.expected_result
                    if self.active_matrix_case
                    else None
                ),
                obtained_result=self._get_obtained_result(),
                overwrite_matrix_case=self.overwrite_matrix_case,
            )
            self._advance_matrix_case()
            self._reset_report_target_after_test()

            self.start_button.config(
                state=tk.NORMAL
            )

            self.status_label.config(text="Estado: Finalizado")
            self._set_status("finalizado")

            if flow_file is None:
                show_warning(
                    self.root,
                    self.WARNING_TITLE,
                    "La ejecución y el Word se guardaron, pero no se creó un "
                    "flujo porque no se registraron acciones. Interactúe con "
                    "la página antes de finalizar la prueba.",
                )
            elif self.recorder.closed_unexpectedly:
                show_warning(
                    self.root,
                    self.WARNING_TITLE,
                    "El navegador se cerró antes de finalizar. "
                    "Se guardaron las acciones disponibles y se generó "
                    "el Word con estado INCOMPLETO."
                )
            else:
                show_info(
                    self.root,
                    self.WARNING_TITLE,
                    self.SUCCESS_MESSAGE
                )

        except Exception as error:

            self._log(f"Error finalizando grabación: {error}")
            self.stop_button.config(state=tk.NORMAL)
            show_error(self.root, self.ERROR_TITLE, str(error))

    def run_selected_flow(self) -> None:
        flow_file = self._select_flow_path()
        if flow_file is None:
            self._log("No se seleccionó un flujo guardado para ejecutar.")
            show_warning(
                self.root,
                self.WARNING_TITLE,
                "Seleccione un flujo guardado antes de reproducirlo.",
            )
            return
        self._run_flow_file(flow_file)

    def run_latest_flow(self) -> None:
        flow_file = self.latest_flow_path or self._find_latest_flow()

        if flow_file is None:
            self._log("No existe un flujo guardado para ejecutar.")
            return

        self._run_flow_file(flow_file)

    def _reload_environments(self) -> None:
        try:
            environments = list_environments(DEFAULT_PATHS)
        except (OSError, ValueError) as error:
            self._log(f"No se pudo leer data/run_config.json: {error}")
            environments = []
        self.environment_combo.config(values=[NO_ENVIRONMENT, *environments])

    def _update_stop_guard_label(self) -> None:
        try:
            settings = load_run_settings(self.environment_var.get(), DEFAULT_PATHS)
        except (OSError, ValueError) as error:
            self._log(f"No se pudo leer la configuración del ambiente: {error}")
            return

        if settings.stop_guard:
            self.stop_guard_label.config(
                text="Freno de pago: activado",
                fg=self.COLORS["accent_dark"],
            )
        else:
            self.stop_guard_label.config(
                text="Freno de pago: desactivado",
                fg=self.COLORS["danger"],
            )

    def _run_flow_file(self, flow_file: Path) -> None:
        self.latest_flow_path = flow_file
        self.run_flow_button.config(state=tk.DISABLED)
        self._log(f"Iniciando reproducción: {flow_file}")

        worker = threading.Thread(
            target=self._run_flow_worker,
            args=(flow_file, self.environment_var.get()),
            daemon=True
        )
        worker.start()

    def _run_flow_worker(self, flow_file: Path, environment: str) -> None:
        try:
            settings = load_run_settings(environment, DEFAULT_PATHS)
            FlowRunner(self._recorder_log, settings=settings).run(flow_file)
        except Exception as error:
            self._log(f"Error ejecutando flujo: {error}")
        finally:
            self.root.after(
                0,
                lambda: self.run_flow_button.config(state=tk.NORMAL)
            )


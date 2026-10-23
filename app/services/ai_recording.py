import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Error as PlaywrightError

from app.audit.page_checks import safe_url
from app.automation.guide_capture import DESCRIBE_ELEMENT_JS, element_kind
from app.automation.run_settings import find_test_value, load_run_settings, matching_blocked_step
from app.config import AppPaths, DEFAULT_PATHS
from app.recorder.browser_recorder import BrowserRecorder
from app.services.artifact_service import TestArtifactService


REF_ATTRIBUTE = "data-qa-ia"
MAX_ELEMENTS = 120
MAX_PAGE_TEXT = 2000
ACTION_TIMEOUT_MS = 10000
SETTLE_TIMEOUT_MS = 15000
KIND_LABELS = {
    "button": "botón",
    "link": "enlace",
    "field": "campo",
    "password": "contraseña",
    "select": "lista",
    "check": "casilla",
}

# Marca cada control visible con un número para que la IA lo nombre.
SCREEN_SCRIPT = f"""
(firstRef) => {{
    {DESCRIBE_ELEMENT_JS}
    const interactive = [
        "a[href]", "button", "input:not([type=hidden])", "textarea", "select", "summary",
        "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]", "[role=tab]",
        "[role=menuitem]", "[role=option]", "[role=combobox]", "[role=switch]",
        "[contenteditable=true]", "label:has(input[type=checkbox], input[type=radio])",
    ].join(",");
    const visible = element => {{
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return rect.width >= 1 && rect.height >= 1
            && style.visibility !== "hidden" && style.display !== "none" && style.opacity !== "0";
    }};
    document.querySelectorAll("[{REF_ATTRIBUTE}]").forEach(item => item.removeAttribute("{REF_ATTRIBUTE}"));
    const items = [];
    let ref = firstRef;
    for (const element of document.querySelectorAll(interactive)) {{
        if (!visible(element)) continue;
        const rect = element.getBoundingClientRect();
        element.setAttribute("{REF_ATTRIBUTE}", String(ref));
        const info = describeElement(element);
        const item = {{
            ...info,
            ref: String(ref),
            label: info.label.slice(0, 100),
            on_screen: rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth,
            disabled: Boolean(element.disabled) || element.getAttribute("aria-disabled") === "true",
        }};
        if (info.tag === "select") item.options = [...element.options].slice(0, 40).map(option => clean(option.text));
        if (["checkbox", "radio"].includes(info.type)) item.checked = element.checked;
        if (info.editable && info.tag !== "select") item.filled = Boolean(element.value || element.textContent);
        if (info.tag === "a") item.href = element.href;
        items.push(item);
        ref++;
    }}
    const headings = [...document.querySelectorAll("h1, h2, h3")]
        .filter(visible).slice(0, 15).map(heading => clean(heading.innerText));
    return {{items, headings, text: clean(document.body ? document.body.innerText : "")}};
}}
"""

# Lo que el freno de pago compara: el mismo formato que graba el grabador.
GUARD_INFO_SCRIPT = """
element => ({
    text: (element.innerText || element.value || "").trim(),
    aria_label: element.getAttribute("aria-label"),
    title: element.title,
    id: element.id || null,
    selector: element.id ? "#" + element.id : null,
})
"""
FIELD_INFO_SCRIPT = """
element => ({
    id: element.id || null,
    name: element.getAttribute("name"),
    placeholder: element.getAttribute("placeholder"),
    selector: element.id ? "#" + element.id : null,
})
"""


class AiRecordingError(ValueError):
    """Un pedido de la IA que no se puede cumplir; el mensaje dice cómo seguir."""


class AiRecordingSession:
    """
    Graba un flujo manejado por una IA: abre el mismo navegador visible del
    grabador, muestra a la IA los controles de la pantalla con un número y
    ejecuta sus clics y textos. El grabador registra todo igual que cuando
    navega una persona, que puede intervenir en la misma ventana (por
    ejemplo, para un captcha).

    Reglas: el freno de pago revisa cada clic de la IA, sin excepción por
    ambiente, y los datos de prueba se escriben por referencia sin que la IA
    vea su valor.
    """

    def __init__(
        self,
        paths: AppPaths = DEFAULT_PATHS,
        log_callback: Optional[Callable[[str], None]] = None,
        recorder_factory=BrowserRecorder,
    ):
        self.paths = paths
        self.log_callback = log_callback
        self.recorder_factory = recorder_factory
        self.recorder = None
        self.name = ""
        self.settings = None
        self._frames: dict[str, object] = {}

    @property
    def active(self) -> bool:
        return self.recorder is not None

    def start(self, url: str, name: str, environment: str | None = None) -> dict:
        if self.active:
            raise AiRecordingError("Ya hay una grabación en curso. Termínala con terminar_grabacion.")
        if not url.startswith(("http://", "https://")):
            raise AiRecordingError("La URL debe empezar con http:// o https://.")
        if not name.strip():
            raise AiRecordingError("Indica un nombre para el flujo.")

        self.settings = load_run_settings(environment, self.paths)
        execution_folder = self.paths.execution_dir(datetime.now().strftime("%Y%m%d_%H%M%S"))
        execution_folder.mkdir(parents=True, exist_ok=True)
        recorder = self.recorder_factory(execution_folder, log_callback=self.log_callback)
        recorder.start(url)
        self.recorder = recorder
        self.name = name.strip()
        return {
            "grabando": True,
            "flujo": self.name,
            "ambiente": self.settings.environment,
            "pasos_bloqueados": list(self.settings.blocked_steps),
            "siguiente": "Llama a ver_pantalla para ver los controles disponibles.",
        }

    def screen(self) -> dict:
        snapshot = self._run(self._read_screen)
        items = sorted(snapshot["items"], key=lambda item: not item["on_screen"])[:MAX_ELEMENTS]
        return {
            "url": safe_url(snapshot["url"]),
            "titulo": snapshot["title"],
            "encabezados": snapshot["headings"],
            "texto": snapshot["text"][:MAX_PAGE_TEXT],
            "controles": [self._describe(item) for item in items],
            "controles_omitidos": max(0, len(snapshot["items"]) - MAX_ELEMENTS),
        }

    def screenshot(self) -> bytes:
        return self._run(lambda page: page.screenshot(timeout=ACTION_TIMEOUT_MS))

    def click(self, ref: str) -> dict:
        def command(page):
            locator = self._locate(ref)
            blocked = matching_blocked_step(
                self.settings.blocked_steps,
                locator.evaluate(GUARD_INFO_SCRIPT, timeout=ACTION_TIMEOUT_MS),
            )
            if blocked:
                raise AiRecordingError(
                    f"El freno de pago no permite que la IA pulse «{blocked}». La grabación "
                    "puede terminar aquí; si hace falta seguir, que lo haga una persona en la ventana."
                )
            locator.click(timeout=ACTION_TIMEOUT_MS)
            self._settle(page)
            return {"ok": True, "url": safe_url(page.url), "titulo": page.title()}

        return self._run(command)

    def type_text(self, ref: str, text: str | None = None, use_test_data: bool = False) -> dict:
        if use_test_data == (text is not None):
            raise AiRecordingError("Envía un texto o usar_dato_de_prueba=true, no ambos.")

        def command(page):
            locator = self._locate(ref)
            value = text
            if use_test_data:
                value = find_test_value(
                    locator.evaluate(FIELD_INFO_SCRIPT, timeout=ACTION_TIMEOUT_MS),
                    self.settings.test_data,
                )
                if value is None:
                    raise AiRecordingError(
                        "No hay dato de prueba para este campo en data/test_data.json. "
                        "Pide a la persona que lo agregue o que lo escriba en la ventana."
                    )
            locator.fill(value, timeout=ACTION_TIMEOUT_MS)
            page.wait_for_timeout(300)
            return {"ok": True, "escrito": "[DATO DE PRUEBA]" if use_test_data else value}

        return self._run(command)

    def select_option(self, ref: str, option: str) -> dict:
        def command(page):
            locator = self._locate(ref)
            try:
                locator.select_option(label=option, timeout=ACTION_TIMEOUT_MS)
            except PlaywrightError:
                locator.select_option(value=option, timeout=ACTION_TIMEOUT_MS)
            self._settle(page)
            return {"ok": True, "elegido": option}

        return self._run(command)

    def finish(self, save: bool = True) -> dict:
        if not self.active:
            raise AiRecordingError("No hay una grabación en curso.")
        recorder = self.recorder
        self.recorder = None
        self._frames = {}
        recorder.stop()
        execution_folder = recorder.execution_folder

        if not save:
            shutil.rmtree(execution_folder, ignore_errors=True)
            return {"guardado": False}

        artifacts = TestArtifactService(self.paths, self.log_callback)
        flow_file = artifacts.save_flow(execution_folder, flow_name=self.name)
        if flow_file is None:
            return {"guardado": False, "motivo": "No se registró ninguna acción."}
        _, qa_actions = artifacts.generate_qa_actions(execution_folder)
        return {
            "guardado": True,
            "flujo": flow_file.stem,
            "acciones": len(qa_actions),
            "pasos": [action.get("description") for action in qa_actions],
            "siguiente": "Puedes generar su guía con generar_guia.",
        }

    # ----------------------------------------------------------
    # En el hilo del navegador
    # ----------------------------------------------------------

    def _run(self, command):
        if not self.active:
            raise AiRecordingError("No hay una grabación en curso. Iníciala con iniciar_grabacion.")
        try:
            return self.recorder.run_in_browser(command)
        except AiRecordingError:
            raise
        except PlaywrightError as error:
            reason = (str(error).strip().splitlines() or [type(error).__name__])[0]
            raise AiRecordingError(
                f"No se pudo completar la acción: {reason}. Si la pantalla cambió, vuelve a llamar a ver_pantalla."
            ) from error
        except TimeoutError as error:
            raise AiRecordingError(
                "La acción tardó demasiado. Revisa con ver_pantalla dónde quedó la página."
            ) from error
        except RuntimeError as error:
            raise AiRecordingError(
                f"{error} Puede que se haya cerrado la ventana del navegador; usa terminar_grabacion "
                "para guardar lo registrado."
            ) from error

    def _read_screen(self, page) -> dict:
        self._frames = {}
        items: list[dict] = []
        headings: list[str] = []
        text = ""
        for index, frame in enumerate(page.frames):
            try:
                result = frame.evaluate(SCREEN_SCRIPT, len(items) + 1)
            except PlaywrightError:
                continue  # iframe que se recargó o se cerró entre medio
            for item in result["items"]:
                self._frames[item["ref"]] = frame
            items.extend(result["items"])
            if index == 0:
                headings, text = result["headings"], result["text"]
        return {"url": page.url, "title": page.title(), "items": items, "headings": headings, "text": text}

    def _locate(self, ref: str):
        frame = self._frames.get(str(ref)) if str(ref).isdigit() else None
        if frame is None:
            raise AiRecordingError(f"No hay un control {ref}. Llama a ver_pantalla para ver los números actuales.")
        locator = frame.locator(f'[{REF_ATTRIBUTE}="{int(ref)}"]')
        if locator.count() == 0:
            raise AiRecordingError("La pantalla cambió desde la última vez. Vuelve a llamar a ver_pantalla.")
        return locator.first

    @staticmethod
    def _settle(page) -> None:
        page.wait_for_timeout(500)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=SETTLE_TIMEOUT_MS)
        except PlaywrightError:
            pass  # una página lenta no invalida la acción; ver_pantalla mostrará dónde quedó

    @staticmethod
    def _describe(item: dict) -> dict:
        described = {
            "ref": item["ref"],
            "tipo": KIND_LABELS[element_kind(item)],
            "etiqueta": item["label"],
            "en_pantalla": item["on_screen"],
        }
        for source, target in (("disabled", "deshabilitado"), ("checked", "marcado"), ("filled", "lleno")):
            if item.get(source):
                described[target] = True
        if "options" in item:
            described["opciones"] = item["options"]
        if item.get("href"):
            described["destino"] = safe_url(item["href"])
        return described

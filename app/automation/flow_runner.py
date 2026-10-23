import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, sync_playwright

from app.audit.page_checks import PageAuditor, safe_url
from app.browser_scripts import DISABLE_PRINT_SCRIPT
from app.automation.browser_options import BrowserOptions, context_options
from app.automation.guide_capture import GuideCapture
from app.automation.run_settings import (
    RunSettings,
    find_test_value,
    load_run_settings,
    matching_blocked_step,
)
from app.config import AppPaths, DEFAULT_PATHS
from app.reporting.run_report import REPORT_FILENAME, RunReportGenerator
from app.reporting.user_guide import GUIDE_DATA_FILENAME, UserGuideGenerator


MASKED_VALUE = "[MASKED]"
FRAME_TIMEOUT_S = 10
STATUS_COMPLETED = "completado"
STATUS_STOPPED = "detenido_por_seguridad"
STATUS_FAILED = "fallido"

RUNTIME_TEXT_SCRIPT = """
element => [
    element.innerText,
    element.value,
    element.getAttribute("aria-label"),
    element.title
].filter(Boolean).join(" ")
"""


@dataclass
class FlowRunResult:
    """Resultado de una reproducción, guardado en result.json."""

    flow_name: str
    status: str
    environment: str | None
    stop_guard: bool
    browser: str
    device: str
    headless: bool
    steps_run: int
    total_steps: int
    run_folder: str
    trace_file: str | None = None
    stopped_at: str | None = None
    error: str | None = None
    findings: dict | None = None
    started_at: str | None = None
    duration_s: float | None = None
    failed_step: int | None = None
    failed_action: str | None = None
    screenshot: str | None = None
    report_file: str | None = None
    guide_file: str | None = None


class FlowRunner:
    """Ejecuta un flujo técnico previamente grabado."""

    def __init__(
        self,
        log_callback: Optional[Callable[[str], None]] = None,
        settings: RunSettings | None = None,
        paths: AppPaths = DEFAULT_PATHS,
        browser_options: BrowserOptions | None = None,
        guide: bool = False,
    ):
        self.log_callback = log_callback
        self.settings = settings
        self.paths = paths
        self.browser_options = browser_options or BrowserOptions()
        self.guide = guide
        self.last_result: FlowRunResult | None = None
        self._guide: GuideCapture | None = None

    def _log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def run(self, flow_file: Path) -> FlowRunResult:
        with open(flow_file, "r", encoding="utf-8") as file:
            flow = json.load(file)

        start_url = flow.get("start_url")
        actions = flow.get("actions", [])

        if not start_url:
            raise ValueError("El flujo no tiene una URL inicial.")

        if not actions:
            raise ValueError("El flujo no contiene acciones.")

        settings = self.settings or load_run_settings(paths=self.paths)
        options = self.browser_options
        flow_name = flow.get("name") or flow_file.stem
        run_folder = self._create_run_folder(flow_name, options)
        result = FlowRunResult(
            flow_name=flow_name,
            status=STATUS_COMPLETED,
            environment=settings.environment,
            stop_guard=settings.stop_guard,
            browser=options.browser,
            device=options.device,
            headless=options.headless,
            steps_run=0,
            total_steps=len(actions),
            run_folder=str(run_folder),
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.last_result = result
        started = time.monotonic()
        current_step = 0

        self._log(f"Abriendo flujo: {flow_file}")
        self._log_settings(settings)
        self._log(f"Navegador: {options.label}.")

        playwright = sync_playwright().start()
        browser = None
        context = None
        tracing = False
        auditor = PageAuditor(settings, self._log)
        self._guide = GuideCapture(run_folder, self._log) if self.guide else None
        page = None

        try:
            browser = self._launch_browser(playwright, options)
            context = browser.new_context(
                **context_options(options, playwright.devices)
            )
            context.add_init_script(DISABLE_PRINT_SCRIPT)
            if settings.trace:
                context.tracing.start(
                    title=flow_name,
                    screenshots=True,
                    snapshots=True,
                )
                tracing = True

            page = context.new_page()
            auditor.attach(page)
            page.goto(start_url, wait_until="domcontentloaded")
            self._log(f"Página inicial: {start_url}")
            self._check_page(auditor, page, 0, run_folder)

            superseded = self._superseded_steps(actions)

            for index, action in enumerate(actions, start=1):
                if index in superseded:
                    result.steps_run = index
                    continue
                auditor.current_step = index
                current_step = index
                stopped_at = self._run_action(page, action, index, settings)
                if stopped_at:
                    result.status = STATUS_STOPPED
                    result.stopped_at = stopped_at
                    result.screenshot = self._screenshot(page, run_folder, "detenido.png")
                    break
                result.steps_run = index
                self._check_page(auditor, page, index, run_folder)

            if result.status == STATUS_COMPLETED:
                self._log("Flujo ejecutado correctamente.")
                if self._guide:
                    self._guide.finish(page)

        except Exception as error:
            result.status = STATUS_FAILED
            result.error = str(error)
            if current_step:
                result.failed_step = current_step
                failed = actions[current_step - 1]
                result.failed_action = failed.get("description") or self._describe(failed)
            if page:
                result.screenshot = self._screenshot(page, run_folder, "fallo.png")
            raise

        finally:
            if tracing:
                result.trace_file = self._stop_tracing(context, run_folder)
            if browser:
                browser.close()
            playwright.stop()
            result.findings = auditor.summary()
            result.duration_s = round(time.monotonic() - started, 1)
            result.report_file = str(run_folder / REPORT_FILENAME)
            self._save_json(run_folder / "findings.json", auditor.to_dict())
            self._save_result(run_folder, result)
            self._log_findings(result.findings)
            self._write_report(run_folder, result)
            if self._guide:
                self._save_json(run_folder / GUIDE_DATA_FILENAME, self._guide.to_dict())
                self._write_guide(run_folder, result, flow_file)
            self._log("Navegador del flujo cerrado.")

        return result

    def _run_action(
        self,
        page,
        action: dict,
        sequence: int,
        settings: RunSettings,
    ) -> str | None:
        """Ejecuta un paso. Devuelve el paso bloqueado si el freno lo detuvo."""

        action_type = action.get("action_type")
        element = action.get("element") or {}
        value = action.get("value")
        description = action.get("description") or action_type

        if action_type in {"input", "change"}:
            test_value = find_test_value(element, settings.test_data)
            if test_value is not None:
                value = test_value
            elif value == MASKED_VALUE:
                self._log(
                    f"Paso {sequence} omitido: campo protegido sin dato de prueba "
                    "(agréguelo en data/test_data.json)."
                )
                self._capture_skipped_step(page, action, sequence)
                return None

        target = self._get_frame(page, action.get("frame_url"))
        locator = self._get_locator(target, element)

        # Antes del freno: la guía muestra también el botón donde se detiene.
        if self._guide and action_type in {"click", "input", "change"}:
            self._guide.capture_step(page, locator, action, sequence, value)

        if action_type == "click":
            blocked_step = self._blocked_step(locator, element, settings)
            if blocked_step:
                self._log(
                    f"Paso {sequence} detenido por seguridad: coincide con "
                    f"el paso bloqueado \"{blocked_step}\". El flujo termina aquí."
                )
                return blocked_step
            locator.click(timeout=10000)

        elif action_type == "input":
            locator.fill(value or "")

        elif action_type == "change":
            if element.get("tag", "").lower() == "select":
                locator.select_option(value or "")
            else:
                locator.fill(value or "")

        else:
            self._log(
                f"Paso {sequence} omitido: acción no soportada ({action_type})."
            )
            return None

        self._log(f"Paso {sequence}: {description}")
        return None

    def _capture_skipped_step(self, page, action: dict, sequence: int) -> None:
        """Un campo protegido sin dato de prueba no se llena, pero la guía lo explica igual."""

        if not self._guide:
            return
        try:
            target = self._get_frame(page, action.get("frame_url"))
            locator = self._get_locator(target, action.get("element") or {})
        except ValueError as error:
            self._log(f"Guía: no se pudo capturar el paso {sequence}: {error}")
            return
        self._guide.capture_step(page, locator, action, sequence)

    def _check_page(self, auditor: PageAuditor, page, step: int, run_folder: Path) -> None:
        page_result = auditor.check_page(page, step)
        if page_result is not None:
            page_result["screenshot"] = self._screenshot(
                page, run_folder, f"pantalla_{step:03d}.png"
            )

    def _screenshot(self, page, run_folder: Path, filename: str) -> str | None:
        try:
            page.screenshot(path=str(run_folder / filename), timeout=10000)
        except PlaywrightError as error:
            self._log(f"No se pudo tomar la captura {filename}: {error}")
            return None
        return filename

    def _write_report(self, run_folder: Path, result: FlowRunResult) -> None:
        try:
            report_file = RunReportGenerator().generate(run_folder)
        except Exception as error:  # el reporte no debe ocultar el resultado
            result.report_file = None
            self._save_result(run_folder, result)
            self._log(f"No se pudo generar el reporte HTML: {error}")
            return
        self._log(f"Reporte HTML: {report_file}")

    def _write_guide(self, run_folder: Path, result: FlowRunResult, flow_file: Path) -> None:
        overrides_file = self.paths.guides_dir / f"{flow_file.stem}.json"
        try:
            guide_file = UserGuideGenerator().generate(run_folder, overrides_file)
        except Exception as error:  # la guía no debe ocultar el resultado
            self._log(f"No se pudo generar la guía de uso: {error}")
            return
        result.guide_file = str(guide_file)
        self._save_result(run_folder, result)
        self._log(f"Guía de uso: {guide_file} (textos editables en {overrides_file})")

    @staticmethod
    def _describe(action: dict) -> str:
        element = action.get("element") or {}
        target = (
            element.get("text")
            or element.get("aria_label")
            or element.get("name")
            or element.get("id")
            or element.get("selector")
            or ""
        )
        return f"{action.get('action_type')} {target}".strip()

    @staticmethod
    def _superseded_steps(actions: list[dict]) -> set[int]:
        """
        Pasos de escritura que el siguiente paso reemplaza.

        El grabador guarda un evento por tecla ("s", "sm", "smi"...). Basta
        con escribir el último valor de cada campo.
        """

        superseded = set()
        for index, (action, following) in enumerate(zip(actions, actions[1:]), start=1):
            if (
                action.get("action_type") == "input"
                and following.get("action_type") in {"input", "change"}
                and FlowRunner._same_element(action, following)
            ):
                superseded.add(index)
        return superseded

    @staticmethod
    def _same_element(first: dict, second: dict) -> bool:
        first_element = first.get("element") or {}
        second_element = second.get("element") or {}
        for key in ("selector", "id", "name"):
            if first_element.get(key) or second_element.get(key):
                return first_element.get(key) == second_element.get(key)
        return False

    @staticmethod
    def _blocked_step(
        locator: Locator,
        element: dict,
        settings: RunSettings,
    ) -> str | None:
        if not settings.stop_guard or not settings.blocked_steps:
            return None

        recorded_match = matching_blocked_step(settings.blocked_steps, element)
        if recorded_match:
            return recorded_match

        runtime_text = locator.evaluate(RUNTIME_TEXT_SCRIPT, timeout=10000)
        return matching_blocked_step(
            settings.blocked_steps,
            element,
            runtime_text or "",
        )

    def _log_settings(self, settings: RunSettings) -> None:
        environment = settings.environment or "sin ambiente"
        if not settings.stop_guard:
            guard = "desactivado"
        elif settings.blocked_steps:
            guard = "activado (" + ", ".join(settings.blocked_steps) + ")"
        else:
            guard = "activado, pero no hay pasos bloqueados en data/run_config.json"
        self._log(f"Ambiente: {environment}. Freno de pago: {guard}.")

    @staticmethod
    def _launch_browser(playwright, options: BrowserOptions):
        try:
            return getattr(playwright, options.browser).launch(
                headless=options.headless
            )
        except PlaywrightError as error:
            if "Executable doesn't exist" in str(error):
                raise RuntimeError(
                    f"Falta instalar el navegador {options.browser}. "
                    f"Ejecute: python -m playwright install {options.browser}"
                ) from error
            raise

    def _create_run_folder(self, flow_name: str, options: BrowserOptions) -> Path:
        name = f"{flow_name}_{options.browser}_{options.device}"
        sanitized_name = re.sub(r"[^a-zA-Z0-9_\-]+", "_", name).strip("_") or "flujo"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_folder = self.paths.runs_dir / f"run_{sanitized_name}_{timestamp}"
        run_folder.mkdir(parents=True, exist_ok=True)
        return run_folder

    def _stop_tracing(self, context, run_folder: Path) -> str | None:
        trace_file = run_folder / "trace.zip"
        self._log("Guardando la traza; en sitios grandes puede tardar unos minutos...")
        try:
            context.tracing.stop(path=str(trace_file))
        except Exception as error:
            self._log(f"No se pudo guardar la traza: {error}")
            return None
        self._log(
            f"Traza guardada: {trace_file}. "
            f"Ábrala con: python -m playwright show-trace \"{trace_file}\""
        )
        return str(trace_file)

    @classmethod
    def _save_result(cls, run_folder: Path, result: FlowRunResult) -> None:
        cls._save_json(run_folder / "result.json", asdict(result))

    @staticmethod
    def _save_json(filepath: Path, data: dict) -> None:
        with filepath.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def _log_findings(self, findings: dict) -> None:
        self._log(
            "Hallazgos: "
            f"{findings['console_errors']} errores de consola, "
            f"{findings['network_errors']} errores de red, "
            f"{findings['accessibility_violations']} problemas de accesibilidad, "
            f"{findings['slow_pages']} de {findings['pages_checked']} pantallas lentas. "
            "Detalle en findings.json."
        )

    @staticmethod
    def _get_frame(page, frame_url: str | None, timeout_s: float = FRAME_TIMEOUT_S):
        """
        Devuelve el iframe donde se grabó la acción, o la página si no hubo
        iframe. Compara sin query, porque suele cambiar en cada carga.
        """

        if not frame_url:
            return page

        wanted = frame_url.split("?", 1)[0]
        deadline = time.monotonic() + timeout_s
        while True:
            for frame in page.frames:
                if frame.url.split("?", 1)[0] == wanted:
                    return frame
            if time.monotonic() >= deadline:
                raise ValueError(f"No se encontró el iframe {safe_url(frame_url)}.")
            page.wait_for_timeout(250)

    @staticmethod
    def _get_locator(page, element: dict) -> Locator:
        selector = element.get("selector")

        if selector:
            return page.locator(selector).first

        element_id = element.get("id")
        if element_id:
            return page.locator(f"#{element_id}").first

        name = element.get("name")
        if name:
            return page.locator(f"[name='{name}']").first

        text = element.get("text")
        if text:
            return page.get_by_text(text, exact=True).first

        raise ValueError("La acción no tiene un selector reproducible.")

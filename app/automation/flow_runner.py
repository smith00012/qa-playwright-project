import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Locator, sync_playwright

from app.automation.run_settings import (
    RunSettings,
    find_test_value,
    load_run_settings,
    matching_blocked_step,
)
from app.config import AppPaths, DEFAULT_PATHS


MASKED_VALUE = "[MASKED]"
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
    steps_run: int
    total_steps: int
    run_folder: str
    trace_file: str | None = None
    stopped_at: str | None = None
    error: str | None = None


class FlowRunner:
    """Ejecuta un flujo técnico previamente grabado."""

    def __init__(
        self,
        log_callback: Optional[Callable[[str], None]] = None,
        settings: RunSettings | None = None,
        paths: AppPaths = DEFAULT_PATHS,
    ):
        self.log_callback = log_callback
        self.settings = settings
        self.paths = paths

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
        flow_name = flow.get("name") or flow_file.stem
        run_folder = self._create_run_folder(flow_name)
        result = FlowRunResult(
            flow_name=flow_name,
            status=STATUS_COMPLETED,
            environment=settings.environment,
            stop_guard=settings.stop_guard,
            steps_run=0,
            total_steps=len(actions),
            run_folder=str(run_folder),
        )

        self._log(f"Abriendo flujo: {flow_file}")
        self._log_settings(settings)

        playwright = sync_playwright().start()
        browser = None
        context = None
        tracing = False

        try:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context()
            if settings.trace:
                context.tracing.start(
                    title=flow_name,
                    screenshots=True,
                    snapshots=True,
                )
                tracing = True

            page = context.new_page()
            page.goto(start_url, wait_until="domcontentloaded")
            self._log(f"Página inicial: {start_url}")

            for index, action in enumerate(actions, start=1):
                stopped_at = self._run_action(page, action, index, settings)
                if stopped_at:
                    result.status = STATUS_STOPPED
                    result.stopped_at = stopped_at
                    break
                result.steps_run = index

            if result.status == STATUS_COMPLETED:
                self._log("Flujo ejecutado correctamente.")

        except Exception as error:
            result.status = STATUS_FAILED
            result.error = str(error)
            raise

        finally:
            if tracing:
                result.trace_file = self._stop_tracing(context, run_folder)
            if browser:
                browser.close()
            playwright.stop()
            self._save_result(run_folder, result)
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
                return None

        locator = self._get_locator(page, element)

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

    def _create_run_folder(self, flow_name: str) -> Path:
        sanitized_name = re.sub(r"[^a-zA-Z0-9_\-]+", "_", flow_name).strip("_") or "flujo"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_folder = self.paths.runs_dir / f"run_{sanitized_name}_{timestamp}"
        run_folder.mkdir(parents=True, exist_ok=True)
        return run_folder

    def _stop_tracing(self, context, run_folder: Path) -> str | None:
        trace_file = run_folder / "trace.zip"
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

    @staticmethod
    def _save_result(run_folder: Path, result: FlowRunResult) -> None:
        with (run_folder / "result.json").open("w", encoding="utf-8") as file:
            json.dump(asdict(result), file, ensure_ascii=False, indent=2)

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

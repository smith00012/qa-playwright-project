import json
import os
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from app.audit.page_checks import safe_url
from app.automation.browser_options import BrowserOptions
from app.automation.run_settings import ENV_VALUE_PREFIX, load_run_settings
from app.config import AppPaths, DEFAULT_PATHS
from app.reporting.run_report import group_by, steps_text, url_pattern
from app.reporting.user_guide import (
    GUIDE_DATA_FILENAME,
    GUIDE_HTML_FILENAME,
    UserGuideGenerator,
    build_guide,
)


REDACTED = "[DATO DE PRUEBA]"
MIN_REDACTED_LENGTH = 4
MAX_GROUPS = 10
CAPTURE_NAME = re.compile(r"^(guia_\d{3}(_pasos)?|pantalla_\d{3}|fallo|detenido)\.png$")
TEXT_KEYS = {"titulo", "introduccion", "pantallas", "pasos", "ocultar", "final"}


class AssistantService:
    """
    Lo que una IA puede consultar y hacer con la herramienta: leer flujos,
    corridas y guías, ver capturas, redactar los textos de la guía y
    generar una guía nueva.

    Reglas: el freno de pago queda siempre activo cuando la IA reproduce un
    flujo, y ningún dato de data/test_data.json sale en las respuestas.
    """

    def __init__(
        self,
        paths: AppPaths = DEFAULT_PATHS,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.paths = paths
        self.log_callback = log_callback

    # ----------------------------------------------------------
    # Consulta
    # ----------------------------------------------------------

    def list_flows(self) -> list[dict]:
        flows = []
        for flow_file in sorted(self.paths.flows_dir.glob("*.json"), reverse=True):
            flow = self._read(flow_file)
            flows.append({
                "flujo": flow_file.stem,
                "nombre": flow.get("name") or flow_file.stem,
                "url_inicial": safe_url(flow.get("start_url") or ""),
                "acciones": len(flow.get("actions") or []),
                "textos_guia_editados": (self.paths.guides_dir / f"{flow_file.stem}.json").exists(),
            })
        return self.redact(flows)

    def list_runs(self, flow: str | None = None, limit: int = 20) -> list[dict]:
        runs = []
        folders = sorted(self.paths.runs_dir.glob("run_*"), key=lambda path: path.name[-15:], reverse=True)
        for folder in folders:
            result = self._read(folder / "result.json")
            if not result or (flow and flow not in {result.get("flow_id"), result.get("flow_name")}):
                continue
            runs.append({
                "corrida": folder.name,
                "flujo": result.get("flow_id"),
                "nombre": result.get("flow_name"),
                "estado": result.get("status"),
                "fecha": result.get("started_at"),
                "navegador": result.get("browser"),
                "dispositivo": result.get("device"),
                "pasos": f"{result.get('steps_run', 0)} de {result.get('total_steps', 0)}",
                "tiene_guia": (folder / GUIDE_DATA_FILENAME).exists(),
            })
            if len(runs) >= limit:
                break
        return self.redact(runs)

    def read_run(self, run: str) -> dict:
        folder = self._run_folder(run)
        result = self._read(folder / "result.json")
        findings = self._read(folder / "findings.json")
        pages = findings.get("pages") or []
        network = findings.get("network_errors") or []
        console = findings.get("console_errors") or []
        return self.redact({
            "corrida": folder.name,
            "flujo": result.get("flow_id"),
            "nombre": result.get("flow_name"),
            "estado": result.get("status"),
            "ambiente": result.get("environment"),
            "freno_pago": result.get("stop_guard"),
            "navegador": result.get("browser"),
            "dispositivo": result.get("device"),
            "fecha": result.get("started_at"),
            "duracion_s": result.get("duration_s"),
            "pasos": f"{result.get('steps_run', 0)} de {result.get('total_steps', 0)}",
            "detenido_en": result.get("stopped_at"),
            "paso_fallido": result.get("failed_step"),
            "accion_fallida": result.get("failed_action"),
            "error": result.get("error"),
            "captura_del_resultado": result.get("screenshot"),
            "resumen_hallazgos": findings.get("summary") or result.get("findings"),
            "pantallas": [
                {
                    "paso": page.get("step"),
                    "url": page.get("url"),
                    "tiempos_ms": page.get("timings_ms"),
                    "lenta": page.get("slow"),
                    "captura": page.get("screenshot"),
                    "accesibilidad": len(((page.get("accessibility") or {}).get("violations")) or []),
                }
                for page in pages
            ],
            "errores_de_red": [
                {
                    "estado": status,
                    "peticion": f"{method} {resource_type}",
                    "ruta": pattern,
                    "veces": len(items),
                    "pasos": steps_text(items),
                }
                for (status, method, resource_type, pattern), items in group_by(
                    network,
                    lambda error: (
                        str(error.get("status") or error.get("error") or ""),
                        error.get("method", ""),
                        error.get("resource_type", ""),
                        url_pattern(error.get("url", "")),
                    ),
                )[:MAX_GROUPS]
            ],
            "errores_de_consola": [
                {"tipo": kind, "mensaje": message, "veces": len(items), "pasos": steps_text(items)}
                for (kind, message), items in group_by(
                    console, lambda error: (error.get("type", ""), error.get("message", ""))
                )[:MAX_GROUPS]
            ],
            "accesibilidad": self._accessibility_rules(pages),
        })

    def read_guide(self, run: str) -> dict:
        folder = self._run_folder(run)
        data = self._read(folder / GUIDE_DATA_FILENAME)
        if not data:
            raise ValueError(f"La corrida {run} no tiene guía. Genérela con generar_guia.")
        result = self._read(folder / "result.json")
        texts_file = self._texts_file(result)
        overrides = self._read(texts_file) if texts_file else {}
        guide = build_guide(data, overrides)
        shots = data.get("shots") or []

        def capture(index):
            marked = folder / (Path(shots[index]["file"]).stem + "_pasos.png")
            return marked.name if marked.exists() else shots[index]["file"]

        final = guide.get("final")
        return self.redact({
            "corrida": folder.name,
            "flujo": result.get("flow_id"),
            "estado": result.get("status"),
            "detenido_en": result.get("stopped_at"),
            "titulo": overrides.get("titulo") or f"Guía de uso: {result.get('flow_name', '')}",
            "introduccion": overrides.get("introduccion") or "",
            "pantallas": [
                {
                    "numero": screen["number"],
                    "titulo": screen["title"],
                    "nota": screen["note"],
                    "url": screen["key"][0],
                    "bloques": [
                        {
                            "captura": capture(block["shot"]),
                            "pasos": [
                                {
                                    "numero": step["number"],
                                    "paso_flujo": step["step"],
                                    "tipo": step["kind"],
                                    "etiqueta": step["label"],
                                    "opcion": step.get("option") or None,
                                    "texto": step["text"],
                                }
                                for step in block["steps"]
                            ],
                        }
                        for block in screen["blocks"]
                    ],
                }
                for screen in guide["screens"]
            ],
            "final": {
                "titulo": final["title"],
                "nota": final["note"],
                "captura": shots[final["shot"]]["file"],
            } if final else None,
            "ocultos": overrides.get("ocultar") or [],
            "archivo_textos": str(texts_file) if texts_file else None,
            "guia_html": str(folder / GUIDE_HTML_FILENAME),
        })

    def capture_path(self, run: str, filename: str) -> Path:
        if not CAPTURE_NAME.match(filename or ""):
            raise ValueError(
                "Nombre de captura no válido. Use los que devuelven leer_guia o leer_corrida "
                "(por ejemplo guia_001_pasos.png)."
            )
        path = self._run_folder(run) / filename
        if not path.exists():
            raise ValueError(f"La corrida {run} no tiene la captura {filename}.")
        return path

    # ----------------------------------------------------------
    # Acciones
    # ----------------------------------------------------------

    def update_guide_texts(self, run: str, changes: dict) -> dict:
        """
        Aplica textos nuevos a la guía del flujo y la regenera con las
        capturas de la corrida. La versión anterior del archivo de textos
        queda como respaldo.
        """

        self._validate_changes(changes)
        folder = self._run_folder(run)
        result = self._read(folder / "result.json")
        texts_file = self._texts_file(result)
        if texts_file is None:
            raise ValueError(
                "Esta corrida es anterior a la conexión con la IA y no indica su flujo. "
                "Genere la guía de nuevo con generar_guia."
            )

        generator = UserGuideGenerator()
        if not texts_file.exists():
            generator.generate(folder, texts_file)
        current = self._read(texts_file)
        backup = texts_file.with_name(texts_file.stem + ".anterior.json")
        shutil.copyfile(texts_file, backup)

        for key, value in changes.items():
            if key in {"pantallas", "pasos"}:
                merged = dict(current.get(key) or {})
                for number, item in value.items():
                    if key == "pantallas":
                        merged[str(number)] = {**(merged.get(str(number)) or {}), **item}
                    else:
                        merged[str(number)] = item
                current[key] = merged
            elif key == "final":
                current[key] = {**(current.get(key) or {}), **value}
            else:
                current[key] = value

        with texts_file.open("w", encoding="utf-8") as file:
            json.dump(current, file, ensure_ascii=False, indent=2)
        html_file = generator.generate(folder, texts_file)
        return {
            "guia_html": str(html_file),
            "guia_docx": str(html_file.with_suffix(".docx")),
            "archivo_textos": str(texts_file),
            "respaldo": str(backup),
            "cambios": sorted(changes),
        }

    def generate_guide(
        self,
        flow: str,
        environment: str | None = None,
        browser: str = "chromium",
        device: str = "Escritorio",
    ) -> dict:
        """Reproduce el flujo en segundo plano con el freno de pago siempre activo."""

        from app.automation.flow_runner import FlowRunner

        flow_file = self._flow_file(flow)
        settings = replace(load_run_settings(environment, self.paths), stop_guard=True, trace=False)
        runner = FlowRunner(
            self.log_callback,
            settings=settings,
            paths=self.paths,
            browser_options=BrowserOptions(browser=browser, device=device, headless=True),
            guide=True,
        )
        try:
            runner.run(flow_file)
        except Exception:  # el resultado ya registra el paso y el error
            pass
        result = runner.last_result
        if result is None:
            raise ValueError(f"No se pudo iniciar la reproducción de {flow}.")
        return self.redact({
            "corrida": Path(result.run_folder).name,
            "estado": result.status,
            "pasos": f"{result.steps_run} de {result.total_steps}",
            "detenido_en": result.stopped_at,
            "paso_fallido": result.failed_step,
            "accion_fallida": result.failed_action,
            "error": result.error,
            "tiene_guia": result.guide_file is not None,
        })

    # ----------------------------------------------------------
    # Datos de prueba
    # ----------------------------------------------------------

    def redact(self, value):
        """Reemplaza cualquier dato de prueba que aparezca en la respuesta."""

        secrets = self._test_values()
        if not secrets:
            return value
        pattern = re.compile("|".join(re.escape(secret) for secret in sorted(secrets, key=len, reverse=True)))

        def clean(item):
            if isinstance(item, str):
                return pattern.sub(REDACTED, item)
            if isinstance(item, list):
                return [clean(element) for element in item]
            if isinstance(item, dict):
                return {key: clean(element) for key, element in item.items()}
            return item

        return clean(value)

    def _test_values(self) -> set[str]:
        values: set[str] = set()

        def collect(item):
            if isinstance(item, dict):
                for element in item.values():
                    collect(element)
            elif isinstance(item, list):
                for element in item:
                    collect(element)
            elif item is not None:
                text = str(item)
                if text.startswith(ENV_VALUE_PREFIX):
                    text = os.environ.get(text[len(ENV_VALUE_PREFIX):], "")
                if len(text) >= MIN_REDACTED_LENGTH:
                    values.add(text)

        collect(self._read(self.paths.test_data_file))
        return values

    # ----------------------------------------------------------
    # Utilidades
    # ----------------------------------------------------------

    @staticmethod
    def _accessibility_rules(pages: list[dict]) -> list[dict]:
        rules: dict[str, dict] = {}
        for page in pages:
            for violation in (page.get("accessibility") or {}).get("violations") or []:
                rule = rules.setdefault(violation.get("id", ""), {
                    "regla": violation.get("id"),
                    "impacto": violation.get("impact"),
                    "ayuda": violation.get("help"),
                    "elementos": 0,
                    "pantallas": [],
                })
                rule["elementos"] += violation.get("nodes", 0)
                if page.get("url") not in rule["pantallas"]:
                    rule["pantallas"].append(page.get("url"))
        return list(rules.values())

    @staticmethod
    def _validate_changes(changes: dict) -> None:
        if not isinstance(changes, dict) or not changes:
            raise ValueError("Envíe al menos un cambio.")
        unknown = set(changes) - TEXT_KEYS
        if unknown:
            raise ValueError(f"Claves no válidas: {', '.join(sorted(unknown))}. Use: {', '.join(sorted(TEXT_KEYS))}.")
        for key in ("titulo", "introduccion"):
            if key in changes and not isinstance(changes[key], str):
                raise ValueError(f"{key} debe ser texto.")
        for key in ("pantallas", "pasos"):
            items = changes.get(key)
            if items is None:
                continue
            if not isinstance(items, dict) or not all(str(number).isdigit() for number in items):
                raise ValueError(f"{key} debe ser un objeto cuyas claves son números.")
        for number, item in (changes.get("pantallas") or {}).items():
            if not isinstance(item, dict) or set(item) - {"titulo", "nota"}:
                raise ValueError(f"La pantalla {number} solo admite titulo y nota.")
        for number, text in (changes.get("pasos") or {}).items():
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"El paso {number} necesita un texto. Para quitarlo, use ocultar.")
        if "ocultar" in changes and not all(str(number).isdigit() for number in changes["ocultar"] or []):
            raise ValueError("ocultar debe ser una lista de números de paso del flujo (paso_flujo).")
        if "final" in changes and (not isinstance(changes["final"], dict) or set(changes["final"]) - {"titulo", "nota"}):
            raise ValueError("final solo admite titulo y nota.")

    def _texts_file(self, result: dict) -> Path | None:
        flow_id = result.get("flow_id")
        return self.paths.guides_dir / f"{flow_id}.json" if flow_id else None

    def _run_folder(self, run: str) -> Path:
        folder = self._child(self.paths.runs_dir, run)
        if not (folder / "result.json").exists():
            raise ValueError(f"No existe la corrida {run}. Use listar_corridas.")
        return folder

    def _flow_file(self, flow: str) -> Path:
        flow_file = self._child(self.paths.flows_dir, f"{flow}.json" if not flow.endswith(".json") else flow)
        if not flow_file.exists():
            raise ValueError(f"No existe el flujo {flow}. Use listar_flujos.")
        return flow_file

    @staticmethod
    def _child(directory: Path, name: str) -> Path:
        """Solo acepta un nombre dentro de la carpeta, nunca una ruta."""

        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError(f"Nombre no válido: {name}")
        return directory / name

    @staticmethod
    def _read(filepath: Path) -> dict:
        if not filepath.exists():
            return {}
        with filepath.open("r", encoding="utf-8") as file:
            return json.load(file)

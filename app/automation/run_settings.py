import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import AppPaths, DEFAULT_PATHS


NO_ENVIRONMENT = "(sin ambiente)"
ENV_VALUE_PREFIX = "env:"
DEFAULT_SLOW_LOAD_MS = 4000


@dataclass(frozen=True)
class RunSettings:
    """Opciones de una reproducción según el ambiente elegido."""

    environment: str | None = None
    stop_guard: bool = True
    blocked_steps: tuple[str, ...] = ()
    test_data: dict[str, str] = field(default_factory=dict)
    trace: bool = True
    accessibility: bool = True
    ignore_patterns: tuple[str, ...] = ()
    slow_load_ms: int = DEFAULT_SLOW_LOAD_MS


def list_environments(paths: AppPaths = DEFAULT_PATHS) -> list[str]:
    config = _read_json(paths.run_config_file)
    return sorted((config.get("ambientes") or {}).keys())


def load_run_settings(
    environment: str | None = None,
    paths: AppPaths = DEFAULT_PATHS,
) -> RunSettings:
    """
    Arma las opciones de la reproducción.

    El freno de pago queda activado salvo que el ambiente elegido
    diga explícitamente "freno_pago": false. Sin ambiente, o con un
    ambiente que no existe en run_config.json, el freno sigue activo.
    """

    if environment == NO_ENVIRONMENT:
        environment = None

    config = _read_json(paths.run_config_file)
    environments = config.get("ambientes") or {}
    environment_config = environments.get(environment, {}) if environment else {}

    blocked_steps = environment_config.get(
        "pasos_bloqueados",
        config.get("pasos_bloqueados", []),
    )
    stop_guard = environment_config.get("freno_pago", True) is not False

    test_data_config = _read_json(paths.test_data_file)
    test_data = dict(test_data_config.get("campos") or {})
    if environment:
        environment_data = (test_data_config.get("ambientes") or {}).get(environment) or {}
        test_data.update(environment_data.get("campos") or {})

    checks = config.get("revisiones") or {}

    return RunSettings(
        environment=environment,
        stop_guard=stop_guard,
        blocked_steps=tuple(str(step) for step in blocked_steps if str(step).strip()),
        test_data={str(key): str(value) for key, value in test_data.items()},
        trace=config.get("trazas", True) is not False,
        accessibility=checks.get("accesibilidad", True) is not False,
        ignore_patterns=tuple(str(item) for item in checks.get("ignorar", []) if str(item).strip()),
        slow_load_ms=int(checks.get("carga_lenta_ms", DEFAULT_SLOW_LOAD_MS)),
    )


def find_test_value(element: dict, test_data: dict[str, str]) -> str | None:
    """Busca el dato de prueba por selector, id, name o placeholder del campo."""

    candidates = (
        element.get("selector"),
        f"#{element['id']}" if element.get("id") else None,
        element.get("id"),
        element.get("name"),
        element.get("placeholder"),
    )
    for key in candidates:
        if key and key in test_data:
            return _resolve_value(test_data[key])
    return None


def matching_blocked_step(
    blocked_steps: tuple[str, ...],
    element: dict,
    runtime_text: str = "",
) -> str | None:
    """
    Devuelve el paso bloqueado que coincide con el elemento, si hay uno.

    Un paso bloqueado coincide si es igual al selector o al id del
    elemento, o si aparece dentro de su texto visible, aria-label,
    title o del texto que tiene el elemento al momento de reproducir.
    """

    exact_values = {
        element.get("selector"),
        element.get("id"),
        f"#{element['id']}" if element.get("id") else None,
    }
    texts = " | ".join(
        _normalize(value)
        for value in (
            element.get("text"),
            element.get("aria_label"),
            element.get("title"),
            runtime_text,
        )
        if value
    )

    for step in blocked_steps:
        if step in exact_values:
            return step
        normalized_step = _normalize(step)
        if normalized_step and normalized_step in texts:
            return step
    return None


def _resolve_value(value: str) -> str:
    if value.startswith(ENV_VALUE_PREFIX):
        variable = value[len(ENV_VALUE_PREFIX):]
        resolved = os.environ.get(variable)
        if resolved is None:
            raise ValueError(
                f"Falta la variable de entorno {variable} usada en test_data.json."
            )
        return resolved
    return value


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _read_json(filepath: Path) -> dict:
    if not filepath.exists():
        return {}
    with filepath.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{filepath.name} debe contener un objeto JSON.")
    return data

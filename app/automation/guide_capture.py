from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Error as PlaywrightError

from app.audit.page_checks import safe_url


CAPTURE_TIMEOUT_MS = 10000

# Funciones de página compartidas con la grabación por IA: la etiqueta que
# un usuario ve de un control y si se escribe en él.
DESCRIBE_ELEMENT_JS = r"""
const clean = text => (text || "").replace(/\s+/g, " ").trim();
const NOT_TYPED = ["submit", "button", "checkbox", "radio", "reset", "image", "file"];
function describeElement(element) {
    const withoutControls = node => {
        if (!node) return "";
        const copy = node.cloneNode(true);
        copy.querySelectorAll("input, select, textarea").forEach(item => item.remove());
        return copy.textContent;
    };
    const tag = element.tagName.toLowerCase();
    const type = (element.getAttribute("type") || "").toLowerCase();
    const forLabel = element.id
        ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)
        : null;
    const labelledBy = (element.getAttribute("aria-labelledby") || "")
        .split(/\s+/)
        .filter(Boolean)
        .map(id => document.getElementById(id)?.textContent)
        .join(" ");
    const buttonValue = tag === "input" && ["submit", "button"].includes(type)
        ? element.value
        : "";
    const image = element.querySelector ? element.querySelector("img") : null;
    const candidates = [
        element.getAttribute("aria-label"),
        labelledBy,
        withoutControls(forLabel || element.closest("label")),
        element.getAttribute("placeholder"),
        buttonValue,
        tag === "select" ? "" : element.innerText,
        element.title,
        element.getAttribute("alt"),
        image && image.alt,
    ];
    return {
        label: candidates.map(clean).find(Boolean) || "",
        tag,
        type,
        role: element.getAttribute("role") || "",
        editable: tag === "textarea" || tag === "select" || element.isContentEditable
            || (tag === "input" && !NOT_TYPED.includes(type)),
    };
}
"""

# Lee lo que un usuario ve del elemento: su etiqueta visible y, en un
# select, el texto de la opción que se va a elegir.
ELEMENT_INFO_SCRIPT = f"""
(element, value) => {{
    {DESCRIBE_ELEMENT_JS}
    const option = element.tagName.toLowerCase() === "select"
        ? [...element.options].find(item => item.value === value)
        : null;
    return {{...describeElement(element), option: option ? clean(option.text) : ""}};
}}
"""
# Clics que no suelen cambiar lo que se ve: entrar a un campo o marcar una casilla.
STAY_KINDS = {"field", "password", "select", "check"}
SCROLL_SCRIPT = "() => [Math.round(window.scrollX), Math.round(window.scrollY)]"


def element_kind(info: dict) -> str:
    """Tipo de control según cómo lo usa una persona."""

    role = info.get("role")
    if info.get("tag") == "select":
        return "select"
    if info.get("type") in {"checkbox", "radio"} or role in {"checkbox", "radio", "switch"}:
        return "check"
    if info.get("type") == "password":
        return "password"
    if info.get("editable"):
        return "field"
    if info.get("tag") == "a" or role == "link":
        return "link"
    return "button"


def element_target(element: dict) -> str:
    return element.get("selector") or element.get("id") or element.get("name") or ""


class GuideCapture:
    """
    Registra, durante una reproducción, lo necesario para la guía de uso:
    una captura de cada pantalla, la etiqueta visible de cada control y su
    posición para marcarlo. Nunca guarda los valores escritos.

    Los pasos seguidos sobre la misma vista (por ejemplo, los campos de un
    formulario) comparten captura. Se toma una nueva cuando cambia la URL,
    el título o el desplazamiento, después de elegir en una lista o después
    de un clic en un botón o enlace, porque puede haber abierto un menú o un
    modal.
    """

    def __init__(self, run_folder: Path, log_callback: Optional[Callable[[str], None]] = None):
        self.run_folder = run_folder
        self.log_callback = log_callback
        self.steps: list[dict] = []
        self.shots: list[dict] = []
        self.final_shot: int | None = None
        self._view_changed = True

    def _log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def capture_step(self, page, locator, action: dict, sequence: int, value=None) -> None:
        try:
            self._capture_step(page, locator, action, sequence, value)
        except PlaywrightError as error:
            reason = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
            self._log(f"Guía: no se pudo capturar el paso {sequence}: {reason}")

    def finish(self, page) -> None:
        """Captura la pantalla con la que termina el flujo."""

        try:
            self.final_shot = self._new_shot(page, *self._view(page))
        except PlaywrightError as error:
            self._log(f"Guía: no se pudo capturar la pantalla final: {error}")

    def to_dict(self) -> dict:
        return {"steps": self.steps, "shots": self.shots, "final_shot": self.final_shot}

    def _capture_step(self, page, locator, action: dict, sequence: int, value) -> None:
        locator.wait_for(state="visible", timeout=CAPTURE_TIMEOUT_MS)
        locator.scroll_into_view_if_needed(timeout=CAPTURE_TIMEOUT_MS)
        info = locator.evaluate(ELEMENT_INFO_SCRIPT, value, timeout=CAPTURE_TIMEOUT_MS)
        box = locator.bounding_box(timeout=CAPTURE_TIMEOUT_MS)
        url, title, scroll = self._view(page)

        shot = len(self.shots) - 1
        last = self.shots[shot] if self.shots else None
        if (
            self._view_changed
            or last is None
            or (last["url"], last["title"], last["scroll"]) != (url, title, scroll)
        ):
            shot = self._new_shot(page, url, title, scroll)

        kind = element_kind(info)
        action_type = action.get("action_type")
        self.steps.append({
            "step": sequence,
            "action_type": action_type,
            "kind": kind,
            "label": info.get("label", ""),
            "option": info.get("option", ""),
            "target": element_target(action.get("element") or {}),
            "url": url,
            "title": title,
            "shot": shot,
            "box": box,
        })
        self._view_changed = (action_type == "click" and kind not in STAY_KINDS) or (
            kind == "select" and action_type == "change"
        )

    def _view(self, page) -> tuple[str, str, list[int]]:
        return safe_url(page.url), page.title(), page.evaluate(SCROLL_SCRIPT)

    def _new_shot(self, page, url: str, title: str, scroll: list[int]) -> int:
        filename = f"guia_{len(self.shots) + 1:03d}.png"
        page.screenshot(path=str(self.run_folder / filename), timeout=CAPTURE_TIMEOUT_MS)
        viewport = page.viewport_size or {}
        self.shots.append({
            "file": filename,
            "url": url,
            "title": title,
            "scroll": scroll,
            "viewport_width": viewport.get("width"),
        })
        return len(self.shots) - 1

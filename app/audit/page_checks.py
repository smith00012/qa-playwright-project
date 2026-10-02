from typing import Callable, Optional
from urllib.parse import parse_qsl, urlsplit

from playwright.sync_api import Error as PlaywrightError

from app.automation.run_settings import RunSettings


LOAD_TIMEOUT_MS = 15000
# Parámetros que identifican el endpoint (no datos personales) y se conservan.
KEPT_QUERY_VALUES = ("action", "wc-ajax")
MAX_AXE_TARGETS = 3
# Cancelaciones normales al navegar, según Chromium, Firefox y WebKit.
IGNORED_REQUEST_FAILURES = ("net::ERR_ABORTED", "NS_BINDING_ABORTED", "Load request cancelled")
# Chromium y WebKit repiten en consola cada respuesta 4xx/5xx, que ya se
# registra como error de red.
DUPLICATED_CONSOLE_PREFIX = "Failed to load resource"

TIMING_SCRIPT = """
async () => {
    const navigation = performance.getEntriesByType("navigation")[0];
    let lcp = null;
    const supported = PerformanceObserver.supportedEntryTypes || [];
    if (supported.includes("largest-contentful-paint")) {
        lcp = await new Promise(resolve => {
            new PerformanceObserver(list => {
                const entries = list.getEntries();
                resolve(entries.length ? entries[entries.length - 1].startTime : null);
            }).observe({ type: "largest-contentful-paint", buffered: true });
            setTimeout(() => resolve(null), 300);
        });
    }
    if (!navigation) {
        return { lcp };
    }
    return {
        ttfb: navigation.responseStart,
        dom_content_loaded: navigation.domContentLoadedEventEnd,
        load: navigation.loadEventEnd,
        lcp,
    };
}
"""


def safe_url(url: str) -> str:
    """
    Quita los valores de la query y el fragmento: pueden llevar correos,
    tokens u otros datos. Conserva los nombres de los parámetros y el
    valor de los que identifican el endpoint (por ejemplo action en
    admin-ajax.php).
    """

    parts = urlsplit(url or "")
    base = f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.scheme else url
    if not parts.query:
        return base
    params = [
        f"{key}={value}" if key in KEPT_QUERY_VALUES else f"{key}=…"
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return f"{base}?{'&'.join(params) or '…'}"


class PageAuditor:
    """
    Revisa cada pantalla de una reproducción: errores de consola,
    peticiones fallidas, accesibilidad con axe y tiempos de carga.

    Es genérico: lo propio de un sitio (ruido a ignorar, umbral de
    carga) llega por RunSettings desde data/run_config.json.
    """

    def __init__(
        self,
        settings: RunSettings,
        log_callback: Optional[Callable[[str], None]] = None,
        axe=None,
    ):
        self.settings = settings
        self.log_callback = log_callback
        self.current_step = 0
        self.console_errors: list[dict] = []
        self.network_errors: list[dict] = []
        self.pages: list[dict] = []
        self._page = None
        self._axe = axe
        self._last_url: str | None = None
        self._accessibility_checked: set[str] = set()

    def _log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def attach(self, page) -> None:
        self._page = page
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

    # ----------------------------------------------------------
    # Eventos del navegador
    # ----------------------------------------------------------

    def _on_console(self, message) -> None:
        if message.type != "error" or message.text.startswith(DUPLICATED_CONSOLE_PREFIX):
            return
        source = (message.location or {}).get("url", "")
        if self._is_ignored(message.text, source):
            return
        self.console_errors.append({
            "step": self.current_step,
            "page": self._current_page_url(),
            "type": "consola",
            "message": message.text[:500],
            "source": safe_url(source),
        })

    def _on_page_error(self, error) -> None:
        text = str(error)
        if self._is_ignored(text):
            return
        self.console_errors.append({
            "step": self.current_step,
            "page": self._current_page_url(),
            "type": "excepción",
            "message": text[:500],
            "source": "",
        })

    def _on_response(self, response) -> None:
        if response.status < 400 or self._is_ignored(response.url):
            return
        request = response.request
        self.network_errors.append({
            "step": self.current_step,
            "page": self._current_page_url(),
            "status": response.status,
            "method": request.method,
            "resource_type": request.resource_type,
            "url": safe_url(response.url),
        })

    def _on_request_failed(self, request) -> None:
        failure = request.failure or ""
        if any(ignored in failure for ignored in IGNORED_REQUEST_FAILURES):
            return
        if self._is_ignored(request.url, failure):
            return
        self.network_errors.append({
            "step": self.current_step,
            "page": self._current_page_url(),
            "status": None,
            "method": request.method,
            "resource_type": request.resource_type,
            "url": safe_url(request.url),
            "error": failure,
        })

    # ----------------------------------------------------------
    # Revisión de la pantalla actual
    # ----------------------------------------------------------

    def check_page(self, page, step: int) -> dict | None:
        """Revisa la pantalla si la URL cambió desde la última revisión."""

        url = page.url
        if url == self._last_url:
            return None
        self._last_url = url
        page_key = safe_url(url)

        loaded = True
        try:
            page.wait_for_load_state("load", timeout=LOAD_TIMEOUT_MS)
        except PlaywrightError:
            loaded = False

        timings = self._measure(page)
        load_ms = timings.get("load")
        page_result = {
            "step": step,
            "url": page_key,
            "loaded": loaded,
            "timings_ms": timings,
            "slow": bool(load_ms and load_ms > self.settings.slow_load_ms) or not loaded,
            "accessibility": None,
        }

        if self.settings.accessibility and page_key not in self._accessibility_checked:
            self._accessibility_checked.add(page_key)
            page_result["accessibility"] = self._run_axe(page)

        self.pages.append(page_result)
        self._log(self._page_summary(page_result))
        return page_result

    def _measure(self, page) -> dict:
        try:
            raw = page.evaluate(TIMING_SCRIPT) or {}
        except PlaywrightError as error:
            self._log(f"No se pudieron medir los tiempos de carga: {error}")
            return {}
        return {
            key: round(value)
            for key, value in raw.items()
            if isinstance(value, (int, float)) and value > 0
        }

    def _run_axe(self, page) -> dict:
        try:
            if self._axe is None:
                from axe_playwright_python.sync_playwright import Axe

                self._axe = Axe()
            response = self._axe.run(page).response
        except Exception as error:  # axe puede fallar por CSP o por la página
            self._log(f"No se pudo revisar la accesibilidad: {error}")
            return {"error": str(error)[:300], "violations": []}

        violations = [
            {
                "id": violation.get("id"),
                "impact": violation.get("impact"),
                "help": violation.get("help"),
                "help_url": violation.get("helpUrl"),
                "nodes": len(violation.get("nodes") or []),
                "targets": [
                    " ".join(map(str, node.get("target") or []))
                    for node in (violation.get("nodes") or [])[:MAX_AXE_TARGETS]
                ],
            }
            for violation in response.get("violations") or []
        ]
        return {"violations": violations}

    # ----------------------------------------------------------
    # Resultado
    # ----------------------------------------------------------

    def summary(self) -> dict:
        return {
            "console_errors": len(self.console_errors),
            "network_errors": len(self.network_errors),
            "accessibility_violations": sum(
                len((page["accessibility"] or {}).get("violations", []))
                for page in self.pages
            ),
            "slow_pages": sum(1 for page in self.pages if page["slow"]),
            "pages_checked": len(self.pages),
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "pages": self.pages,
            "console_errors": self.console_errors,
            "network_errors": self.network_errors,
        }

    def _page_summary(self, page_result: dict) -> str:
        parts = [f"Revisión de {page_result['url']}:"]
        load_ms = page_result["timings_ms"].get("load")
        if not page_result["loaded"]:
            parts.append("no terminó de cargar")
        elif load_ms:
            parts.append(f"carga {load_ms} ms" + (" (lenta)" if page_result["slow"] else ""))
        accessibility = page_result["accessibility"]
        if accessibility is not None:
            parts.append(f"{len(accessibility['violations'])} problemas de accesibilidad")
        parts.append(
            f"{len(self.console_errors)} errores de consola y "
            f"{len(self.network_errors)} de red acumulados"
        )
        return " ".join(parts[:1]) + " " + ", ".join(parts[1:]) + "."

    def _current_page_url(self) -> str:
        try:
            return safe_url(self._page.url) if self._page else ""
        except PlaywrightError:
            return ""

    def _is_ignored(self, *values: str) -> bool:
        lowered = [value.casefold() for value in values if value]
        return any(
            pattern.casefold() in value
            for pattern in self.settings.ignore_patterns
            for value in lowered
        )

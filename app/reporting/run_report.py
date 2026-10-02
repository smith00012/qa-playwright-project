import json
from collections import defaultdict
from html import escape
from pathlib import Path


REPORT_FILENAME = "report.html"

STATUS_LABELS = {
    "completado": ("Completado", "ok"),
    "detenido_por_seguridad": ("Detenido por seguridad", "warn"),
    "fallido": ("Fallido", "bad"),
}
IMPACT_ORDER = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
IMPACT_LABELS = {
    "critical": "Crítico",
    "serious": "Grave",
    "moderate": "Moderado",
    "minor": "Menor",
}
TIMING_LABELS = (
    ("ttfb", "TTFB"),
    ("dom_content_loaded", "DOM listo"),
    ("load", "Carga"),
    ("lcp", "LCP"),
)


MAX_EXAMPLES = 3


def url_pattern(url: str) -> str:
    """
    Clave para agrupar URLs parecidas: la URL completa si identifica un
    endpoint (lleva query), o su carpeta si es un recurso estático, para
    que cientos de imágenes de una misma carpeta cuenten como un hallazgo.
    """

    if "?" in url:
        return url
    return url.rsplit("/", 1)[0] + "/" if "/" in url else url


def group_by(items: list[dict], key) -> list[tuple]:
    groups: dict = {}
    for item in items:
        groups.setdefault(key(item), []).append(item)
    return sorted(groups.items(), key=lambda group: -len(group[1]))


def steps_text(items: list[dict]) -> str:
    steps = sorted({item.get("step") for item in items if item.get("step") is not None})
    if len(steps) > 4:
        return f"{steps[0]}–{steps[-1]}"
    return ", ".join(str(step) for step in steps)


class RunReportGenerator:
    """
    Genera report.html de una corrida a partir de result.json y
    findings.json. El HTML es autónomo: estilos en línea y capturas
    referenciadas con rutas relativas dentro de la misma carpeta.
    """

    def generate(self, run_folder: Path) -> Path:
        result = self._read(run_folder / "result.json")
        findings = self._read(run_folder / "findings.json")
        report_file = run_folder / REPORT_FILENAME
        report_file.write_text(self.render(result, findings), encoding="utf-8")
        return report_file

    def render(self, result: dict, findings: dict) -> str:
        sections = [
            self._header(result),
            self._outcome(result),
            self._summary(findings.get("summary") or {}),
            self._pages(findings.get("pages") or []),
            self._network(findings.get("network_errors") or []),
            self._console(findings.get("console_errors") or []),
            self._accessibility(findings.get("pages") or []),
            self._trace(result),
        ]
        title = escape(f"Reporte · {result.get('flow_name', 'Flujo')}")
        return (
            "<!doctype html>\n<html lang=\"es\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{title}</title>\n<style>{STYLES}</style>\n</head>\n<body>\n<main>\n"
            + "\n".join(section for section in sections if section)
            + "\n</main>\n</body>\n</html>\n"
        )

    # ----------------------------------------------------------
    # Secciones
    # ----------------------------------------------------------

    def _header(self, result: dict) -> str:
        label, tone = STATUS_LABELS.get(result.get("status"), (result.get("status", ""), "warn"))
        mode = "segundo plano" if result.get("headless") else "visible"
        guard = "activado" if result.get("stop_guard") else "desactivado"
        meta = [
            ("Fecha", (result.get("started_at") or "").replace("T", " ")),
            ("Duración", f"{result['duration_s']} s" if result.get("duration_s") is not None else ""),
            ("Ambiente", result.get("environment") or "sin ambiente"),
            ("Freno de pago", guard),
            ("Navegador", f"{result.get('browser', '')} · {result.get('device', '')} · {mode}"),
            ("Pasos", f"{result.get('steps_run', 0)} de {result.get('total_steps', 0)}"),
        ]
        items = "".join(
            f"<div><dt>{escape(name)}</dt><dd>{escape(str(value))}</dd></div>"
            for name, value in meta
            if value
        )
        return (
            "<header>"
            "<p class=\"eyebrow\">Reporte de corrida</p>"
            f"<h1>{escape(result.get('flow_name', 'Flujo'))}</h1>"
            f"<span class=\"badge {tone}\">{escape(label)}</span>"
            f"<dl class=\"meta\">{items}</dl>"
            "</header>"
        )

    def _outcome(self, result: dict) -> str:
        status = result.get("status")
        screenshot = self._image(result.get("screenshot"), "Captura del momento")
        if status == "fallido":
            step = result.get("failed_step")
            where = f"Falló en el paso {step}" if step else "Falló antes de iniciar los pasos"
            action = result.get("failed_action")
            detail = f": {escape(action)}" if action else ""
            return (
                "<section class=\"callout bad\"><h2>Resultado</h2>"
                f"<p><strong>{escape(where)}</strong>{detail}.</p>"
                f"<pre>{escape(result.get('error') or '')}</pre>{screenshot}</section>"
            )
        if status == "detenido_por_seguridad":
            return (
                "<section class=\"callout warn\"><h2>Resultado</h2>"
                "<p>El freno de pago detuvo el flujo antes del paso bloqueado "
                f"<strong>“{escape(result.get('stopped_at') or '')}”</strong>. "
                "No es una falla: el flujo llegó hasta donde se permite en este ambiente.</p>"
                f"{screenshot}</section>"
            )
        return (
            "<section class=\"callout ok\"><h2>Resultado</h2>"
            "<p>El flujo se completó sin errores de ejecución.</p></section>"
        )

    def _summary(self, summary: dict) -> str:
        tiles = [
            ("Errores de red", summary.get("network_errors", 0)),
            ("Errores de consola", summary.get("console_errors", 0)),
            ("Problemas de accesibilidad", summary.get("accessibility_violations", 0)),
            ("Pantallas lentas", summary.get("slow_pages", 0)),
        ]
        cards = "".join(
            f"<div class=\"tile{' flagged' if value else ''}\">"
            f"<span class=\"value\">{value}</span><span class=\"label\">{escape(label)}</span></div>"
            for label, value in tiles
        )
        checked = summary.get("pages_checked", 0)
        return (
            "<section><h2>Hallazgos</h2>"
            f"<div class=\"tiles\">{cards}</div>"
            f"<p class=\"note\">{checked} pantallas revisadas. Los hallazgos no cambian el "
            "resultado del flujo: un flujo puede completarse y tener hallazgos.</p></section>"
        )

    def _pages(self, pages: list[dict]) -> str:
        if not pages:
            return ""
        rows = []
        for page in pages:
            timings = page.get("timings_ms") or {}
            timing_text = " · ".join(
                f"{label} {timings[key]} ms" for key, label in TIMING_LABELS if key in timings
            ) or "sin medición"
            if not page.get("loaded", True):
                timing_text = "No terminó de cargar · " + timing_text
            accessibility = page.get("accessibility")
            if accessibility is None:
                axe_text = "revisada antes"
            elif accessibility.get("error"):
                axe_text = "no se pudo revisar"
            else:
                axe_text = str(len(accessibility.get("violations") or []))
            thumbnail = self._image(page.get("screenshot"), f"Paso {page.get('step')}", thumbnail=True)
            row_class = ' class="flagged"' if page.get("slow") else ""
            slow_tag = ' <span class="tag bad">lenta</span>' if page.get("slow") else ""
            rows.append(
                f"<tr{row_class}>"
                f"<td class=\"num\">{page.get('step')}</td>"
                f"<td class=\"url\">{escape(page.get('url', ''))}</td>"
                f"<td>{escape(timing_text)}{slow_tag}</td>"
                f"<td class=\"num\">{escape(axe_text)}</td>"
                f"<td>{thumbnail}</td></tr>"
            )
        return (
            "<section><h2>Pantallas recorridas</h2><div class=\"table-wrap\"><table>"
            "<thead><tr><th>Paso</th><th>URL</th><th>Tiempos</th><th>Accesibilidad</th><th>Captura</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div></section>"
        )

    def _network(self, errors: list[dict]) -> str:
        if not errors:
            return self._empty("Errores de red", "No hubo respuestas 4xx/5xx ni peticiones fallidas.")
        groups = group_by(
            errors,
            lambda error: (
                str(error.get("status") or error.get("error") or ""),
                error.get("method", ""),
                error.get("resource_type", ""),
                url_pattern(error.get("url", "")),
            ),
        )
        rows = "".join(
            "<tr>"
            f"<td class=\"num\">{escape(status)}</td>"
            f"<td>{escape(method)} · {escape(resource_type)}</td>"
            f"<td class=\"url\">{self._examples([error.get('url', '') for error in items])}</td>"
            f"<td class=\"num\">{len(items)}</td>"
            f"<td class=\"num\">{escape(steps_text(items))}</td></tr>"
            for (status, method, resource_type, _pattern), items in groups
        )
        return (
            "<section><h2>Errores de red</h2>"
            "<p class=\"note\">Agrupados por estado, tipo y ruta. Se muestran hasta tres URLs de ejemplo.</p>"
            "<div class=\"table-wrap\"><table>"
            "<thead><tr><th>Estado</th><th>Petición</th><th>URL</th><th>Veces</th><th>Pasos</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div></section>"
        )

    def _console(self, errors: list[dict]) -> str:
        if not errors:
            return self._empty("Errores de consola", "No hubo errores de JavaScript.")
        groups = group_by(errors, lambda error: (error.get("type", ""), error.get("message", "")))
        rows = "".join(
            "<tr>"
            f"<td>{escape(kind)}</td>"
            f"<td><code>{escape(message)}</code></td>"
            f"<td class=\"url\">{self._examples([error.get('source') or error.get('page', '') for error in items])}</td>"
            f"<td class=\"num\">{len(items)}</td>"
            f"<td class=\"num\">{escape(steps_text(items))}</td></tr>"
            for (kind, message), items in groups
        )
        return (
            "<section><h2>Errores de consola</h2><div class=\"table-wrap\"><table>"
            "<thead><tr><th>Tipo</th><th>Mensaje</th><th>Origen</th><th>Veces</th><th>Pasos</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div></section>"
        )

    @staticmethod
    def _examples(urls: list[str]) -> str:
        unique = list(dict.fromkeys(url for url in urls if url))
        shown = "<br>".join(escape(url) for url in unique[:MAX_EXAMPLES])
        hidden = len(unique) - MAX_EXAMPLES
        return shown + (f"<div class=\"sub\">y {hidden} más</div>" if hidden > 0 else "")

    def _accessibility(self, pages: list[dict]) -> str:
        rules: dict[str, dict] = {}
        affected: dict[str, list[str]] = defaultdict(list)
        for page in pages:
            for violation in (page.get("accessibility") or {}).get("violations") or []:
                rule_id = violation.get("id", "")
                rule = rules.setdefault(rule_id, {**violation, "nodes": 0, "targets": []})
                rule["nodes"] += violation.get("nodes", 0)
                rule["targets"] = (rule["targets"] + violation.get("targets", []))[:3]
                affected[rule_id].append(page.get("url", ""))

        if not rules:
            return self._empty("Accesibilidad", "axe no encontró problemas en las pantallas revisadas.")

        ordered = sorted(rules.values(), key=lambda rule: IMPACT_ORDER.get(rule.get("impact"), 9))
        rows = []
        for rule in ordered:
            impact = rule.get("impact") or ""
            tone = "bad" if impact in {"critical", "serious"} else "warn"
            targets = "".join(f"<code>{escape(target)}</code>" for target in rule["targets"])
            pages_text = "<br>".join(escape(url) for url in dict.fromkeys(affected[rule["id"]]))
            rows.append(
                "<tr>"
                f"<td><span class=\"tag {tone}\">{escape(IMPACT_LABELS.get(impact, impact))}</span></td>"
                f"<td><a href=\"{escape(rule.get('help_url') or '#')}\">{escape(rule.get('id', ''))}</a>"
                f"<div class=\"sub\">{escape(rule.get('help') or '')}</div></td>"
                f"<td class=\"num\">{rule['nodes']}</td>"
                f"<td class=\"targets\">{targets}</td>"
                f"<td class=\"url\">{pages_text}</td></tr>"
            )
        return (
            "<section><h2>Accesibilidad</h2><div class=\"table-wrap\"><table>"
            "<thead><tr><th>Impacto</th><th>Regla</th><th>Elementos</th><th>Ejemplos</th><th>Pantallas</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div></section>"
        )

    def _trace(self, result: dict) -> str:
        if not result.get("trace_file"):
            return ""
        trace = escape(result["trace_file"])
        return (
            "<section><h2>Traza</h2>"
            "<p>Paso a paso con capturas, DOM, consola y red. Desde la carpeta del proyecto:</p>"
            f"<pre>.\\.venv\\Scripts\\python.exe -m playwright show-trace \"{trace}\"</pre></section>"
        )

    # ----------------------------------------------------------
    # Utilidades
    # ----------------------------------------------------------

    @staticmethod
    def _empty(title: str, message: str) -> str:
        return f"<section><h2>{escape(title)}</h2><p class=\"note\">{escape(message)}</p></section>"

    @staticmethod
    def _image(filename: str | None, alt: str, thumbnail: bool = False) -> str:
        if not filename:
            return ""
        source = escape(filename)
        css_class = "thumb" if thumbnail else "shot"
        return (
            f"<a href=\"{source}\" target=\"_blank\">"
            f"<img class=\"{css_class}\" src=\"{source}\" alt=\"{escape(alt)}\"></a>"
        )

    @staticmethod
    def _read(filepath: Path) -> dict:
        if not filepath.exists():
            return {}
        with filepath.open("r", encoding="utf-8") as file:
            return json.load(file)


STYLES = """
:root {
  --bg: #f1f4f2; --panel: #ffffff; --text: #203332; --muted: #657773;
  --border: #d9e3df; --brand: #163a3a; --accent: #216b66;
  --ok: #2e806b; --ok-bg: #e6f2ee; --warn: #9a6b12; --warn-bg: #fbf3e2;
  --bad: #b94d58; --bad-bg: #f8eeeb; --code-bg: #f7faf8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111a1a; --panel: #182424; --text: #e4ecea; --muted: #9db0ab;
    --border: #2b3b3a; --brand: #0d2626; --accent: #6cc3bb;
    --ok: #6cc3a5; --ok-bg: #16302a; --warn: #e5b35a; --warn-bg: #33291a;
    --bad: #e88a93; --bad-bg: #3a1f23; --code-bg: #121c1c;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 "Segoe UI", system-ui, sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 0 16px 48px; }
header { background: var(--brand); color: #f7faf8; margin: 0 -16px 24px; padding: 28px 32px; }
.eyebrow { margin: 0; font-size: 12px; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: #bfd0cb; }
h1 { margin: 4px 0 12px; font-size: 26px; }
h2 { font-size: 17px; margin: 0 0 12px; }
section { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 20px; margin-bottom: 16px; }
.meta { display: flex; flex-wrap: wrap; gap: 8px 28px; margin: 16px 0 0; }
.meta dt { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: #bfd0cb; }
.meta dd { margin: 0; font-weight: 600; }
.badge, .tag { display: inline-block; border-radius: 999px; font-weight: 700; }
.badge { padding: 4px 12px; font-size: 13px; }
.tag { padding: 1px 8px; font-size: 12px; }
.ok { background: var(--ok-bg); color: var(--ok); }
.warn { background: var(--warn-bg); color: var(--warn); }
.bad { background: var(--bad-bg); color: var(--bad); }
.callout.ok { border-left: 4px solid var(--ok); }
.callout.warn { border-left: 4px solid var(--warn); }
.callout.bad { border-left: 4px solid var(--bad); }
.callout { color: var(--text); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.tile { border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
.tile .value { display: block; font-size: 28px; font-weight: 700; }
.tile .label { color: var(--muted); font-size: 13px; }
.tile.flagged .value { color: var(--bad); }
.note { color: var(--muted); margin: 12px 0 0; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; vertical-align: top; padding: 8px 10px; border-bottom: 1px solid var(--border); }
th { color: var(--muted); font-weight: 600; white-space: nowrap; }
tr.flagged td { background: var(--bad-bg); }
td.num { text-align: right; white-space: nowrap; }
td.url { word-break: break-all; min-width: 200px; }
td.targets code { display: block; margin-bottom: 4px; }
.sub { color: var(--muted); }
a { color: var(--accent); }
code, pre { font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px; }
code { background: var(--code-bg); padding: 1px 4px; border-radius: 4px; word-break: break-all; }
pre { background: var(--code-bg); border: 1px solid var(--border); border-radius: 6px;
  padding: 12px; white-space: pre-wrap; word-break: break-all; }
img.thumb { width: 140px; border: 1px solid var(--border); border-radius: 4px; display: block; }
img.shot { max-width: 100%; border: 1px solid var(--border); border-radius: 6px; margin-top: 12px; }
@media (max-width: 640px) { header { padding: 22px 16px; } h1 { font-size: 21px; } }
"""

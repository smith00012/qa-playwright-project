import json
import tempfile
import unittest
from pathlib import Path

from app.reporting.run_report import REPORT_FILENAME, RunReportGenerator


RESULT = {
    "flow_name": "Compra <curso>",
    "status": "fallido",
    "environment": None,
    "stop_guard": True,
    "browser": "chromium",
    "device": "Escritorio",
    "headless": True,
    "steps_run": 3,
    "total_steps": 10,
    "started_at": "2026-10-02T15:00:00",
    "duration_s": 12.5,
    "failed_step": 4,
    "failed_action": "click Comprar ahora",
    "error": "Locator.click: Timeout 10000ms exceeded.",
    "screenshot": "fallo.png",
    "trace_file": "data/runs/run_x/trace.zip",
}

FINDINGS = {
    "summary": {
        "console_errors": 1,
        "network_errors": 1,
        "accessibility_violations": 2,
        "slow_pages": 1,
        "pages_checked": 2,
    },
    "pages": [
        {
            "step": 0,
            "url": "https://sitio.test/",
            "loaded": True,
            "timings_ms": {"ttfb": 90, "load": 4500},
            "slow": True,
            "screenshot": "pantalla_000.png",
            "accessibility": {"violations": [
                {"id": "region", "impact": "moderate", "help": "Contenido fuera de regiones",
                 "help_url": "https://x.test/region", "nodes": 1, "targets": [".support-fab"]},
                {"id": "image-alt", "impact": "critical", "help": "Imágenes sin texto alternativo",
                 "help_url": "https://x.test/image-alt", "nodes": 2, "targets": ["img.hero"]},
            ]},
        },
        {
            "step": 2,
            "url": "https://sitio.test/checkout/",
            "loaded": True,
            "timings_ms": {"load": 900},
            "slow": False,
            "screenshot": None,
            "accessibility": None,
        },
    ],
    "console_errors": [
        {"step": 2, "page": "https://sitio.test/checkout/", "type": "excepción",
         "message": "<script>alert(1)</script>", "source": ""},
    ],
    "network_errors": [
        {"step": 2, "page": "https://sitio.test/checkout/", "status": 400, "method": "GET",
         "resource_type": "xhr", "url": "https://sitio.test/wp-admin/admin-ajax.php?action=usil_consultar_cupos&nonce=…"},
    ],
}


class RunReportTests(unittest.TestCase):
    def test_generates_report_file_from_run_folder(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_folder = Path(temporary_directory)
            (run_folder / "result.json").write_text(json.dumps(RESULT), encoding="utf-8")
            (run_folder / "findings.json").write_text(json.dumps(FINDINGS), encoding="utf-8")

            report_file = RunReportGenerator().generate(run_folder)

            self.assertEqual(report_file.name, REPORT_FILENAME)
            self.assertIn("<!doctype html>", report_file.read_text(encoding="utf-8"))

    def test_failed_run_shows_step_error_and_screenshot(self):
        html = RunReportGenerator().render(RESULT, FINDINGS)

        self.assertIn("Fallido", html)
        self.assertIn("Falló en el paso 4", html)
        self.assertIn("click Comprar ahora", html)
        self.assertIn('src="fallo.png"', html)
        self.assertIn("usil_consultar_cupos", html)
        self.assertIn('src="pantalla_000.png"', html)

    def test_escapes_page_content(self):
        html = RunReportGenerator().render(RESULT, FINDINGS)

        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("Compra &lt;curso&gt;", html)

    def test_accessibility_rules_are_ordered_by_impact(self):
        html = RunReportGenerator().render(RESULT, FINDINGS)

        self.assertLess(html.index("image-alt"), html.index(">region<"))

    def test_stopped_run_is_not_presented_as_failure(self):
        stopped = {**RESULT, "status": "detenido_por_seguridad", "stopped_at": "Continuar al pago",
                   "screenshot": "detenido.png", "error": None, "failed_step": None}

        html = RunReportGenerator().render(stopped, {"summary": {}})

        self.assertIn("Detenido por seguridad", html)
        self.assertIn("Continuar al pago", html)
        self.assertIn("No es una falla", html)
        self.assertNotIn("Falló en el paso", html)

    def test_repeated_network_errors_are_grouped(self):
        emoji = [
            {"step": step, "status": None, "error": "net::ERR_FAILED", "method": "GET",
             "resource_type": "image", "url": f"https://s.w.org/images/core/emoji/{code}.svg", "page": "p"}
            for step, code in enumerate(range(241))
        ]
        findings = {**FINDINGS, "network_errors": FINDINGS["network_errors"] + emoji}

        html = RunReportGenerator().render(RESULT, findings)

        self.assertIn("<td class=\"num\">241</td>", html)
        self.assertIn("y 238 más", html)
        self.assertIn("0–240", html)
        self.assertEqual(html.count("s.w.org/images/core/emoji/"), 3)
        self.assertIn("usil_consultar_cupos", html)


if __name__ == "__main__":
    unittest.main()

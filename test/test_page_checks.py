import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.audit.page_checks import PageAuditor, safe_url
from app.automation.run_settings import RunSettings, load_run_settings
from app.config import AppPaths


class FakeAxe:
    def __init__(self, violations: list[dict]):
        self.violations = violations
        self.calls = 0

    def run(self, _page):
        self.calls += 1
        return SimpleNamespace(response={"violations": self.violations})


class FakePage:
    def __init__(self, url: str, timings: dict):
        self.url = url
        self.timings = timings
        self.handlers: dict = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def wait_for_load_state(self, _state, timeout=None):
        return None

    def evaluate(self, _script):
        return self.timings


def response(status: int, url: str):
    request = SimpleNamespace(method="GET", resource_type="script")
    return SimpleNamespace(status=status, url=url, request=request)


def console(kind: str, text: str, source: str = ""):
    return SimpleNamespace(type=kind, text=text, location={"url": source})


VIOLATION = {
    "id": "image-alt",
    "impact": "critical",
    "help": "Images must have alternate text",
    "helpUrl": "https://dequeuniversity.com/rules/axe/4.12/image-alt",
    "nodes": [{"target": ["img.hero"]}, {"target": ["img.logo"]}],
}


class PageAuditorTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage("https://sitio.test/checkout?email=a@b.test", {"load": 1200, "ttfb": 80.4})
        self.axe = FakeAxe([VIOLATION])
        self.settings = RunSettings(ignore_patterns=("googletagmanager",), slow_load_ms=1000)
        self.auditor = PageAuditor(self.settings, axe=self.axe)
        self.auditor.attach(self.page)

    def test_safe_url_removes_query_values(self):
        self.assertEqual(safe_url("https://a.test/x?email=a@b.test#f"), "https://a.test/x?email=…")
        self.assertEqual(
            safe_url("https://a.test/wp-admin/admin-ajax.php?action=usil_consultar_cupos&nonce=abc"),
            "https://a.test/wp-admin/admin-ajax.php?action=usil_consultar_cupos&nonce=…",
        )
        self.assertEqual(safe_url("https://a.test/x"), "https://a.test/x")

    def test_records_only_console_errors_and_skips_ignored_sources(self):
        self.auditor.current_step = 2
        self.page.handlers["console"](console("warning", "aviso"))
        self.page.handlers["console"](console("error", "Failed to load resource: 404", "https://sitio.test/a.js"))
        self.page.handlers["console"](console("error", "x is undefined", "https://www.googletagmanager.com/gtm.js"))
        self.page.handlers["console"](console("error", "Uncaught TypeError", "https://sitio.test/app.js?v=1"))

        self.assertEqual(len(self.auditor.console_errors), 1)
        error = self.auditor.console_errors[0]
        self.assertEqual(error["step"], 2)
        self.assertEqual(error["page"], "https://sitio.test/checkout?email=…")
        self.assertEqual(error["source"], "https://sitio.test/app.js?v=…")

    def test_records_http_errors_and_real_request_failures(self):
        self.page.handlers["response"](response(200, "https://sitio.test/ok"))
        self.page.handlers["response"](response(404, "https://sitio.test/falta.js"))
        aborted = SimpleNamespace(failure="net::ERR_ABORTED", url="https://sitio.test/a", method="GET", resource_type="image")
        refused = SimpleNamespace(failure="net::ERR_CONNECTION_REFUSED", url="https://api.test/x", method="POST", resource_type="fetch")
        self.page.handlers["requestfailed"](aborted)
        cancelled = SimpleNamespace(failure="Load request cancelled", url="https://sitio.test/b", method="GET", resource_type="script")
        self.page.handlers["requestfailed"](cancelled)
        self.page.handlers["requestfailed"](refused)

        statuses = [(error["status"], error["url"]) for error in self.auditor.network_errors]
        self.assertEqual(statuses, [(404, "https://sitio.test/falta.js"), (None, "https://api.test/x")])

    def test_checks_each_new_url_once_and_runs_axe_once_per_page(self):
        first = self.auditor.check_page(self.page, 0)
        repeated = self.auditor.check_page(self.page, 1)
        self.page.url = "https://sitio.test/gracias"
        self.auditor.check_page(self.page, 2)
        self.page.url = "https://sitio.test/checkout?email=otro@b.test"
        self.auditor.check_page(self.page, 3)

        self.assertIsNone(repeated)
        self.assertEqual(self.axe.calls, 2)
        self.assertTrue(first["slow"])
        self.assertEqual(first["timings_ms"], {"load": 1200, "ttfb": 80})
        self.assertEqual(first["accessibility"]["violations"][0]["targets"], ["img.hero", "img.logo"])
        self.assertIsNone(self.auditor.pages[-1]["accessibility"])

    def test_summary_counts_findings(self):
        self.auditor.check_page(self.page, 0)
        self.page.handlers["response"](response(500, "https://sitio.test/api"))

        summary = self.auditor.to_dict()["summary"]

        self.assertEqual(summary["network_errors"], 1)
        self.assertEqual(summary["accessibility_violations"], 1)
        self.assertEqual(summary["slow_pages"], 1)
        self.assertEqual(summary["pages_checked"], 1)

    def test_accessibility_can_be_disabled(self):
        auditor = PageAuditor(RunSettings(accessibility=False), axe=self.axe)

        auditor.check_page(self.page, 0)

        self.assertEqual(self.axe.calls, 0)


class ChecksSettingsTests(unittest.TestCase):
    def test_loads_checks_from_run_config(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = AppPaths(data_dir=Path(temporary_directory))
            paths.run_config_file.write_text(
                json.dumps({"revisiones": {"accesibilidad": False, "ignorar": ["hotjar"], "carga_lenta_ms": 2500}}),
                encoding="utf-8",
            )

            settings = load_run_settings(paths=paths)

        self.assertFalse(settings.accessibility)
        self.assertEqual(settings.ignore_patterns, ("hotjar",))
        self.assertEqual(settings.slow_load_ms, 2500)


if __name__ == "__main__":
    unittest.main()

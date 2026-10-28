import json
import tempfile
import unittest
from pathlib import Path

from app.config import AppPaths
from app.services.ai_recording import (
    GUARD_INFO_SCRIPT,
    AiRecordingError,
    AiRecordingSession,
    mark_ai_typed_values,
)


class FakeLocator:
    def __init__(self, guard_info=None, field_info=None):
        self.guard_info = guard_info or {"text": "Ingresar"}
        self.field_info = field_info or {"id": "email"}
        self.calls: list[tuple] = []

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def evaluate(self, script, timeout=None):
        return self.guard_info if script == GUARD_INFO_SCRIPT else self.field_info

    def click(self, timeout=None):
        self.calls.append(("click",))

    def fill(self, value, timeout=None):
        self.calls.append(("fill", value))


class FakeFrame:
    def __init__(self, locator):
        self._locator = locator

    def locator(self, _selector):
        return self._locator


class FakePage:
    url = "https://site/login?token=abc"

    def wait_for_timeout(self, _ms):
        pass

    def wait_for_load_state(self, *_args, **_kwargs):
        pass

    def title(self):
        return "Ingresar"


class FakeRecorder:
    def __init__(self, execution_folder, log_callback=None, device="Escritorio"):
        self.execution_folder = execution_folder
        self.device = device
        self.stopped = False

    def start(self, url):
        self.url = url

    def run_in_browser(self, command):
        return command(FakePage())

    def stop(self):
        self.stopped = True
        (self.execution_folder / "actions.json").write_text(json.dumps([
            {"sequence": 1, "action_type": "click", "url": self.url, "element": {"text": "Ingresar"}},
        ]), encoding="utf-8")


class AiRecordingSessionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.paths = AppPaths(Path(self.folder.name))
        self.paths.ensure_data_directories()
        self.paths.run_config_file.write_text(json.dumps({
            "pasos_bloqueados": ["Continuar al pago"],
            "ambientes": {"pruebas": {"freno_pago": False}},
        }), encoding="utf-8")
        self.paths.test_data_file.write_text(json.dumps({"campos": {"email": "ana@demo.pe"}}), encoding="utf-8")
        self.session = AiRecordingSession(self.paths, recorder_factory=FakeRecorder)

    def tearDown(self):
        self.folder.cleanup()

    def _with_control(self, locator):
        self.session._frames = {"1": FakeFrame(locator)}
        return locator

    def test_guard_blocks_ai_click_even_when_environment_disables_it(self):
        self.session.start("https://site", "Compra", "pruebas")
        locator = self._with_control(FakeLocator(guard_info={"text": "Continuar al pago"}))

        with self.assertRaises(AiRecordingError) as raised:
            self.session.click("1")

        self.assertIn("freno de pago", str(raised.exception))
        self.assertEqual(locator.calls, [])

    def test_click_reports_url_without_query_values(self):
        self.session.start("https://site", "Ingreso")
        locator = self._with_control(FakeLocator())

        answer = self.session.click("1")

        self.assertEqual(locator.calls, [("click",)])
        self.assertNotIn("abc", answer["url"])

    def test_test_data_is_typed_but_never_returned(self):
        self.session.start("https://site", "Ingreso")
        locator = self._with_control(FakeLocator())

        answer = self.session.type_text("1", use_test_data=True)

        self.assertEqual(locator.calls, [("fill", "ana@demo.pe")])
        self.assertNotIn("ana@demo.pe", json.dumps(answer))

    def test_missing_test_data_is_explained(self):
        self.session.start("https://site", "Ingreso")
        self._with_control(FakeLocator(field_info={"id": "clave"}))

        with self.assertRaises(AiRecordingError) as raised:
            self.session.type_text("1", use_test_data=True)

        self.assertIn("test_data.json", str(raised.exception))

    def test_text_and_test_data_are_exclusive(self):
        self.session.start("https://site", "Ingreso")
        for args in ({"text": "x", "use_test_data": True}, {}):
            with self.assertRaises(AiRecordingError):
                self.session.type_text("1", **args)

    def test_unknown_or_stale_ref_asks_for_screen_again(self):
        self.session.start("https://site", "Ingreso")
        for ref in ("9", "x"):
            with self.assertRaises(AiRecordingError) as raised:
                self.session.click(ref)
            self.assertIn("ver_pantalla", str(raised.exception))

    def test_rejects_non_web_urls_and_second_session(self):
        with self.assertRaises(AiRecordingError):
            self.session.start("file:///C:/Windows", "x")
        self.session.start("https://site", "Ingreso")
        with self.assertRaises(AiRecordingError):
            self.session.start("https://site", "Otro")

    def test_values_typed_by_ai_are_marked_fixed_and_test_data_is_not(self):
        password = {"selector": "#password", "id": "password"}
        actions = [
            {"sequence": 1, "action_type": "input", "element": password, "value": "[MASKED]"},
            {"sequence": 2, "action_type": "click", "element": {"text": "Ingresar"}},
            {"sequence": 3, "action_type": "input", "element": password, "value": "[MASKED]"},
            {"sequence": 4, "action_type": "change", "element": password, "value": "[MASKED]"},
        ]
        # 1: dato de prueba (administrador); 3-4: la clave que la IA escribió para otro usuario.
        writes = [(0, {"selector": "#password"}, None), (2, {"selector": "#password"}, "Clave-Chofer-26")]

        mark_ai_typed_values(actions, writes)

        self.assertNotIn("fixed_value", actions[0])
        self.assertEqual(actions[0]["value"], "[MASKED]")
        self.assertEqual(
            [(a["value"], a.get("fixed_value")) for a in actions[2:]],
            [("Clave-Chofer-26", True), ("Clave-Chofer-26", True)],
        )

    def test_recording_opens_the_chosen_phone_and_rejects_unknown_devices(self):
        answer = self.session.start("https://site", "Celular", device="Pixel 7")

        self.assertEqual(answer["dispositivo"], "Pixel 7")
        self.assertEqual(self.session.recorder.device, "Pixel 7")
        self.session.finish(save=False)
        with self.assertRaises(AiRecordingError):
            self.session.start("https://site", "Celular", device="Nokia 3310")

    def test_finish_saves_flow(self):
        self.session.start("https://site", "Ingreso demo")

        answer = self.session.finish()

        self.assertTrue(answer["guardado"])
        self.assertTrue((self.paths.flows_dir / f"{answer['flujo']}.json").exists())
        self.assertFalse(self.session.active)


if __name__ == "__main__":
    unittest.main()

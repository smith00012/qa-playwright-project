import unittest

from app.automation.flow_runner import FlowRunner
from app.automation.run_settings import RunSettings


class FakeLocator:
    def __init__(self, runtime_text: str = "", options: tuple[str, ...] = ()):
        self.runtime_text = runtime_text
        self.options = options
        self.inside_modal = False
        self.modal_waits = 0
        self.calls: list[tuple] = []

    @property
    def first(self):
        return self

    def evaluate(self, script, arg=None, timeout=None):
        if arg is not None:
            return arg in self.options
        if "closest('dialog[open]')" in script:
            return self.inside_modal
        return self.runtime_text

    def click(self, timeout=None):
        self.calls.append(("click",))

    def fill(self, value):
        self.calls.append(("fill", value))

    def select_option(self, value=None, label=None):
        self.calls.append(("select_option", value) if label is None else ("select_label", label))


class FakePage:
    def __init__(self, locator: FakeLocator):
        self._locator = locator

    def locator(self, _selector):
        return self._locator

    def wait_for_timeout(self, _ms):
        pass

    def wait_for_function(self, _script, timeout=None):
        self._locator.modal_waits += 1


GUARD_ON = RunSettings(stop_guard=True, blocked_steps=("Continuar al pago",))
GUARD_OFF = RunSettings(stop_guard=False, blocked_steps=("Continuar al pago",))


class FlowRunnerActionTests(unittest.TestCase):
    def setUp(self):
        self.messages: list[str] = []
        self.runner = FlowRunner(self.messages.append)

    def _run(
        self,
        action: dict,
        settings: RunSettings,
        runtime_text: str = "",
        options: tuple[str, ...] = (),
    ):
        locator = FakeLocator(runtime_text, options)
        stopped_at = self.runner._run_action(FakePage(locator), action, 1, settings)
        return stopped_at, locator.calls

    def test_guard_stops_before_clicking_blocked_step(self):
        action = {
            "action_type": "click",
            "element": {"selector": "#pay", "text": "Continuar al pago"},
        }

        stopped_at, calls = self._run(action, GUARD_ON)

        self.assertEqual(stopped_at, "Continuar al pago")
        self.assertEqual(calls, [])
        self.assertIn("detenido por seguridad", self.messages[-1])

    def test_guard_checks_text_of_element_at_replay_time(self):
        action = {"action_type": "click", "element": {"selector": "button.primary"}}

        stopped_at, calls = self._run(action, GUARD_ON, runtime_text="Continuar al pago")

        self.assertEqual(stopped_at, "Continuar al pago")
        self.assertEqual(calls, [])

    def test_disabled_guard_clicks_blocked_step(self):
        action = {
            "action_type": "click",
            "element": {"selector": "#pay", "text": "Continuar al pago"},
        }

        stopped_at, calls = self._run(action, GUARD_OFF)

        self.assertIsNone(stopped_at)
        self.assertEqual(calls, [("click",)])

    def test_checkbox_change_after_click_is_not_filled(self):
        action = {
            "action_type": "change",
            "value": "on",
            "element": {"selector": "#terms", "tag": "INPUT"},
        }

        stopped_at, calls = self._run(action, GUARD_ON, runtime_text="checkbox")

        self.assertIsNone(stopped_at)
        self.assertEqual(calls, [])
        self.assertIn("omitido", self.messages[-1])

    def test_masked_value_without_test_data_is_skipped(self):
        action = {
            "action_type": "input",
            "element": {"selector": "#password", "name": "password"},
            "value": "[MASKED]",
        }

        stopped_at, calls = self._run(action, GUARD_ON)

        self.assertIsNone(stopped_at)
        self.assertEqual(calls, [])
        self.assertIn("sin dato de prueba", self.messages[-1])

    def test_test_data_replaces_masked_and_recorded_values(self):
        settings = RunSettings(test_data={"password": "clave-qa", "#email": "qa@example.test"})
        masked = {
            "action_type": "input",
            "element": {"selector": "#password", "name": "password"},
            "value": "[MASKED]",
        }
        recorded = {
            "action_type": "change",
            "element": {"tag": "INPUT", "id": "email"},
            "value": "grabado@example.test",
        }

        _, masked_calls = self._run(masked, settings)
        _, recorded_calls = self._run(recorded, settings)

        self.assertEqual(masked_calls, [("fill", "clave-qa")])
        self.assertEqual(recorded_calls, [("fill", "qa@example.test")])

    def test_value_typed_by_ai_is_not_replaced_by_test_data(self):
        settings = RunSettings(test_data={"#password": "clave-admin"})
        action = {
            "action_type": "change",
            "element": {"tag": "INPUT", "selector": "#password"},
            "value": "Clave-Supervisora-1",
            "fixed_value": True,
        }

        _, calls = self._run(action, settings)

        self.assertEqual(calls, [("fill", "Clave-Supervisora-1")])

    def test_select_uses_label_when_recorded_value_no_longer_exists(self):
        action = {
            "action_type": "change",
            "element": {"tag": "SELECT", "selector": "#v-bus"},
            "value": "id-de-la-grabacion",
            "option_label": "Bus 01 · BUS-701",
        }

        _, missing = self._run(action, GUARD_OFF, options=("id-nuevo",))
        _, present = self._run(action, GUARD_OFF, options=("id-de-la-grabacion",))

        self.assertEqual(missing, [("select_label", "Bus 01 · BUS-701")])
        self.assertEqual(present, [("select_option", "id-de-la-grabacion")])

    def test_waits_for_open_notice_before_acting_outside_it(self):
        action = {"action_type": "click", "element": {"selector": "#salir", "text": "Salir"}}
        outside = FakeLocator()
        inside = FakeLocator()
        inside.inside_modal = True

        self.runner._run_action(FakePage(outside), action, 1, GUARD_OFF)
        self.runner._run_action(FakePage(inside), action, 1, GUARD_OFF)

        self.assertEqual((outside.modal_waits, inside.modal_waits), (1, 0))
        self.assertEqual(outside.calls, [("click",)])

    def test_test_values_are_not_written_to_log(self):
        settings = RunSettings(test_data={"password": "clave-qa"})
        action = {
            "action_type": "input",
            "element": {"name": "password"},
            "value": "[MASKED]",
        }

        self._run(action, settings)

        self.assertFalse(any("clave-qa" in message for message in self.messages))

    def test_only_last_keystroke_of_each_field_is_replayed(self):
        email = {"selector": "#billing_email", "name": "billing_email"}
        actions = [
            {"action_type": "click", "element": email},
            {"action_type": "input", "element": email, "value": "s"},
            {"action_type": "input", "element": email, "value": "sm"},
            {"action_type": "change", "element": email, "value": "sm"},
            {"action_type": "input", "element": {"selector": "#phone"}, "value": "9"},
            {"action_type": "input", "element": {"selector": "#city"}, "value": "lima"},
        ]

        self.assertEqual(FlowRunner._superseded_steps(actions), {2, 3})

    def test_action_inside_iframe_uses_that_frame(self):
        frame_locator = FakeLocator()
        frame = FakePage(frame_locator)
        frame.url = "https://pagos.test/card/card-cvc.html?session=abc"
        page = FakePage(FakeLocator())
        page.frames = [page, frame]
        page.url = "https://sitio.test/pagar"
        action = {
            "action_type": "input",
            "element": {"selector": "#cvc", "name": "cvc"},
            "value": "123",
            "frame_url": "https://pagos.test/card/card-cvc.html?session=xyz",
        }

        self.runner._run_action(page, action, 1, RunSettings())

        self.assertEqual(frame_locator.calls, [("fill", "123")])
        self.assertEqual(page._locator.calls, [])

    def test_missing_iframe_raises_clear_error(self):
        page = FakePage(FakeLocator())
        page.frames = [page]
        page.url = "https://sitio.test/pagar"
        page.wait_for_timeout = lambda _ms: None

        with self.assertRaises(ValueError):
            FlowRunner._get_frame(page, "https://pagos.test/card.html?token=1", timeout_s=0)


if __name__ == "__main__":
    unittest.main()

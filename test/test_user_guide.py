import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.automation.flow_runner import FlowRunner
from app.automation.guide_capture import GuideCapture, element_kind
from app.automation.run_settings import RunSettings
from app.evidence.step_markers import MARKER_COLOR, draw_step_markers
from app.reporting.user_guide import (
    GUIDE_DATA_FILENAME,
    UserGuideGenerator,
    build_guide,
    default_step_text,
    guide_steps,
)


def step(number, kind, label="", action_type="click", target=None, shot=0, url="https://site/login", title="Ingresar", **extra):
    return {
        "step": number,
        "action_type": action_type,
        "kind": kind,
        "label": label,
        "option": extra.get("option", ""),
        "target": target or f"#el{number}",
        "url": url,
        "title": title,
        "shot": shot,
        "box": extra.get("box", {"x": 10, "y": 10, "width": 40, "height": 20}),
    }


LOGIN = {
    "steps": [
        step(1, "field", "Correo electrónico *", target="#email"),
        step(2, "field", "Correo electrónico *", action_type="input", target="#email"),
        step(3, "password", "Contraseña", action_type="input", target="#password"),
        step(4, "button", "Ingresar"),
        step(5, "link", "Mis cursos", url="https://site/panel", title="Panel", shot=1),
    ],
    "shots": [
        {"file": "guia_001.png", "url": "https://site/login", "title": "Ingresar", "scroll": [0, 0], "viewport_width": 100},
        {"file": "guia_002.png", "url": "https://site/panel", "title": "Panel", "scroll": [0, 0], "viewport_width": 100},
        {"file": "guia_003.png", "url": "https://site/cursos", "title": "Mis cursos", "scroll": [0, 0], "viewport_width": 100},
    ],
    "final_shot": 2,
}


class StepTextTests(unittest.TestCase):
    def test_texts_name_the_control_without_typed_values(self):
        self.assertEqual(default_step_text(step(1, "field", "Correo electrónico *")), "Completa **Correo electrónico**.")
        self.assertEqual(default_step_text(step(1, "password", "Clave")), "Escribe tu contraseña en **Clave**.")
        self.assertEqual(default_step_text(step(1, "button", "Ingresar")), "Haz clic en **Ingresar**.")
        self.assertEqual(default_step_text(step(1, "check", "Acepto")), "Marca **Acepto**.")
        self.assertEqual(
            default_step_text(step(1, "select", "Departamento", option="Lima")),
            "En **Departamento**, elige **Lima**.",
        )

    def test_unlabeled_controls_point_to_the_marker(self):
        self.assertEqual(default_step_text(step(1, "button")), "Haz clic en el elemento señalado.")
        self.assertEqual(default_step_text(step(1, "field")), "Completa el campo señalado.")

    def test_click_before_typing_in_same_field_is_dropped(self):
        shown = [item["step"] for item in guide_steps(LOGIN["steps"])]

        self.assertEqual(shown, [2, 3, 4, 5])

    def test_change_after_typing_in_same_field_is_dropped(self):
        steps = [
            step(1, "password", "Clave", action_type="input", target="#pass"),
            step(2, "select", "Sede", action_type="change", target="#sede"),
            step(3, "password", "Clave", action_type="change", target="#pass"),
        ]

        self.assertEqual([item["step"] for item in guide_steps(steps)], [1, 2])

    def test_element_kind(self):
        self.assertEqual(element_kind({"tag": "input", "type": "checkbox"}), "check")
        self.assertEqual(element_kind({"tag": "input", "type": "password", "editable": True}), "password")
        self.assertEqual(element_kind({"tag": "select", "editable": True}), "select")
        self.assertEqual(element_kind({"tag": "a"}), "link")
        self.assertEqual(element_kind({"tag": "div"}), "button")


class BuildGuideTests(unittest.TestCase):
    def test_groups_steps_by_screen_and_numbers_them_in_order(self):
        guide = build_guide(LOGIN, {})

        self.assertEqual([screen["title"] for screen in guide["screens"]], ["Ingresar", "Panel"])
        numbers = [
            [item["number"] for block in screen["blocks"] for item in block["steps"]]
            for screen in guide["screens"]
        ]
        self.assertEqual(numbers, [[1, 2, 3], [4]])
        self.assertEqual(guide["final"]["title"], "Mis cursos")

    def test_overrides_replace_titles_texts_and_hide_steps(self):
        overrides = {
            "pantallas": {"1": {"titulo": "Inicia sesión", "nota": "Usa tu cuenta USIL."}},
            "pasos": {"4": "Pulsa **Ingresar** para entrar."},
            "ocultar": [3],
        }

        guide = build_guide(LOGIN, overrides)
        first = guide["screens"][0]
        texts = [item["text"] for item in first["blocks"][0]["steps"]]

        self.assertEqual(first["title"], "Inicia sesión")
        self.assertEqual(first["note"], "Usa tu cuenta USIL.")
        self.assertEqual(texts, ["Completa **Correo electrónico**.", "Pulsa **Ingresar** para entrar."])


class GeneratorTests(unittest.TestCase):
    def test_writes_html_word_and_editable_template(self):
        with tempfile.TemporaryDirectory() as folder:
            run_folder = Path(folder)
            for shot in LOGIN["shots"]:
                Image.new("RGB", (200, 120), "white").save(run_folder / shot["file"])
            (run_folder / GUIDE_DATA_FILENAME).write_text(json.dumps(LOGIN), encoding="utf-8")
            (run_folder / "result.json").write_text(
                json.dumps({"flow_name": "Ingreso <demo>", "status": "completado"}), encoding="utf-8"
            )
            overrides_file = run_folder / "guides" / "flow_demo.json"

            html_file = UserGuideGenerator().generate(run_folder, overrides_file)
            html = html_file.read_text(encoding="utf-8")
            template = json.loads(overrides_file.read_text(encoding="utf-8"))

            self.assertIn("<strong>Correo electrónico</strong>", html)
            self.assertIn("guia_001_pasos.png", html)
            self.assertIn("Ingreso &lt;demo&gt;", html)
            self.assertNotIn("<demo>", html)
            self.assertTrue((run_folder / "guia.docx").exists())
            self.assertEqual(template["pantallas"]["1"]["titulo"], "Ingresar")
            self.assertEqual(template["pasos"]["4"], "Haz clic en **Ingresar**.")

    def test_markers_are_scaled_to_device_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "shot.png"
            target = Path(folder) / "shot_pasos.png"
            Image.new("RGB", (300, 300), "white").save(source)

            draw_step_markers(source, target, [(1, {"x": 40, "y": 40, "width": 30, "height": 20})], 100)

            image = Image.open(target).convert("RGBA")
            # Con escala 3, el borde izquierdo queda en (40 - 4) * 3 = 108.
            self.assertEqual(image.getpixel((109, 150)), MARKER_COLOR)
            # El número queda a la izquierda, separado del recuadro.
            self.assertEqual(image.getpixel((10, 150)), MARKER_COLOR)
            self.assertEqual(image.getpixel((100, 150)), (255, 255, 255, 255))


class FakeGuideLocator:
    def __init__(self, info: dict):
        self.info = info

    def wait_for(self, **_kwargs):
        pass

    def scroll_into_view_if_needed(self, **_kwargs):
        pass

    def evaluate(self, _script, _arg=None, timeout=None):
        return self.info

    def bounding_box(self, timeout=None):
        return {"x": 1, "y": 2, "width": 3, "height": 4}


class FakeGuidePage:
    url = "https://site/login?token=secreto"
    viewport_size = {"width": 1440, "height": 980}

    def __init__(self):
        self.screenshots = 0

    def title(self):
        return "Ingresar"

    def evaluate(self, _script):
        return [0, 0]

    def screenshot(self, path, timeout=None):
        self.screenshots += 1


class GuideCaptureTests(unittest.TestCase):
    def test_form_fields_share_a_shot_and_clicks_take_a_new_one(self):
        page = FakeGuidePage()
        capture = GuideCapture(Path("."))
        field = FakeGuideLocator({"tag": "input", "type": "email", "label": "Correo", "editable": True})
        button = FakeGuideLocator({"tag": "button", "type": "submit", "label": "Ingresar", "editable": False})

        capture.capture_step(page, field, {"action_type": "input", "element": {"selector": "#email"}}, 1, "a@b.pe")
        capture.capture_step(page, button, {"action_type": "click", "element": {}}, 2)
        capture.capture_step(page, button, {"action_type": "click", "element": {}}, 3)

        self.assertEqual([item["shot"] for item in capture.steps], [0, 0, 1])
        self.assertEqual(page.screenshots, 2)
        self.assertNotIn("secreto", capture.steps[0]["url"])
        self.assertNotIn("a@b.pe", json.dumps(capture.to_dict()))


class RecordingGuide:
    def __init__(self):
        self.steps: list[int] = []

    def capture_step(self, _page, _locator, _action, sequence, _value=None):
        self.steps.append(sequence)


class FakeLocator:
    first = property(lambda self: self)

    def evaluate(self, _script, timeout=None):
        return ""

    def click(self, timeout=None):
        raise AssertionError("El freno debió detener el clic.")


class FakePage:
    def locator(self, _selector):
        return FakeLocator()


class FlowRunnerGuideTests(unittest.TestCase):
    def test_blocked_step_is_documented_but_not_clicked(self):
        runner = FlowRunner(lambda _message: None)
        runner._guide = RecordingGuide()
        settings = RunSettings(stop_guard=True, blocked_steps=("Continuar al pago",))
        action = {"action_type": "click", "element": {"selector": "#pay", "text": "Continuar al pago"}}

        stopped_at = runner._run_action(FakePage(), action, 7, settings)

        self.assertEqual(stopped_at, "Continuar al pago")
        self.assertEqual(runner._guide.steps, [7])


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from app.automation.flow_runner import FlowRunResult
from app.config import AppPaths
from app.reporting.user_guide import GUIDE_DATA_FILENAME
from app.services.assistant_service import REDACTED, AssistantService


RUN = "run_Ingreso_chromium_Escritorio_20261023_150000"
GUIDE = {
    "steps": [
        {"step": 2, "action_type": "input", "kind": "field", "label": "Correo", "option": "",
         "target": "#email", "url": "https://site/login", "title": "Ingresar", "shot": 0,
         "box": {"x": 1, "y": 1, "width": 5, "height": 5}},
        {"step": 3, "action_type": "click", "kind": "button", "label": "Ingresar", "option": "",
         "target": "#go", "url": "https://site/login", "title": "Ingresar", "shot": 0,
         "box": {"x": 1, "y": 10, "width": 5, "height": 5}},
    ],
    "shots": [{"file": "guia_001.png", "url": "https://site/login", "title": "Ingresar",
               "scroll": [0, 0], "viewport_width": 50}],
    "final_shot": None,
}


class AssistantServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.paths = AppPaths(Path(self.folder.name))
        self.paths.ensure_data_directories()
        self.service = AssistantService(self.paths)

        (self.paths.flows_dir / "flow_ingreso.json").write_text(
            json.dumps({"name": "Ingreso", "start_url": "https://site/login?u=ana@demo.pe", "actions": [{}, {}]}),
            encoding="utf-8",
        )
        self.paths.test_data_file.write_text(
            json.dumps({"campos": {"#email": "ana@demo.pe", "#clave": "Secreta123"}}), encoding="utf-8"
        )
        run = self.paths.runs_dir / RUN
        run.mkdir()
        (run / "result.json").write_text(json.dumps({
            "flow_id": "flow_ingreso", "flow_name": "Ingreso", "status": "fallido", "steps_run": 2,
            "total_steps": 3, "failed_step": 3,
            "error": 'Locator.fill: Timeout. waiting for fill("Secreta123")',
        }), encoding="utf-8")
        (run / GUIDE_DATA_FILENAME).write_text(json.dumps(GUIDE), encoding="utf-8")
        Image.new("RGB", (50, 30), "white").save(run / "guia_001.png")
        self.run = run

    def tearDown(self):
        self.folder.cleanup()

    def test_test_data_never_appears_in_answers(self):
        answer = json.dumps(self.service.read_run(RUN))

        self.assertNotIn("Secreta123", answer)
        self.assertIn(REDACTED, answer)

    def test_lists_flows_and_runs(self):
        self.assertEqual(self.service.list_flows()[0]["flujo"], "flow_ingreso")
        self.assertNotIn("ana@demo.pe", json.dumps(self.service.list_flows()))
        runs = self.service.list_runs("flow_ingreso")
        self.assertEqual([run["corrida"] for run in runs], [RUN])
        self.assertTrue(runs[0]["tiene_guia"])

    def test_rejects_paths_outside_data(self):
        for name in ("..", "../flows", "..\\flows", "C:/Windows"):
            with self.assertRaises(ValueError):
                self.service.read_run(name)
        with self.assertRaises(ValueError):
            self.service.capture_path(RUN, "../result.json")

    def test_read_guide_gives_flow_step_keys_and_texts(self):
        guide = self.service.read_guide(RUN)
        steps = guide["pantallas"][0]["bloques"][0]["pasos"]

        self.assertEqual([step["paso_flujo"] for step in steps], [2, 3])
        self.assertEqual(steps[1]["texto"], "Haz clic en **Ingresar**.")

    def test_update_texts_merges_keeps_backup_and_regenerates(self):
        self.service.update_guide_texts(RUN, {"pasos": {"3": "Pulsa **Ingresar**."}})
        answer = self.service.update_guide_texts(RUN, {
            "introduccion": "Así entras a tu cuenta.",
            "pantallas": {"1": {"nota": "Ten a mano tu correo."}},
        })

        texts = json.loads(Path(answer["archivo_textos"]).read_text(encoding="utf-8"))
        html = (self.run / "guia.html").read_text(encoding="utf-8")
        self.assertEqual(texts["pasos"]["3"], "Pulsa **Ingresar**.")
        self.assertEqual(texts["pantallas"]["1"], {"titulo": "Ingresar", "nota": "Ten a mano tu correo."})
        self.assertIn("Así entras a tu cuenta.", html)
        self.assertIn("Pulsa <strong>Ingresar</strong>.", html)
        self.assertTrue(Path(answer["respaldo"]).exists())

    def test_rejects_unknown_or_malformed_changes(self):
        for changes in ({}, {"freno_pago": False}, {"pasos": {"tres": "x"}}, {"pasos": {"3": ""}},
                        {"pantallas": {"1": {"url": "x"}}}):
            with self.assertRaises(ValueError):
                self.service.update_guide_texts(RUN, changes)

    def test_generate_guide_keeps_stop_guard_on(self):
        self.paths.run_config_file.write_text(
            json.dumps({"ambientes": {"pruebas": {"freno_pago": False}}}), encoding="utf-8"
        )
        seen = {}

        class FakeRunner:
            def __init__(self, _log, settings, paths, browser_options, guide):
                seen.update(settings=settings, options=browser_options, guide=guide)
                self.last_result = None

            def run(self, flow_file):
                self.last_result = FlowRunResult(
                    flow_name="Ingreso", status="detenido_por_seguridad", environment="pruebas",
                    stop_guard=True, browser="chromium", device="Escritorio", headless=True,
                    steps_run=2, total_steps=3, run_folder=str(self.paths_run), guide_file="guia.html",
                )

        FakeRunner.paths_run = self.run
        with mock.patch("app.automation.flow_runner.FlowRunner", FakeRunner):
            answer = self.service.generate_guide("flow_ingreso", "pruebas")

        self.assertTrue(seen["settings"].stop_guard)
        self.assertTrue(seen["options"].headless)
        self.assertTrue(seen["guide"])
        self.assertEqual(answer["corrida"], RUN)


if __name__ == "__main__":
    unittest.main()

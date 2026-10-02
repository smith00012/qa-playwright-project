import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.automation.run_settings import (
    NO_ENVIRONMENT,
    find_test_value,
    list_environments,
    load_run_settings,
    matching_blocked_step,
)
from app.config import AppPaths


RUN_CONFIG = {
    "pasos_bloqueados": ["Continuar al pago"],
    "ambientes": {
        "pruebas": {"freno_pago": False},
        "produccion": {"freno_pago": True},
        "staging": {"pasos_bloqueados": ["#place_order"]},
    },
}


class RunSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.paths = AppPaths(data_dir=Path(self.temporary_directory.name))

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _write(self, filepath: Path, data: dict) -> None:
        filepath.write_text(json.dumps(data), encoding="utf-8")

    def test_guard_is_active_without_configuration(self):
        settings = load_run_settings(paths=self.paths)

        self.assertTrue(settings.stop_guard)
        self.assertEqual(settings.blocked_steps, ())
        self.assertFalse(settings.trace)
        self.assertEqual(list_environments(self.paths), [])

    def test_trace_is_enabled_only_explicitly(self):
        self._write(self.paths.run_config_file, {"trazas": True})

        self.assertTrue(load_run_settings(paths=self.paths).trace)

    def test_guard_stays_active_without_environment_or_with_unknown_one(self):
        self._write(self.paths.run_config_file, RUN_CONFIG)

        for environment in (None, NO_ENVIRONMENT, "inexistente"):
            settings = load_run_settings(environment, self.paths)
            self.assertTrue(settings.stop_guard)
            self.assertEqual(settings.blocked_steps, ("Continuar al pago",))

    def test_environment_can_disable_guard_or_override_blocked_steps(self):
        self._write(self.paths.run_config_file, RUN_CONFIG)

        self.assertFalse(load_run_settings("pruebas", self.paths).stop_guard)
        self.assertTrue(load_run_settings("produccion", self.paths).stop_guard)
        self.assertEqual(
            load_run_settings("staging", self.paths).blocked_steps,
            ("#place_order",),
        )
        self.assertEqual(
            list_environments(self.paths),
            ["produccion", "pruebas", "staging"],
        )

    def test_environment_test_data_overrides_shared_fields(self):
        self._write(
            self.paths.test_data_file,
            {
                "campos": {"email": "general@example.test", "nombre": "QA"},
                "ambientes": {"pruebas": {"campos": {"email": "pruebas@example.test"}}},
            },
        )

        settings = load_run_settings("pruebas", self.paths)

        self.assertEqual(settings.test_data["email"], "pruebas@example.test")
        self.assertEqual(settings.test_data["nombre"], "QA")

    def test_finds_test_value_by_selector_id_or_name(self):
        test_data = {"#billing_email": "a@example.test", "password": "secreto"}

        self.assertEqual(
            find_test_value({"id": "billing_email"}, test_data),
            "a@example.test",
        )
        self.assertEqual(find_test_value({"name": "password"}, test_data), "secreto")
        self.assertIsNone(find_test_value({"name": "telefono"}, test_data))

    def test_test_value_can_come_from_environment_variable(self):
        test_data = {"password": "env:QA_TEST_PASSWORD"}

        with mock.patch.dict(os.environ, {"QA_TEST_PASSWORD": "desde-entorno"}):
            self.assertEqual(
                find_test_value({"name": "password"}, test_data),
                "desde-entorno",
            )

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                find_test_value({"name": "password"}, test_data)

    def test_blocked_step_matches_text_ignoring_case_and_spaces(self):
        blocked = ("Continuar al pago",)

        self.assertEqual(
            matching_blocked_step(blocked, {"text": "  CONTINUAR   al pago  →"}),
            "Continuar al pago",
        )
        self.assertEqual(
            matching_blocked_step(blocked, {"selector": "button.btn"}, "Continuar al pago"),
            "Continuar al pago",
        )
        self.assertIsNone(matching_blocked_step(blocked, {"text": "Continuar"}))

    def test_blocked_step_matches_selector_or_id(self):
        blocked = ("#place_order",)

        self.assertEqual(
            matching_blocked_step(blocked, {"id": "place_order"}),
            "#place_order",
        )
        self.assertIsNone(matching_blocked_step(blocked, {"id": "other"}))


if __name__ == "__main__":
    unittest.main()

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    """Rutas de datos que utiliza la aplicación."""

    data_dir: Path = Path("data")

    @property
    def executions_dir(self) -> Path:
        return self.data_dir / "executions"

    @property
    def flows_dir(self) -> Path:
        return self.data_dir / "flows"

    @property
    def test_cases_dir(self) -> Path:
        return self.data_dir / "test_cases"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def report_target_file(self) -> Path:
        return self.data_dir / "report_target.json"

    @property
    def run_config_file(self) -> Path:
        return self.data_dir / "run_config.json"

    @property
    def test_data_file(self) -> Path:
        return self.data_dir / "test_data.json"

    def execution_dir(self, execution_id: str) -> Path:
        return self.executions_dir / f"execution_{execution_id}"

    def ensure_data_directories(self) -> None:
        for directory in (
            self.executions_dir,
            self.flows_dir,
            self.test_cases_dir,
            self.runs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


DEFAULT_PATHS = AppPaths()

# Architecture and Contribution Guide

## Current structure

| Path | Responsibility |
| --- | --- |
| `app/ui/` | Tkinter views and user interactions; matrix workflow lives in `case_matrix_panel.py` |
| `app/services/` | Use cases that coordinate recording, storage, and reports |
| `app/models/` | Framework-independent action and element data in `action.py` |
| `app/recorder/` | Browser event collection, sensitive-data sanitization, and conversion to QA actions |
| `app/automation/` | Replay of recorded, site-agnostic flows, run settings per environment, browser and device options, step capture for the user guide |
| `app/audit/` | Generic per-page checks during replay: console, network, accessibility (axe), load timings |
| `app/storage/` | JSON persistence and Excel matrix reading |
| `app/evidence/` | Screenshot capture and evidence formatting |
| `app/reporting/` | Word test cases, HTML run report, and the step-by-step user guide (HTML and Word) |
| `scripts/` | Manual diagnostics and maintenance commands |
| `test/` | Automated unit and integration tests |

The intended dependency direction is UI -> services -> models and adapters. Models should not import Tkinter, Playwright, or file-system implementations. Keep changes incremental: `MainWindow` and `BrowserRecorder` still coordinate several responsibilities and should be split only when a feature gives a clear boundary and tests protect the existing behavior.

## Choosing a pattern

Use the existing layered structure for the desktop application. Keep browser recording and replay generic because they work with user-selected websites.

Page Object Model is appropriate for deterministic, site-specific test suites where pages and locators are known. It is not a good global abstraction for this recorder: recorded flows can target arbitrary sites. If site-specific reusable automation is added later, keep page objects in a separate package and let those tests use Playwright directly; do not make the generic recorder depend on them.

## Change workflow

1. Put UI widgets and presentation state in `app/ui/`.
2. Put multi-step application workflows in `app/services/`.
3. Keep data structures in `app/models/` without framework dependencies.
4. Put external I/O behind the existing storage, evidence, reporting, recorder, or automation modules.
5. Add tests in `test/`, not beside production modules. Name production modules by their responsibility rather than with a `test_` prefix.
6. Run the full unit suite and security checks before merging.

## Security notes

- Use synthetic accounts and data. Screenshots can still expose information visible in the browser even when input values are masked.
- Never commit credentials, access tokens, personal test data, generated executions, flows, or reports.
- Keep report and output filenames sanitized; do not build paths directly from untrusted input.
- SonarQube credentials belong in environment variables or the CI secret store, never in this repository.

## Local quality checks

Install development tools and run tests:

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s test
python -m bandit -r app -ll
python -m coverage run -m unittest discover -s test
python -m coverage xml
```

`sonar-project.properties` configures source/test discovery and coverage import. Install the SonarScanner separately, then provide `SONAR_HOST_URL` and `SONAR_TOKEN` through the shell or CI secrets before invoking `sonar-scanner`. Do not add server URLs or tokens to the project file.
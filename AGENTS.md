# Repository Guidelines

## Project Structure & Modules
- `optimize_student_sections.py`: Core OR-Tools CP-SAT model. Reads Excel, writes `out/sections_plan.csv`, `out/assignments.csv`, `out/report.txt`.
- `app.py`: FastAPI service exposing `/run`, `/jobs/{id}`, `/download/...`. Stores per-run files under `data/<job-id>/` and limits concurrency via `MAX_CONCURRENT`.
- `data/`: Uploads and job outputs (ephemeral). Add to `.gitignore`.
- `out/`: Outputs from local CLI runs (ephemeral). Add to `.gitignore`.
- `requirements.txt`, `README.md`: Dependencies and usage.

## Build, Test, and Dev Commands
- Create env: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
- Run CLI: `python optimize_student_sections.py --input "2025입학생_수강생일괄등록양식(sample).xlsx" --output-dir out --slots 4 --rooms-per-slot 7 --extra-rooms-per-slot 1 --cap 28 --maxcap 30 --time-limit 60 --workers 8`.
- Run API (dev): `uvicorn app:app --reload --port 8000` then open `/` or `/docs`.

## Coding Style & Naming
- Python 3.12, PEP 8, 4-space indent; prefer type hints and module-level constants.
- Names: functions `snake_case`, classes `CapWords`, constants `UPPER_SNAKE`.
- Keep solver logic pure (`build_and_solve`); isolate I/O in CLI/API layers.

## Testing Guidelines
- Framework: pytest (recommended). Place tests in `tests/`, files `test_*.py`.
- Fixtures: a tiny Excel with 3–5 students and 2–3 subjects under `tests/fixtures/`.
- Suggested checks: parser yields expected subjects; zero/known unassigned on small cases; CSV headers/row counts match expectations.

## Commit & Pull Request Guidelines
- Commits: imperative mood, short summary (e.g., `solver: tighten capacity linkage`), include rationale in body.
- PRs: clear description, sample input and expected outputs, linked issues, and performance notes (time/workers). Update `README.md` if flags or file formats change.
- Don’t commit `data/`, `out/`, `.venv/`, or `__pycache__/`.

## Security & Configuration Tips
- Treat Excel data as sensitive (student PII). Keep `data/` out of VCS and avoid sharing raw files.
- Adjust `MAX_CONCURRENT` in `app.py` conservatively for server resources.
- Bind `uvicorn` to `0.0.0.0` only when deploying; prefer `--reload` locally.

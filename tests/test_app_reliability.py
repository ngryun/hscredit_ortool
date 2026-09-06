import asyncio
import io
import json
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as web


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "BASE", tmp_path)
    monkeypatch.setattr(web, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(web, "JOBS", {})
    monkeypatch.setattr(web, "BACKGROUND_TASKS", set())
    monkeypatch.setattr(web, "RUN_SEM", asyncio.Semaphore(1))
    with TestClient(web.app) as client:
        yield client


def workbook():
    output = io.BytesIO()
    pd.DataFrame({"학번": [10001, 10002], "이름": ["테스트1", "테스트2"], "A": [1, 1]}).to_excel(output, index=False)
    return output.getvalue()


@pytest.mark.parametrize("data", [
    {"slots": "invalid"}, {"slots": "0"}, {"slots": "27"},
    {"rooms": "-1"}, {"cap": "0"}, {"maxcap": "20"},
    {"extra_total": "abc"}, {"extra_total": "-1"},
    {"constraints_json": "{"}, {"constraints_json": "[]"},
    {"constraints_json": '{"A": 1}'},
    {"constraints_json": '{"A": {"maxTotal": 1.5}}'},
    {"constraints_json": '{"A": {"maxTotal": -1}}'},
    {"constraints_json": '{"A": {"maxTotal": true}}'},
    {"constraints_json": '{"A": {"unknown": 1}}'},
    {"section_totals_json": '{"A": null}'},
    {"section_totals_json": '{"A": "bad"}'},
    {"section_totals_json": '{"A": 2}', "constraints_json": '{"A": {"maxTotal": 1}}'},
    {"slots": "1", "section_totals_json": '{"A": 2}', "constraints_json": '{"A": {"maxPerSlot": 1}}'},
])
def test_invalid_requests_do_not_create_jobs(client, tmp_path, data):
    response = client.post("/run", data=data, files={"xlsx": ("input.xlsx", b"placeholder")})
    assert response.status_code == 422
    assert response.json()["error"]
    assert not web.JOBS
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("files", [None, {"xlsx": ("input.xlsx", b"")}, {"xlsx": ("input.txt", b"text")}])
def test_missing_or_invalid_upload(client, files):
    response = client.post("/run", files=files)
    assert response.status_code == 422
    assert not web.JOBS


def test_valid_settings_reach_optimizer(client, monkeypatch):
    captured = []
    async def optimizer(*args):
        captured.append(args)
    monkeypatch.setattr(web, "run_optimizer", optimizer)
    response = client.post("/run", files={"xlsx": ("input.xlsx", workbook())}, data={
        "slots": "2", "rooms": "3", "extra": "0", "cap": "20", "maxcap": "25",
        "group": "G1,G2", "extra_total": "0",
        "constraints_json": json.dumps({"A": {"maxPerSlot": 1, "maxTotal": 2}}),
        "section_totals_json": json.dumps({"A": 2}),
    })
    assert response.status_code == 200
    client.portal.call(asyncio.sleep, 0)
    args = captured[0]
    assert args[3:10] == (2, 3, 0, 20, 25, "G1,G2", 0)
    constraints = pd.read_csv(args[10])
    assert constraints.iloc[0].to_dict() == {"subject": "A", "max_sections_per_slot": 1, "max_total_sections": 2}
    assert pd.read_csv(args[11]).iloc[0]["total_sections"] == 2


def test_launch_failure_sets_error_and_schedules_cleanup(client, monkeypatch, tmp_path):
    async def fail_launch(*args, **kwargs):
        raise OSError("test launch failure")
    cleaned = []
    async def cleanup(job_id, delay_seconds=3600):
        cleaned.append(job_id)
    monkeypatch.setattr(web.asyncio, "create_subprocess_exec", fail_launch)
    monkeypatch.setattr(web, "cleanup_job_folder", cleanup)
    response = client.post("/run", files={"xlsx": ("input.xlsx", workbook())})
    job = response.json()["job"]
    client.portal.call(asyncio.sleep, 0)
    status = client.get(f"/jobs/{job}").json()
    assert status["status"] == "ERROR"
    assert "test launch failure" in status["error"]
    assert cleaned == [job]


def test_cleanup_removes_files_and_cached_student_data(client, tmp_path):
    folder = tmp_path / "job"
    folder.mkdir()
    (folder / "input.xlsx").write_bytes(b"student information")
    web.JOBS["job"] = {"dir": folder, "status": "ERROR", "unassigned": {"A": [{"name": "학생"}]}}
    client.portal.call(web.cleanup_job_folder, "job", 0)
    assert not folder.exists()
    assert "job" not in web.JOBS
    assert client.get("/jobs/job").status_code == 404


def test_real_web_optimizer_and_export(client, monkeypatch):
    monkeypatch.setattr(web, "SOLVER_TIME_LIMIT", 5)
    monkeypatch.setattr(web, "SOLVER_WORKERS", 1)
    web.REPORTS_DIR.mkdir()
    response = client.post("/run", files={"xlsx": ("input.xlsx", workbook())})
    assert response.status_code == 200
    job = response.json()["job"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = client.get(f"/jobs/{job}").json()
        if status["status"] in ("DONE", "ERROR"):
            break
        time.sleep(0.02)
    assert status["status"] == "DONE", status
    assert status["summary"]["total_assigned"] == 2
    assert status["summary"]["total_unassigned"] == 0
    assert status["pivot"]
    export = client.post(f"/jobs/{job}/export", json={})
    assert export.status_code == 200
    assert pd.ExcelFile(io.BytesIO(export.content)).sheet_names
    downloaded = client.get(status["assignments"])
    assert downloaded.status_code == 200
    assert len(pd.read_csv(io.BytesIO(downloaded.content))) == 2
    assert not (web.JOBS[job]["dir"] / "input.xlsx").exists()
    assert len(list(web.REPORTS_DIR.glob("*.txt"))) == 1


def test_archived_report_available_after_job_expiry(client):
    job = "00000000-0000-4000-8000-000000000001"
    web.REPORTS_DIR.mkdir()
    report = web.REPORTS_DIR / f"20260906_120000_{job}.txt"
    report.write_text("anonymous statistics", encoding="utf-8")
    response = client.get(f"/download/{job}/report.txt")
    assert response.status_code == 200
    assert response.text == "anonymous statistics"
    assert report.exists()
    assert client.get("/download/*/report.txt").status_code == 404

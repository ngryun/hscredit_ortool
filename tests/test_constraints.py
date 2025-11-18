import tempfile
from pathlib import Path
import pandas as pd

import importlib.util
import sys
from pathlib import Path as _Path


def _load_solver():
    root = _Path(__file__).resolve().parents[1]
    mod_path = root / "optimize_student_sections.py"
    spec = importlib.util.spec_from_file_location("optimize_student_sections", str(mod_path))
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


build_and_solve = _load_solver().build_and_solve


def make_students(subjects, choices_per_student):
    students = []
    for i, ch in enumerate(choices_per_student, start=1):
        students.append({
            "student_id": str(i).zfill(5),
            "name": f"S{i}",
            "choices": ch,
        })
    return students


def write_constraints_csv(tmpdir: Path, rows: list[dict]) -> Path:
    df = pd.DataFrame(rows)
    path = tmpdir / "constraints.csv"
    df.to_csv(path, index=False)
    return path


def write_fixed_sections_csv(tmpdir: Path, rows: list[dict]) -> Path:
    df = pd.DataFrame(rows)
    path = tmpdir / "fixed_sections.csv"
    df.to_csv(path, index=False)
    return path


def test_max_per_slot_limit_enforced():
    # Demand: 35 students all choose subject 'A'
    subjects = ["A"]
    students = make_students(subjects, [["A"] for _ in range(35)])

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cpath = write_constraints_csv(tmp, [{
            "subject": "A",
            "max_sections_per_slot": 1,
            "max_total_sections": "",
        }])
        sections_df, assignments_df, _ = build_and_solve(
            students=students,
            subjects_all=subjects,
            slots_g=2,
            rooms_per_slot=5,
            extra_rooms_per_slot=1,
            default_cap=28,
            default_maxcap=30,
            caps_override_csv=None,
            time_limit_s=10.0,
            num_workers=1,
            extra_total_limit=None,
            subject_constraints_csv=str(cpath),
        )
        # For each slot, 'A' must have num_sections <= 1
        for slot, grp in sections_df.groupby("slot"):
            a = grp[grp["subject"] == "A"]
            if a.empty:
                continue
            assert int(a.iloc[0]["num_sections"]) <= 1


def test_max_total_limit_enforced():
    # Demand: 35 students choose 'A' and 10 choose 'B'
    subjects = ["A", "B"]
    choices = [["A"] for _ in range(35)] + [["B"] for _ in range(10)]
    students = make_students(subjects, choices)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cpath = write_constraints_csv(tmp, [{
            "subject": "A",
            "max_sections_per_slot": "",
            "max_total_sections": 1,
        }])
        sections_df, assignments_df, _ = build_and_solve(
            students=students,
            subjects_all=subjects,
            slots_g=2,
            rooms_per_slot=5,
            extra_rooms_per_slot=1,
            default_cap=28,
            default_maxcap=30,
            caps_override_csv=None,
            time_limit_s=10.0,
            num_workers=1,
            extra_total_limit=None,
            subject_constraints_csv=str(cpath),
        )
        total_A = int(sections_df[sections_df["subject"] == "A"]["num_sections"].sum())
        assert total_A <= 1


def test_fixed_sections_enforced():
    subjects = ["A"]
    students = make_students(subjects, [["A"] for _ in range(40)])
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fixed_path = write_fixed_sections_csv(tmp, [{
            "subject": "A",
            "total_sections": 2,
        }])
        sections_df, assignments_df, _ = build_and_solve(
            students=students,
            subjects_all=subjects,
            slots_g=3,
            rooms_per_slot=5,
            extra_rooms_per_slot=1,
            default_cap=28,
            default_maxcap=30,
            caps_override_csv=None,
            time_limit_s=10.0,
            num_workers=1,
            extra_total_limit=None,
            subject_constraints_csv=None,
            phase1_time_ratio=0.5,
            fixed_sections_csv=str(fixed_path),
        )
        total_opened = int(sections_df[sections_df["subject"] == "A"]["num_sections"].sum())
        assert total_opened == 2

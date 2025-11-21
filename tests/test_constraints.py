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


_solver_mod = _load_solver()
build_and_solve = _solver_mod.build_and_solve
read_students_xlsx = _solver_mod.read_students_xlsx


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


def write_grouped_xlsx(tmpdir: Path) -> Path:
    rows = 8
    cols = 7
    data = [[None for _ in range(cols)] for _ in range(rows)]
    # Semester row (index 1)
    data[1][4] = "1학기"
    data[1][5] = "1학기"
    # Group row (index 2)
    data[2][4] = "G1"
    data[2][5] = "G2"
    # Subject names (index 3)
    data[3][4] = "Subject A"
    data[3][5] = "Subject B"
    # Unique ID row (index 4)
    data[4][4] = "U1"
    data[4][5] = "U2"
    # Student rows (start at index 5)
    data[5][2] = 30001
    data[5][4] = 1
    data[5][5] = 0
    data[6][2] = 30002
    data[6][4] = 0
    data[6][5] = 1
    data[7][2] = 30003
    data[7][4] = 1
    data[7][5] = 1
    df = pd.DataFrame(data)
    path = tmpdir / "grouped.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, header=False, index=False)
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


def test_multiple_groups_filtering():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        xlsx = write_grouped_xlsx(tmp)
        _, all_subjects = read_students_xlsx(str(xlsx))
        assert set(all_subjects) == {"Subject A", "Subject B"}

        students_g1, subjects_g1 = read_students_xlsx(str(xlsx), target_group="G1")
        assert subjects_g1 == ["Subject A"]
        assert all(all(choice != "Subject B" for choice in stu["choices"]) for stu in students_g1)

        _, subjects_combo = read_students_xlsx(str(xlsx), target_group="G1,G2")
        assert set(subjects_combo) == {"Subject A", "Subject B"}

        _, subjects_list = read_students_xlsx(str(xlsx), target_group=["G1", "G2"])
        assert set(subjects_list) == {"Subject A", "Subject B"}

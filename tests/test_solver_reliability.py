import pandas as pd
import pytest

import optimize_student_sections as solver


def solve(**overrides):
    settings = dict(
        students=[{"student_id": "10001", "name": "테스트", "choices": ["A"]}],
        subjects_all=["A"], slots_g=1, rooms_per_slot=2,
        extra_rooms_per_slot=0, default_cap=28, default_maxcap=30,
        caps_override_csv=None, time_limit_s=5, num_workers=1,
    )
    settings.update(overrides)
    return solver.build_and_solve(**settings)


@pytest.mark.parametrize("settings", [
    {"slots_g": 0}, {"slots_g": 27}, {"slots_g": 1.5},
    {"rooms_per_slot": -1}, {"extra_rooms_per_slot": -1},
    {"default_cap": 0}, {"default_maxcap": 27},
    {"extra_total_limit": -1}, {"num_workers": -1},
    {"time_limit_s": float("nan")}, {"time_limit_s": -1},
    {"subjects_all": ["A", "A"]}, {"subjects_all": ["B"]},
])
def test_invalid_settings_fail_before_solving(settings, monkeypatch):
    def unexpected_solve(*args, **kwargs):
        pytest.fail("Invalid settings reached the solver")
    monkeypatch.setattr(solver.cp_model.CpSolver, "Solve", unexpected_solve)
    with pytest.raises(ValueError):
        solve(**settings)


@pytest.mark.parametrize("status", [solver.cp_model.UNKNOWN, solver.cp_model.MODEL_INVALID])
def test_no_solution_never_reads_variable_values(monkeypatch, status):
    monkeypatch.setattr(solver.cp_model.CpSolver, "Solve", lambda *args: status)
    def unexpected_value(*args):
        pytest.fail("Read variables without a solution")
    monkeypatch.setattr(solver.cp_model.CpSolver, "Value", unexpected_value)
    with pytest.raises(RuntimeError, match="최적화 실패"):
        solve()


def test_conflicting_fixed_sections_report_infeasible(tmp_path):
    fixed = tmp_path / "fixed.csv"
    pd.DataFrame([{"subject": "A", "total_sections": 2}]).to_csv(fixed, index=False)
    with pytest.raises(RuntimeError, match="INFEASIBLE"):
        solve(rooms_per_slot=1, fixed_sections_csv=str(fixed))


def test_fixed_sections_allow_small_classes_in_one_slot(tmp_path):
    fixed = tmp_path / "fixed.csv"
    pd.DataFrame([{"subject": "A", "total_sections": 2}]).to_csv(fixed, index=False)
    students = [dict(student_id=str(i), name=f"학생{i}", choices=["A"]) for i in range(2)]
    sections, assignments, _ = solve(students=students, fixed_sections_csv=str(fixed))
    assert sections.num_sections.sum() == 2
    assert assignments.status.tolist() == ["assigned", "assigned"]
    assert assignments.section_label.nunique() == 2


@pytest.mark.parametrize("students", [[], [dict(student_id="1", name="학생", choices=[])]])
def test_empty_choices_keep_output_schema(students):
    sections, assignments, report = solve(students=students)
    assert sections.empty and assignments.empty
    assert list(sections) == ["subject", "slot", "num_sections", "total_enrolled"]
    assert list(assignments) == ["student_id", "name", "subject", "slot", "section_label", "status"]
    assert "Total unassigned (student-subject pairs): 0" in report


def test_all_unassigned_is_valid_when_no_rooms():
    sections, assignments, _ = solve(rooms_per_slot=0)
    assert sections.empty
    assert assignments.status.tolist() == ["unassigned"]


def test_lexicographic_without_time_limit():
    _, assignments, report = solve(use_lexicographic=True, time_limit_s=0)
    assert assignments.status.tolist() == ["assigned"]
    assert "OPTIMAL SOLUTION" in report


def test_lexicographic_retains_phase_one_solution_on_timeout(monkeypatch):
    original_solve = solver.cp_model.CpSolver.Solve
    calls = 0
    def controlled_solve(self, *args):
        nonlocal calls
        calls += 1
        return original_solve(self, *args) if calls == 1 else solver.cp_model.UNKNOWN
    monkeypatch.setattr(solver.cp_model.CpSolver, "Solve", controlled_solve)
    _, assignments, report = solve(use_lexicographic=True)
    assert calls == 2
    assert assignments.status.tolist() == ["assigned"]
    assert "PHASE1_FALLBACK" in report
    assert "OPTIMAL SOLUTION" not in report


def test_lexicographic_does_not_overstate_optimality(monkeypatch):
    original_solve = solver.cp_model.CpSolver.Solve
    calls = 0
    def controlled_solve(self, *args):
        nonlocal calls
        calls += 1
        status = original_solve(self, *args)
        return solver.cp_model.FEASIBLE if calls == 1 else status
    monkeypatch.setattr(solver.cp_model.CpSolver, "Solve", controlled_solve)
    _, assignments, report = solve(use_lexicographic=True)
    assert assignments.status.tolist() == ["assigned"]
    assert "FEASIBLE SOLUTION" in report
    assert "OPTIMAL SOLUTION" not in report


@pytest.mark.parametrize("loader,row", [
    (solver.load_caps_override, {"subject": "A", "cap": 30, "maxcap": 28}),
    (solver.load_caps_override, {"subject": "A", "cap": 0, "maxcap": 28}),
    (solver.load_subject_constraints_override, {"subject": "A", "max_sections_per_slot": 1.5}),
    (solver.load_subject_constraints_override, {"subject": "A", "max_total_sections": -1}),
    (solver.load_fixed_sections_csv, {"subject": "A", "total_sections": 2.5}),
])
def test_invalid_csv_values_are_not_truncated_or_ignored(tmp_path, loader, row):
    path = tmp_path / "settings.csv"
    pd.DataFrame([row]).to_csv(path, index=False)
    with pytest.raises(ValueError):
        loader(str(path))

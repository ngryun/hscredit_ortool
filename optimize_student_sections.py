#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Optimize student-to-section assignments for a single selection group using OR-Tools CP-SAT.

Input Excel format (per user's sheet):
- Column A: 학번 (student_id)
- Column B: 이름 (name)
- Columns C..: subject names, values are 1 (chosen) or 0 (not chosen)

We decide:
- n[t,s]: number of sections opened for subject t in slot s (s in 0..S-1)
- a[u,t,s]: 1 if student u takes subject t in slot s (chosen subjects only)
- uMiss[u,t]: 1 if student u's chosen subject t is unassigned
- over[t,s]: excess over capacity for (t,s), bounded by (maxCap - cap)*n[t,s]
- extra[s]: extra sections beyond rooms_per_slot (bounded by extra_rooms_per_slot)

Objective:
Minimize W1 * sum(uMiss) + W2 * sum(over) + W3 * sum(extra)

Hard constraints enforced:
- Each student can take at most one subject per slot
- For each chosen (u,t): sum_s a[u,t,s] + uMiss[u,t] = 1
- sum_u a[u,t,s] <= cap(t) * n[t,s] + over[t,s]
- sum_t n[t,s] <= rooms_per_slot + extra[s]
- over[t,s] <= (maxCap(t) - cap(t)) * n[t,s]
- 0 <= extra[s] <= extra_rooms_per_slot

Optional per-subject cap/maxCap via a CSV in config.

Outputs:
- sections_plan.csv: subject, slot(a|b|c|...), num_sections, total_enrolled
- assignments.csv: student_id, name, subject, slot, section_label, status (assigned/unassigned)
- report.txt: summary stats

Author: ChatGPT (OR-Tools CP-SAT model)
"""

import argparse
import math
from pathlib import Path
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import pandas as pd
import warnings

# Silence harmless openpyxl default style warning for some workbooks
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module=r"openpyxl\.styles\.stylesheet"
)

# OR-Tools
from ortools.sat.python import cp_model
import sys, json, time

SLOT_LABELS = "abcdefghijklmnopqrstuvwxyz"  # for pretty slot names


@dataclass
class Caps:
    cap: int
    maxcap: int


def load_caps_override(path: Optional[str]) -> Dict[str, Caps]:
    if not path:
        return {}
    df = pd.read_csv(path)
    # expected columns: subject, cap, maxcap (case-insensitive ok)
    cols = {c.lower(): c for c in df.columns}
    subs = {}
    for _, r in df.iterrows():
        subj = str(r[cols.get("subject", "subject")]).strip()
        cap = int(r[cols.get("cap", "cap")])
        maxcap = int(r[cols.get("maxcap", "maxcap")])
        subs[subj] = Caps(cap=cap, maxcap=maxcap)
    return subs


def read_students_xlsx(xlsx_path: str, target_group: Optional[str] = None) -> Tuple[List[Dict], List[str]]:
    """Read students + choices from Excel, supporting multiple formats.

    Supported formats:
    1) Wide (original):
       - Row 1 header with columns: [학번, 이름, <subjects...>]
       - Cells are 1/0 (or Y/N) for choices.

    2) Grade-split header (new):
        - Columns A,B,C = 1/2/3학년 학번 (5자리); pick highest non-zero among C,B,A per row.
        - Row 2 = 학기, Row 3 = 선택그룹 (merged OK), Row 4 = 과목명, Row 5 = 고유번호(무시),
         Row 6.. = 데이터; from column E onward are subject choice cells with 1 for chosen.
       - Name may not be present; we use the ID as name placeholder.
       - Optional: if target_group provided (e.g., 'a'), only include that group's subjects.
    """

    def norm(s):
        return " ".join(str(s).split())

    # Try reading with header to detect original format quickly
    try:
        df = pd.read_excel(xlsx_path, sheet_name=0)
        df.columns = [str(c).strip() for c in df.columns]
        looks_original = len(df.columns) >= 3 and (
            any("학번" in c or c.lower() in {"id", "student_id"} for c in df.columns[:2]) or
            any("이름" in c or c.lower() in {"name"} for c in df.columns[:2])
        )
    except Exception:
        df = pd.DataFrame()
        looks_original = False

    if looks_original and not df.empty:
        # Original wide format path
        student_id_col = df.columns[0]
        student_name_col = df.columns[1]
        subject_cols = [str(c).strip() for c in df.columns[2:]]
        subject_cols = [norm(c) for c in subject_cols]
        df = df.rename(columns={old: new for old, new in zip(df.columns[2:], subject_cols)})
        students = []
        for _, row in df.iterrows():
            sid = str(row[student_id_col]).strip()
            name = str(row.get(student_name_col, "")).strip()
            choices = []
            for subj in subject_cols:
                val = row.get(subj, 0)
                chosen = False
                try:
                    chosen = int(val) == 1
                except Exception:
                    chosen = str(val).strip().upper() in {"1","Y","O","YES","TRUE"}
                if chosen:
                    choices.append(subj)
            if sid or choices:
                students.append({"student_id": sid, "name": name or sid, "choices": choices})
        return students, subject_cols

    # Fall back to the grade-split header format
    df0 = pd.read_excel(xlsx_path, sheet_name=0, header=None)
    # Expect at least 6 rows and 5+ columns (A..E)
    if df0.shape[0] < 6 or df0.shape[1] < 5:
        raise ValueError("Unsupported Excel layout: expected at least 6 rows and 5 columns for the new format")

    # Column indices (0-based)
    COL_A, COL_B, COL_C, COL_D, COL_E = 0, 1, 2, 3, 4
    ROW_SEM, ROW_GRP, ROW_SUBJ, ROW_UNIQ, ROW_DATA_START = 1, 2, 3, 4, 5

    # Subjects from row 4 (index 3), columns E..end
    sem_row = df0.iloc[ROW_SEM, COL_E:]
    grp_row = df0.iloc[ROW_GRP, COL_E:]
    subj_row = df0.iloc[ROW_SUBJ, COL_E:]

    subjects: List[Optional[str]] = []
    groups: List[Optional[str]] = []
    semesters: List[Optional[str]] = []
    used = set()
    for j, base in enumerate(subj_row):
        if pd.isna(base):
            subjects.append(None)
            groups.append(None)
            semesters.append(None)
            continue
        base_s = norm(base)
        if not base_s:
            subjects.append(None)
            groups.append(None)
            semesters.append(None)
            continue
        sem = sem_row.iloc[j] if j < len(sem_row) else None
        grp = grp_row.iloc[j] if j < len(grp_row) else None
        # forward-fill merged header cells
        prev_sem = semesters[-1] if semesters else None
        prev_grp = groups[-1] if groups else None
        if (sem is None or (isinstance(sem, float) and math.isnan(sem)) or str(sem).strip() == ""):
            sem = prev_sem
        if (grp is None or (isinstance(grp, float) and math.isnan(grp)) or str(grp).strip() == ""):
            grp = prev_grp
        sem_s = str(sem).strip() if sem is not None and not pd.isna(sem) else ""
        grp_s = str(grp).strip() if grp is not None and not pd.isna(grp) else ""
        name = base_s
        # Ensure uniqueness if duplicated subject names appear across groups/semesters
        if name in used and (sem_s or grp_s):
            tag = "-".join([p for p in [sem_s, grp_s] if p])
            name = f"{base_s} [{tag}]"
        k = 2
        while name in used:
            name = f"{base_s} ({k})"
            k += 1
        used.add(name)
        subjects.append(name)
        groups.append(grp_s)
        semesters.append(sem_s)

    # Build include mask by target_group
    def _norm_lower(x: Optional[str]) -> str:
        return " ".join(str(x).split()).lower() if x is not None else ""
    tgt = _norm_lower(target_group) if target_group else None
    include_col: List[bool] = []  # aligned with columns E..end
    for j in range(len(subj_row)):
        subj = subjects[j] if j < len(subjects) else None
        if not subj:
            include_col.append(False)
            continue
        if tgt is None:
            include_col.append(True)
        else:
            include_col.append(_norm_lower(groups[j] if j < len(groups) else None) == tgt)

    # Data rows start at row index 5
    students: List[Dict] = []
    for i in range(ROW_DATA_START, df0.shape[0]):
        # Pick highest non-zero among C, B, A (3학년 > 2학년 > 1학년)
        sid_val = None
        for ci in (COL_C, COL_B, COL_A):
            v = df0.iat[i, ci] if ci < df0.shape[1] else None
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            try:
                iv = int(v)
            except Exception:
                # sometimes stored as strings
                try:
                    iv = int(str(v).strip())
                except Exception:
                    continue
            if iv != 0:
                sid_val = iv
                break
        if sid_val is None:
            # no valid id on this row; skip if no choices later
            sid_str = ""
        else:
            sid_str = str(sid_val).zfill(5)

        # Extract choices from E..end for this row
        choices: List[str] = []
        for j, col in enumerate(range(COL_E, df0.shape[1])):
            subj = subjects[j] if j < len(subjects) else None
            if not subj:
                continue
            if not include_col[j]:
                continue
            val = df0.iat[i, col]
            chosen = False
            try:
                chosen = int(val) == 1
            except Exception:
                chosen = str(val).strip() == "1"
            if chosen:
                choices.append(subj)

        if sid_str or choices:
            students.append({
                "student_id": sid_str,
                # Name may be absent in this layout; use ID as placeholder
                "name": sid_str or "",
                "choices": choices,
            })

    # Filter out empty subject names from the final list
    subjects_all = [s for s, inc in zip(subjects, include_col) if s and inc]
    return students, subjects_all


def build_and_solve(students: List[Dict],
                    subjects_all: List[str],
                    slots_g: int,
                    rooms_per_slot: int,
                    extra_rooms_per_slot: int,
                    default_cap: int,
                    default_maxcap: int,
                    caps_override_csv: Optional[str],
                    time_limit_s: float,
                    num_workers: int) -> Tuple[pd.DataFrame, pd.DataFrame, str]:
    # Demand order helps with stable post-processing
    demand = Counter()
    for s in students:
        for t in s["choices"]:
            demand[t] += 1
    subjects_sorted = [t for t, _ in demand.most_common()]
    # Keep subjects with zero demand (rare)
    for t in subjects_all:
        if t not in subjects_sorted:
            subjects_sorted.append(t)

    # Per-subject caps
    subs_caps = load_caps_override(caps_override_csv)
    def cap_of(t: str) -> int:
        return subs_caps.get(t, Caps(default_cap, default_maxcap)).cap
    def maxcap_of(t: str) -> int:
        return subs_caps.get(t, Caps(default_cap, default_maxcap)).maxcap

    # Indices
    U = len(students)
    T = len(subjects_sorted)
    S = slots_g
    U_idx = range(U)
    T_idx = range(T)
    S_idx = range(S)

    # Model
    model = cp_model.CpModel()

    # Decision variables
    a = {}       # a[u,t,s] in {0,1} if (u,t) chosen
    uMiss = {}   # uMiss[u,t] in {0,1}
    n = {}       # n[t,s] >= 0
    over = {}    # over[t,s] >= 0
    extra = {}   # extra[s] in [0, extra_rooms_per_slot]

    # Build chosen map for speed
    # chosen_map[u][t] tells whether student u chose subject t
    chosen_map = [[False]*T for _ in range(U)]
    subj_index = {t:i for i,t in enumerate(subjects_sorted)}
    for u, stu in enumerate(students):
        for tname in stu["choices"]:
            t = subj_index[tname]
            chosen_map[u][t] = True

    # Variables
    for u in U_idx:
        for t in T_idx:
            if chosen_map[u][t]:
                for s in S_idx:
                    a[(u,t,s)] = model.NewBoolVar(f"a_u{u}_t{t}_s{s}")
                uMiss[(u,t)] = model.NewBoolVar(f"uMiss_u{u}_t{t}")

    for t in T_idx:
        for s in S_idx:
            n[(t,s)] = model.NewIntVar(0, 1000, f"n_t{t}_s{s}")
            over[(t,s)] = model.NewIntVar(0, (maxcap_of(subjects_sorted[t])-cap_of(subjects_sorted[t]))*1000,
                                          f"over_t{t}_s{s}")
    for s in S_idx:
        extra[s] = model.NewIntVar(0, extra_rooms_per_slot, f"extra_s{s}")

    # Constraints
    # Student per slot: <=1
    for u in U_idx:
        for s in S_idx:
            vars_in = [a[(u,t,s)] for t in T_idx if chosen_map[u][t]]
            if vars_in:
                model.Add(sum(vars_in) <= 1)

    # Each chosen (u,t) must be assigned to one slot or marked unassigned
    for u in U_idx:
        for t in T_idx:
            if chosen_map[u][t]:
                model.Add(sum(a[(u,t,s)] for s in S_idx) + uMiss[(u,t)] == 1)

    # Capacity linkage
    for t in T_idx:
        tname = subjects_sorted[t]
        cap_t = cap_of(tname)
        maxcap_t = maxcap_of(tname)
        for s in S_idx:
            sum_assign = sum(a[(u,t,s)] for u in U_idx if chosen_map[u][t])
            model.Add(sum_assign <= cap_t * n[(t,s)] + over[(t,s)])
            model.Add(over[(t,s)] <= (maxcap_t - cap_t) * n[(t,s)])

    # Rooms per slot
    for s in S_idx:
        model.Add(sum(n[(t,s)] for t in T_idx) <= rooms_per_slot + extra[s])

    # Objective
    W1 = 10**6
    W2 = 10**4
    W3 = 10**5
    obj_terms = []
    obj_terms += [W1 * uMiss[(u,t)] for (u,t) in uMiss.keys()]
    obj_terms += [W2 * over[(t,s)] for t in T_idx for s in S_idx]
    obj_terms += [W3 * extra[s] for s in S_idx]
    model.Minimize(sum(obj_terms))

    # Solve
    solver = cp_model.CpSolver()
    if time_limit_s and time_limit_s > 0:
        solver.parameters.max_time_in_seconds = float(time_limit_s)
    if num_workers and num_workers > 0:
        solver.parameters.num_search_workers = int(num_workers)

    # Emit real progress on each improving solution (solutions callback)
    class ProgressCallback(cp_model.CpSolverSolutionCallback):
        def __init__(self, uMiss_vars: Dict[tuple, cp_model.IntVar]):
            super().__init__()
            self._solutions = 0
            self._uMiss_vars = list(uMiss_vars.values())
        def OnSolutionCallback(self):
            self._solutions += 1
            try:
                unassigned = 0
                for v in self._uMiss_vars:
                    # Boolean var; Value is 0/1
                    unassigned += int(self.Value(v))
                payload = {
                    "solutions": self._solutions,
                    "wall_time": float(self.WallTime()),
                    "objective": float(self.ObjectiveValue()),
                    "unassigned": int(unassigned),
                }
                print("[PROGRESS] " + json.dumps(payload, ensure_ascii=False), flush=True)
            except Exception:
                # Best-effort progress; ignore callback errors
                pass

    cb = ProgressCallback(uMiss)
    solver.parameters.log_to_stdout = False
    solver.parameters.log_search_progress = False
    status = solver.Solve(model, cb)
    status_str = solver.StatusName(status)

    # Extract
    if S <= 0:
        raise ValueError("slots must be >= 1")
    if S > len(SLOT_LABELS):
        raise ValueError(f"slots ({S}) exceed supported labels ({len(SLOT_LABELS)})")
    slot_labels = [SLOT_LABELS[i] for i in range(S)]
    # Sections plan
    sp_rows = []
    slot_section_count = {s: 0 for s in S_idx}
    assigned_counts = defaultdict(int)  # (t,s) -> assigned
    for t in T_idx:
        for s in S_idx:
            nn = int(solver.Value(n[(t,s)]))
            if nn > 0:
                total_enrolled = 0
                for u in U_idx:
                    if chosen_map[u][t]:
                        if int(solver.Value(a[(u,t,s)])) == 1:
                            total_enrolled += 1
                sp_rows.append({
                    "subject": subjects_sorted[t],
                    "slot": slot_labels[s],
                    "num_sections": nn,
                    "total_enrolled": total_enrolled
                })
                slot_section_count[s] += nn
                assigned_counts[(t,s)] = total_enrolled
    sections_plan_df = pd.DataFrame(sp_rows).sort_values(["slot","subject"]).reset_index(drop=True)

    # Assignments (student-level), and post-assign section labels (balanced)
    assign_rows = []
    for u in U_idx:
        for t in T_idx:
            if not chosen_map[u][t]:
                continue
            placed = False
            for s in S_idx:
                if int(solver.Value(a[(u,t,s)])) == 1:
                    assign_rows.append({
                        "student_id": students[u]["student_id"],
                        "name": students[u]["name"],
                        "subject": subjects_sorted[t],
                        "slot": slot_labels[s],
                        "section_label": None,  # fill later
                        "status": "assigned"
                    })
                    placed = True
                    break
            if not placed:
                assign_rows.append({
                    "student_id": students[u]["student_id"],
                    "name": students[u]["name"],
                    "subject": subjects_sorted[t],
                    "slot": None,
                    "section_label": None,
                    "status": "unassigned"
                })
    assignments_df = pd.DataFrame(assign_rows)

    # Post-assign students into concrete sections (1..n) for each (t,s)
    # Fill up to cap, then allow up to maxcap if solver used "over".
    def post_label():
        # quick lookup by (subj, slot)
        by_key = defaultdict(list)
        for idx, r in assignments_df[assignments_df["status"]=="assigned"].iterrows():
            by_key[(r["subject"], r["slot"])].append(idx)

        # Build labels
        for (subj, slot), idx_list in by_key.items():
            t = subj_index[subj]
            s = slot_labels.index(slot)
            nn = int(solver.Value(n[(t,s)]))
            if nn <= 0:
                continue
            cap_t = cap_of(subj)
            maxcap_t = maxcap_of(subj)
            # target capacities per section (balanced)
            enrolled = len(idx_list)
            # Distribute as evenly as possible with cap_t (soft), but not exceeding maxcap_t
            # We'll do round-robin up to cap_t, then additional up to maxcap_t.
            # Initialize counters
            per_sec = [0]*nn
            labels = [f"{subj}_{slot}_{i+1}" for i in range(nn)]
            ptr = 0
            # Primary fill up to cap_t
            for idx in idx_list:
                # move ptr to a section that still has room (<cap or (<maxcap if all reached cap))
                tries = 0
                placed = False
                while tries < nn and per_sec[ptr] >= cap_t:
                    ptr = (ptr + 1) % nn
                    tries += 1
                if per_sec[ptr] < cap_t:
                    assignments_df.at[idx, "section_label"] = labels[ptr]
                    per_sec[ptr] += 1
                    ptr = (ptr + 1) % nn
                    placed = True
                    continue
                # if all at least cap, allow up to maxcap
                tries = 0
                while tries < nn and per_sec[ptr] >= maxcap_t:
                    ptr = (ptr + 1) % nn
                    tries += 1
                if per_sec[ptr] < maxcap_t:
                    assignments_df.at[idx, "section_label"] = labels[ptr]
                    per_sec[ptr] += 1
                    ptr = (ptr + 1) % nn
                    placed = True
                if not placed:
                    # shouldn't happen if capacity constraints held
                    assignments_df.at[idx, "section_label"] = labels[ptr]
                    per_sec[ptr] += 1
                    ptr = (ptr + 1) % nn

    post_label()

    # Report
    total_unassigned = int((assignments_df["status"]=="unassigned").sum())
    students_with_unassigned = assignments_df[assignments_df["status"]=="unassigned"]["student_id"].nunique()
    rep_lines = []
    rep_lines.append("=== Optimization Report (OR-Tools CP-SAT) ===")
    rep_lines.append(f"Solver status: {status_str}")
    rep_lines.append(f"Students: {len(students)} | Subjects: {len(subjects_all)}")
    rep_lines.append(f"Slots: {S} | Rooms/slot: {rooms_per_slot} | Extra allowed/slot: {extra_rooms_per_slot}")
    rep_lines.append("")
    rep_lines.append(f"Total assignments: {len(assignments_df)-total_unassigned}")
    rep_lines.append(f"Total unassigned (student-subject pairs): {total_unassigned}")
    rep_lines.append(f"Students with at least one unassigned: {students_with_unassigned}")
    rep_lines.append("")
    # Slot usage
    rep_lines.append("Slot usage:")
    for s in S_idx:
        rep_lines.append(f"  - slot {slot_labels[s]}: sections_open={slot_section_count.get(s,0)} (limit={rooms_per_slot})")
    report_text = "\n".join(rep_lines)

    return sections_plan_df, assignments_df, report_text


def main():
    ap = argparse.ArgumentParser(description="Student sectioning optimizer (CP-SAT).")
    ap.add_argument("--input", required=True, help="Input Excel (.xlsx): A=학번, B=이름, C..=subjects (1/0)")
    ap.add_argument("--output-dir", required=True, help="Output directory for CSVs/reports")
    ap.add_argument("--slots", type=int, default=4, help="Number of slots (default: 4)")
    ap.add_argument("--rooms-per-slot", type=int, default=7, help="Classrooms per slot (default: 7)")
    ap.add_argument("--extra-rooms-per-slot", type=int, default=1, help="Extra sections allowed per slot beyond the rooms limit (default: 1)")
    ap.add_argument("--cap", type=int, default=28, help="Default capacity per section (default: 28)")
    ap.add_argument("--maxcap", type=int, default=30, help="Default max capacity per section (default: 30)")
    ap.add_argument("--caps-csv", default=None, help="Optional CSV with per-subject cap/maxcap overrides (columns: subject,cap,maxcap)")
    ap.add_argument("--group", default=None, help="Optional selection group filter for new Excel format (e.g., 'a').")
    ap.add_argument("--time-limit", type=float, default=60.0, help="Solver time limit in seconds (default: 60)")
    ap.add_argument("--workers", type=int, default=8, help="Number of solver workers (default: 8)")
    args = ap.parse_args()

    students, subjects = read_students_xlsx(args.input, target_group=args.group)
    sections_df, assignments_df, report_text = build_and_solve(
        students=students,
        subjects_all=subjects,
        slots_g=args.slots,
        rooms_per_slot=args.rooms_per_slot,
        extra_rooms_per_slot=args.extra_rooms_per_slot,
        default_cap=args.cap,
        default_maxcap=args.maxcap,
        caps_override_csv=args.caps_csv,
        time_limit_s=args.time_limit,
        num_workers=args.workers
    )

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    sections_path = out / "sections_plan.csv"
    assignments_path = out / "assignments.csv"
    report_path = out / "report.txt"
    sections_df.to_csv(sections_path, index=False)
    assignments_df.to_csv(assignments_path, index=False)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print("Wrote:")
    print(" -", sections_path)
    print(" -", assignments_path)
    print(" -", report_path)


if __name__ == "__main__":
    main()

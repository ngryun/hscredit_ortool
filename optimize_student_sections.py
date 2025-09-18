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

# OR-Tools
from ortools.sat.python import cp_model

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


def read_students_xlsx(xlsx_path: str) -> Tuple[List[Dict], List[str]]:
    df = pd.read_excel(xlsx_path, sheet_name=0)
    df.columns = [str(c).strip() for c in df.columns]
    if len(df.columns) < 3:
        raise ValueError("Expected at least 3 columns: 학번, 이름, and subject columns with 1/0")
    student_id_col = df.columns[0]
    student_name_col = df.columns[1]
    subject_cols = [str(c).strip() for c in df.columns[2:]]
    # Normalize subject names (trim/collapse spaces)
    def norm(s):
        return " ".join(str(s).split())
    subject_cols = [norm(c) for c in subject_cols]
    df = df.rename(columns={old: new for old, new in zip(df.columns[2:], subject_cols)})
    students = []
    for _, row in df.iterrows():
        sid = str(row[student_id_col]).strip()
        name = str(row[student_name_col]).strip()
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
        students.append({"student_id": sid, "name": name, "choices": choices})
    return students, subject_cols


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

    status = solver.Solve(model)
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
    ap.add_argument("--time-limit", type=float, default=60.0, help="Solver time limit in seconds (default: 60)")
    ap.add_argument("--workers", type=int, default=8, help="Number of solver workers (default: 8)")
    args = ap.parse_args()

    students, subjects = read_students_xlsx(args.input)
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

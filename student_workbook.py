"""One Excel interpretation shared by preview, optimization and template export."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from numbers import Real

import openpyxl
import pandas as pd


def cell_text(value):
    if value is None or (isinstance(value, Real) and math.isnan(value)):
        return ""
    return str(value).strip()


def normalize_student_id(value):
    """Preserve textual IDs, including leading zeros, without float round trips."""
    text = cell_text(value)
    if isinstance(value, Real):
        if not math.isfinite(value) or int(value) != value:
            raise ValueError("학번은 정수 또는 문자열이어야 합니다.")
        return str(int(value))
    return re.sub(r"^([0-9]+)\.0+$", r"\1", text)


def is_selected(value):
    if isinstance(value, Real):
        return value == 1
    return cell_text(value).upper() in {"1", "1.0", "Y", "O", "YES", "TRUE"}


def read_assignments_csv(path):
    # pandas' default inference loses 00101 and treats literal "NA" as missing.
    return pd.read_csv(path, dtype=str, keep_default_na=False)


@dataclass
class SubjectColumn:
    column: int  # one-based Excel column
    name: str
    group: str = ""
    semester: str = ""


@dataclass
class StudentWorkbook:
    students: list[dict]
    student_rows: list[int]  # one-based Excel rows, aligned with students
    columns: list[SubjectColumn]

    @property
    def subjects(self):
        return [column.name for column in self.columns]

    def preview(self):
        counts = Counter(choice for student in self.students for choice in student["choices"])
        # Korean five-digit IDs: grade + two class digits + two student digits.
        classes = {s["student_id"][:3] for s in self.students
                   if len(s["student_id"]) == 5 and s["student_id"].isdigit()}
        return {
            "groups": sorted({c.group for c in self.columns if c.group}),
            "semesters": sorted({c.semester for c in self.columns if c.semester}),
            "subjects": [{"name": c.name, "count": counts[c.name], "group": c.group} for c in self.columns],
            "headcount": len(self.students),
            "class_count": len(classes),
        }


def _group_filter(raw):
    if raw is None:
        return set()
    tokens = re.split(r"[,|;/]", raw) if isinstance(raw, str) else raw
    return {" ".join(cell_text(token).split()).lower() for token in tokens if cell_text(token)}


def read_student_workbook(source, target_group=None):
    """Read the first worksheet and retain source coordinates for writing results."""
    try:
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        try:
            rows = list(wb.worksheets[0].iter_rows(values_only=True))
        finally:
            wb.close()
    except Exception as exc:
        raise ValueError("엑셀 파일을 읽을 수 없습니다. 올바른 .xlsx 파일인지 확인하세요.") from exc
    if not rows:
        raise ValueError("엑셀 양식이 비어 있습니다.")
    first = [cell_text(v) for v in rows[0][:2]]
    simple = len(rows[0]) >= 3 and any(
        "학번" in v or "이름" in v or v.lower() in {"id", "student_id", "name"}
        for v in first
    )
    if not simple and (len(rows) < 5 or len(rows[0]) < 5):
        raise ValueError("지원하지 않는 엑셀 양식입니다. 학번·이름 헤더 또는 학년별 수강신청 양식을 확인하세요.")

    subject_row, first_column, data_start = (0, 2, 1) if simple else (3, 4, 5)
    columns, used = [], set()
    semester, group = "", ""
    for index in range(first_column, len(rows[subject_row])):
        if not simple:
            # Carry merged headers across even an empty subject column.
            semester = cell_text(rows[1][index]) or semester
            group = cell_text(rows[2][index]) or group
        base = " ".join(cell_text(rows[subject_row][index]).split())
        if not base:
            continue
        name = base
        if name in used and (semester or group):
            tag = "-".join(v for v in (semester, group) if v)
            name = f"{base} [{tag}]"
        suffix = 2
        while name in used:
            name = f"{base} ({suffix})"
            suffix += 1
        used.add(name)
        columns.append(SubjectColumn(index + 1, name, group, semester))
    if not columns:
        raise ValueError("엑셀 양식에 과목명이 없습니다.")

    target_groups = _group_filter(target_group)
    if target_groups and not simple:
        known = {" ".join(c.group.split()).lower() for c in columns}
        if target_groups - known:
            raise ValueError("선택한 그룹을 엑셀에서 찾을 수 없습니다. 그룹 이름을 확인하세요.")
    included = [c for c in columns if simple or not target_groups or " ".join(c.group.split()).lower() in target_groups]
    students, student_rows, seen_ids = [], [], {}
    for row_index, row in enumerate(rows[data_start:], start=data_start + 1):
        if simple:
            sid = normalize_student_id(row[0])
            name = cell_text(row[1])
        else:
            sid = ""
            for value in reversed(row[:3]):
                candidate = normalize_student_id(value)
                if candidate and candidate.strip('0'):
                    sid = candidate.zfill(5) if candidate.isdigit() else candidate
                    break
            name = cell_text(row[3])
        any_choice = any(is_selected(row[c.column - 1]) for c in columns)
        if not sid:
            if any_choice or name:
                raise ValueError(f"{row_index}행: 학번이 없습니다. 학번을 입력해 주세요.")
            continue
        if sid in seen_ids:
            raise ValueError(f"{row_index}행: {seen_ids[sid]}행과 학번이 중복됩니다. 중복 행을 확인하세요.")
        seen_ids[sid] = row_index
        choices = [c.name for c in included if is_selected(row[c.column - 1])]
        students.append({"student_id": sid, "name": name or sid, "choices": choices})
        student_rows.append(row_index)
    return StudentWorkbook(students, student_rows, included)

import io

import openpyxl
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from openpyxl.styles import PatternFill

import app as web
from optimize_student_sections import read_students_xlsx, generate_filled_template


def save_workbook(tmp_path, rows, grouped=False):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    if grouped:
        ws.merge_cells('E2:H2')
        ws.merge_cells('E3:G3')
    path = tmp_path / 'input.xlsx'
    wb.save(path)
    wb.close()
    return path


def preview(path):
    with TestClient(web.app) as client:
        return client.post('/inspect', files={'xlsx': ('input.xlsx', path.read_bytes())})


def grouped_book(tmp_path):
    return save_workbook(tmp_path, [
        [None] * 8,
        [None, None, None, None, '1학기', None, None, None],
        [None, None, None, None, 'G1', None, None, 'G2'],
        [None, None, None, None, '수학', None, '영어', '수학'],
        [None, None, None, None, 'U1', None, 'U2', 'U3'],
        [10101, 20101, 30101, '가학생', 1, None, 'Y', 1],
        [None, None, 31101, '나학생', '1', None, 0, 1],
    ], grouped=True)


def test_preview_lists_all_subjects_and_matches_choices(tmp_path):
    path = save_workbook(tmp_path, [
        ['학번', '이름', '수학', '영어', '과학'],
        [10101, '가학생', 'Y', 0, 'O'],
        [11101, '나학생', 1.9, 'TRUE', 1],
    ])
    response = preview(path)
    assert response.status_code == 200
    result = response.json()
    assert [(s['name'], s['count']) for s in result['subjects']] == [('수학', 1), ('영어', 1), ('과학', 2)]
    students, subjects = read_students_xlsx(path)
    assert subjects == ['수학', '영어', '과학']
    assert students[1]['choices'] == ['영어', '과학']
    assert result['headcount'] == len(students) == 2
    assert result['class_count'] == 2


def test_blank_rows_and_text_identifiers_are_preserved(tmp_path):
    path = save_workbook(tmp_path, [
        ['학번', '이름', 'A'], ['00101', 'NA', 1],
        [None, None, None], ['101', '나학생', 1],
    ])
    students, _ = read_students_xlsx(path)
    assert [(s['student_id'], s['name']) for s in students] == [('00101', 'NA'), ('101', '나학생')]


def test_duplicate_subjects_are_distinct_in_preview_and_solver(tmp_path):
    path = save_workbook(tmp_path, [['학번', '이름', 'A', ' A '], [10101, '학생', 1, 1]])
    students, subjects = read_students_xlsx(path)
    assert len(set(subjects)) == 2
    assert students[0]['choices'] == subjects
    assert [s['name'] for s in preview(path).json()['subjects']] == subjects


def test_grouped_names_ids_and_headers_are_consistent(tmp_path):
    path = grouped_book(tmp_path)
    students, subjects = read_students_xlsx(path, target_group='g1')
    assert subjects == ['수학', '영어']
    assert students[0] == {'student_id': '30101', 'name': '가학생', 'choices': ['수학', '영어']}
    result = preview(path).json()
    assert result['subjects'] == [
        {'name': '수학', 'count': 2, 'group': 'G1'},
        {'name': '영어', 'count': 1, 'group': 'G1'},
        {'name': '수학 [1학기-G2]', 'count': 2, 'group': 'G2'},
    ]
    assert result['class_count'] == 2


def write_assignments(tmp_path, records):
    path = tmp_path / 'assignments.csv'
    pd.DataFrame(records, columns=['student_id', 'name', 'subject', 'slot', 'section_label', 'status']).to_csv(path, index=False)
    return path


def test_filled_template_uses_solver_sheet_ids_and_subject_keys(tmp_path):
    path = grouped_book(tmp_path)
    wb = openpyxl.load_workbook(path)
    wb.worksheets[0]['E6'].fill = PatternFill('solid', fgColor='FFFF00')
    wb.create_sheet('메모')['A1'] = '보존'
    wb.active = 1
    wb.save(path)
    wb.close()
    assignments = write_assignments(tmp_path, [
        ['30101', '가학생', '수학', 'A', '수학_A_1', 'assigned'],
        ['30101', '가학생', '영어', '', '', 'unassigned'],
        ['30101', '가학생', '수학 [1학기-G2]', 'B', '수학_B_1', 'assigned'],
        ['31101', '나학생', '수학', 'B', '수학_B_1', 'assigned'],
    ])
    output = tmp_path / 'filled.xlsx'
    generate_filled_template(path, assignments, output)
    wb = openpyxl.load_workbook(output)
    ws = wb.worksheets[0]
    assert [ws[cell].value for cell in ('E6', 'G6', 'H6', 'E7')] == ['A', '미배정', 'B', 'B']
    assert ws['H7'].value == 1  # not in this optimization, retain original selection
    assert ws['E6'].fill.fgColor.rgb == '00FFFF00'
    assert wb['메모']['A1'].value == '보존'
    wb.close()


def test_export_does_not_merge_leading_zero_ids(tmp_path):
    assignments = write_assignments(tmp_path, [
        ['00101', 'NA', 'A', 'A', 'A_A_1', 'assigned'],
        ['101', '나학생', 'A', '', '', 'unassigned'],
    ])
    output = tmp_path / 'export.xlsx'
    web.build_export_xlsx(assignments, output)
    wb = openpyxl.load_workbook(output)
    rows = list(wb.active.iter_rows(min_row=3, values_only=True))
    assert len(rows) == 2
    assert {r[0]: r[1] for r in rows} == {'00101': 'NA', '101': '나학생'}
    wb.close()


@pytest.mark.parametrize('rows,message', [
    ([['学番', '氏名', 'A']], '양식'),
    ([['학번', '이름', 'A'], [None, '학생', 1]], '학번'),
    ([['학번', '이름', 'A'], [10101, '가', 1], [10101, '나', 1]], '중복'),
])
def test_invalid_input_returns_actionable_error(tmp_path, monkeypatch, rows, message):
    path = save_workbook(tmp_path, rows)
    response = preview(path)
    assert response.status_code == 422
    assert message in response.json()['error']
    with pytest.raises(ValueError, match=message):
        read_students_xlsx(path)
    jobs_dir = tmp_path / 'jobs'
    monkeypatch.setattr(web, 'BASE', jobs_dir)
    monkeypatch.setattr(web, 'JOBS', {})
    with TestClient(web.app) as client:
        run = client.post('/run', files={'xlsx': ('input.xlsx', path.read_bytes())})
    assert run.status_code == 422
    assert message in run.json()['error']
    assert not web.JOBS
    assert not jobs_dir.exists()


def test_corrupt_workbook_is_not_reported_as_empty_success():
    with TestClient(web.app) as client:
        response = client.post('/inspect', files={'xlsx': ('broken.xlsx', b'not an xlsx')})
    assert response.status_code == 422
    assert response.json()['error']


def test_header_only_workbook_has_zero_students(tmp_path):
    path = save_workbook(tmp_path, [['학번', '이름', 'A', 'B']])
    assert read_students_xlsx(path) == ([], ['A', 'B'])
    result = preview(path).json()
    assert result['headcount'] == 0
    assert [s['count'] for s in result['subjects']] == [0, 0]


def test_unknown_group_is_reported(tmp_path):
    with pytest.raises(ValueError, match='그룹'):
        read_students_xlsx(grouped_book(tmp_path), target_group='G3')


@pytest.mark.parametrize('grouped', [False, True])
def test_cli_round_trip_preserves_student_and_selection_identity(tmp_path, grouped):
    import subprocess
    import sys
    from pathlib import Path
    from student_workbook import read_assignments_csv, read_student_workbook

    if grouped:
        path = grouped_book(tmp_path)
        group_args = ['--group', 'G1']
    else:
        path = save_workbook(tmp_path, [
            ['학번', '이름', '수학', '수학', 'NA'],
            ['00101', 'NA', 'Y', 0, 1],
            ['101', '다른학생', 0, 'O', 'TRUE'],
        ])
        group_args = []
    output = tmp_path / 'out'
    process = subprocess.run([
        sys.executable, str(Path(__file__).resolve().parents[1] / 'run_local.py'), '--solver',
        '--input', str(path), '--output-dir', str(output),
        '--slots', '3', '--rooms-per-slot', '3', '--time-limit', '5', '--workers', '1',
        *group_args,
    ], capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stderr
    assignments = read_assignments_csv(output / 'assignments.csv')
    parsed = read_student_workbook(path, target_group='G1' if grouped else None)
    expected = {(s['student_id'], c) for s in parsed.students for c in s['choices']}
    actual = set(zip(assignments.student_id, assignments.subject))
    assert actual == expected
    assert set(assignments.status) == {'assigned'}
    assert dict(zip(assignments.student_id, assignments.name)) == {s['student_id']: s['name'] for s in parsed.students}
    wb = openpyxl.load_workbook(output / 'filled_template.xlsx')
    ws = wb.worksheets[0]
    for student, row in zip(parsed.students, parsed.student_rows):
        for column in parsed.columns:
            if column.name in student['choices']:
                record = assignments[(assignments.student_id == student['student_id']) & (assignments.subject == column.name)].iloc[0]
                assert ws.cell(row, column.column).value == record['slot']
    if grouped:
        assert ws['H6'].value == ws['H7'].value == 1
    wb.close()

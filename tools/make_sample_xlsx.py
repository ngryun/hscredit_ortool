# CI 스모크 테스트용 가짜 수강신청 xlsx 생성 (단순 양식: A=학번, B=이름, C..=과목 1/0)
# 사용법: python tools/make_sample_xlsx.py [출력경로]
import sys

from openpyxl import Workbook

SUBJECTS = ["물리학", "화학", "생명과학", "지구과학", "경제", "정치와 법"]


def main(out_path: str = "sample_students.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.append(["학번", "이름"] + SUBJECTS)
    for i in range(60):
        sid = 10101 + i
        name = f"학생{i + 1:02d}"
        # 학생마다 과목 3개를 결정적으로 선택 (난수 불필요)
        picks = {(i + k) % len(SUBJECTS) for k in (0, 2, 3)}
        row = [sid, name] + [1 if j in picks else 0 for j in range(len(SUBJECTS))]
        ws.append(row)
    wb.save(out_path)
    print(f"sample written: {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample_students.xlsx")

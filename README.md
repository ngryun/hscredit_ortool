# OR-Tools 기반 학생-과목 섹션 배정기 (미배정 최소화)

## 기능
- 엑셀(학번/이름/과목=1/0)을 읽어, **미배정 최소화**를 최우선 목표로 섹션 수·슬롯·학생 배정을 동시에 최적화합니다.
- 제약
  - 슬롯당 개설 섹션 수 ≤ `rooms_per_slot`(학급/교실 수) + `extra_rooms_per_slot`
  - 과목별 정원 `cap` 및 최대정원 `maxcap` (과목별 예외 CSV 지원)
  - 한 학생은 **슬롯마다 1과목**만 수강
- 출력
  - `sections_plan.csv`: 과목×슬롯별 개설 반수·수강인원
  - `assignments.csv`: 학생별 과목 배정(슬롯/섹션 라벨 포함)
  - `report.txt`: 요약

## 설치
```bash
python -m venv .venv
source .venv/bin/activate  # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
```

## 실행
```bash
python optimize_student_sections.py \
  --input "2025입학생_수강생일괄등록양식(sample).xlsx" \
  --output-dir out \
  --slots 4 \
  --rooms-per-slot 7 \
  --extra-rooms-per-slot 1 \
  --cap 28 \
  --maxcap 30 \
  --time-limit 60 \
  --workers 8
```

### 과목별 정원 예외
CSV(`caps.csv`) 형식:
```csv
subject,cap,maxcap
법과 사회,26,28
확률과 통계,32,34
```
옵션 추가:
```bash
--caps-csv caps.csv
```

## 입력 엑셀 형식
- A열: 학번
- B열: 이름
- C열 이후: 과목명 열(값=1이면 선택, 0이면 미선택)

## 모델(간단 설명)
- 결정변수
  - `n[t,s]`(정수): 과목 t의 슬롯 s에서 개설 섹션 수
  - `a[u,t,s]`(0/1): 학생 u가 과목 t를 슬롯 s에서 듣는지
  - `uMiss[u,t]`(0/1): 학생 u의 과목 t 미배정
  - `over[t,s]`(정수): `(maxcap-cap)` 범위 내 초과좌석
  - `extra[s]`(정수): 슬롯 s의 **여분 섹션 수**(rooms 초과분, 상한은 `extra_rooms_per_slot`)
- 목적함수
  - `W1*∑uMiss + W2*∑over + W3*∑extra` (W1≫W2≥W3)

## 출력 예시
- `sections_plan.csv`: `subject,slot,num_sections,total_enrolled`
- `assignments.csv`: `student_id,name,subject,slot,section_label,status`

## 참고
- OR-Tools CP-SAT 사용: 전역적으로 **미배정 최소**를 보장하는 방향으로 탐색합니다.
- 필요 시 교사 시수/가용, 금지 슬롯, 반수 상하한 등 제약을 추가 확장 가능합니다.

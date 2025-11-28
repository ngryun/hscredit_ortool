# OR-Tools 기반 학생-과목 섹션 배정기

고등학교 이동수업(이동반) 편성을 위한 자동 최적화 도구입니다. Google OR-Tools의 CP-SAT 솔버를 사용하여 학생들의 과목 선택을 바탕으로 **미배정 학생을 최소화**하면서 교실 및 시간표 제약을 만족하는 최적의 분반 계획을 생성합니다.

## 주요 기능
- **미배정 최소화**: 모든 학생이 원하는 과목을 최대한 수강할 수 있도록 최적화
- **제약 조건 지원**:
  - 슬롯(시간대)당 개설 가능한 교실 수 제한
  - 과목별 정원 및 최대정원 설정 (과목별 개별 설정 가능)
  - 한 학생은 동일 시간대에 1과목만 수강
- **상세한 결과 제공**:
  - `sections_plan.csv`: 과목×슬롯별 개설 반 수 및 수강인원
  - `assignments.csv`: 학생별 상세 배정 결과 (슬롯/섹션 라벨 포함)
  - `report.txt`: 최적화 요약 통계
- **두 가지 실행 방식**: CLI(명령줄) 및 웹 GUI 지원

## 설치
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 실행 방법

### 1. CLI (명령줄 인터페이스)
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

### 2. GUI (웹 인터페이스)
웹 브라우저에서 직관적으로 사용할 수 있는 인터페이스를 제공합니다.

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

실행 후 웹 브라우저에서 `http://localhost:8000`에 접속하세요.

#### 온라인 테스트 서버
직접 설치 없이 테스트해보실 수 있습니다:
- **테스트 링크**: http://43.200.187.18:8000/
- ⚠️ 서버 과부하 문제로 동시접속 2명으로 제한되며, 계산속도가 느립니다

**GUI 기능**:
- 엑셀 파일 드래그 앤 드롭 업로드
- 실시간 과목별 선택 인원 및 권장 반 수 미리보기
- 과목별 개설 반 수 직접 지정
- 과목별 제약 조건 설정 (시간대별 최대 개설 반 수 등)
- 최적화 진행 상황 실시간 표시
- 결과 파일 다운로드 및 시각화된 배정 현황 확인

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

## 출력 파일
- `sections_plan.csv`: 과목×슬롯별 개설 반 수 및 총 수강 인원
  - 형식: `subject,slot,num_sections,total_enrolled`
- `assignments.csv`: 학생별 상세 배정 결과
  - 형식: `student_id,name,subject,slot,section_label,status`
  - status: `assigned` (배정됨) 또는 `unassigned` (미배정)
- `report.txt`: 최적화 통계 및 요약 보고서

## 기술 세부사항

### 최적화 모델
- **결정변수**:
  - `n[t,s]`(정수): 과목 t의 슬롯 s에서 개설 섹션 수
  - `a[u,t,s]`(0/1): 학생 u가 과목 t를 슬롯 s에서 듣는지
  - `uMiss[u,t]`(0/1): 학생 u의 과목 t 미배정
  - `over[t,s]`(정수): `(maxcap-cap)` 범위 내 초과좌석
  - `extra[s]`(정수): 슬롯 s의 여분 섹션 수(rooms 초과분, 상한은 `extra_rooms_per_slot`)
- **목적함수**: `W1*∑uMiss + W2*∑over + W3*∑extra` (W1≫W2≥W3)
  - 미배정 학생 최소화를 최우선 목표로, 초과 수용 및 여분 교실 사용을 최소화

### 확장 가능성
- 교사 시수/가용 시간 제약
- 특정 과목의 금지 슬롯 지정
- 과목별 개설 반 수 상하한 설정
- 기타 사용자 정의 제약 조건

## 개인정보 보호

이 프로그램은 학생 개인정보(학번, 이름) 보호를 위해 다음과 같이 설계되었습니다:

### 웹 GUI 사용 시
- **다운로드 후 자동 삭제**: 각 파일을 다운로드하면 해당 파일이 즉시 삭제됩니다
  - 모든 파일을 어떤 순서로든 다운로드 가능
  - 다운로드한 파일은 서버에서 즉시 삭제됨
  - 전체 작업 폴더는 **1시간 후 자동 삭제**
- 통계 정보만 포함된 `report.txt`는 `data/reports/` 디렉토리에 영구 보관됩니다
- 작업 완료 후 1시간 이내에 필요한 파일을 모두 다운로드하세요

### CLI 사용 시
- 출력 파일에는 학생 개인정보가 포함되므로 사용 후 즉시 삭제를 권장합니다
- Git 저장소에는 `data/` 디렉토리가 제외되어 있어 실수로 커밋되지 않습니다

### 주의사항
⚠️ 프로젝트 폴더를 압축하거나 공유할 때 `data/` 디렉토리가 포함되지 않도록 주의하세요.
⚠️ 같은 파일을 재다운로드할 수 없으니 필요한 파일은 한 번에 모두 다운로드하세요.

## 문의 및 기여

버그 리포트나 기능 제안은 GitHub Issues를 통해 제출해 주세요.

## 개발자

**남궁연** (설악고등학교 교사)
- 블로그: https://namgungyeon.tistory.com/138

## 라이선스

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)

이 프로그램은 고교학점제 운영을 돕기 위한 목적으로 개발되었습니다.

이 프로젝트는 [Creative Commons Attribution-NonCommercial 4.0 International License](https://creativecommons.org/licenses/by-nc/4.0/) (CC BY-NC 4.0) 하에 배포됩니다.

**사용 조건**:
- ✅ 교육 및 연구 목적으로 자유롭게 사용 가능
- ✅ 수정 및 재배포 가능
- ✅ 저작자 표시 필수 (남궁연, 설악고등학교)
- ❌ 상업적 이용 불가

상업적 사용을 원하시는 경우 개발자에게 별도 문의해 주세요.

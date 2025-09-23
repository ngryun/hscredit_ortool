# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an OR-Tools based student-to-section assignment optimizer for Korean school course scheduling (이동반 편성). The system minimizes unassigned students while respecting classroom capacity and slot constraints using constraint programming (CP-SAT).

## Core Architecture

### Main Components
- `optimize_student_sections.py` - Core OR-Tools CP-SAT optimization engine with constraint modeling
- `app.py` - FastAPI web server with async job processing and file upload/download
- Input: Excel files with student selections (학번/이름/과목 columns)
- Output: CSV files for section plans, student assignments, and summary reports

### Key Data Flow
1. Excel input parsing → student preferences matrix
2. CP-SAT model creation with decision variables: sections per slot, student assignments, unassignment flags
3. Constraint solving with weighted objectives (minimize unassigned >> minimize overcapacity >> minimize extra rooms)
4. Output generation: sections_plan.csv, assignments.csv, report.txt

## Development Commands

### Environment Setup
```bash
python -m venv .venv
source .venv/bin/activate  # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
```

### Core Optimizer (CLI)
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

### Web Server
```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

### Per-Subject Capacity Overrides
Create `caps.csv` with format:
```csv
subject,cap,maxcap
법과 사회,26,28
확률과 통계,32,34
```

Add `--caps-csv caps.csv` to optimizer command.

## Key Constraints & Model

### Decision Variables
- `n[t,s]`: number of sections for subject t in slot s
- `a[u,t,s]`: binary assignment of student u to subject t in slot s
- `uMiss[u,t]`: unassignment flag for student u's chosen subject t
- `over[t,s]`: overcapacity seats within maxcap limit
- `extra[s]`: extra sections beyond base room allocation

### Critical Constraints
- One subject per student per slot
- Section capacity limits with overcapacity buffer
- Room availability per slot with extra room allocation
- All chosen subjects must be assigned or flagged as unassigned

### Objective Function
Minimize `W1*∑uMiss + W2*∑over + W3*∑extra` where W1≫W2≥W3 prioritizes minimizing unassigned students.

## Input/Output Formats

### Excel Input Structure
- Column A: 학번 (student ID)
- Column B: 이름 (student name)
- Columns C+: Subject names with 1/0 values for selection

### Output Files
- `sections_plan.csv`: subject, slot (a/b/c/...), num_sections, total_enrolled
- `assignments.csv`: student_id, name, subject, slot, section_label, status
- `report.txt`: optimization summary and statistics
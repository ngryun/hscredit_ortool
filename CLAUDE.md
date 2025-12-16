# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

고교학점제 운영을 위한 이동반 편성 프로그램 - A student section assignment optimization tool for Korean high school credit system using Google OR-Tools CP-SAT solver. The system minimizes unassigned students while respecting classroom capacity and scheduling constraints.

**Purpose**: Automatically generate optimal class section assignments based on student course selections, classroom availability, and various constraints.

**Key Technologies**:
- Google OR-Tools CP-SAT solver for constraint programming optimization
- FastAPI web server with async processing
- Pandas/openpyxl for Excel data handling
- Python 3.12+

## Commands

### Environment Setup
```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Running the Optimizer (CLI)
```bash
# Basic usage
python optimize_student_sections.py \
  --input "2025입학생_수강생일괄등록양식(sample).xlsx" \
  --output-dir out \
  --slots 4 \
  --rooms-per-slot 7 \
  --cap 28 \
  --maxcap 30 \
  --time-limit 60 \
  --workers 8

# With per-subject capacity overrides
python optimize_student_sections.py \
  --input data.xlsx \
  --output-dir out \
  --slots 4 \
  --rooms-per-slot 7 \
  --cap 28 \
  --maxcap 30 \
  --caps-csv caps.csv

# With subject constraints (max sections per slot)
python optimize_student_sections.py \
  --input data.xlsx \
  --output-dir out \
  --slots 4 \
  --rooms-per-slot 7 \
  --subject-constraints-csv constraints.csv
```

### Running the Web GUI
```bash
# Start the FastAPI server
uvicorn app:app --host 0.0.0.0 --port 8000

# Access at http://localhost:8000
```

### Running Tests
```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_constraints.py

# Run with verbose output
pytest -v tests/
```

## Architecture

### Core Optimization Model (`optimize_student_sections.py`)

**Decision Variables**:
- `n[t,s]` (integer): Number of sections for subject `t` in slot `s`
- `a[u,t,s]` (0/1): Whether student `u` takes subject `t` in slot `s`
- `uMiss[u,t]` (0/1): Whether student `u` is unassigned for chosen subject `t`
- `over[t,s]` (integer): Excess enrollment beyond capacity for section (t,s), bounded by `(maxcap - cap) * n[t,s]`
- `extra[s]` (integer): Extra sections beyond `rooms_per_slot`, bounded by `extra_rooms_per_slot`

**Objective Function**:
```
Minimize: W1 * Σ(uMiss) + W2 * Σ(over) + W3 * Σ(extra)
```
Where W1 >> W2 >= W3 to prioritize minimizing unassigned students, then overflow, then extra rooms.

**Hard Constraints**:
- Each student can take at most one subject per time slot
- For each chosen subject: `Σ_s a[u,t,s] + uMiss[u,t] = 1` (must be assigned or marked as unassigned)
- Enrollment limit: `Σ_u a[u,t,s] <= cap(t) * n[t,s] + over[t,s]`
- Room availability: `Σ_t n[t,s] <= rooms_per_slot + extra[s]`
- Overflow bound: `over[t,s] <= (maxcap(t) - cap(t)) * n[t,s]`
- Extra room bound: `0 <= extra[s] <= extra_rooms_per_slot`

**Key Functions**:
- `build_and_solve()`: Main solver entry point that constructs CP-SAT model and runs optimization
- `read_students_xlsx()`: Parses input Excel files (supports both simple and complex formats with grade/semester/group headers)
- `load_caps_override()`: Loads per-subject capacity overrides from CSV
- `load_subject_constraints_override()`: Loads per-subject constraints (max sections per slot, max total sections)

### Web Application (`app.py`)

**Architecture Pattern**: FastAPI async web server with background task processing and automatic file cleanup.

**Key Features**:
- Concurrent execution limiting via `asyncio.Semaphore` (default: 2 concurrent optimizations)
- UUID-based work directories for isolation (`data/{uuid}/`)
- Automatic file cleanup: downloaded files deleted immediately, work directories deleted after 1 hour
- Report archival: anonymized reports saved to `data/reports/` permanently

**Important Routes**:
- `POST /upload`: Upload Excel file and get preview of subjects/enrollments
- `POST /optimize`: Run optimization with provided configuration (slots, rooms, constraints)
- `GET /status/{job_id}`: Poll optimization progress and retrieve results
- `GET /download/{job_id}/{filename}`: Download result files (triggers immediate deletion)

**Progress Updates**: Optimization progress is tracked by polling subprocess output and parsing status JSON from the solver.

### Input Format

**Simple Excel Format** (detected if row 1 contains "학번" in column A):
- Column A: `학번` (student ID)
- Column B: `이름` (student name)
- Columns C+: Subject names, values = 1 (chosen) or 0 (not chosen)

**Complex Excel Format** (grade-split with semester/group headers):
- Row 2 (index 1): Semester labels (columns E+)
- Row 3 (index 2): Group labels (columns E+)
- Row 4 (index 3): Subject names (columns E+)
- Row 6+ (index 5+): Student data
  - Columns A-C: Student IDs by grade (1학년, 2학년, 3학년)
  - Column D: Student name
  - Columns E+: Course selections (1 = chosen)

**CSV Overrides**:

`caps.csv` - Per-subject capacity overrides:
```csv
subject,cap,maxcap
법과 사회,26,28
확률과 통계,32,34
```

`constraints.csv` - Per-subject section constraints:
```csv
subject,max_sections_per_slot,max_total_sections
수학,2,5
영어,3,
```

`fixed_sections.csv` - Pre-assigned section counts:
```csv
subject,slot,num_sections
물리학,0,2
화학,1,3
```

### Output Files

- `sections_plan.csv`: Section opening plan
  - Columns: `subject`, `slot` (a/b/c/...), `num_sections`, `total_enrolled`

- `assignments.csv`: Student assignment details
  - Columns: `student_id`, `name`, `subject`, `slot`, `section_label`, `status` (assigned/unassigned)

- `report.txt`: Summary statistics (total students, assignments, unassigned count, optimization status)

## Development Notes

### Privacy & Data Handling

The `data/` directory is gitignored and contains potentially sensitive student information. All work directories (`data/{uuid}/`) are automatically deleted 1 hour after creation in the web GUI. Only anonymized report files in `data/reports/` are kept permanently.

### Optimization Performance

- Default solver timeout: 60 seconds (configurable via `--time-limit`)
- Parallel workers: 8 (configurable via `--workers`)
- For large problem instances (>500 students, >30 subjects), consider increasing time limit
- The solver uses hint variables to guide search toward better solutions

### Testing

Test files use dynamic import of `optimize_student_sections.py` to avoid packaging complexity:
```python
def _load_solver():
    root = Path(__file__).resolve().parents[1]
    mod_path = root / "optimize_student_sections.py"
    spec = importlib.util.spec_from_file_location("optimize_student_sections", str(mod_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

### Common Modifications

**Changing concurrent execution limit** (app.py:28):
```python
MAX_CONCURRENT = 2  # Adjust this number
```

**Adjusting objective weights** (optimize_student_sections.py):
Look for weight constants `W1`, `W2`, `W3` in `build_and_solve()` function.

**Adding new constraints**:
1. Add constraint parameters to `build_and_solve()` function signature
2. Create decision variables using `model.NewIntVar()` or `model.NewBoolVar()`
3. Add constraints using `model.Add()` or similar methods
4. Update CLI argument parser in `main()` if needed

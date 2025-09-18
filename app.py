# app.py  (Redis 없이 동시 실행 제한 버전)
import asyncio, uuid, subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI()
BASE = Path("data"); BASE.mkdir(exist_ok=True, parents=True)

# ← 여기 숫자만 조절하면 동시 실행 개수 제한 가능 (예: 2~3)
MAX_CONCURRENT = 2
sema = asyncio.Semaphore(MAX_CONCURRENT)
from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <h2>이동반 편성 최적화</h2>
    <form action="/run" method="post" enctype="multipart/form-data">
      <div>엑셀 파일: <input type="file" name="xlsx" required></div>
      <div>slots: <input name="slots" type="number" value="4"></div>
      <div>rooms: <input name="rooms" type="number" value="7"></div>
      <div>extra: <input name="extra" type="number" value="1"></div>
      <div>cap: <input name="cap" type="number" value="28"></div>
      <div>maxcap: <input name="maxcap" type="number" value="30"></div>
      <button type="submit">실행</button>
    </form>
    <p>또는 <a href="/docs">/docs</a>에서 테스트해도 됩니다.</p>
    """

# 아주 간단한 인메모리 상태 저장소 (서버 재시작 시 초기화되는 점만 유의)
JOBS = {}  # job_id -> {"status": "PENDING|RUNNING|DONE|ERROR", "dir": Path, "error": str|None}

async def run_optimizer(job_id: str, xlsx_path: Path, out_dir: Path,
                        slots: int, rooms: int, extra: int, cap: int, maxcap: int):
    async with sema:  # 동시 실행 개수 제한
        JOBS[job_id]["status"] = "RUNNING"
        cmd = [
            "python", "optimize_student_sections.py",
            "--input", str(xlsx_path),
            "--output-dir", str(out_dir),
            "--slots", str(slots),
            "--rooms-per-slot", str(rooms),
            "--extra-rooms-per-slot", str(extra),
            "--cap", str(cap),
            "--maxcap", str(maxcap),
            "--time-limit", "90",
            "--workers", "4"  # 머신 코어/워커 수에 맞춰 조정
        ]
        # 비동기 실행
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if proc.returncode == 0:
            JOBS[job_id]["status"] = "DONE"
        else:
            JOBS[job_id]["status"] = "ERROR"
            JOBS[job_id]["error"] = err.decode(errors="ignore")

@app.post("/run")
async def run(
    xlsx: UploadFile,
    slots: int = Form(4),
    rooms: int = Form(7),
    extra: int = Form(1),
    cap: int = Form(28),
    maxcap: int = Form(30),
):
    job_id = str(uuid.uuid4())
    job_dir = BASE / job_id; job_dir.mkdir(parents=True, exist_ok=True)
    out_dir = job_dir / "out"; out_dir.mkdir(exist_ok=True)
    xlsx_path = job_dir / "input.xlsx"
    with open(xlsx_path, "wb") as f:
        f.write(await xlsx.read())

    JOBS[job_id] = {"status": "PENDING", "dir": job_dir, "error": None}
    # 백그라운드 태스크 시작 (즉시 응답)
    asyncio.create_task(run_optimizer(job_id, xlsx_path, out_dir, slots, rooms, extra, cap, maxcap))

    return {"job": job_id, "status_url": f"/jobs/{job_id}"}

@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    info = JOBS.get(job_id)
    if not info:
        return JSONResponse(status_code=404, content={"error": "job not found"})
    resp = {"job": job_id, "status": info["status"]}
    if info["status"] == "DONE":
        resp.update({
            "sections": f"/download/{job_id}/sections_plan.csv",
            "assignments": f"/download/{job_id}/assignments.csv",
            "report": f"/download/{job_id}/report.txt",
        })
    if info["status"] == "ERROR":
        resp["error"] = info["error"]
    return resp

@app.get("/download/{job_id}/{name}")
def download(job_id: str, name: str):
    info = JOBS.get(job_id)
    if not info: return JSONResponse(status_code=404, content={"error":"job not found"})
    path = info["dir"] / "out" / name
    if not path.exists(): return JSONResponse(status_code=404, content={"error":"file not found"})
    return FileResponse(path)

# 실행: uvicorn app:app --host 0.0.0.0 --port 8000

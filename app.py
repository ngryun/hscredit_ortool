# app.py  (Redis 없이 동시 실행 제한 버전)
import asyncio, uuid, subprocess
import csv
from pathlib import Path
from fastapi import FastAPI, UploadFile, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import pandas as pd
import io
import warnings

# Silence harmless openpyxl default style warning for some workbooks
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module=r"openpyxl\.styles\.stylesheet"
)

app = FastAPI()
BASE = Path("data"); BASE.mkdir(exist_ok=True, parents=True)
# Serve static assets (mascot video)
app.mount("/asset", StaticFiles(directory="asset"), name="asset")

# ← 여기 숫자만 조절하면 동시 실행 개수 제한 가능 (예: 2~3)
MAX_CONCURRENT = 2
sema = asyncio.Semaphore(MAX_CONCURRENT)
from fastapi.responses import HTMLResponse

# Slot label order for pivot sorting
SLOT_LABELS = "abcdefghijklmnopqrstuvwxyz"

def build_pivot(assignments_csv: Path):
    try:
        if not assignments_csv.exists():
            return None
        df_all = pd.read_csv(assignments_csv)
        if df_all.empty:
            return None
        # Subject universe
        subjects = sorted(df_all["subject"].astype(str).unique().tolist())
        # Assigned data for section counts
        df_asg = df_all[df_all.get("status") == "assigned"].copy()
        if not df_asg.empty:
            parts = df_asg["section_label"].astype(str).str.rsplit("_", n=2, expand=True)
            secnum = pd.to_numeric(parts[2], errors="coerce").fillna(0).astype(int)
            df_asg["slot_section"] = df_asg["slot"].astype(str) + "-" + secnum.astype(str)
            ct_sec = pd.crosstab(df_asg["subject"], df_asg["slot_section"]).astype(int)
            sec_cols = ct_sec.columns.tolist()
        else:
            ct_sec = pd.DataFrame()
            sec_cols = []
        # Sort section columns by slot then number
        def sort_key(col: str):
            try:
                slot, num = col.split("-", 1)
                si = SLOT_LABELS.index(slot) if slot in SLOT_LABELS else 999
                ni = int(num)
                return (si, ni)
            except Exception:
                return (999, 999)
        sec_cols_sorted = sorted(sec_cols, key=sort_key)
        # Demand and unassigned counts
        demand = df_all.groupby("subject").size()
        unassigned = df_all[df_all.get("status") == "unassigned"].groupby("subject").size()
        # Build table rows
        table = pd.DataFrame(index=subjects)
        for c in sec_cols_sorted:
            table[c] = ct_sec.get(c, pd.Series(0, index=ct_sec.index)).reindex(subjects, fill_value=0)
        # Total = demand (assigned + unassigned)
        table["Total"] = demand.reindex(subjects, fill_value=0)
        # Order columns: Total then sections, and sort subjects by Total desc
        ordered = ["Total"] + sec_cols_sorted
        table = table.sort_values("Total", ascending=False)
        # Build JSON-friendly structure
        columns = ["Subject"] + ordered
        rows = []
        row_meta = {}
        for subj in table.index.tolist():
            vals = [int(table.at[subj, c]) for c in ordered]
            rows.append([subj] + vals)
            ua = int(unassigned.get(subj, 0))
            assigned = int(vals[0] - ua) if vals else 0
            row_meta[str(subj)] = {"unassigned": max(ua, 0), "assigned": max(assigned, 0)}
        # Build group headers from section columns
        groups = []
        last_slot = None
        secs: list[int] = []
        for col in sec_cols_sorted:
            try:
                slot, num = col.split("-", 1)
                n = int(num)
            except Exception:
                slot, n = col, 0
            if last_slot is None or slot != last_slot:
                if last_slot is not None:
                    groups.append({"slot": last_slot, "sections": secs})
                last_slot = slot
                secs = [n]
            else:
                secs.append(n)
        if last_slot is not None:
            groups.append({"slot": last_slot, "sections": secs})
        # Slot-level metadata: total opened sections per slot (from sections_plan if available)
        slot_meta = {}
        try:
            sp_path = assignments_csv.parent / "sections_plan.csv"
            if sp_path.exists():
                sp_df = pd.read_csv(sp_path)
                agg = sp_df.groupby("slot")["num_sections"].sum().to_dict()
                for sl, cnt in agg.items():
                    slot_meta[str(sl)] = {"sections_open": int(cnt)}
            else:
                # Fallback: approximate by counting distinct (subject, slot, section_no) seen in assignments
                seen = {}
                for col in sec_cols_sorted:
                    try:
                        slot, num = col.split("-", 1)
                        seen.setdefault(slot, set()).add(int(num))
                    except Exception:
                        continue
                for sl, nums in seen.items():
                    slot_meta[str(sl)] = {"sections_open": int(len(nums))}
        except Exception:
            pass
        return {"columns": columns, "rows": rows, "groups": groups, "row_meta": row_meta, "slot_meta": slot_meta}
    except Exception:
        return None

@app.get("/", response_class=HTMLResponse)
def index():
    return f"""
    <!doctype html>
    <html lang=\"ko\">
    <head>
      <meta charset=\"utf-8\" />
      <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
      <title>학생-과목 섹션 배정기</title>
      <script src=\"https://cdn.tailwindcss.com\"></script>
      <style>
        :root {{
          --sage-50: #F6F8F6;
          --sage-100: #E9F0EC;
          --sage-200: #D5E2DA;
          --sage-300: #C0D2C6;
          --sage-400: #A8C0AE;
          --sage-500: #8FAA96;
          --sage-600: #7A957F;
          --sage-700: #647A68;
        }}
        .bg-sage-50 {{ background-color: var(--sage-50); }}
        .bg-sage-100 {{ background-color: var(--sage-100); }}
        .bg-sage-500 {{ background-color: var(--sage-500); }}
        .bg-sage-600 {{ background-color: var(--sage-600); }}
        .hover\\:bg-sage-200:hover {{ background-color: var(--sage-200); }}
        .hover\\:bg-sage-700:hover {{ background-color: var(--sage-700); }}
        .text-sage-700 {{ color: var(--sage-700); }}
        .text-sage-800 {{ color: var(--sage-700); }}
        .ring-sage-200 {{ --tw-ring-color: var(--sage-200); }}
        .ring-sage-300 {{ --tw-ring-color: var(--sage-300); }}
        .focus\\:ring-sage-300:focus {{ --tw-ring-color: var(--sage-300); }}
        .file\\:bg-sage-100 {{ background-color: var(--sage-100); }}
        .file\\:text-sage-800 {{ color: var(--sage-700); }}
      </style>
    </head>
    <body class=\"bg-sage-50 text-stone-800\">
      <div class=\"mx-auto max-w-5xl p-6\">
        <header class=\"mb-6\">
          <h1 class=\"text-2xl font-semibold tracking-tight text-stone-900\">학생-과목 섹션 배정기</h1>
          <p class=\"text-sm text-stone-500\">OR-Tools 기반 미배정 최소화 모델 · 동시 실행 제한: {MAX_CONCURRENT}</p>
        </header>

        <section class=\"bg-white border border-stone-200 rounded-xl shadow-sm\">
          <div class=\"p-5\">
            <form id=\"run-form\" enctype=\"multipart/form-data\">
              <div class=\"mb-4\">
                <label class=\"block text-sm font-medium text-stone-700 mb-1\">엑셀 파일 (.xlsx)</label>
                <input class=\"block w-full text-sm file:mr-4 file:py-2 file:px-3 file:rounded-md file:border-0 file:text-sm file:font-medium file:bg-sage-100 file:text-sage-800 hover:file:bg-sage-200 border border-stone-300 rounded-lg p-2 bg-white\" type=\"file\" name=\"xlsx\" accept=\".xlsx\" required />
              </div>

              
              <div id=\"group-row\" class=\"mb-4 hidden\"> 
                <label class=\"block text-sm font-medium text-stone-700 mb-1\">선택그룹</label>
                <select id=\"group-select\" name=\"group\" class=\"w-full rounded-lg border border-stone-300 p-2 bg-white\"></select>
                <p class=\"mt-1 text-xs text-stone-500\">선택그룹이 여러 개 감지되었습니다. 이동반 편성하고자 하는 선택그룹을 선택해주세요.</p>
              </div>
              
              <div class=\"grid grid-cols-1 md:grid-cols-3 gap-3\">
                <div>
                  <label class=\"block text-sm text-stone-700 mb-1\">선택수</label>
                  <input class=\"w-full rounded-lg border border-stone-300 p-2 focus:outline-none focus:ring-2 focus:ring-sage-300\" type=\"number\" name=\"slots\" min=\"1\" value=\"4\" />
                </div>
                <div>
                  <label class=\"block text-sm text-stone-700 mb-1\">rooms</label>
                  <input class=\"w-full rounded-lg border border-stone-300 p-2 focus:outline-none focus:ring-2 focus:ring-sage-300\" type=\"number\" name=\"rooms\" min=\"1\" value=\"7\" />
                </div>
                <div>
                  <label class=\"block text-sm text-stone-700 mb-1\">extra</label>
                  <input class=\"w-full rounded-lg border border-stone-300 p-2 focus:outline-none focus:ring-2 focus:ring-sage-300\" type=\"number\" name=\"extra\" min=\"0\" value=\"1\" />
                </div>
                <div>
                  <label class=\"block text-sm text-stone-700 mb-1\">교실정원</label>
                  <input class=\"w-full rounded-lg border border-stone-300 p-2 focus:outline-none focus:ring-2 focus:ring-sage-300\" type=\"number\" name=\"cap\" min=\"1\" value=\"28\" />
                </div>
                <div>
                  <label class=\"block text-sm text-stone-700 mb-1\">교실최대정원</label>
                  <input class=\"w-full rounded-lg border border-stone-300 p-2 focus:outline-none focus:ring-2 focus:ring-sage-300\" type=\"number\" name=\"maxcap\" min=\"1\" value=\"30\" />
                </div>
                <div>
                  <label class=\"block text-sm text-stone-700 mb-1\">all extra (total)</label>
                  <input class=\"w-full rounded-lg border border-stone-300 p-2 focus:outline-none focus:ring-2 focus:ring-sage-300\" type=\"number\" name=\"extra_total\" min=\"0\" placeholder=\"무제한이면 비워두기\" />
                </div>
              </div>
              <div class=\"mt-5 flex items-center gap-3\">
                <button id=\"run-submit\" type=\"submit\" class=\"px-4 py-2 rounded-lg bg-sage-600 text-white hover:bg-sage-700 focus:ring-2 focus:ring-sage-300\">실행</button>
                <a class=\"text-sm text-stone-500 hover:text-stone-700\" href=\"/docs\">API 문서 보기</a>
              </div>
            </form>
          </div>
        </section>

        <section class=\"mt-6 bg-white border border-stone-200 rounded-xl shadow-sm\">
          <div class=\"p-5\">
            <div class=\"flex items-center justify-between mb-3\">
              <h2 class=\"text-lg font-medium text-stone-900\">상태 & 결과</h2>
              <span id=\"status-badge\" class=\"hidden inline-flex items-center px-2 py-1 text-xs rounded-full bg-sage-50 text-sage-700 ring-1 ring-sage-200\">PENDING</span>
            </div>
            <div id=\"status-note\" class=\"text-sm text-stone-500\">실행하면 여기에서 진행상황과 다운로드 링크가 표시됩니다.</div>
            <div id=\"job-info\" class=\"mt-2 text-sm text-stone-600 hidden\"></div>
            <div id=\"mascot\" class=\"mt-4 hidden flex justify-center items-center gap-4\">
              <video id=\"mascot-video\" src=\"/asset/beori2.mp4\" autoplay loop muted playsinline class=\"w-36 h-36 md:w-40 md:h-40 object-contain rounded-lg shadow-sm ring-1 ring-sage-200\"></video>
              <img id=\"mascot-done\" src=\"/asset/beori2_done.png\" onerror=\"this.onerror=null; this.src='/asset/beori_done2.png';\" alt=\"done\" class=\"hidden w-36 h-36 md:w-40 md:h-40 object-contain rounded-lg shadow-sm ring-1 ring-sage-200\" />
            </div>
              <div id=\"downloads\" class=\"mt-4 space-x-2 hidden\"></div>
            <div id=\"pivot-wrap\" class=\"mt-6 hidden overflow-x-auto\">
              <table id=\"pivot-table\" class=\"min-w-full text-sm\"></table>
            </div>

            <!-- Constraint Settings Modal -->
            <div id=\"constraint-modal\" class=\"fixed inset-0 bg-black bg-opacity-50 hidden z-50 flex items-center justify-center\">
              <div class=\"bg-white rounded-lg p-6 w-96 max-w-full mx-4\">
                <div class=\"flex justify-between items-center mb-4\">
                  <h3 id=\"modal-subject-title\" class=\"text-lg font-medium text-stone-900\">과목 제약 조건 설정</h3>
                  <button id=\"close-modal\" class=\"text-stone-400 hover:text-stone-600\">
                    <svg class=\"w-6 h-6\" fill=\"none\" stroke=\"currentColor\" viewBox=\"0 0 24 24\">
                      <path stroke-linecap=\"round\" stroke-linejoin=\"round\" stroke-width=\"2\" d=\"M6 18L18 6M6 6l12 12\"></path>
                    </svg>
                  </button>
                </div>
                <div class=\"space-y-4\">
                  <div>
                    <label class=\"block text-sm font-medium text-stone-700 mb-1\">슬롯당 최대 반 수</label>
                    <input id=\"max-per-slot\" type=\"number\" min=\"0\" placeholder=\"제한 없음\" class=\"w-full rounded-lg border border-stone-300 p-2 focus:outline-none focus:ring-2 focus:ring-sage-300\" />
                    <p class=\"mt-1 text-xs text-stone-500\">각 시간대에 이 과목이 개설할 수 있는 최대 반 수</p>
                  </div>
                  <div>
                    <label class=\"block text-sm font-medium text-stone-700 mb-1\">전체 최대 반 수</label>
                    <input id=\"max-total\" type=\"number\" min=\"0\" placeholder=\"제한 없음\" class=\"w-full rounded-lg border border-stone-300 p-2 focus:outline-none focus:ring-2 focus:ring-sage-300\" />
                    <p class=\"mt-1 text-xs text-stone-500\">모든 시간대를 통틀어 이 과목이 개설할 수 있는 최대 반 수</p>
                  </div>
                  <div class=\"flex justify-end space-x-3 pt-4\">
                    <button id=\"clear-constraints\" class=\"px-4 py-2 text-stone-600 hover:text-stone-800\">제약 해제</button>
                    <button id=\"save-constraints\" class=\"px-4 py-2 bg-sage-600 text-white rounded-lg hover:bg-sage-700\">저장</button>
                  </div>
                </div>
              </div>
            </div>
            <pre id=\"error-box\" class=\"mt-4 hidden text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg p-3 whitespace-pre-wrap\"></pre>
            
          </div>
        </section>

        <footer class=\"mt-8 text-xs text-stone-400\">
          <p>입력 파일 예시는 README를 참고하세요. 민감정보(PII)가 포함될 수 있으니 공유에 유의하세요.</p>
        </footer>
      </div>

      <script>
        const form = document.getElementById('run-form');
        const submitBtn = document.getElementById('run-submit');
        const statusBadge = document.getElementById('status-badge');
        const statusNote = document.getElementById('status-note');
        const downloads = document.getElementById('downloads');
        const jobInfo = document.getElementById('job-info');
        const errBox = document.getElementById('error-box');
        const pivotWrap = document.getElementById('pivot-wrap');
        const pivotTable = document.getElementById('pivot-table');
        const inputFile = form.querySelector('input[name="xlsx"]');
        const groupRow = document.getElementById('group-row');
        const groupSel = document.getElementById('group-select');
        const mascot = document.getElementById('mascot');
        const mascotVideo = document.getElementById('mascot-video');
        const mascotDone = document.getElementById('mascot-done');
        // Preview UI is removed; provide safe dummies for legacy code paths
        const inspectWrap = {{ classList: {{ add: ()=>{{}}, remove: ()=>{{}} }} }};
        const inspectBody = {{ innerHTML: '' }};
        let pollTimer = null;
        let pivotData = null;
        let lastInspect = null;
        let sortAsc = false; // sort by Total (desc by default)
        let runStartMs = null;    // client-side ticking timer
        let tickTimer = null;     // interval handle for elapsed seconds
        let lastProgress = null;  // latest progress payload
        let subjectConstraints = {{}};  // subject name -> {{maxPerSlot, maxTotal}}
        let currentModalSubject = null;  // currently editing subject in modal

        function setStatus(text, color='sage') {{
          statusBadge.textContent = text;
          statusBadge.classList.remove('hidden');
          statusBadge.className = `inline-flex items-center px-2 py-1 text-xs rounded-full bg-${{color}}-50 text-${{color}}-700 ring-1 ring-${{color}}-200`;
        }}

        function clearDownloads() {{
          downloads.innerHTML = '';
          downloads.classList.add('hidden');
        }}

        function drawPivot() {{
          const cols = pivotData.columns;
          const rows = pivotData.rows.slice().sort((a,b) => {{
            const va = Number(a[1]||0);
            const vb = Number(b[1]||0);
            return sortAsc ? (va - vb) : (vb - va);
          }});
          // Build grouped headers
          const groups = pivotData.groups || [];
          const slotMeta = pivotData.slot_meta || {{}};
          let hTop = '<tr>' +
            '<th class="px-3 py-2 text-left text-stone-700 border-b border-stone-200" rowspan="2">Subject</th>' +
            '<th id="th-total" class="px-3 py-2 text-right text-stone-700 border-b border-stone-200 cursor-pointer select-none" rowspan="2">총인원(미배정) ' + (sortAsc ? '▲' : '▼') + '</th>';
          for (const g of groups) {{
            const meta = slotMeta[g.slot] || {{}};
            const opened = Number(meta.sections_open || 0).toLocaleString();
            hTop += `<th class=\"px-3 py-2 text-center text-stone-700 border-b border-stone-200\" colspan=\"${{g.sections.length}}\">` +
                    `<div class=\"font-medium\">${{g.slot}}</div>` +
                    `<div class=\"text-xs text-stone-500\">총 ${{opened}}반</div>` +
                    `</th>`;
          }}
          hTop += '</tr>';
          let hSub = '<tr>';
          for (const g of groups) {{
            for (const n of g.sections) {{
              hSub += `<th class=\"px-3 py-1 text-right text-stone-600 border-b border-stone-200\">${{n}}</th>`;
            }}
          }}
          hSub += '</tr>';
          const trs = rows.map(r => {{
            const subject = String(r[0]);
            const total = Number(r[1] || 0);
            const ua = (pivotData.row_meta && pivotData.row_meta[subject]) ? Number(pivotData.row_meta[subject].unassigned || 0) : 0;
            let cells = '';
            // Subject with gear icon
            const hasConstraints = subjectConstraints[subject] && (subjectConstraints[subject].maxPerSlot || subjectConstraints[subject].maxTotal);
            const constraintIndicator = hasConstraints ?
              '<span class="inline-block w-2 h-2 bg-sage-500 rounded-full ml-1" title="제약 조건 설정됨"></span>' : '';
            cells += `<td class="px-3 py-1 text-left border-b border-stone-100">
              <div class="flex items-center gap-2">
                <span>${{subject}}</span>
                ${{constraintIndicator}}
                <button data-subject="${{subject}}" class="constraint-btn text-stone-400 hover:text-stone-600" title="제약 조건 설정">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path>
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path>
                  </svg>
                </button>
              </div>
            </td>`;
            // Total (Unassigned)
            cells += `<td class=\"px-3 py-1 text-right border-b border-stone-100\">${{total.toLocaleString()}} (${{ua.toLocaleString()}})</td>`;
            // Section counts start at index 2
            for (let i = 2; i < r.length; i++) {{
              const v = Number(r[i] || 0);
              cells += `<td class=\"px-3 py-1 text-right border-b border-stone-100\">${{v.toLocaleString()}}</td>`;
            }}
            return `<tr class=\"odd:bg-white even:bg-sage-50\">${{cells}}</tr>`;
          }}).join('');
          pivotTable.innerHTML = `
            <thead class=\"bg-sage-100 sticky top-0\">${{hTop}}${{hSub}}</thead>
            <tbody>${{trs}}</tbody>
          `;
          pivotWrap.classList.remove('hidden');
          // Attach sorter
          const thTotal = document.getElementById('th-total');
          if (thTotal) {{
            thTotal.onclick = () => {{ sortAsc = !sortAsc; drawPivot(); }};
            thTotal.title = '총인원 정렬 토글';
          }}
        }}

        function renderPivot(pivot) {{
          pivotData = pivot;
          if (!pivot || !pivot.columns || !pivot.rows) {{
            pivotWrap.classList.add('hidden');
            pivotTable.innerHTML = '';
            return;
          }}
          drawPivot();
        }}

        // Preview: Subject, 총인원, slots columns filled with 0
        function renderSubjectPreview(info) {{
          try {{
            const subs = Array.isArray(info?.subjects) ? info.subjects : [];
            const slots = Number(form.querySelector('input[name="slots"]').value || 4);
            const labels = 'abcdefghijklmnopqrstuvwxyz'.slice(0, Math.max(0, slots)).split('');
            if (!subs.length) {{ pivotWrap.classList.add('hidden'); pivotTable.innerHTML=''; return; }}
            let thead = '<tr>' +
              '<th class="px-3 py-2 text-left text-stone-700 border-b border-stone-200">Subject</th>' +
              '<th class="px-3 py-2 text-right text-stone-700 border-b border-stone-200">총인원</th>';
            for (const lb of labels) {{ thead += `<th class=\"px-3 py-2 text-right text-stone-700 border-b border-stone-200\">${{lb}}</th>`; }}
            thead += '</tr>';
            const rows = subs.map(s => {{
              const total = Number(s.count||0).toLocaleString();
              const hasConstraints = subjectConstraints[s.name] && (subjectConstraints[s.name].maxPerSlot || subjectConstraints[s.name].maxTotal);
              const constraintIndicator = hasConstraints ?
                '<span class="inline-block w-2 h-2 bg-sage-500 rounded-full ml-1" title="제약 조건 설정됨"></span>' : '';
              let cells = '';
              cells += `<td class="px-3 py-1 text-left border-b border-stone-100">
                <div class="flex items-center gap-2">
                  <span>${{s.name}}</span>
                  ${{constraintIndicator}}
                  <button data-subject="${{s.name}}" class="constraint-btn text-stone-400 hover:text-stone-600" title="제약 조건 설정">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.5 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path>
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path>
                    </svg>
                  </button>
                </div>
              </td>`;
              cells += `<td class=\"px-3 py-1 text-right border-b border-stone-100\">${{total}}</td>`;
              for (const _ of labels) {{ cells += `<td class=\"px-3 py-1 text-right border-b border-stone-100\">0</td>`; }}
              return `<tr class=\"odd:bg-white even:bg-sage-50\">${{cells}}</tr>`;
            }}).join('');
            pivotTable.innerHTML = `<thead class=\"bg-sage-100\">${{thead}}</thead><tbody>${{rows}}</tbody>`;
            pivotWrap.classList.remove('hidden');
          }} catch (err) {{ console.warn('preview render error', err); }}
        }}

        // Inspect uploaded file for quick preview (groups, semesters, subjects, headcount)
        async function inspectFile(file) {{
          const fd = new FormData();
          fd.append('xlsx', file);
          const res = await fetch('/inspect', {{ method: 'POST', body: fd }});
          if (!res.ok) return null;
          const js = await res.json();
          return js;
        }}

        inputFile.addEventListener('change', async (e) => {{
          const f = e.target.files && e.target.files[0];
          groupSel.innerHTML = '';
          groupRow.classList.add('hidden');
          if (!f) return;
          try {{
            const info = await inspectFile(f);
            const groups = (info && Array.isArray(info.groups)) ? info.groups : [];
            // Suggest rooms by class_count
            const roomsInp = form.querySelector('input[name="rooms"]');
            if (roomsInp && Number(info?.class_count||0) > 0) roomsInp.value = String(Number(info.class_count));
            // Cache and render preview
            lastInspect = info;
            renderSubjectPreview(lastInspect);
            // Inline preview (kept for backward-compat; ensure braces escaped in f-string)
            try {{
              const subs = Array.isArray(info?.subjects) ? info.subjects : [];
              const slots = Number(form.querySelector('input[name="slots"]').value || 4);
              const labels = 'abcdefghijklmnopqrstuvwxyz'.slice(0, Math.max(0, slots)).split('');
              if (subs.length) {{
                let thead = '<tr>' +
                  '<th class="px-3 py-2 text-left text-stone-700 border-b border-stone-200">Subject</th>' +
                  '<th class="px-3 py-2 text-right text-stone-700 border-b border-stone-200">총인원</th>';
                for (const lb of labels) {{ thead += `<th class=\"px-3 py-2 text-right text-stone-700 border-b border-stone-200\">${{lb}}</th>`; }}
                thead += '</tr>';
                const rows = subs.map(s => {{
                  const total = Number(s.count||0).toLocaleString();
                  const hasConstraints = subjectConstraints[s.name] && (subjectConstraints[s.name].maxPerSlot || subjectConstraints[s.name].maxTotal);
                  const constraintIndicator = hasConstraints ?
                    '<span class="inline-block w-2 h-2 bg-sage-500 rounded-full ml-1" title="제약 조건 설정됨"></span>' : '';
                  let cells = '';
                  cells += `<td class="px-3 py-1 text-left border-b border-stone-100">
                    <div class="flex items-center gap-2">
                      <span>${{s.name}}</span>
                      ${{constraintIndicator}}
                      <button data-subject="${{s.name}}" class="constraint-btn text-stone-400 hover:text-stone-600" title="제약 조건 설정">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path>
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path>
                        </svg>
                      </button>
                    </div>
                  </td>`;
                  cells += `<td class=\"px-3 py-1 text-right border-b border-stone-100\">${{total}}</td>`;
                  for (const _ of labels) {{ cells += `<td class=\"px-3 py-1 text-right border-b border-stone-100\">0</td>`; }}
                  return `<tr class=\"odd:bg-white even:bg-sage-50\">${{cells}}</tr>`;
                }}).join('');
                pivotTable.innerHTML = `<thead class=\"bg-sage-100\">${{thead}}</thead><tbody>${{rows}}</tbody>`;
                pivotWrap.classList.remove('hidden');
              }}
            }} catch (err) {{ console.warn('preview render error', err); }}
            // Render preview summary
            if (info) {{
              const head = Number(info.headcount || 0).toLocaleString();
              const semesters = (info.semesters || []).join(', ');
              const gstr = groups.join(', ');
              const subjects = Array.isArray(info.subjects) ? info.subjects : [];
              const subjHtml = subjects.slice(0, 50).map(s => `
                <li class=\"flex justify-between\">
                  <span class=\"truncate\" title=\"${{s.name}}\">${{s.name}}</span>
                  <span class=\"text-stone-700 ml-3\">${{Number(s.count||0).toLocaleString()}}</span>
                </li>`).join('');
              inspectBody.innerHTML = `
                <div class=\"mb-2\">총 인원: <span class=\"font-medium\">${{head}}</span></div>
                ${{semesters ? `<div class=\\"mb-2\\">학기: <span class=\\"font-medium\\">${{semesters}}</span></div>` : ''}}
                ${{gstr ? `<div class=\\"mb-2\\">선택그룹: <span class=\\"font-medium\\">${{gstr}}</span></div>` : ''}}
                <div class=\"mb-1\">과목 및 선택 수 (상위 50)</div>
                <ul class=\"space-y-0.5\">${{subjHtml}}</ul>
              `;
              inspectWrap.classList.remove('hidden');
            }} else {{
              inspectBody.innerHTML = '';
              inspectWrap.classList.add('hidden');
            }}
            // Group selection
            if (groups.length > 1) {{
              for (const g of groups) {{
                const opt = document.createElement('option');
                opt.value = String(g);
                opt.textContent = String(g);
                groupSel.appendChild(opt);
              }}
              groupRow.classList.remove('hidden');
            }} else if (groups.length === 1) {{
              const opt = document.createElement('option');
              opt.value = String(groups[0]);
              opt.textContent = String(groups[0]);
              groupSel.appendChild(opt);
              groupRow.classList.add('hidden');
            }}
          }} catch (err) {{
            console.warn('inspect error', err);
            inspectBody.innerHTML = '';
            inspectWrap.classList.add('hidden');
          }}
        }});

        // Re-render preview when slots value changes
        const slotsInp = form.querySelector('input[name="slots"]');
        if (slotsInp) {{
          const rerender = () => {{ if (lastInspect) renderSubjectPreview(lastInspect); }};
          slotsInp.addEventListener('input', rerender);
          slotsInp.addEventListener('change', rerender);
        }}

        // Constraint modal functions
        const constraintModal = document.getElementById('constraint-modal');
        const modalTitle = document.getElementById('modal-subject-title');
        const maxPerSlotInput = document.getElementById('max-per-slot');
        const maxTotalInput = document.getElementById('max-total');
        const closeModalBtn = document.getElementById('close-modal');
        const clearConstraintsBtn = document.getElementById('clear-constraints');
        const saveConstraintsBtn = document.getElementById('save-constraints');

        function openConstraintModal(subjectName) {{
          currentModalSubject = subjectName;
          modalTitle.textContent = `${{subjectName}} - 제약 조건 설정`;

          // Load existing constraints
          const existing = subjectConstraints[subjectName] || {{}};
          maxPerSlotInput.value = existing.maxPerSlot || '';
          maxTotalInput.value = existing.maxTotal || '';

          constraintModal.classList.remove('hidden');
        }}

        function closeConstraintModal() {{
          constraintModal.classList.add('hidden');
          currentModalSubject = null;
        }}

        function saveConstraints() {{
          if (!currentModalSubject) return;

          const maxPerSlot = maxPerSlotInput.value.trim();
          const maxTotal = maxTotalInput.value.trim();

          if (!maxPerSlot && !maxTotal) {{
            // No constraints - remove from object
            delete subjectConstraints[currentModalSubject];
          }} else {{
            const constraints = {{}};
            if (maxPerSlot) constraints.maxPerSlot = parseInt(maxPerSlot);
            if (maxTotal) constraints.maxTotal = parseInt(maxTotal);
            subjectConstraints[currentModalSubject] = constraints;
          }}

          // Re-render current table to show constraint indicators
          if (lastInspect) {{
            renderSubjectPreview(lastInspect);
          }} else if (pivotData) {{
            drawPivot();
          }}

          closeConstraintModal();
        }}

        function clearConstraints() {{
          if (!currentModalSubject) return;
          delete subjectConstraints[currentModalSubject];

          // Re-render current table
          if (lastInspect) {{
            renderSubjectPreview(lastInspect);
          }} else if (pivotData) {{
            drawPivot();
          }}

          closeConstraintModal();
        }}

        // Modal event listeners
        closeModalBtn.addEventListener('click', closeConstraintModal);
        saveConstraintsBtn.addEventListener('click', saveConstraints);
        clearConstraintsBtn.addEventListener('click', clearConstraints);

        // Close modal when clicking outside
        constraintModal.addEventListener('click', (e) => {{
          if (e.target === constraintModal) closeConstraintModal();
        }});

        // Close modal on Escape key
        document.addEventListener('keydown', (e) => {{
          if (e.key === 'Escape' && !constraintModal.classList.contains('hidden')) {{
            closeConstraintModal();
          }}
        }});

        // Add event delegation for constraint buttons
        document.addEventListener('click', (e) => {{
          if (e.target.closest('.constraint-btn')) {{
            const btn = e.target.closest('.constraint-btn');
            const subject = btn.getAttribute('data-subject');
            if (subject) {{
              openConstraintModal(subject);
            }}
          }}
        }});

        // Make functions globally available (for backward compatibility)
        window.openConstraintModal = openConstraintModal;

        form.addEventListener('submit', async (e) => {{
          e.preventDefault();
          clearDownloads();
          errBox.classList.add('hidden');
          statusNote.textContent = '업로드 중...';
          setStatus('PENDING');
          submitBtn.disabled = true;
          try {{
            const fd = new FormData(form);

            // Add constraint data as a hidden input field instead of direct FormData append
            console.log('[DEBUG] Current subjectConstraints:', subjectConstraints);
            const constraintsJsonStr = Object.keys(subjectConstraints).length > 0 ?
              JSON.stringify(subjectConstraints) : '{{}}';
            console.log('[DEBUG] Sending constraints JSON:', constraintsJsonStr);
            console.log('[DEBUG] About to submit form...');

            // Create a hidden input field for constraints
            const constraintsInput = document.createElement('input');
            constraintsInput.type = 'hidden';
            constraintsInput.name = 'constraints_json';
            constraintsInput.value = constraintsJsonStr;
            form.appendChild(constraintsInput);

            // Recreate FormData to include the new input
            const newFd = new FormData(form);

            // Remove the temporary input
            form.removeChild(constraintsInput);

            console.log('[DEBUG] Sending POST request to /run...');
            const res = await fetch('/run', {{ method: 'POST', body: newFd }});
            console.log('[DEBUG] Received response:', res.status, res.statusText);
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || '실행 실패');
            jobInfo.classList.remove('hidden');
            jobInfo.textContent = `작업 ID: ${{data.job}}`;
            statusNote.textContent = '대기열에 추가되었습니다. 처리 중...';
            if (pollTimer) clearInterval(pollTimer);
            pollTimer = setInterval(async () => {{
              const st = await fetch(`/jobs/${{data.job}}`);
              const js = await st.json();
              if (js.status === 'RUNNING') {{
                setStatus('RUNNING');
                if (mascot) mascot.classList.remove('hidden');
                if (mascotVideo) mascotVideo.classList.remove('hidden');
                if (mascotDone) mascotDone.classList.add('hidden');
                if (js.progress) {{ lastProgress = js.progress; }}
                if (runStartMs === null) {{
                  runStartMs = Date.now();
                  if (tickTimer) {{ clearInterval(tickTimer); tickTimer = null; }}
                  tickTimer = setInterval(() => {{
                    const secs = ((Date.now() - runStartMs) / 1000).toFixed(1);
                    const sols = (lastProgress && lastProgress.solutions !== undefined) ? Number(lastProgress.solutions||0).toLocaleString() : '0';
                    const ua = (lastProgress && lastProgress.unassigned !== undefined) ? ', 미배정 ' + Number(lastProgress.unassigned||0).toLocaleString() : '';
                    statusNote.textContent = `계산 중 · ${{secs}}s · 솔루션 ${{sols}}${{ua}}`;
                  }}, 200);
                }} else if (js.progress) {{
                  // refresh cached numbers even if timer already running
                  lastProgress = js.progress;
                }}
                // Immediate text update (before next tick)
                const secsNow = ((runStartMs ? (Date.now() - runStartMs) : 0) / 1000).toFixed(1);
                const solsNow = (lastProgress && lastProgress.solutions !== undefined) ? Number(lastProgress.solutions||0).toLocaleString() : '0';
                const uaNow = (lastProgress && lastProgress.unassigned !== undefined) ? ', 미배정 ' + Number(lastProgress.unassigned||0).toLocaleString() : '';
                statusNote.textContent = `계산 중 · ${{secsNow}}s · 솔루션 ${{solsNow}}${{uaNow}}`;
              }} else if (js.status === 'DONE') {{
                setStatus('DONE');
                if (mascot) mascot.classList.remove('hidden');
                if (mascotVideo) mascotVideo.classList.add('hidden');
                if (mascotDone) mascotDone.classList.remove('hidden');
                if (tickTimer) {{ clearInterval(tickTimer); tickTimer = null; }}
                runStartMs = null; lastProgress = null;
                if (js.summary && js.summary.total_unassigned !== undefined) {{
                  const ua = Number(js.summary.total_unassigned||0).toLocaleString();
                  statusNote.textContent = `미배정 ${{ua}}명으로 최종 완료했습니다.`;
                }} else {{
                  statusNote.textContent = '완료되었습니다. 아래 파일을 다운로드하세요.';
                }}
                clearInterval(pollTimer);
                downloads.classList.remove('hidden');
                downloads.innerHTML = `
                  <a class=\"inline-flex items-center px-3 py-2 rounded-lg bg-sage-600 text-white hover:bg-sage-700\" href=\"${{js.sections}}\" download>sections_plan.csv</a>
                  <a class=\"inline-flex items-center px-3 py-2 rounded-lg bg-sage-600 text-white hover:bg-sage-700\" href=\"${{js.assignments}}\" download>assignments.csv</a>
                  <a class=\"inline-flex items-center px-3 py-2 rounded-lg bg-sage-600 text-white hover:bg-sage-700\" href=\"${{js.report}}\" download>report.txt</a>
                  ${{js.constraints ? `<a class=\\\"inline-flex items-center px-3 py-2 rounded-lg bg-sage-600 text-white hover:bg-sage-700\\\" href=\\\"${{js.constraints}}\\\" download>constraints.csv</a>` : ''}}
                `;
                if (js.pivot) {{ renderPivot(js.pivot); }} else {{ renderPivot(null); }}
                submitBtn.disabled = false;
               }} else if (js.status === 'ERROR') {{
                setStatus('ERROR', 'rose');
                if (mascot) mascot.classList.add('hidden');
                if (mascotVideo) mascotVideo.classList.add('hidden');
                if (mascotDone) mascotDone.classList.add('hidden');
                if (tickTimer) {{ clearInterval(tickTimer); tickTimer = null; }}
                runStartMs = null; lastProgress = null;
                statusNote.textContent = '오류가 발생했습니다.';
                errBox.textContent = js.error || 'Unknown error';
                errBox.classList.remove('hidden');
                renderPivot(null);
                clearInterval(pollTimer);
                submitBtn.disabled = false;
               }} else if (js.status === 'PENDING') {{
                setStatus('PENDING');
                if (mascot) mascot.classList.add('hidden');
                if (mascotVideo) mascotVideo.classList.add('hidden');
                if (mascotDone) mascotDone.classList.add('hidden');
                if (tickTimer) {{ clearInterval(tickTimer); tickTimer = null; }}
                runStartMs = null; lastProgress = null;
                statusNote.textContent = '대기 중...';
              }}
            }}, 2000);
          }} catch (err) {{
            setStatus('ERROR', 'rose');
            statusNote.textContent = '요청 중 오류가 발생했습니다.';
            errBox.textContent = String(err);
            errBox.classList.remove('hidden');
            submitBtn.disabled = false;
          }}
        }});
      </script>
    </body>
    </html>
    """

# 아주 간단한 인메모리 상태 저장소 (서버 재시작 시 초기화되는 점만 유의)
JOBS = {}  # job_id -> {"status": "PENDING|RUNNING|DONE|ERROR", "dir": Path, "error": str|None}

async def run_optimizer(job_id: str, xlsx_path: Path, out_dir: Path,
                        slots: int, rooms: int, extra: int, cap: int, maxcap: int, group: str | None = None,
                        extra_total: int | None = None, constraints_csv_path: Path | None = None):
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
            "--time-limit", "120",
            "--workers", "8"  # 머신 코어/워커 수에 맞춰 조정
        ]
        if group:
            cmd += ["--group", str(group)]
        if extra_total is not None:
            cmd += ["--extra-total", str(extra_total)]
        if constraints_csv_path and constraints_csv_path.exists():
            cmd += ["--constraints-csv", str(constraints_csv_path)]
        # Debug: show launch command and whether constraints CSV attached
        try:
            print("[DEBUG] Launching optimizer:", " ".join(cmd))
            if constraints_csv_path:
                print(f"[DEBUG] constraints_csv_path: {constraints_csv_path} exists={constraints_csv_path.exists()}")
        except Exception:
            pass
        # 비동기 실행
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        # Stream stdout to capture progress
        async def read_stdout():
            try:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    txt = line.decode(errors="ignore").strip()
                    if txt.startswith("[PROGRESS]"):
                        import json as _json
                        try:
                            js = _json.loads(txt[len("[PROGRESS]"):].strip())
                            JOBS[job_id]["progress"] = js
                        except Exception:
                            pass
            except Exception:
                pass

        async def read_stderr():
            try:
                JOBS[job_id]["_stderr"] = b""
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    JOBS[job_id]["_stderr"] += line
            except Exception:
                pass

        await asyncio.gather(read_stdout(), read_stderr())
        await proc.wait()
        if proc.returncode == 0:
            JOBS[job_id]["status"] = "DONE"
            # Build pivot for quick UI preview
            assignments_csv = out_dir / "assignments.csv"
            JOBS[job_id]["pivot"] = build_pivot(assignments_csv)
            # Compute simple summary metrics
            try:
                if assignments_csv.exists():
                    df_asg = pd.read_csv(assignments_csv)
                    total_unassigned = int((df_asg.get("status") == "unassigned").sum())
                    students_with_unassigned = int(df_asg[df_asg.get("status") == "unassigned"]["student_id"].nunique())
                    total_assigned = int((df_asg.get("status") == "assigned").sum())
                    JOBS[job_id]["summary"] = {
                        "total_unassigned": total_unassigned,
                        "students_with_unassigned": students_with_unassigned,
                        "total_assigned": total_assigned,
                    }
            except Exception:
                pass
        else:
            JOBS[job_id]["status"] = "ERROR"
            err = JOBS[job_id].get("_stderr", b"")
            JOBS[job_id]["error"] = (err or b"").decode(errors="ignore")

@app.post("/run")
async def run(request: Request):
    # Parse form data manually
    form_data = await request.form()
    print(f"[DEBUG] All form data keys: {list(form_data.keys())}")

    xlsx = form_data.get("xlsx")
    slots = int(form_data.get("slots", 4))
    rooms = int(form_data.get("rooms", 7))
    extra = int(form_data.get("extra", 1))
    cap = int(form_data.get("cap", 28))
    maxcap = int(form_data.get("maxcap", 30))
    group = form_data.get("group")
    extra_total = form_data.get("extra_total")
    constraints_json = form_data.get("constraints_json")

    print(f"[DEBUG] Received constraints_json: {constraints_json}")
    job_id = str(uuid.uuid4())
    job_dir = BASE / job_id; job_dir.mkdir(parents=True, exist_ok=True)
    out_dir = job_dir / "out"; out_dir.mkdir(exist_ok=True)
    xlsx_path = job_dir / "input.xlsx"
    with open(xlsx_path, "wb") as f:
        f.write(await xlsx.read())

    # Handle optional constraints from JSON
    constraints_csv_path = None
    if constraints_json:
        try:
            import json
            constraints_data = json.loads(constraints_json)
            print(f"[DEBUG] Received constraints JSON: {constraints_json}")
            print(f"[DEBUG] Parsed constraints data: {constraints_data}")

            if constraints_data:
                # Save constraints.csv under out_dir so it's downloadable via /download route
                constraints_csv_path = out_dir / "constraints.csv"

                # Convert JSON to CSV format (use csv.writer for proper newlines/quoting)
                with open(constraints_csv_path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["subject", "max_sections_per_slot", "max_total_sections"])
                    for subject, constraint in constraints_data.items():
                        max_per_slot = constraint.get("maxPerSlot")
                        max_total = constraint.get("maxTotal")
                        # Normalize None/empty to ''
                        mps = "" if max_per_slot is None or str(max_per_slot).strip() == "" else int(max_per_slot)
                        mt = "" if max_total is None or str(max_total).strip() == "" else int(max_total)
                        writer.writerow([str(subject), mps, mt])
                        print(f"[DEBUG] Added constraint: {subject} -> maxPerSlot={mps}, maxTotal={mt}")

                print(f"[DEBUG] Created constraints CSV at: {constraints_csv_path}")
        except Exception as e:
            print(f"[DEBUG] Error parsing constraints: {e}")
            pass  # Ignore constraint parsing errors

    JOBS[job_id] = {"status": "PENDING", "dir": job_dir, "error": None}
    # 백그라운드 태스크 시작 (즉시 응답)
    et_val = None
    try:
        if extra_total is not None and str(extra_total).strip() != "":
            et_val = int(str(extra_total).strip())
    except Exception:
        et_val = None
    asyncio.create_task(run_optimizer(job_id, xlsx_path, out_dir, slots, rooms, extra, cap, maxcap, group, et_val, constraints_csv_path))

    return {"job": job_id, "status_url": f"/jobs/{job_id}"}

@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    info = JOBS.get(job_id)
    if not info:
        return JSONResponse(status_code=404, content={"error": "job not found"})
    resp = {"job": job_id, "status": info["status"]}
    if info.get("progress"):
        resp["progress"] = info["progress"]
    if info.get("progress"):
        resp["progress"] = info["progress"]
    if info.get("summary"):
        resp["summary"] = info["summary"]
    if info["status"] == "DONE":
        resp.update({
            "sections": f"/download/{job_id}/sections_plan.csv",
            "assignments": f"/download/{job_id}/assignments.csv",
            "report": f"/download/{job_id}/report.txt",
        })
        # Expose constraints CSV if present (saved under out/)
        try:
            cspath = info["dir"] / "out" / "constraints.csv"
            if cspath.exists():
                resp["constraints"] = f"/download/{job_id}/constraints.csv"
        except Exception:
            pass
        if info.get("pivot"):
            resp["pivot"] = info["pivot"]
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

@app.post("/inspect")
async def inspect(xlsx: UploadFile):
    """Parse the uploaded Excel and return a preview with subject counts.
    Subject names are normalized and disambiguated exactly like the solver,
    so constraints set in the UI match the model's subject keys.
    """
    try:
        content = await xlsx.read()

        def norm(s):
            return " ".join(str(s).split())

        # Try original wide format first
        try:
            df = pd.read_excel(io.BytesIO(content), sheet_name=0)
            df.columns = [str(c).strip() for c in df.columns]
            looks_original = len(df.columns) >= 3 and (
                any("학번" in c or c.lower() in {"id", "student_id"} for c in df.columns[:2]) or
                any("이름" in c or c.lower() in {"name"} for c in df.columns[:2])
            )
        except Exception:
            df = pd.DataFrame(); looks_original = False

        if looks_original and not df.empty:
            id_col = df.columns[0]
            # Normalize subject column headers like the solver
            subject_cols = [norm(c) for c in df.columns[2:]]
            # Rename the dataframe columns so counts use normalized names
            df = df.rename(columns={old: new for old, new in zip(df.columns[2:], subject_cols)})
            # headcount = non-empty id rows
            headcount = int(df[id_col].notna().sum())
            # class_count from 5-digit student id: class = 3rd digit (index 2)
            classes = set()
            for v in df[id_col].dropna().astype(str):
                s = "".join(ch for ch in str(v) if ch.isdigit())
                if len(s) >= 3:
                    classes.add(s[2])
            subjects = []
            for c in subject_cols:
                col = df[c]
                try:
                    cnt = int((pd.to_numeric(col, errors='coerce').fillna(0.0) == 1).sum())
                except Exception:
                    cnt = int((col.astype(str).str.strip() == '1').sum())
                subjects.append({"name": c, "count": cnt})
            subjects.sort(key=lambda x: (-x["count"], x["name"]))
            return {"groups": [], "semesters": [], "subjects": subjects, "headcount": headcount, "class_count": len(classes)}

        # New headered layout (with group/semester headers)
        df0 = pd.read_excel(io.BytesIO(content), sheet_name=0, header=None)
        if df0.shape[0] < 6 or df0.shape[1] < 5:
            return {"groups": [], "semesters": [], "subjects": [], "headcount": 0, "class_count": 0}
        COL_A, COL_B, COL_C, COL_E = 0, 1, 2, 4
        ROW_SEM, ROW_GRP, ROW_SUBJ, ROW_DATA = 1, 2, 3, 5
        sem_row = df0.iloc[ROW_SEM, COL_E:]
        grp_row = df0.iloc[ROW_GRP, COL_E:]
        subj_row = df0.iloc[ROW_SUBJ, COL_E:]

        subjects_names = []  # normalized + uniquely tagged names (or None)
        sems = []
        grps = []
        used = set()
        for j, base in enumerate(subj_row):
            if pd.isna(base):
                subjects_names.append(None)
                sems.append(None)
                grps.append(None)
                continue
            base_s = norm(base)
            if not base_s:
                subjects_names.append(None)
                sems.append(None)
                grps.append(None)
                continue
            sem = sem_row.iloc[j] if j < len(sem_row) else None
            grp = grp_row.iloc[j] if j < len(grp_row) else None
            # forward-fill merged header cells
            prev_sem = sems[-1] if sems else None
            prev_grp = grps[-1] if grps else None
            if (sem is None or (isinstance(sem, float) and pd.isna(sem)) or str(sem).strip() == ""):
                sem = prev_sem
            if (grp is None or (isinstance(grp, float) and pd.isna(grp)) or str(grp).strip() == ""):
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
            subjects_names.append(name)
            sems.append(sem_s)
            grps.append(grp_s)

        # counts aligned with columns E..end
        subj_counts = []
        for j, sname in enumerate(subjects_names):
            if not sname:
                continue
            col = df0.iloc[ROW_DATA:, COL_E + j]
            try:
                cnt = int((pd.to_numeric(col, errors='coerce').fillna(0.0) == 1).sum())
            except Exception:
                cnt = int((col.astype(str).str.strip() == '1').sum())
            subj_counts.append({"name": sname, "count": cnt})
        subj_counts.sort(key=lambda x: (-x["count"], x["name"]))

        # headcount and class_count by student id across A/B/C
        headcount = 0
        classes = set()
        for i in range(ROW_DATA, df0.shape[0]):
            vals = []
            for ci in (COL_C, COL_B, COL_A):
                v = df0.iat[i, ci] if ci < df0.shape[1] else None
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    continue
                s = str(v).strip()
                if s == '' or s == '0':
                    continue
                vals.append(s)
            if vals:
                headcount += 1
                sid = vals[0]
                sd = "".join(ch for ch in str(sid) if ch.isdigit())
                if len(sd) >= 3:
                    classes.add(sd[2])
        uniq_groups = sorted({g for g in grps if g})
        uniq_sems = sorted({s for s in sems if s})
        return {"groups": uniq_groups, "semesters": uniq_sems, "subjects": subj_counts, "headcount": headcount, "class_count": len(classes)}
    except Exception:
        return {"groups": [], "semesters": [], "subjects": [], "headcount": 0, "class_count": 0}

# 실행: uvicorn app:app --host 0.0.0.0 --port 8000

# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — Windows 설치형(onedir) 빌드
#   pyinstaller hscredit.spec
# 결과물: dist/hscredit-local/hscredit-local.exe (더블클릭 → 브라우저 자동 열림)
from PyInstaller.utils.hooks import collect_all

# ortools는 훅만으로 네이티브 DLL(cp_model_helper 등)이 누락되어 전체 수집 필요
ortools_datas, ortools_binaries, ortools_hiddenimports = collect_all("ortools")

a = Analysis(
    ["run_local.py"],
    pathex=[],
    binaries=ortools_binaries,
    datas=[
        # HTML이 참조하는 마스코트 미디어만 번들 (학생 데이터 파일은 제외)
        # app/optimize_student_sections 모듈은 import 추적으로 자동 포함됨
        ("asset/beori2.mp4", "asset"),
        ("asset/beori_done2.png", "asset"),
    ] + ortools_datas,
    hiddenimports=[
        # uvicorn을 문자열 임포트 없이 객체로 구동하지만, 내부 의존성은 명시 필요
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ] + ortools_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="hscredit-local",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # 초기 릴리스는 콘솔 유지: 오류 발생 시 교사가 화면을 캡처해 전달할 수 있음
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="hscredit-local",
)

# run_local.py — 설치형(로컬 실행) 진입점
#
# 사용법:
#   python run_local.py          # 로컬 서버 시작 + 브라우저 자동 열림
#   (PyInstaller로 빌드한 exe도 이 파일을 진입점으로 사용)
#
# PyInstaller 빌드에서는 이 exe가 두 가지 역할을 겸한다:
#   1) 인자 없이 실행     → uvicorn 웹 서버 + 브라우저 열기
#   2) `--solver <args>`  → optimize_student_sections.py의 CLI로 동작
#      (app.py가 솔버를 subprocess로 띄울 때 자기 자신을 재실행하는 방식.
#       기존 [PROGRESS] stdout 파이프 프로토콜을 그대로 유지한다.)
import multiprocessing
import socket
import sys
import threading
import webbrowser


def _find_free_port(start: int = 8787, tries: int = 20) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"사용 가능한 포트를 찾지 못했습니다 ({start}~{start + tries - 1})")


def main():
    multiprocessing.freeze_support()

    if len(sys.argv) > 1 and sys.argv[1] == "--solver":
        # 솔버 모드: 나머지 인자를 그대로 CLI에 전달
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        import optimize_student_sections
        optimize_student_sections.main()
        return

    import uvicorn
    import app as app_module

    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"* 이동반 편성 프로그램을 시작합니다: {url}")
    print("* 종료하려면 이 창을 닫거나 Ctrl+C를 누르세요.")
    threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    # frozen 환경에서는 import 문자열("app:app")이 아닌 앱 객체를 직접 전달해야 한다
    uvicorn.run(app_module.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()

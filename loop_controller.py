# loop_controller.py
import subprocess
import time
import psutil
import socket
import sys
import signal
import os
import requests
from typing import Any, Tuple, cast

LISTENER_SCRIPT = "listener.py"
DEBUGPY_PORT    = 7789   # VSCode debug adapter listen 포트
SHUTDOWN_CODE   = 123
listener_proc   = None

# debugpy 프로세스(자식) 정리; Pylance 경고 무시용 캐스트 포함
def kill_debugpy():
    for conn in psutil.net_connections(kind="inet"):
        laddr = conn.laddr
        if isinstance(laddr, tuple):
            addr_tuple = cast(Tuple[Any, Any], laddr)
            port = addr_tuple[1]
        else:
            port = getattr(laddr, "port", None)
        if conn.status == psutil.CONN_LISTEN and port == DEBUGPY_PORT and conn.pid:
            try:
                print(f"[🔪] debugpy 종료: PID={conn.pid} on port {DEBUGPY_PORT}")
                psutil.Process(conn.pid).kill()
            except:
                pass

def handle_sigint(signum, frame):
    print("\n[⚠️] Ctrl+C 감지—loop_controller 종료")
    if listener_proc and listener_proc.poll() is None:
        listener_proc.kill()
    kill_debugpy()
    os._exit(0)

signal.signal(signal.SIGINT, handle_sigint)

# listener.py 프로세스 실행
def start_listener():
    global listener_proc
    # 혹시 떠 있는 구 버전 listener.py 있으면 정리
    for proc in psutil.process_iter(['pid','cmdline']):
        try:
            if LISTENER_SCRIPT in ' '.join(proc.info.get('cmdline') or []):
                proc.kill()
        except:
            pass
    listener_proc = subprocess.Popen([sys.executable, LISTENER_SCRIPT])
    return listener_proc

# VSCode debug adapter(attach listen)가 포트 열릴 때까지 대기
def wait_for_debugpy():
    print(f"[🕓] 디버거 포트({DEBUGPY_PORT}) 연결 대기 중...")
    while True:
        if listener_proc and listener_proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("localhost", DEBUGPY_PORT), timeout=1):
                print("[✅] 디버거 연결 확인됨!")
                return True
        except:
            time.sleep(0.1)

# Lambda 호출 (예외 트리거 & remote attach 유도)
def invoke_lambda():
    url = "https://8ddph17th5.execute-api.us-east-1.amazonaws.com/default/testcapture"
    print("[🌐] Lambda 호출 중...")
    try:
        resp = requests.post(url, json={})
        print(f"[✅] 호출 완료: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"[❗] 호출 실패: {e}")

def main():
    print("[▶️] Infinite debug loop 시작")
    first_run = True                  # <-- 처음 플래그
    try:
        while True:
            proc = start_listener()

            # 1) VSCode debug adapter가 listen 중인지 확인
            if not wait_for_debugpy():
                print("[❌] listener.py가 중단됨—전체 종료")
                break

            # 2) 첫 실행이면 invoke_lambda 스킵, 이후부터 호출
            if first_run:
                print("[ℹ️] 첫 디버깅 세션—Lambda 호출 건너뜀")
                first_run = False
            else:
                invoke_lambda()

            # 3) listener.py(타이머 서버) 종료 대기
            exit_code = proc.wait()
            print(f"[ℹ️] listener.py 종료 (code={exit_code})")

            kill_debugpy()

            # 4) Lambda(어댑터)에서 보낸 shutdown 신호면 전체 종료
            if exit_code == SHUTDOWN_CODE:
                print("[✅] Shutdown signal 처리—전체 종료")
                break

            print("[⚠️] listener.py 비정상 종료—3초 후 재시작")
            time.sleep(3)

    except KeyboardInterrupt:
        handle_sigint(None, None)
    finally:
        os._exit(0)

if __name__ == "__main__":
    main()

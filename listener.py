# # listener.py 수정된 버전
# import socket
# import json
# import threading
# import time
# import sys
# import os
# import datetime
# import signal

# PORT = 6689
# SHUTDOWN_CODE = 123
# sock = None
# shutdown_flag = threading.Event()  # 스레드 간 shutdown 신호 공유

# # Ctrl+C 핸들러: 수동 종료
# def handle_sigint(signum, frame):
#     print("\n[⚠️] Ctrl+C 감지—listener 종료")
#     if sock:
#         try:
#             sock.close()
#             print("[✖️] 리스닝 소켓 닫음")
#         except:
#             pass
    
#     # shutdown 플래그가 설정되어 있으면 SHUTDOWN_CODE로 종료
#     exit_code = SHUTDOWN_CODE if shutdown_flag.is_set() else 0
#     print(f"[🔚] 종료 코드: {exit_code}")
#     os._exit(exit_code)

# signal.signal(signal.SIGINT, handle_sigint)

# # 남은 시간 출력 루프
# def print_remaining_time(initial_ms):
#     print(f"[⏱️] 타이머 시작됨 (초기값: {initial_ms} ms)")
#     start = time.time()
#     warned = False
#     while True:
#         # shutdown 플래그 확인
#         if shutdown_flag.is_set():
#             print("[🔚] Shutdown 신호로 타이머 중단")
#             return
            
#         elapsed = int((time.time() - start) * 1000)
#         remaining = max(0, initial_ms - elapsed)
#         if not warned and remaining <= 5000:
#             print("⚠️ 경고: 타임아웃까지 5초 남았습니다!")
#             warned = True
#         print(f"[⏱️] 남은 시간: {remaining} ms")
#         if remaining <= 0:
#             print("❌ 타이머 종료—listener 재시작")
#             os.execv(sys.executable, [sys.executable] + sys.argv)
#         time.sleep(0.5)

# # Lambda에서 보내는 연결(타이머 / shutdown) 처리
# def handle_connection(conn, addr):
#     global sock
#     try:
#         data = conn.recv(1024)
#         payload = json.loads(data.decode('utf-8'))
        
#         # shutdown 신호
#         if payload.get('shutdown'):
#             print(f"[🚨] Shutdown signal 수신 from {addr}")
#             shutdown_flag.set()  # 플래그 설정
#             conn.close()
            
#             # 메인 스레드가 정리할 수 있도록 잠시 대기
#             time.sleep(0.1)
            
#             print(f"[🔚] Shutdown 처리 완료 - 메인 스레드로 제어 이관")
#             return  # os._exit() 대신 return으로 메인 스레드에 맡김
            
#         # timeout 신호
#         remaining_ms = int(payload.get('remaining_ms', 0))
#         print(f"[📨] 수신됨 from {addr} | timeout: {remaining_ms} ms")
#         threading.Thread(
#             target=print_remaining_time,
#             args=(remaining_ms,),
#             daemon=True
#         ).start()
#     except Exception as e:
#         print(f"[❗] 처리 오류 from {addr}: {e}")
#     finally:
#         conn.close()

# def main():
#     global sock
#     # 문제 매처를 위해 반드시 이 두 줄을 찍습니다.
#     print("listener.py:1:1: 디버깅 대기 중")
#     print("디버깅 준비 완료")

#     # 타이머 수신용 TCP 서버
#     sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
#     sock.bind(("0.0.0.0", PORT))
#     sock.listen()
#     sock.settimeout(1.0)
#     print(f"listener.py 시작: {datetime.datetime.now()}")

#     try:
#         while True:
#             # shutdown 플래그 확인
#             if shutdown_flag.is_set():
#                 print("[🔚] Shutdown 플래그 감지 - 메인 루프 종료")
#                 break
                
#             try:
#                 conn, addr = sock.accept()
#             except socket.timeout:
#                 continue
#             except OSError:
#                 # 소켓이 닫혔을 때 shutdown 플래그 확인
#                 if shutdown_flag.is_set():
#                     print("[🔚] Shutdown으로 인한 소켓 종료")
#                     break
#                 else:
#                     print("[❗] 예상치 못한 소켓 오류")
#                     break
                    
#             threading.Thread(
#                 target=handle_connection,
#                 args=(conn, addr),
#                 daemon=True
#             ).start()
#     except KeyboardInterrupt:
#         pass
#     finally:
#         if sock:
#             sock.close()
#             print("[✖️] 리스닝 소켓 닫음 (finally)")
        
#         # shutdown 플래그에 따른 종료 코드 결정
#         exit_code = SHUTDOWN_CODE if shutdown_flag.is_set() else 0
#         print(f"[🛑] listener.py 종료 (code={exit_code})")
#         sys.exit(exit_code)  # os._exit() 대신 sys.exit() 사용

# if __name__ == "__main__":
#     main()
# listener.py 수정된 버전 - 파일 저장 기능 추가
import socket
import json
import threading
import time
import sys
import os
import datetime
import signal

PORT = 6689
SHUTDOWN_CODE = 123
sock = None
shutdown_flag = threading.Event()  # 스레드 간 shutdown 신호 공유

# 디버그 데이터 저장 폴더 설정
DEBUG_DATA_DIR = "debug_data"

# Ctrl+C 핸들러: 수동 종료
def handle_sigint(signum, frame):
    print("\n[⚠️] Ctrl+C 감지—listener 종료")
    if sock:
        try:
            sock.close()
            print("[✖️] 리스닝 소켓 닫음")
        except:
            pass
    
    # shutdown 플래그가 설정되어 있으면 SHUTDOWN_CODE로 종료
    exit_code = SHUTDOWN_CODE if shutdown_flag.is_set() else 0
    print(f"[🔚] 종료 코드: {exit_code}")
    os._exit(exit_code)

signal.signal(signal.SIGINT, handle_sigint)

# 디버그 데이터 저장 함수
def save_debug_data(data_type, filename, content, file_size):
    """Lambda에서 전송된 디버그 데이터를 파일로 저장"""
    try:
        # 디버그 데이터 폴더 생성
        if not os.path.exists(DEBUG_DATA_DIR):
            os.makedirs(DEBUG_DATA_DIR)
            print(f"[📁] 생성됨: {DEBUG_DATA_DIR}")
        
        # 타임스탬프 추가한 파일명 생성
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 파일명 처리 (확장자 유지)
        if '.' in filename:
            name, ext = filename.rsplit('.', 1)
            safe_filename = f"{timestamp}_{name}.{ext}"
        else:
            safe_filename = f"{timestamp}_{filename}.json"
        
        file_path = os.path.join(DEBUG_DATA_DIR, safe_filename)
        
        # 파일 저장
        with open(file_path, 'w', encoding='utf-8') as f:
            if isinstance(content, str):
                f.write(content)
            else:
                json.dump(content, f, indent=2, ensure_ascii=False)
        
        actual_size = os.path.getsize(file_path)
        
        print(f"[💾] 파일 저장 완료!")
        print(f"    📂 경로: {file_path}")
        print(f"    📊 타입: {data_type}")
        print(f"    📏 크기: {actual_size} bytes (전송: {file_size} bytes)")
        print(f"    📅 시간: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        return True
        
    except Exception as e:
        print(f"[❌] 파일 저장 실패: {e}")
        import traceback
        print(f"[❌] 상세 오류: {traceback.format_exc()}")
        return False

# 남은 시간 출력 루프
def print_remaining_time(initial_ms):
    print(f"[⏱️] 타이머 시작됨 (초기값: {initial_ms} ms)")
    start = time.time()
    warned = False
    while True:
        # shutdown 플래그 확인
        if shutdown_flag.is_set():
            print("[🔚] Shutdown 신호로 타이머 중단")
            return
            
        elapsed = int((time.time() - start) * 1000)
        remaining = max(0, initial_ms - elapsed)
        if not warned and remaining <= 5000:
            print("⚠️ 경고: 타임아웃까지 5초 남았습니다!")
            warned = True
        print(f"[⏱️] 남은 시간: {remaining} ms")
        if remaining <= 0:
            print("❌ 타이머 종료—listener 재시작")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        time.sleep(0.5)

# Lambda에서 보내는 대용량 데이터 수신 함수
def receive_large_data(conn, expected_size=None):
    """큰 데이터를 청크 단위로 안전하게 수신"""
    try:
        all_data = b""
        
        while True:
            chunk = conn.recv(8192)  # 8KB씩 수신
            if not chunk:
                break
            all_data += chunk
            
            # 예상 크기가 있으면 체크
            if expected_size and len(all_data) >= expected_size:
                break
                
            # JSON 종료 확인 (간단한 방법)
            try:
                json.loads(all_data.decode('utf-8'))
                break  # 완전한 JSON이면 종료
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue  # 아직 불완전하면 계속 수신
        
        return all_data
        
    except Exception as e:
        print(f"[❗] 대용량 데이터 수신 오류: {e}")
        return b""

# Lambda에서 보내는 연결(타이머 / shutdown / 파일 저장) 처리
def handle_connection(conn, addr):
    global sock
    try:
        print(f"[🔗] 연결됨: {addr}")
        
        # 첫 번째 청크 수신
        initial_data = conn.recv(1024)
        
        if not initial_data:
            print(f"[❗] 빈 데이터 수신 from {addr}")
            return
        
        # JSON 파싱 시도 (작은 데이터인지 확인)
        try:
            payload = json.loads(initial_data.decode('utf-8'))
            
            # 완전한 JSON을 받았으면 처리
            handle_payload(payload, addr, initial_data)
            
        except json.JSONDecodeError:
            # 불완전한 JSON이면 나머지 데이터 수신
            print(f"[📦] 대용량 데이터 감지 - 추가 수신 중...")
            
            remaining_data = receive_large_data(conn)
            full_data = initial_data + remaining_data
            
            try:
                payload = json.loads(full_data.decode('utf-8'))
                handle_payload(payload, addr, full_data)
                
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                print(f"[❗] JSON 파싱 실패 from {addr}: {e}")
                print(f"[📏] 수신 데이터 크기: {len(full_data)} bytes")
                
    except Exception as e:
        print(f"[❗] 연결 처리 오류 from {addr}: {e}")
        import traceback
        print(f"[❗] 상세 오류: {traceback.format_exc()}")
    finally:
        try:
            conn.close()
        except:
            pass

def handle_payload(payload, addr, raw_data):
    """페이로드 타입별 처리"""
    try:
        # 1. Shutdown 신호 처리
        if payload.get('shutdown'):
            print(f"[🚨] Shutdown signal 수신 from {addr}")
            shutdown_flag.set()  # 플래그 설정
            
            # 메인 스레드가 정리할 수 있도록 잠시 대기
            time.sleep(0.1)
            
            print(f"[🔚] Shutdown 처리 완료 - 메인 스레드로 제어 이관")
            return
        
        # 2. Timeout 신호 처리
        if 'remaining_ms' in payload and 'data_type' not in payload:
            remaining_ms = int(payload.get('remaining_ms', 0))
            print(f"[📨] Timeout 신호 수신 from {addr} | timeout: {remaining_ms} ms")
            threading.Thread(
                target=print_remaining_time,
                args=(remaining_ms,),
                daemon=True
            ).start()
            return
        
        # 3. 파일 저장 처리
        data_type = payload.get('data_type')
        if data_type:
            filename = payload.get('filename', f'debug_data_{int(time.time())}.json')
            content = payload.get('content', '')
            file_size = payload.get('file_size', len(raw_data))
            
            print(f"[📥] 파일 데이터 수신 from {addr}")
            print(f"    📄 파일명: {filename}")
            print(f"    🏷️ 타입: {data_type}")
            print(f"    📏 크기: {file_size} bytes")
            
            # 파일 저장
            success = save_debug_data(data_type, filename, content, file_size)
            
            if success:
                print(f"[✅] 파일 저장 성공: {filename}")
            else:
                print(f"[❌] 파일 저장 실패: {filename}")
            
            return
        
        # 4. 기타 데이터 처리
        print(f"[❓] 알 수 없는 데이터 타입 from {addr}")
        print(f"[📋] 페이로드 키: {list(payload.keys())}")
        
        # 일반적인 디버그 데이터로 저장 시도
        if len(payload) > 1:  # 단순 신호가 아니면
            filename = f"unknown_data_{int(time.time())}.json"
            save_debug_data("unknown", filename, payload, len(raw_data))
        
    except Exception as e:
        print(f"[❗] 페이로드 처리 오류: {e}")
        import traceback
        print(f"[❗] 상세 오류: {traceback.format_exc()}")

def main():
    global sock
    
    print(f"""
🚀 Enhanced Listener 시작
📅 시간: {datetime.datetime.now()}
📂 저장 폴더: {DEBUG_DATA_DIR}
🌐 리스닝 포트: {PORT}
""")
    
    # 문제 매처를 위해 반드시 이 두 줄을 찍습니다.
    print("listener.py:1:1: 디버깅 대기 중")
    print("디버깅 준비 완료")

    # 타이머 수신용 TCP 서버
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", PORT))
    sock.listen(5)  # 큐 크기 증가
    sock.settimeout(1.0)

    try:
        while True:
            # shutdown 플래그 확인
            if shutdown_flag.is_set():
                print("[🔚] Shutdown 플래그 감지 - 메인 루프 종료")
                break
                
            try:
                conn, addr = sock.accept()
                print(f"[🔗] 새 연결: {addr}")
            except socket.timeout:
                continue
            except OSError:
                # 소켓이 닫혔을 때 shutdown 플래그 확인
                if shutdown_flag.is_set():
                    print("[🔚] Shutdown으로 인한 소켓 종료")
                    break
                else:
                    print("[❗] 예상치 못한 소켓 오류")
                    break
                    
            # 각 연결을 별도 스레드에서 처리
            threading.Thread(
                target=handle_connection,
                args=(conn, addr),
                daemon=True
            ).start()
            
    except KeyboardInterrupt:
        print("\n[⚠️] Ctrl+C로 인한 종료")
    finally:
        if sock:
            sock.close()
            print("[✖️] 리스닝 소켓 닫음 (finally)")
        
        # shutdown 플래그에 따른 종료 코드 결정
        exit_code = SHUTDOWN_CODE if shutdown_flag.is_set() else 0
        print(f"[🛑] listener.py 종료 (code={exit_code})")
        sys.exit(exit_code)  # os._exit() 대신 sys.exit() 사용

if __name__ == "__main__":
    main()
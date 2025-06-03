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
# listener.py 수정된 버전 - 파일 수신 기능 추가
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

def receive_large_data(conn, timeout=30):
    """큰 데이터를 안전하게 수신하는 함수"""
    conn.settimeout(timeout)
    data_parts = []
    total_size = 0
    start_time = time.time()
    
    try:
        while True:
            # shutdown 플래그 확인
            if shutdown_flag.is_set():
                print("[🔚] Shutdown으로 데이터 수신 중단")
                return b""
                
            # 타임아웃 체크
            if time.time() - start_time > timeout:
                print(f"[⚠️] 데이터 수신 타임아웃 ({timeout}초)")
                break
            
            try:
                chunk = conn.recv(8192)  # 8KB 청크
                if not chunk:
                    break  # 연결 종료
                    
                data_parts.append(chunk)
                total_size += len(chunk)
                
                # 너무 큰 데이터 방지 (10MB 제한)
                if total_size > 10 * 1024 * 1024:
                    print(f"[⚠️] 데이터 크기 초과 ({total_size} bytes) - 수신 중단")
                    break
                    
                # JSON 완료 확인 (간단한 휴리스틱)
                combined_data = b"".join(data_parts)
                try:
                    # JSON이 완성되었는지 확인
                    json.loads(combined_data.decode('utf-8'))
                    print(f"[✅] 완전한 JSON 데이터 수신 완료 ({total_size} bytes)")
                    return combined_data
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # 아직 완성되지 않음, 계속 수신
                    continue
                    
            except socket.timeout:
                # 일시적 타임아웃, 계속 시도
                continue
            except socket.error as e:
                print(f"[❗] 소켓 에러: {e}")
                break
                
    except Exception as e:
        print(f"[❗] 데이터 수신 중 오류: {e}")
    
    # 부분 데이터라도 반환
    return b"".join(data_parts)

def save_received_file(payload):
    """수신한 파일 데이터를 로컬에 저장"""
    try:
        filename = payload.get('filename', f'debug_data_{int(time.time())}.json')
        content = payload.get('content', '')
        file_size = payload.get('file_size', len(content))
        timestamp = payload.get('timestamp', datetime.datetime.now().isoformat())
        source = payload.get('source', 'unknown')
        
        # 로컬 저장 경로 확인 및 생성
        local_save_dir = "src/debug_data"
        if not os.path.exists(local_save_dir):
            os.makedirs(local_save_dir, exist_ok=True)
            print(f"[📁] 디렉토리 생성: {local_save_dir}")
        
        # 파일명 중복 처리
        base_name, ext = os.path.splitext(filename)
        file_path = os.path.join(local_save_dir, filename)
        counter = 1
        
        while os.path.exists(file_path):
            new_filename = f"{base_name}_{counter}{ext}"
            file_path = os.path.join(local_save_dir, new_filename)
            counter += 1
        
        # 파일 저장
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # 저장 결과 출력
        actual_size = os.path.getsize(file_path)
        print(f"[📁] 파일 저장 완료!")
        print(f"    📄 파일명: {os.path.basename(file_path)}")
        print(f"    📏 크기: {actual_size:,} bytes (예상: {file_size:,})")
        print(f"    📂 경로: {file_path}")
        print(f"    🕐 시간: {timestamp}")
        print(f"    🌩️ 출처: {source}")
        
        # 파일 내용 간단 검증
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                test_data = json.load(f)
            
            if isinstance(test_data, dict):
                callstacks_count = len(test_data.get('callstacks', []))
                total_variables = test_data.get('summary', {}).get('total_variables', 0)
                print(f"    📊 콜스택 프레임: {callstacks_count}개")
                print(f"    🔢 총 변수: {total_variables}개")
        except Exception as validation_error:
            print(f"    ⚠️ 파일 검증 실패: {validation_error}")
        
        return True
        
    except Exception as e:
        print(f"[❗] 파일 저장 실패: {e}")
        return False

# Lambda에서 보내는 연결(타이머 / shutdown / 파일) 처리
def handle_connection(conn, addr):
    global sock
    try:
        print(f"[🔗] 연결 수락: {addr}")
        
        # 큰 데이터 수신 지원
        raw_data = receive_large_data(conn, timeout=30)
        
        if not raw_data:
            print(f"[⚠️] {addr}에서 빈 데이터 수신")
            return
        
        # JSON 파싱 시도
        try:
            payload = json.loads(raw_data.decode('utf-8'))
            print(f"[📨] JSON 파싱 성공 from {addr} ({len(raw_data):,} bytes)")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[❗] JSON 파싱 실패 from {addr}: {e}")
            # 원시 데이터 일부 출력 (디버깅용)
            preview = raw_data[:200].decode('utf-8', errors='ignore')
            print(f"[👁️] 데이터 미리보기: {preview}...")
            return
        
        # 🚨 shutdown 신호 처리
        if payload.get('shutdown'):
            print(f"[🚨] Shutdown signal 수신 from {addr}")
            shutdown_flag.set()  # 플래그 설정
            
            # 메인 스레드가 정리할 수 있도록 잠시 대기
            time.sleep(0.1)
            print(f"[🔚] Shutdown 처리 완료 - 메인 스레드로 제어 이관")
            return
        
        # 📁 debug_data 파일 처리 (새로 추가)
        elif payload.get('data_type') == 'debug_data':
            print(f"[📁] Debug 파일 데이터 수신 from {addr}")
            success = save_received_file(payload)
            if success:
                print(f"[✅] Debug 파일 저장 성공!")
            else:
                print(f"[❌] Debug 파일 저장 실패!")
            return
        
        # ⏰ timeout 신호 처리 (기존)
        elif 'remaining_ms' in payload:
            remaining_ms = int(payload.get('remaining_ms', 0))
            session_info = payload.get('debug_session', 'unknown')
            print(f"[📨] Timeout 신호 수신 from {addr}")
            print(f"    ⏰ 남은 시간: {remaining_ms} ms")
            print(f"    🏷️ 세션: {session_info}")
            
            threading.Thread(
                target=print_remaining_time,
                args=(remaining_ms,),
                daemon=True
            ).start()
            return
        
        # 🤷 기타 데이터 처리
        else:
            print(f"[❓] 알 수 없는 데이터 타입 from {addr}")
            print(f"    🔑 키들: {list(payload.keys())}")
            # 진단용 정보 출력
            for key, value in payload.items():
                if isinstance(value, str) and len(value) > 100:
                    print(f"    {key}: {value[:100]}... ({len(value)} chars)")
                else:
                    print(f"    {key}: {value}")
            return
            
    except Exception as e:
        print(f"[❗] 연결 처리 오류 from {addr}: {e}")
        import traceback
        print(f"[🔍] 상세 오류: {traceback.format_exc()}")
    finally:
        try:
            conn.close()
            print(f"[🔌] 연결 종료: {addr}")
        except:
            pass

def main():
    global sock
    # 문제 매처를 위해 반드시 이 두 줄을 찍습니다.
    print("listener.py:1:1: 디버깅 대기 중")
    print("디버깅 준비 완료")

    # 타이머 + 파일 수신용 TCP 서버
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", PORT))
    sock.listen(5)  # 백로그 증가
    sock.settimeout(1.0)
    
    print(f"listener.py 시작: {datetime.datetime.now()}")
    print(f"[🌐] 포트 {PORT}에서 다음을 수신 대기:")
    print(f"    ⏰ 타임아웃 신호 (remaining_ms)")
    print(f"    🚨 종료 신호 (shutdown)")
    print(f"    📁 Debug 파일 (debug_data)")

    try:
        while True:
            # shutdown 플래그 확인
            if shutdown_flag.is_set():
                print("[🔚] Shutdown 플래그 감지 - 메인 루프 종료")
                break
                
            try:
                conn, addr = sock.accept()
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
        pass
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
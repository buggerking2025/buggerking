# listener.py - 8바이트 헤더 방식 적용된 완전한 버전
import socket
import json
import struct  # 추가: 8바이트 헤더 처리용
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

# # 디버그 데이터 저장 함수
# def save_debug_data(data_type, filename, content, file_size):
#     """Lambda에서 전송된 디버그 데이터를 파일로 저장"""
#     try:
#         # 디버그 데이터 폴더 생성
#         if not os.path.exists(DEBUG_DATA_DIR):
#             os.makedirs(DEBUG_DATA_DIR)
#             print(f"[📁] 생성됨: {DEBUG_DATA_DIR}")
        
#         # 타임스탬프 추가한 파일명 생성
#         timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
#         # 파일명 처리 (확장자 유지)
#         if '.' in filename:
#             name, ext = filename.rsplit('.', 1)
#             safe_filename = f"{timestamp}_{name}.{ext}"
#         else:
#             safe_filename = f"{timestamp}_{filename}.json"
        
#         file_path = os.path.join(DEBUG_DATA_DIR, safe_filename)
        
#         # 파일 저장
#         with open(file_path, 'w', encoding='utf-8') as f:
#             if isinstance(content, str):
#                 f.write(content)
#             else:
#                 json.dump(content, f, indent=2, ensure_ascii=False)
        
#         actual_size = os.path.getsize(file_path)
        
#         print(f"[💾] 파일 저장 완료!")
#         print(f"    📂 경로: {file_path}")
#         print(f"    📊 타입: {data_type}")
#         print(f"    📏 크기: {actual_size} bytes (전송: {file_size} bytes)")
#         print(f"    📅 시간: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
#         return True
        
#     except Exception as e:
#         print(f"[❌] 파일 저장 실패: {e}")
#         import traceback
#         print(f"[❌] 상세 오류: {traceback.format_exc()}")
#         return False

def save_debug_data(payload):
    """Lambda에서 전송된 디버그 데이터를 파일로 저장"""
    try:
        # 디버그 데이터 폴더 생성
        if not os.path.exists(DEBUG_DATA_DIR):
            os.makedirs(DEBUG_DATA_DIR)
            print(f"[📁] 생성됨: {DEBUG_DATA_DIR}")
        
        # 타임스탬프 추가한 파일명 생성
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_unified_callstack.json"
        file_path = os.path.join(DEBUG_DATA_DIR, filename)
        
        # 파일 저장
        with open(file_path, 'w', encoding='utf-8') as f:
            if isinstance(payload, str):
                f.write(payload)
            else:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        
        actual_size = os.path.getsize(file_path)
        
        print(f"[💾] 파일 저장 완료!")
        print(f"    📂 경로: {file_path}")
        print(f"    📊 타입: unified_callstack")
        print(f"    📏 크기: {actual_size} bytes")
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

def send_dap_message(sock, data, message_type_str: str):
    """
    지정된 타입과 데이터를 사용하여 고정 크기 헤더와 가변 크기 바디로 구성된 메시지를 전송합니다.
    헤더는 4바이트 메시지 타입 문자열과 4바이트 바디 크기 정수로 구성됩니다. (총 8바이트 헤더)
    수신측에서는 이 헤더를 먼저 읽고 파싱하여 바디의 크기를 알아낸 후, 해당 크기만큼 바디를 읽습니다.

    :param sock: 소켓 객체
    :param data: 전송할 데이터 (dict만 지원 - 자동으로 JSON 변환됨)
    :param message_type_str: 메시지 타입을 나타내는 4자리 문자열 (예: "TIME", "SHUT", "CAPT").
                             4자보다 짧으면 공백으로 패딩되고, 길면 4자로 절단됩니다.
    :return: 성공 시 True, 실패 시 False
    """
    try:
        # 모든 데이터는 dict → JSON으로 처리 (프로토콜 단순화)
        if isinstance(data, dict):
            body_bytes = json.dumps(data).encode('utf-8')
        else:
            error_msg = f"Unsupported data type: {type(data)}. Only dict is supported (automatically converted to JSON)."
            print(f"❌ [DAP-SEND] 데이터 타입 오류 ({message_type_str}): {error_msg}")
            raise TypeError(error_msg)

        body_length = len(body_bytes)

        # 헤더 생성 (총 8바이트)
        # 1. 메시지 타입 (4바이트 ASCII)
        type_str_fixed_length = message_type_str.ljust(4)[:4]
        type_bytes_for_header = type_str_fixed_length.encode('ascii')

        # 2. 바디 길이 (4바이트 big-endian unsigned integer)
        body_length_bytes = struct.pack('>I', body_length)

        header_bytes = type_bytes_for_header + body_length_bytes
        
        message_to_send = header_bytes + body_bytes
        sock.sendall(message_to_send)
        
        total_sent = len(message_to_send)
        print(f"📤 [DAP-SEND] '{message_type_str}' 전송 완료: header={len(header_bytes)}B, body={body_length}B. 총 {total_sent}B.")
        return True
        
    except TypeError: 
        return False 
    except Exception as e:
        print(f"❌ [DAP-SEND] '{message_type_str}' 전송 실패 (오류: {type(e).__name__}): {e}")
        return False

def receive_dap_message(conn):
    """
    고정 크기 헤더와 가변 크기 바디로 구성된 메시지를 수신합니다.
    헤더는 4바이트 메시지 타입 문자열과 4바이트 바디 크기 정수로 구성됩니다. (총 8바이트 헤더)
    
    :param conn: 소켓 연결 객체
    :return: 성공 시 (message_type, data) 튜플, 실패 시 None
    """
    try:
        # 1단계: 헤더 8바이트 수신
        header_bytes = _receive_exact_bytes(conn, 8)
        if header_bytes is None:
            print("❌ [DAP-RECV] 헤더 수신 실패")
            return None
        
        # 2단계: 헤더 파싱
        # 메시지 타입 (4바이트 ASCII)
        type_bytes = header_bytes[:4]
        message_type = type_bytes.decode('ascii').rstrip()  # 오른쪽 공백 제거
        
        # 바디 길이 (4바이트 big-endian unsigned integer)
        body_length_bytes = header_bytes[4:8]
        body_length = struct.unpack('>I', body_length_bytes)[0]
        
        print(f"📥 [DAP-RECV] 헤더 파싱 완료: type='{message_type}', body_length={body_length}B")
        
        # 3단계: 바디 수신 (길이가 0이면 빈 바이트)
        if body_length == 0:
            body_bytes = b''
        else:
            body_bytes = _receive_exact_bytes(conn, body_length)
            if body_bytes is None:
                print(f"❌ [DAP-RECV] 바디 수신 실패 (예상: {body_length}B)")
                return None
        
        # 4단계: JSON 데이터 변환
        json_str = body_bytes.decode('utf-8')
        data = json.loads(json_str)
        
        total_received = 8 + body_length
        print(f"📥 [DAP-RECV] '{message_type}' 수신 완료: header=8B, body={body_length}B. 총 {total_received}B.")
        
        return (message_type, data)
        
    except json.JSONDecodeError as e:
        print(f"❌ [DAP-RECV] JSON 파싱 실패: {e}")
        return None
    except UnicodeDecodeError as e:
        print(f"❌ [DAP-RECV] UTF-8 디코딩 실패: {e}")
        return None
    except Exception as e:
        print(f"❌ [DAP-RECV] 수신 실패 (오류: {type(e).__name__}): {e}")
        return None

def _receive_exact_bytes(conn, num_bytes):
    """
    소켓에서 정확히 지정된 바이트 수만큼 데이터를 수신합니다.
    """
    received_data = b''
    remaining_bytes = num_bytes
    
    while remaining_bytes > 0:
        try:
            chunk = conn.recv(remaining_bytes)
            if not chunk:  # 연결이 닫힌 경우
                print(f"❌ [DAP-RECV] 연결 종료됨 (수신된: {len(received_data)}B, 예상: {num_bytes}B)")
                return None
            
            received_data += chunk
            remaining_bytes -= len(chunk)
            
        except Exception as e:
            print(f"❌ [DAP-RECV] 바이트 수신 오류: {e}")
            return None
    
    return received_data

def find_latest_callstack_file():
    """가장 최근의 callstack 파일 찾기"""
    try:
        print(f"🔍 [FILE-SEARCH] {DEBUG_DATA_DIR} 폴더에서 파일 검색...")

        if not os.path.exists(DEBUG_DATA_DIR):
            print(f"❌ [FILE-SEARCH] 폴더 없음: {DEBUG_DATA_DIR}")
            return None
        
        debug_files = []
        all_files = os.listdir(DEBUG_DATA_DIR)
        print(f"📁 [FILE-SEARCH] 전체 파일 개수: {len(all_files)}")
        
        for filename in all_files:
            if "unified_callstack" in filename and filename.endswith('.json'):
                filepath = os.path.join(DEBUG_DATA_DIR, filename)
                mtime = os.path.getmtime(filepath)
                file_size = os.path.getsize(filepath)
                
                debug_files.append((mtime, filepath, filename, file_size))
                print(f"✅ [FILE-SEARCH] callstack 파일 발견: {filename} ({file_size} bytes)")
        
        if debug_files:
            # 시간순 정렬 (최신 순)
            debug_files.sort(reverse=True)
            latest_file = debug_files[0]

            print(f"🏆 [FILE-SEARCH] 최신 파일: {latest_file[2]}")
            print(f"🏆 [FILE-SEARCH] 수정 시간: {datetime.datetime.fromtimestamp(latest_file[0])}")
            print(f"🏆 [FILE-SEARCH] 파일 크기: {latest_file[3]} bytes")

            return latest_file[1]  # 파일 경로 반환
        
        print(f"❌ [FILE-SEARCH] callstack 파일 없음")
        return None
        
    except Exception as e:
        print(f"❌ [FILE-SEARCH] 검색 오류: {e}")
        return None

def handle_timeout_and_send_json(payload, conn, addr):
    """타이머 + JSON 파일 전송 (연결 유지) - 8바이트 헤더 방식"""
    remaining_ms = int(payload.get('remaining_ms', 0))
    print(f"📨 [JSON-SEND] Timeout 신호 수신 from {addr} | timeout: {remaining_ms} ms")
    
    # 1) 타이머 스레드 시작
    threading.Thread(
        target=print_remaining_time,
        args=(remaining_ms,),
        daemon=True
    ).start()
    
    # 2) JSON 파일 찾기 및 전송
    latest_file = find_latest_callstack_file()
    
    if latest_file:
        print(f"📤 [JSON-SEND] JSON 파일 발견: {os.path.basename(latest_file)}")
        
        try:
            # 파일 크기 확인
            file_size = os.path.getsize(latest_file)
            print(f"📤 [JSON-SEND] 파일 크기: {file_size} bytes")
            
            # JSON 파일 읽기
            with open(latest_file, 'r', encoding='utf-8') as f:
                json_content = f.read()
                json_dict = json.loads(json_content)  # str → dict 변환
            
            print(f"📤 [JSON-SEND] 파일 내용 읽기 완료: {len(json_content)} chars")
            
            # 8바이트 헤더 방식으로 전송 (JSON 타입)
            success = send_dap_message(conn, json_dict, "CAPT")
            
            if success:
                print(f"✅ [JSON-SEND] 전송 완료! 총 {len(json_content)} chars")
            else:
                print(f"❌ [JSON-SEND] 전송 실패!")
            
        except Exception as e:
            print(f"❌ [JSON-SEND] 전송 실패: {e}")
            import traceback
            print(f"❌ [JSON-SEND] 상세: {traceback.format_exc()}")
    
    else:
        print(f"❌ [JSON-SEND] 전송할 JSON 파일 없음")

# Lambda에서 보내는 연결(타이머 / shutdown / 파일 저장 / 상태 복구) 처리
def handle_connection(conn, addr):
    global sock
    try:
        print(f"[🔗] 연결됨: {addr}")
        
        # 8바이트 헤더 방식으로 메시지 수신
        result = receive_dap_message(conn)
        
        if not result:
            print(f"[❗] 메시지 수신 실패 from {addr}")
            return
        
        message_type, data = result
        print(f"[📥] 수신된 메시지 타입: '{message_type}', 데이터 타입: {type(data)}")
        
            
        # 🔥 특별 처리: remaining_ms 신호면 연결 유지하고 JSON 전송
        if message_type.upper() == 'TIME' and 'remaining_ms' in data:
            handle_timeout_and_send_json(data, conn, addr)
            return
            
        # 일반 처리 (CAPT, SHUT, EROR 등)
        handle_payload(data, addr, message_type)
                
    except Exception as e:
        print(f"[❗] 연결 처리 오류 from {addr}: {e}")
        import traceback
        print(f"[❗] 상세 오류: {traceback.format_exc()}")
    finally:
        try:
            conn.close()
        except:
            pass

def handle_payload(payload, addr, message_type):
    """페이로드 타입별 처리"""
    try:
        print(f"📥 [PAYLOAD] 페이로드 수신 from {addr}: {list(payload.keys()) if isinstance(payload, dict) else type(payload)}")
        
        # 1. Shutdown 신호 처리
        if message_type.upper() == 'SHUT':
            print(f"🚨 Shutdown signal 수신 from {addr}")
            shutdown_flag.set()  # 플래그 설정
            
            # 메인 스레드가 정리할 수 있도록 잠시 대기
            time.sleep(0.1)
            
            print(f"🔚 Shutdown 처리 완료 - 메인 스레드로 제어 이관")
            return
        
        # 2. 파일 저장 처리
        elif message_type.upper() == 'CAPT':
            print(f"📥 캡처 데이터 수신 from {addr}")
            # 파일 저장 (payload만 전달)
            success = save_debug_data(payload)
            
            if success:
                print(f"✅ 파일 저장 성공")
            else:
                print(f"❌ 파일 저장 실패")

            return
        
        else:
            # 3. 기타 타입(EROR, EMPT 등) 처리
            raise ValueError(f"잘못된 메시지 타입: {message_type}")
        
        # # 일반적인 디버그 데이터로 저장 시도
        # if len(payload) > 1:  # 단순 신호가 아니면
        #     filename = f"unknown_data_{int(time.time())}.json"
        #     save_debug_data("unknown", filename, payload, 0)
        
    except Exception as e:
        print(f"[❗] 페이로드 처리 오류 from {addr}: {e}")

def main():
    global sock
    
    print(f"""
🚀 Enhanced Listener 시작 (8바이트 헤더 방식 적용)
📅 시간: {datetime.datetime.now()}
📂 저장 폴더: {DEBUG_DATA_DIR}
🌐 리스닝 포트: {PORT}
🔧 통신 방식: 8바이트 헤더 (4바이트 타입 + 4바이트 길이)
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
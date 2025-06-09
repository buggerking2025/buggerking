# listener.py - 상태 복구 기능 추가된 완전한 버전 (DAP 표준 적용)
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

def send_dap_message(conn, data):
    """DAP 표준 형식으로 데이터 전송"""
    try:
        # JSON 데이터를 바이트로 변환
        if isinstance(data, str):
            data_bytes = data.encode('utf-8')
        elif isinstance(data, dict):
            data_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8')
        else:
            data_bytes = data
        
        # DAP 헤더 생성
        content_length = len(data_bytes)
        header = f"Content-Length: {content_length}\r\n\r\n".encode('ascii')
        
        # 헤더 + 데이터 전송
        conn.sendall(header + data_bytes)
        
        print(f"[📤] DAP 전송 완료: {content_length} bytes (헤더 포함)")
        return True
        
    except Exception as e:
        print(f"[❌] DAP 전송 실패: {e}")
        return False

def receive_message_with_fallback(conn):
    """DAP 방식 시도 후 기존 방식으로 fallback"""
    try:
        print(f"[📥] 메시지 수신 시작 (DAP 우선, fallback 지원)")
        
        # 먼저 조금 읽어서 DAP 헤더인지 확인
        conn.settimeout(2.0)  # 2초 타임아웃
        initial_data = conn.recv(1024)  # 처음 1024바이트만

        if not initial_data:
            print(f"[❌] 연결 즉시 종료됨")
            return None
        
        print(f"[📥] 초기 데이터: {repr(initial_data[:32])}")
        
        # DAP 헤더인지 확인
        if initial_data.startswith(b"Content-Length:"):
            print(f"[✅] DAP 형식 감지됨")
            return receive_dap_message_continue(conn, initial_data)
        else:
            print(f"[⚠️] DAP 형식 아님 - 기존 방식으로 fallback")
            return receive_legacy_message(conn, initial_data)
            
    except socket.timeout:
        print(f"[❌] 초기 데이터 수신 타임아웃")
        return None
    except Exception as e:
        print(f"[❌] 메시지 수신 오류: {e}")
        return None

def receive_dap_message_continue(conn, initial_data):
    """이미 읽은 초기 데이터와 함께 DAP 메시지 완성"""
    try:
        # 헤더 완성까지 읽기
        header_data = initial_data
        while b"\r\n\r\n" not in header_data:
            chunk = conn.recv(1024)
            if not chunk:
                print(f"[❌] DAP 헤더 읽기 중 연결 종료")
                return None
            header_data += chunk
            
            if len(header_data) > 1024:
                print(f"[❌] DAP 헤더가 너무 김")
                return None
        
        # Content-Length 파싱
        try:
            header_str = header_data.decode('ascii')
            content_length = None
            for line in header_str.split('\r\n'):
                if line.startswith('Content-Length:'):
                    content_length = int(line.split(':', 1)[1].strip())
                    break
            
            if content_length is None:
                print(f"[❌] Content-Length 파싱 실패")
                return None
                
            print(f"[📥] DAP Content-Length: {content_length}")
            
        except (UnicodeDecodeError, ValueError) as e:
            print(f"[❌] DAP 헤더 파싱 오류: {e}")
            return None
        
        # 이미 읽은 데이터에서 실제 JSON 부분 추출
        header_end_pos = header_data.find(b"\r\n\r\n") + 4
        data_bytes = header_data[header_end_pos:]
        
        # 나머지 데이터 읽기
        while len(data_bytes) < content_length:
            remaining = content_length - len(data_bytes)
            chunk = conn.recv(min(remaining, 8192))
            if not chunk:
                print(f"[❌] DAP 데이터 읽기 중 연결 종료")
                return None
            data_bytes += chunk
        
        print(f"[✅] DAP 수신 완료: {len(data_bytes)} bytes")
        return data_bytes
        
    except Exception as e:
        print(f"[❌] DAP 완성 오류: {e}")
        return None

def receive_legacy_message(conn, initial_data):
    """기존 방식으로 JSON 수신 (JSON 파싱으로 완료 감지)"""
    try:
        print(f"[📥] 기존 방식으로 수신 중...")
        
        all_data = initial_data
        json_complete = False
        
        # 초기 데이터로 JSON 완성 여부 확인
        try:
            json.loads(all_data.decode('utf-8'))
            json_complete = True
            print(f"[✅] 초기 데이터만으로 JSON 완성")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        
        # JSON이 완성될 때까지 계속 읽기
        while not json_complete:
            try:
                chunk = conn.recv(8192)
                if not chunk:
                    print(f"[❌] 기존 방식 수신 중 연결 종료")
                    break
                all_data += chunk
                
                # JSON 완성 여부 확인
                try:
                    json.loads(all_data.decode('utf-8'))
                    json_complete = True
                    print(f"[✅] JSON 완성 감지: {len(all_data)} bytes")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # 아직 불완전하면 계속
                    if len(all_data) > 10 * 1024 * 1024:  # 10MB 제한
                        print(f"[❌] 데이터가 너무 큼: {len(all_data)} bytes")
                        return None
                    continue
                    
            except socket.timeout:
                print(f"[❌] 기존 방식 수신 타임아웃")
                return None
        
        if json_complete:
            print(f"[✅] 기존 방식 수신 완료: {len(all_data)} bytes")
            return all_data
        else:
            print(f"[❌] JSON 완성되지 않음")
            return None
            
    except Exception as e:
        print(f"[❌] 기존 방식 수신 오류: {e}")
        return None

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
    """타이머 + JSON 파일 전송 (연결 유지) - DAP 방식"""
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
            
            print(f"📤 [JSON-SEND] 파일 내용 읽기 완료: {len(json_content)} chars")
            
            # DAP 방식으로 전송
            success = send_dap_message(conn, json_content)
            
            if success:
                print(f"✅ [JSON-SEND] DAP 전송 완료! 총 {len(json_content)} chars")
            else:
                print(f"❌ [JSON-SEND] DAP 전송 실패!")
            
        except Exception as e:
            print(f"❌ [JSON-SEND] 전송 실패: {e}")
            import traceback
            print(f"❌ [JSON-SEND] 상세: {traceback.format_exc()}")
            
            # 오류 응답도 DAP 방식으로
            error_response = {
                "has_state": False,
                "error": str(e),
                "message": "파일 읽기 실패"
            }
            send_dap_message(conn, error_response)
    
    else:
        print(f"❌ [JSON-SEND] 전송할 JSON 파일 없음")
        
        # 빈 응답도 DAP 방식으로 전송
        empty_response = {
            "has_state": False,
            "message": "전송할 상태 파일이 없습니다"
        }
        
        try:
            send_dap_message(conn, empty_response)
            print(f"📤 [JSON-SEND] 빈 응답 전송 완료")
        except Exception as e:
            print(f"❌ [JSON-SEND] 빈 응답 전송 실패: {e}")

# Lambda에서 보내는 연결(타이머 / shutdown / 파일 저장 / 상태 복구) 처리
def handle_connection(conn, addr):
    global sock
    try:
        print(f"[🔗] 연결됨: {addr}")
        
        # DAP 방식 시도, 실패 시 기존 방식으로 fallback
        message_data = receive_message_with_fallback(conn)
        
        if not message_data:
            print(f"[❗] 메시지 수신 실패 from {addr}")
            return
        
        # JSON 파싱
        try:
            payload = json.loads(message_data.decode('utf-8'))
            
            # 🔥 특별 처리: remaining_ms 신호면 연결 유지하고 JSON 전송
            if 'remaining_ms' in payload and 'data_type' not in payload:
                handle_timeout_and_send_json(payload, conn, addr)
                return
            
            # 일반 처리
            handle_payload(payload, addr, message_data)
            
        except json.JSONDecodeError as e:
            print(f"[❗] JSON 파싱 실패 from {addr}: {e}")
            print(f"[📏] 수신 데이터 크기: {len(message_data)} bytes")
            print(f"[📋] 데이터 미리보기: {message_data[:200]}")
                
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
        print(f"📥 [PAYLOAD] 페이로드 수신 from {addr}: {list(payload.keys()) if isinstance(payload, dict) else type(payload)}")
        
        # 1. Shutdown 신호 처리
        if payload.get('shutdown'):
            print(f"🚨 Shutdown signal 수신 from {addr}")
            shutdown_flag.set()  # 플래그 설정
            
            # 메인 스레드가 정리할 수 있도록 잠시 대기
            time.sleep(0.1)
            
            print(f"🔚 Shutdown 처리 완료 - 메인 스레드로 제어 이관")
            return
        
        # 2. 파일 저장 처리
        data_type = payload.get('data_type')
        if data_type:
            filename = payload.get('filename', f'debug_data_{int(time.time())}.json')
            content = payload.get('content', '')
            file_size = payload.get('file_size', len(raw_data))
            
            print(f"📥 파일 데이터 수신 from {addr}")
            print(f"    📄 파일명: {filename}")
            print(f"    🏷️ 타입: {data_type}")
            print(f"    📏 크기: {file_size} bytes")
            
            # 파일 저장
            success = save_debug_data(data_type, filename, content, file_size)
            
            if success:
                print(f"✅ 파일 저장 성공: {filename}")
            else:
                print(f"❌ 파일 저장 실패: {filename}")
            
            return
        
        # 3. 기타 데이터 처리
        print(f"❓ 알 수 없는 데이터 타입 from {addr}")
        print(f"📋 페이로드 키: {list(payload.keys())}")
        
        # 일반적인 디버그 데이터로 저장 시도
        if len(payload) > 1:  # 단순 신호가 아니면
            filename = f"unknown_data_{int(time.time())}.json"
            save_debug_data("unknown", filename, payload, len(raw_data))
        
    except Exception as e:
        print(f"❗ 페이로드 처리 오류: {e}")
        import traceback
        print(f"❗ 상세 오류: {traceback.format_exc()}")

def main():
    global sock
    
    print(f"""
🚀 Enhanced Listener 시작 (DAP 표준 적용)
📅 시간: {datetime.datetime.now()}
📂 저장 폴더: {DEBUG_DATA_DIR}
🌐 리스닝 포트: {PORT}
🔧 통신 방식: DAP (Debug Adapter Protocol)
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
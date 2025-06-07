# listener.py - 상태 복구 기능 추가된 완전한 버전
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

def handle_state_recovery_request(payload, addr):
    """Lambda 상태 복구 요청 처리"""
    try:
        print(f"🔄 [STATE-RECOVERY] 요청 정보:")
        print(f"    세션 ID: {payload.get('lambda_session_id', 'unknown')}")
        print(f"    함수명: {payload.get('function_name', 'unknown')}")
        print(f"    타임스탬프: {payload.get('timestamp', 'unknown')}")
        
        # 가장 최근 callstack 파일 찾기
        latest_file = find_latest_callstack_file()
        
        if latest_file:
            print(f"✅ [STATE-RECOVERY] 최신 파일 발견: {os.path.basename(latest_file)}")
            
            # 파일 크기 확인
            file_size = os.path.getsize(latest_file)
            print(f"📏 [STATE-RECOVERY] 파일 크기: {file_size} bytes")
            
            # 파일 내용 읽기
            with open(latest_file, 'r', encoding='utf-8') as f:
                state_data = json.load(f)
            
            print(f"📊 [STATE-RECOVERY] JSON 로드 성공!")
            print(f"📊 [STATE-RECOVERY] 최상위 키: {list(state_data.keys())}")
            
            total_locals = 0
            total_globals = 0
            
            if "callstacks" in state_data:
                callstack_count = len(state_data["callstacks"])
                print(f"📊 [STATE-RECOVERY] callstacks 개수: {callstack_count}")
                
                # 통계 출력
                for frame in state_data["callstacks"]:
                    total_locals += len(frame.get("variables", {}).get("locals", []))
                    total_globals += len(frame.get("variables", {}).get("globals", []))
                
                print(f"📊 [STATE-RECOVERY] 총 locals 변수: {total_locals}")
                print(f"📊 [STATE-RECOVERY] 총 globals 변수: {total_globals}")
            
            # Lambda로 전송할 응답 구성
            response = {
                "has_state": True,
                "state": state_data,
                "restored_from": os.path.basename(latest_file),
                "file_size": file_size,
                "timestamp": datetime.datetime.now().isoformat(),
                "message": "이전 디버깅 상태 복구 데이터 전송 완료",
                "stats": {
                    "total_frames": len(state_data.get("callstacks", [])),
                    "total_locals": total_locals,
                    "total_globals": total_globals
                }
            }
            
            print(f"📤 [STATE-RECOVERY] 응답 준비 완료 (has_state: True)")
            
        else:
            print(f"❌ [STATE-RECOVERY] 복구할 상태 파일 없음")
            response = {
                "has_state": False,
                "message": "복구할 이전 디버깅 상태가 없습니다",
                "timestamp": datetime.datetime.now().isoformat(),
                "searched_directory": DEBUG_DATA_DIR
            }
            
            print(f"📤 [STATE-RECOVERY] 응답 준비 완료 (has_state: False)")
        
        return response
        
    except Exception as e:
        print(f"❌ [STATE-RECOVERY] 처리 오류: {e}")
        import traceback
        print(f"❌ [STATE-RECOVERY] 상세: {traceback.format_exc()}")
        
        error_response = {
            "has_state": False,
            "error": str(e),
            "timestamp": datetime.datetime.now().isoformat()
        }
        return error_response

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

# Lambda에서 보내는 연결(타이머 / shutdown / 파일 저장 / 상태 복구) 처리
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
            
            # # 🔥 상태 복구 요청 처리 우선!
            # if payload.get('action') == 'request_state_recovery':
            #     print(f"🔄 [STATE-RECOVERY] 상태 복구 요청 감지! from {addr}")
            #     response = handle_state_recovery_request(payload, addr)
                
            #     # 응답 전송
            #     response_data = json.dumps(response, ensure_ascii=False).encode('utf-8')
            #     print(f"📤 [STATE-RECOVERY] 응답 전송 중... ({len(response_data)} bytes)")
            #     conn.sendall(response_data)
            #     print(f"✅ [STATE-RECOVERY] 응답 전송 완료!")
            #     return
            
            # # 완전한 JSON을 받았으면 처리
            # handle_payload(payload, addr, initial_data)

            # 🔥 특별 처리: remaining_ms 신호면 연결 유지하고 JSON 전송
            if 'remaining_ms' in payload and 'data_type' not in payload:
                handle_timeout_and_send_json(payload, conn, addr)
                return
            
            # 일반 처리
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

def handle_timeout_and_send_json(payload, conn, addr):
    """타이머 + JSON 파일 전송 (연결 유지)"""
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
            
            json_bytes = json_content.encode('utf-8')
            print(f"📤 [JSON-SEND] 인코딩 후 크기: {len(json_bytes)} bytes")
            
            # 청크 단위로 전송
            chunk_size = 8192
            total_chunks = (len(json_bytes) + chunk_size - 1) // chunk_size
            
            print(f"📤 [JSON-SEND] {total_chunks}개 청크로 전송 시작...")
            
            for i in range(0, len(json_bytes), chunk_size):
                chunk = json_bytes[i:i + chunk_size]
                conn.sendall(chunk)
                
                chunk_num = i // chunk_size + 1
                print(f"📤 [JSON-SEND] 청크 {chunk_num}/{total_chunks} 전송 완료 ({len(chunk)} bytes)")
                
                time.sleep(0.01)  # 짧은 딜레이 (안정성)
            
            print(f"✅ [JSON-SEND] 전송 완료! 총 {len(json_bytes)} bytes")
            
        except Exception as e:
            print(f"❌ [JSON-SEND] 전송 실패: {e}")
            import traceback
            print(f"❌ [JSON-SEND] 상세: {traceback.format_exc()}")
    
    else:
        print(f"❌ [JSON-SEND] 전송할 JSON 파일 없음")
        
        # 빈 응답 전송
        empty_response = {
            "has_state": False,
            "message": "전송할 상태 파일이 없습니다"
        }
        
        try:
            response_data = json.dumps(empty_response).encode('utf-8')
            conn.sendall(response_data)
            print(f"📤 [JSON-SEND] 빈 응답 전송 완료")
        except Exception as e:
            print(f"❌ [JSON-SEND] 빈 응답 전송 실패: {e}")

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
        
        # # 2. Timeout 신호 처리
        # if 'remaining_ms' in payload and 'data_type' not in payload:
        #     remaining_ms = int(payload.get('remaining_ms', 0))
        #     print(f"📨 Timeout 신호 수신 from {addr} | timeout: {remaining_ms} ms")
        #     threading.Thread(
        #         target=print_remaining_time,
        #         args=(remaining_ms,),
        #         daemon=True
        #     ).start()

        #     # 🔥 NEW: JSON 파일 전송 (connection은 아직 열려있음)
        #     send_json_file_to_lambda(addr)
        #     return
        
        # 3. 파일 저장 처리
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
        
        # 4. 기타 데이터 처리
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
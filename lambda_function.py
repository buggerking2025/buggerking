import debugpy
import json
import socket
import time

def request_previous_state(reinvoked=False):
    """개발자 PC에서 이전 디버깅 상태 요청"""
    print("🔄 [REQUEST-STATE] 이전 디버깅 상태 요청 시작...")
    if reinvoked:
        print("🔁 [REQUEST-STATE] 재호출로 인한 상태 복구 시도")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)  # 10초 타임아웃
            
            print("🔄 [REQUEST-STATE] 개발자 PC에 연결 중... (165.194.27.213:6689)")
            sock.connect(("165.194.27.213", 6689))
            print("✅ [REQUEST-STATE] 연결 성공!")
            
            
            # 응답 수신 (큰 JSON 파일 대응)
            print("📥 [REQUEST-STATE] 응답 수신 시작...")
            response_data = b""
            
            while True:
                chunk = sock.recv(8192)  # 8KB씩 수신
                if not chunk:
                    break
                response_data += chunk
                print(f"📥 [REQUEST-STATE] 청크 수신: {len(chunk)} bytes (총: {len(response_data)} bytes)")
                
                # JSON 완성 체크
                try:
                    json.loads(response_data.decode('utf-8'))
                    print("✅ [REQUEST-STATE] 완전한 JSON 수신 감지!")
                    break
                except json.JSONDecodeError:
                    print("⏳ [REQUEST-STATE] JSON 수신 중...")
                    continue
            
            sock.close()
            
            if response_data:
                print(f"🎉 [REQUEST-STATE] 총 {len(response_data)} bytes 수신 완료!")
                
                try:
                    response_json = json.loads(response_data.decode('utf-8'))
                    print("✅ [REQUEST-STATE] JSON 파싱 성공!")
                    
                    # 📊 받은 JSON 내용 상세 로깅
                    print("=" * 80)
                    print("📋 [RECEIVED-JSON] 수신된 JSON 파일 내용:")
                    print("=" * 80)
                    
                    # JSON 구조 분석
                    if isinstance(response_json, dict):
                        print(f"📁 [JSON-STRUCTURE] 최상위 키들: {list(response_json.keys())}")
                        
                        # has_state 확인
                        if "has_state" in response_json:
                            has_state = response_json["has_state"]
                            print(f"🔍 [JSON-CONTENT] has_state: {has_state}")
                            
                            if has_state:
                                print("✅ [JSON-CONTENT] 복구할 상태 데이터 있음!")
                                
                                # state 데이터 상세 분석
                                if "state" in response_json:
                                    state_data = response_json["state"]
                                    print(f"📊 [STATE-DATA] state 타입: {type(state_data)}")
                                    
                                    if isinstance(state_data, dict):
                                        print(f"📊 [STATE-DATA] state 키들: {list(state_data.keys())}")
                                        
                                        # callstacks 분석
                                        if "callstacks" in state_data:
                                            callstacks = state_data["callstacks"]
                                            print(f"📊 [CALLSTACKS] callstacks 개수: {len(callstacks)}")
                                            
                                            for i, frame in enumerate(callstacks):
                                                print(f"📊 [FRAME-{i}] frame_id: {frame.get('frame_id', 'unknown')}")
                                                print(f"📊 [FRAME-{i}] function: {frame.get('function', 'unknown')}")
                                                print(f"📊 [FRAME-{i}] file: {frame.get('file', 'unknown')}")
                                                print(f"📊 [FRAME-{i}] line: {frame.get('line', 'unknown')}")
                                                
                                                # 변수 개수 확인
                                                variables = frame.get('variables', {})
                                                locals_count = len(variables.get('locals', []))
                                                globals_count = len(variables.get('globals', []))
                                                
                                                print(f"📊 [FRAME-{i}] locals 변수 개수: {locals_count}")
                                                print(f"📊 [FRAME-{i}] globals 변수 개수: {globals_count}")
                                                
                                                # 첫 번째 프레임의 변수 몇 개 샘플 출력
                                                if i == 0 and locals_count > 0:
                                                    print(f"📋 [FRAME-{i}] locals 샘플:")
                                                    for j, var in enumerate(variables['locals'][:3]):  # 처음 3개만
                                                        var_name = var.get('name', 'unknown')
                                                        var_value = str(var.get('value', ''))[:50]  # 처음 50자만
                                                        var_type = var.get('type', 'unknown')
                                                        print(f"📋 [FRAME-{i}]   {j+1}. {var_name} = {var_value}... ({var_type})")
                                                
                                                print("-" * 40)
                                        
                                        # 메타데이터 출력
                                        if "summary" in state_data:
                                            summary = state_data["summary"]
                                            print(f"📊 [SUMMARY] {summary}")
                                            
                                # 복구 소스 파일 정보
                                if "restored_from" in response_json:
                                    restored_from = response_json["restored_from"]
                                    print(f"📁 [SOURCE-FILE] 복구 소스: {restored_from}")
                                    
                            else:
                                print("❌ [JSON-CONTENT] 복구할 상태 없음")
                                if "message" in response_json:
                                    print(f"📝 [MESSAGE] {response_json['message']}")
                        
                        # 전체 JSON 크기 정보
                        json_str = json.dumps(response_json, indent=2)
                        print(f"📏 [JSON-SIZE] 전체 JSON 크기: {len(json_str)} 문자")
                        print(f"📏 [JSON-SIZE] 전체 JSON 라인 수: {len(json_str.splitlines())}")
                        
                    else:
                        print(f"⚠️ [JSON-TYPE] 예상과 다른 JSON 타입: {type(response_json)}")
                        print(f"📋 [JSON-CONTENT] 내용: {str(response_json)[:200]}...")
                    
                    print("=" * 80)
                    return True
                    
                except json.JSONDecodeError as e:
                    print(f"❌ [REQUEST-STATE] JSON 파싱 실패: {e}")
                    print(f"📋 [RAW-DATA] 받은 데이터 (처음 500자): {response_data[:500]}")
                    return False
            else:
                print("❌ [REQUEST-STATE] 응답 데이터 없음")
                return False
                    
        except Exception as e:
            print(f"❌ [REQUEST-STATE] 전체 요청 실패: {e}")
            import traceback
            print(f"❌ [REQUEST-STATE] 상세 오류: {traceback.format_exc()}")
            return False

def lambda_handler(event, context):
    print("🚀 Lambda 핸들러 시작!")

    # 쿼리스트링 파싱 (reinvoked는 로깅용이지만 전송하지 않음)
    params = event.get("queryStringParameters", {}) or {}
    reinvoked = params.get("reinvoked") == "true"

    if reinvoked:
        print("🔁 Lambda가 재호출되었습니다! 디버깅 상태 복구 시도 중...")
        # 🔥 첫 번째로 이전 상태 복구 시도
        print("🔄 이전 디버깅 상태 복구 시도...")
        state_recovered = request_previous_state(reinvoked)

        if state_recovered:
            print("🎉 상태 복구 요청 성공! (위의 로그 확인)")
        else:
            print("❌ 상태 복구 실패 또는 복구할 상태 없음")
    else:
        print("🌟 실패!")
    
    print("-" * 60)
    print("🐛 일반 디버깅 시작...")
    
    # 기존 디버깅 코드
    try:
        x = 1 / 0  # 예외 발생
    except Exception:
        debugpy.connect(('165.194.27.213', 7789))
        debugpy.wait_for_client()
        print("✅ 디버거 연결됨")

        remaining = context.get_remaining_time_in_millis()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30.0)  # 30초 타임아웃 설정
        sock.connect(("165.194.27.213", 6689))
        
        # 1) remaining_ms 전송
        msg = json.dumps({"remaining_ms": remaining}).encode('utf-8')
        sock.sendall(msg)
        print(f"📤 timeout = {remaining} ms 전송 완료")
        
        # 2) 첫 실행이 아니라면 JSON 응답 대기
        params = event.get("queryStringParameters", {}) or {}
        reinvoked = params.get("reinvoked") == "true"
        
        if reinvoked:
            print("🔄 이전 상태 JSON 응답 대기 중...")
            
            # JSON 응답 수신
            json_data = receive_json_from_developer(sock)
            
            if json_data:
                print(f"✅ JSON 수신 성공! 크기: {len(json_data)} bytes")
                
                # 간단한 파싱 테스트
                try:
                    parsed_json = json.loads(json_data.decode('utf-8'))
                    
                    print(f"📊 JSON 구조 확인:")
                    print(f"  - 최상위 키: {list(parsed_json.keys())}")
                    
                    if "callstacks" in parsed_json:
                        callstacks = parsed_json["callstacks"]
                        print(f"  - callstacks 개수: {len(callstacks)}")
                        
                        if len(callstacks) > 0:
                            first_frame = callstacks[0]
                            print(f"  - 첫 번째 프레임: {first_frame.get('function', 'unknown')}")
                            
                            variables = first_frame.get('variables', {})
                            locals_count = len(variables.get('locals', []))
                            globals_count = len(variables.get('globals', []))
                            print(f"  - 변수 개수: locals={locals_count}, globals={globals_count}")
                    
                    print("🎉 JSON 파일 전송 테스트 성공!")
                    
                except json.JSONDecodeError as e:
                    print(f"❌ JSON 파싱 실패: {e}")
                    print(f"📋 받은 데이터 (처음 200자): {json_data[:200]}")
                    
            else:
                print("❌ JSON 수신 실패")
        
        sock.close()
        
    except Exception as e:
        print(f"❗ 소켓 통신 실패: {e}")
        import traceback
        print(f"❗ 상세 오류: {traceback.format_exc()}")

    # 기존 디버깅 루프
    for i in range(3):  # 테스트용으로 3번만
        print(f"[루프 {i}] 중단점 진입 전")
        debugpy.breakpoint()
        print(f"[루프 {i}] 중단점 통과 후")
        time.sleep(1)

    return {
        "statusCode": 200,
        "body": json.dumps("JSON 전송 테스트 완료"),
    }

def receive_json_from_developer(sock):
    """개발자로부터 JSON 파일 수신"""
    try:
        print("📥 JSON 수신 시작...")
        all_data = b""
        chunk_count = 0
        
        while True:
            chunk = sock.recv(8192)  # 8KB씩 수신
            if not chunk:
                print("📥 수신 완료 (연결 종료)")
                break
                
            all_data += chunk
            chunk_count += 1
            print(f"📥 청크 {chunk_count} 수신: {len(chunk)} bytes (총: {len(all_data)} bytes)")
            
            # JSON 완성도 체크
            try:
                json.loads(all_data.decode('utf-8'))
                print(f"✅ 완전한 JSON 감지! 총 {chunk_count}개 청크")
                break
            except json.JSONDecodeError:
                # 아직 불완전하면 계속 수신
                continue
            except UnicodeDecodeError:
                # 인코딩 문제면 계속 수신
                continue
        
        print(f"📥 최종 수신 완료: {len(all_data)} bytes")
        return all_data
        
    except Exception as e:
        print(f"❗ JSON 수신 오류: {e}")
        import traceback
        print(f"❗ 상세 오류: {traceback.format_exc()}")
        return None
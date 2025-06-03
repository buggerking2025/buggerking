# import debugpy
# import json
# import socket
# import time
# import random


# def lambda_handler(event, context):
#     try:
#         # 🔥 의도적으로 예외 발생시켜 디버깅 모드 진입
#         x = 1 / 0  # 예외 발생
#     except Exception:
#         # 🐛 디버거 연결
#         debugpy.connect(('165.194.27.213', 7789))  # 개발자 IP 
#         debugpy.wait_for_client()
#         print("✅ 디버거 연결됨")
        
#         # ⏰ 남은 시간 정보 전송
#         remaining = context.get_remaining_time_in_millis()
#         try:
#             sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#             sock.connect(("165.194.27.213", 6689))  # 개발자 PC IP + 수신 포트
#             msg = json.dumps({
#                 "remaining_ms": remaining,
#                 "debug_session": "lambda_debugging_started",
#                 "timestamp": time.time()
#             }).encode('utf-8')
#             sock.sendall(msg)
#             sock.close()
#             print(f"📤 timeout = {remaining} ms 전송 완료")
#         except Exception as e:
#             print(f"❗ 전송 실패: {e}")
        
#         # 🎯 첫 번째 중단점 - 디버깅 세션 시작
#         debugpy.breakpoint()
    
        
#         # 📊 테스트 변수들
#         a = 11
#         b = 22
#         c = ['lambda', 'debug']
#         d = {
#             'name': 'lambda_test', 
#             'value': 123,
#             'remaining_time': remaining,
#             'context_info': {
#                 'function_name': context.function_name if hasattr(context, 'function_name') else 'unknown',
#                 'request_id': context.aws_request_id if hasattr(context, 'aws_request_id') else 'unknown'
#             }
#         }
        
#         # 🔧 내부 함수들 정의
#         def calculate(x, y):
#             """Lambda에서 계산 수행"""
#             print(f"🧮 계산 수행: {x} * {y}")
#             result = x * y
#             debugpy.breakpoint()  # 함수 내 중단점
#             return result + random.randint(1, 10)
        
#         def factorial(n):
#             """재귀 함수 예제 (Lambda 환경에서)"""
#             if n <= 1:
#                 return 1
            
#             # n이 3일 때 중단점
#             if n == 3:
#                 print(f"🔄 재귀 함수에서 n={n}일 때 중단점")
#                 debugpy.breakpoint()
            
#             return n * factorial(n - 1)
        
#         def divide(x, y):
#             """Lambda에서 나누기 연산"""
#             try:
#                 result = x / y
#                 return result
#             except ZeroDivisionError as e:
#                 print(f"❌ Lambda에서 0으로 나누기 에러 발생")
#                 debugpy.breakpoint()  # 예외 발생 시 중단점
#                 raise e
        
#         # 🚀 함수 실행 테스트
#         print("🚀 Lambda에서 계산 함수 호출...")
#         calc_result = calculate(a, b)
#         print(f"계산 결과: {calc_result}")
        
#         # 🎲 랜덤 조건부 실행
#         random_value = random.randint(1, 10)
#         print(f"🎲 랜덤 값: {random_value}")
        
#         if random_value > 5:
#             def temp_lambda_function():
#                 """Lambda 내부 임시 함수"""
#                 temp_a = 11
#                 temp_b = 22
#                 print(f"🔧 Lambda 임시 함수 실행: a={temp_a}, b={temp_b}")
#                 debugpy.breakpoint()  # 조건부 중단점
#                 return temp_a + temp_b
            
#             temp_result = temp_lambda_function()
#             d['temp_result'] = temp_result
        
#         # 🔄 재귀 함수 호출
#         print("🔄 Lambda에서 재귀 함수 호출...")
#         factorial_result = factorial(5)
#         print(f"factorial(5) = {factorial_result}")
        
#         # 🔁 제한적 반복문 (Lambda 타임아웃 고려)
#         print("🔁 Lambda에서 반복문 실행...")
#         max_iterations = min(3, (remaining // 2000))  # 남은 시간에 따라 조절
        
#         for i in range(max_iterations):
#             current_remaining = context.get_remaining_time_in_millis()
#             print(f"반복 {i+1}/{max_iterations} (남은 시간: {current_remaining}ms)")
            
#             c.append(f"lambda_item_{i}")
            
#             # 타임아웃 체크
#             if current_remaining < 5000:  # 5초 미만이면 중단
#                 print("⏰ 타임아웃 임박으로 반복 중단")
#                 break
            
#             debugpy.breakpoint()  # 반복문 내 중단점
#             time.sleep(0.5)  # 짧은 대기
        
#         # 🚨 예외 처리 테스트
#         print("🚨 Lambda에서 예외 처리 테스트...")
#         try:
#             divide_result = divide(10, 0)
#         except ZeroDivisionError:
#             print("✅ Lambda에서 0으로 나누기 예외 처리 완료")
#             d['exception_handled'] = True
        
#         # 📈 최종 상태 요약
#         final_state = {
#             "variables": {
#                 "a": a, "b": b, "c": c, "d": d
#             },
#             "results": {
#                 "calc_result": calc_result,
#                 "factorial_result": factorial_result,
#                 "random_value": random_value
#             },
#             "lambda_context": {
#                 "remaining_time_start": remaining,
#                 "remaining_time_end": context.get_remaining_time_in_millis(),
#                 "iterations_completed": max_iterations
#             }
#         }
        
#         print("📊 최종 상태 요약:")
#         print(json.dumps(final_state, indent=2, default=str))
        
#         # 🔚 마지막 중단점
#         print("🔚 Lambda 디버깅 완료 직전")
#         debugpy.breakpoint()
        
#         # 📤 최종 상태 전송
#         try:
#             sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#             sock.connect(("165.194.27.213", 6689))
#             final_msg = json.dumps({
#                 "debug_session": "lambda_debugging_completed",
#                 "final_state": final_state,
#                 "timestamp": time.time()
#             }).encode('utf-8')
#             sock.sendall(final_msg)
#             sock.close()
#             print("📤 최종 상태 전송 완료")
#         except Exception as e:
#             print(f"❗ 최종 상태 전송 실패: {e}")
        
#         print("🎉 Lambda 디버깅 완료!")
        
#         return {
#             "statusCode": 200,  # 성공으로 변경
#             "body": json.dumps({
#                 "message": "Lambda 디버깅 세션 완료",
#                 "debug_summary": final_state,
#                 "total_time_used": remaining - context.get_remaining_time_in_millis()
#             }, default=str),
#         }

# # 🧪 로컬 테스트용 (Lambda 환경이 아닐 때)
# if __name__ == "__main__":
#     class MockContext:
#         def __init__(self):
#             self.start_time = time.time() * 1000
#             self.function_name = "test_lambda"
#             self.aws_request_id = "test-request-123"
        
#         def get_remaining_time_in_millis(self):
#             elapsed = (time.time() * 1000) - self.start_time
#             return max(0, 300000 - elapsed)  # 5분 제한 시뮬레이션
    
#     mock_context = MockContext()
#     result = lambda_handler({}, mock_context)
#     print("🧪 로컬 테스트 결과:", result)
# app.py
import debugpy
import json
import socket
import time

def lambda_handler(event, context):
    try:
        x = 1 / 0  # 예외 발생
    except Exception:
        debugpy.connect(('165.194.27.213', 7789))  # 개발자 IP 
        
        debugpy.wait_for_client()
        print("✅ 디버거 연결됨")

        remaining = context.get_remaining_time_in_millis()

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("165.194.27.213", 6689))  # 개발자 PC IP + 수신 포트
            msg = json.dumps({"remaining_ms": remaining}).encode('utf-8')
            sock.sendall(msg)
            sock.close()
            print(f"📤 timeout = {remaining} ms 전송 완료")
        except Exception as e:
            print(f"❗ 전송 실패: {e}")

        for i in range(10):
            print(f"[루프 {i}] 중단점 진입 전")
            debugpy.breakpoint()
            print(f"[루프 {i}] 중단점 통과 후")
            time.sleep(1)

        return {
            "statusCode": 500,
            "body": json.dumps("디버깅 진입"),
        }

# main.py - 수정된 데이터 전송 방식
# import debugpy
# import json
# import socket
# import time
# import os
# import sys
# import base64

# def send_data_to_local(data, data_type="general"):
#     """안전한 데이터 전송 함수"""
#     try:
#         # JSON 직렬화
#         if isinstance(data, dict):
#             json_data = data
#         else:
#             json_data = {"data": str(data), "type": data_type}
        
#         # timestamp 추가
#         json_data["timestamp"] = time.time()
#         json_data["data_type"] = data_type
        
#         # JSON을 문자열로 변환
#         json_str = json.dumps(json_data, ensure_ascii=True, default=str)
        
#         # UTF-8로 인코딩
#         data_bytes = json_str.encode('utf-8')
        
#         # 소켓 연결 및 전송
#         sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#         sock.settimeout(5.0)  # 5초 타임아웃
#         sock.connect(("165.194.27.213", 6689))
        
#         # 단순 전송 (헤더 없이)
#         sock.sendall(data_bytes)
#         sock.close()
        
#         print(f"📤 {data_type} 데이터 전송 완료 ({len(data_bytes)} bytes)")
#         return True
        
#     except Exception as e:
#         print(f"❗ {data_type} 데이터 전송 실패: {e}")
#         return False

# def lambda_handler(event, context):
#     try:
#         x = 1 / 0
#     except Exception:
#         debugpy.connect(('165.194.27.213', 7789))
#         debugpy.wait_for_client()
#         print("✅ 디버거 연결됨")
        
#         # 🔍 **1단계: Layer 진단**
#         print("\n=== LAYER 진단 시작 ===")
#         layer_diagnosis = {
#             "diagnostic": "layer_diagnosis",
#             "layer_paths": [],
#             "pydevd_locations": [],
#             "modified_code_found": False,
#             "functions_found": []
#         }
        
#         # Lambda Layer 경로들 확인
#         potential_layer_paths = [
#             "/opt/python",
#             "/opt/python/lib/python3.9/site-packages", 
#             "/opt/python/lib/python3.10/site-packages",
#             "/opt/python/lib/python3.11/site-packages",
#             "/opt/python/lib/python3.12/site-packages",
#             "/var/runtime",
#             "/var/task"
#         ]
        
#         for path in potential_layer_paths:
#             if os.path.exists(path):
#                 layer_diagnosis["layer_paths"].append(path)
#                 print(f"✅ Layer 경로 발견: {path}")
                
#                 # pydevd 관련 파일 찾기
#                 try:
#                     for root, dirs, files in os.walk(path):
#                         for file in files:
#                             if 'pydevd_comm.py' in file:
#                                 full_path = os.path.join(root, file)
#                                 layer_diagnosis["pydevd_locations"].append(full_path)
#                                 print(f"🔍 pydevd_comm.py 발견: {full_path}")
                                
#                                 # 파일 내용 확인 (수정된 코드가 있는지)
#                                 try:
#                                     with open(full_path, 'r', encoding='utf-8') as f:
#                                         content = f.read()
#                                         if "lambda_environment" in content:
#                                             layer_diagnosis["modified_code_found"] = True
#                                             print("✅ 수정된 코드 발견!")
#                                         if "send_debug_data_to_local" in content:
#                                             layer_diagnosis["functions_found"].append("send_debug_data_to_local")
#                                         if "create_accurate_callstacks_with_variables" in content:
#                                             layer_diagnosis["functions_found"].append("create_accurate_callstacks_with_variables")
#                                 except Exception as e:
#                                     print(f"파일 읽기 실패: {e}")
#                 except Exception as e:
#                     print(f"디렉토리 탐색 실패: {e}")
        
#         # Layer 진단 결과 전송
#         send_data_to_local(layer_diagnosis, "layer_diagnosis")
        
#         # 🔧 **2단계: 강제 로딩 시도**
#         print("\n=== 강제 로딩 시도 ===")
#         force_load_result = {
#             "diagnostic": "force_load_attempt",
#             "sys_path_before": sys.path.copy(),
#             "modules_before": list(sys.modules.keys()),
#             "force_load_success": False,
#             "errors": []
#         }
        
#         try:
#             # Layer 경로를 sys.path 맨 앞에 추가
#             for path in layer_diagnosis["layer_paths"]:
#                 if path not in sys.path:
#                     sys.path.insert(0, path)
#                     print(f"📁 sys.path에 추가: {path}")
            
#             # pydevd_comm 모듈 강제 재로딩
#             if 'pydevd_comm' in sys.modules:
#                 print("🔄 pydevd_comm 모듈 제거 후 재로딩")
#                 del sys.modules['pydevd_comm']
            
#             # 강제 import
#             import pydevd_comm
#             print(f"✅ pydevd_comm 재로딩 성공: {pydevd_comm.__file__}")
            
#             # 함수 확인
#             if hasattr(pydevd_comm, 'internal_get_variable_json'):
#                 print("✅ internal_get_variable_json 함수 발견")
                
#                 # 함수 소스 확인
#                 import inspect
#                 try:
#                     source = inspect.getsource(pydevd_comm.internal_get_variable_json)
#                     if "lambda_environment" in source:
#                         force_load_result["force_load_success"] = True
#                         print("🎉 수정된 함수 로딩 성공!")
#                     else:
#                         force_load_result["errors"].append("함수는 있지만 수정된 코드가 없음")
#                         print("❌ 기본 함수만 있음")
#                 except Exception as e:
#                     force_load_result["errors"].append(f"소스 확인 실패: {e}")
#             else:
#                 force_load_result["errors"].append("internal_get_variable_json 함수 없음")
                
#         except Exception as e:
#             force_load_result["errors"].append(f"강제 로딩 실패: {e}")
#             print(f"❌ 강제 로딩 실패: {e}")
        
#         force_load_result["sys_path_after"] = sys.path.copy()
#         force_load_result["modules_after"] = list(sys.modules.keys())
        
#         # 강제 로딩 결과 전송
#         send_data_to_local(force_load_result, "force_load_result")
        
#         # 🎯 **3단계: 수동 변수 수집**
#         print("\n=== 수동 변수 수집 시작 ===")
        
#         # 테스트 변수들
#         a = 11
#         b = 22 
#         c = ['lambda', 'debug', 'test']
#         d = {
#             'name': 'lambda_test',
#             'value': 123,
#             'nested': {
#                 'level1': {'level2': 'deep_value'},
#                 'array': [1, 2, 3, 4, 5]
#             }
#         }
        
#         # ComplexClass 인스턴스 생성 (문서에서 본 클래스)
#         class ComplexClass:
#             def __init__(self, name, values=None):
#                 self.name = name
#                 self.values = values if values is not None else []
#                 self.metadata = {
#                     'created': time.time(),
#                     'updated': None,
#                     'tags': set(['lambda', 'debug'])
#                 }
#                 self.children = []

#             def add_value(self, value):
#                 self.values.append(value)
#                 self.metadata['updated'] = time.time()

#             def summary(self):
#                 return {
#                     'name': self.name,
#                     'value_count': len(self.values),
#                     'tags': list(self.metadata['tags']),
#                     'children_count': len(self.children)
#                 }
        
#         complex_obj = ComplexClass("lambda_instance")
#         complex_obj.add_value("test_value_1")
#         complex_obj.add_value("test_value_2")
        
#         # 수동으로 변수 정보 수집
#         manual_variables = {
#             "diagnostic": "manual_variables",
#             "frame_info": {
#                 "function": "lambda_handler",
#                 "file": "/var/task/main.py",
#                 "line": "current_breakpoint_line"
#             },
#             "locals": {
#                 "a": {"value": str(a), "type": type(a).__name__},
#                 "b": {"value": str(b), "type": type(b).__name__},
#                 "c": {"value": str(c), "type": type(c).__name__, "length": len(c)},
#                 "d": {"value": str(d)[:200], "type": type(d).__name__, "keys": list(d.keys())},
#                 "complex_obj": {
#                     "value": str(complex_obj)[:200],
#                     "type": type(complex_obj).__name__,
#                     "summary": complex_obj.summary()
#                 }
#             },
#             "globals_sample": {},
#             "call_stack": []
#         }
        
#         # 글로벌 변수 샘플 (중요한 것들만)
#         important_globals = ['__name__', '__file__', 'lambda_handler', 'debugpy', 'json', 'socket', 'time']
#         for name in important_globals:
#             if name in globals():
#                 try:
#                     val = globals()[name]
#                     manual_variables["globals_sample"][name] = {
#                         "value": str(val)[:100],
#                         "type": type(val).__name__
#                     }
#                 except:
#                     manual_variables["globals_sample"][name] = {"error": "access_failed"}
        
#         # 콜스택 정보 수집
#         import traceback
#         import inspect
        
#         try:
#             current_frame = inspect.currentframe()
#             stack_info = []
            
#             for frame_info in inspect.stack()[:5]:  # 상위 5개 프레임만
#                 stack_info.append({
#                     "filename": frame_info.filename,
#                     "function": frame_info.function,
#                     "lineno": frame_info.lineno,
#                     "code": frame_info.code_context[0].strip() if frame_info.code_context else ""
#                 })
            
#             manual_variables["call_stack"] = stack_info
            
#         except Exception as e:
#             manual_variables["call_stack"] = [{"error": str(e)}]
        
#         # 첫 번째 중단점 - 변수 수집 테스트
#         print(f"\n=== 첫 번째 중단점 ===")
#         print(f"변수들: a={a}, b={b}, c={c}")
#         debugpy.breakpoint()  # 여기서 Variables 창을 확인해보세요!
        
#         # 수동 변수 정보 전송
#         send_data_to_local(manual_variables, "manual_variables")
        
#         # 두 번째 중단점 - 복잡한 객체 테스트
#         print(f"\n=== 두 번째 중단점 ===")
#         print(f"복잡한 객체: {complex_obj.summary()}")
#         debugpy.breakpoint()  # 여기서도 Variables 창 확인!
        
#         # 최종 상태 전송
#         final_state = {
#             "diagnostic": "final_state",
#             "layer_diagnosis_summary": {
#                 "layer_paths_found": len(layer_diagnosis["layer_paths"]),
#                 "pydevd_locations": len(layer_diagnosis["pydevd_locations"]),
#                 "modified_code_found": layer_diagnosis["modified_code_found"]
#             },
#             "force_load_summary": {
#                 "success": force_load_result["force_load_success"],
#                 "errors_count": len(force_load_result["errors"])
#             },
#             "manual_collection_summary": {
#                 "locals_count": len(manual_variables["locals"]),
#                 "globals_count": len(manual_variables["globals_sample"]),
#                 "stack_depth": len(manual_variables["call_stack"])
#             },
#             "debugging_status": "completed"
#         }
        
#         send_data_to_local(final_state, "final_state")
        
#         print("🎉 Lambda 진단 및 변수 수집 완료!")
        
#         return {
#             "statusCode": 200,
#             "body": json.dumps({
#                 "message": "진단 완료 - debug_data 폴더 확인",
#                 "layer_found": layer_diagnosis["modified_code_found"],
#                 "force_load_success": force_load_result["force_load_success"],
#                 "manual_collection": "completed"
#             }),
#         }

# # 로컬 테스트용
# if __name__ == "__main__":
#     class MockContext:
#         def get_remaining_time_in_millis(self):
#             return 300000
    
#     result = lambda_handler({}, MockContext())
#     print("로컬 테스트 결과:", result)
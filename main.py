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
# import debugpy
# import json
# import socket
# import time

# def lambda_handler(event, context):
#     try:
#         x = 1 / 0  # 예외 발생
#     except Exception:
#         debugpy.connect(('165.194.27.213', 7789))  # 개발자 IP 
        
#         debugpy.wait_for_client()
#         print("✅ 디버거 연결됨")

#         remaining = context.get_remaining_time_in_millis()

#         try:
#             sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#             sock.connect(("165.194.27.213", 6689))  # 개발자 PC IP + 수신 포트
#             msg = json.dumps({"remaining_ms": remaining}).encode('utf-8')
#             sock.sendall(msg)
#             sock.close()
#             print(f"📤 timeout = {remaining} ms 전송 완료")
#         except Exception as e:
#             print(f"❗ 전송 실패: {e}")

#         for i in range(10):
#             print(f"[루프 {i}] 중단점 진입 전")
#             debugpy.breakpoint()
#             print(f"[루프 {i}] 중단점 통과 후")
#             time.sleep(1)

#         return {
#             "statusCode": 500,
#             "body": json.dumps("디버깅 진입"),
#         }
# main.py - 디버깅 진단 버전
import debugpy
import json
import socket
import time
import os
import sys

def lambda_handler(event, context):
    try:
        x = 1 / 0
    except Exception:
        debugpy.connect(('165.194.27.213', 7789))
        debugpy.wait_for_client()
        print("✅ 디버거 연결됨")
        
        # 🔍 진단 1: 어떤 pydevd 모듈이 로드되었는지 확인
        print("\n=== PYDEVD 모듈 진단 ===")
        try:
            import pydevd_comm
            print(f"✅ pydevd_comm 모듈 로드됨: {pydevd_comm.__file__}")
            
            # internal_get_variable_json 함수가 있는지 확인
            if hasattr(pydevd_comm, 'internal_get_variable_json'):
                print("✅ internal_get_variable_json 함수 발견!")
                
                # 함수 소스 코드 일부 확인 (우리가 수정한 부분이 있는지)
                import inspect
                source_lines = inspect.getsource(pydevd_comm.internal_get_variable_json)
                if "lambda_environment" in source_lines:
                    print("✅ 우리가 수정한 코드가 포함되어 있음!")
                else:
                    print("❌ 우리가 수정한 코드가 없음 - 기본 버전")
            else:
                print("❌ internal_get_variable_json 함수 없음")
                
        except ImportError as e:
            print(f"❌ pydevd_comm import 실패: {e}")
        
        # 🔍 진단 2: debugpy 모듈 정보
        print(f"\n=== DEBUGPY 모듈 진단 ===")
        print(f"debugpy 위치: {debugpy.__file__}")
        print(f"debugpy 버전: {getattr(debugpy, '__version__', 'unknown')}")
        
        # 🔍 진단 3: sys.path 확인 (어디에서 모듈을 가져오는지)
        print(f"\n=== SYS.PATH 확인 ===")
        for i, path in enumerate(sys.path[:5]):  # 처음 5개만
            print(f"{i}: {path}")
        
        # 🔍 진단 4: 현재 작업 디렉토리와 파일 구조
        print(f"\n=== 파일 시스템 확인 ===")
        print(f"현재 작업 디렉토리: {os.getcwd()}")
        
        try:
            # 현재 디렉토리의 파일들
            files = os.listdir('.')
            print(f"현재 디렉토리 파일들: {files[:10]}")  # 처음 10개만
            
            # pydevd 관련 파일들이 있는지 확인
            pydevd_files = [f for f in files if 'pydevd' in f]
            if pydevd_files:
                print(f"pydevd 관련 파일들: {pydevd_files}")
            else:
                print("pydevd 관련 파일 없음")
                
        except Exception as e:
            print(f"파일 시스템 확인 실패: {e}")
        
        # 🔍 진단 5: 실제로 함수가 호출되는지 테스트
        print(f"\n=== 함수 호출 테스트 ===")
        try:
            # 함수에 monkey patch를 적용해서 호출되는지 확인
            original_func = None
            if hasattr(pydevd_comm, 'internal_get_variable_json'):
                original_func = pydevd_comm.internal_get_variable_json
                
                def traced_function(*args, **kwargs):
                    print("🎯 internal_get_variable_json 함수가 호출됨!")
                    
                    # 진단 정보를 로컬로 전송
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.connect(("165.194.27.213", 6689))
                        msg = json.dumps({
                            "diagnostic": "function_called",
                            "function": "internal_get_variable_json",
                            "args_count": len(args),
                            "kwargs_keys": list(kwargs.keys()),
                            "timestamp": time.time()
                        }).encode('utf-8')
                        sock.sendall(msg)
                        sock.close()
                        print("📤 함수 호출 정보 전송 완료")
                    except Exception as e:
                        print(f"❗ 함수 호출 정보 전송 실패: {e}")
                    
                    return original_func(*args, **kwargs)
                
                # monkey patch 적용
                pydevd_comm.internal_get_variable_json = traced_function
                print("✅ 함수 추적 패치 적용 완료")
            else:
                print("❌ internal_get_variable_json 함수를 찾을 수 없어서 패치 불가")
                
        except Exception as e:
            print(f"❌ 함수 추적 패치 실패: {e}")
        
        # 🔍 진단 정보를 로컬로 전송
        diagnostic_info = {
            "diagnostic_type": "lambda_environment_check",
            "pydevd_comm_loaded": 'pydevd_comm' in sys.modules,
            "pydevd_comm_location": getattr(sys.modules.get('pydevd_comm'), '__file__', 'not_found'),
            "has_internal_get_variable_json": hasattr(sys.modules.get('pydevd_comm'), 'internal_get_variable_json'),
            "debugpy_location": debugpy.__file__,
            "cwd": os.getcwd(),
            "sys_path_first_5": sys.path[:5],
            "lambda_env_vars": {k: v for k, v in os.environ.items() if 'AWS' in k or 'LAMBDA' in k}
        }
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("165.194.27.213", 6689))
            msg = json.dumps({
                "diagnostic": "environment_info",
                "data": diagnostic_info,
                "timestamp": time.time()
            }).encode('utf-8')
            sock.sendall(msg)
            sock.close()
            print("📤 진단 정보 전송 완료")
        except Exception as e:
            print(f"❗ 진단 정보 전송 실패: {e}")
        
        # 테스트 변수들
        a = 11
        b = 22
        c = ['lambda', 'debug']
        
        print(f"\n=== 첫 번째 중단점 (변수 a={a}, b={b}) ===")
        debugpy.breakpoint()  # 여기서 변수를 확인해보세요
        
        print(f"=== 두 번째 중단점 ===")
        debugpy.breakpoint()  # 여기서도 확인
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "진단 완료",
                "diagnostic_sent": True
            }),
        }
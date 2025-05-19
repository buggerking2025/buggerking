# import debugpy
# from complexclass import ComplexClass

# # Start the debugger
# debugpy.connect(('localhost', 5678))  # Connect to the debugger
# debugpy.wait_for_client()  # Wait for the debugger to attach
# debugpy.breakpoint()  # Set a breakpoint here

# comp_class = ComplexClass(name="Example", values=[1, 2, 3])
# comp_class.add_value(4)
# comp_class.add_tag("example")
# comp_class.add_child(ComplexClass(name="Child", values=[5, 6]))

# a = 11
# b = 22
# c = ['a', 'b']

# # Your main code goes here
# print("Hello, World!")
# # You can set breakpoints in your code here
# for i in range(5):
#     print(f"Count: {i}")
#     debugpy.breakpoint()  # Set a breakpoint here
# main.py
"""
debug_tracer 모듈을 사용한 예제 코드
디버깅 세션 중 자동으로 상태를 캡처하고 저장합니다.
"""

# 가장 먼저 디버그 추적 모듈을 import
import debug_tracer

# 디버깅 추적 시작
# debug_tracer.start_tracing(auto_save=True, save_interval=1.0)

# debugpy import
import debugpy
import time
import random
from complexclass import ComplexClass

# 디버거 설정
debugpy.connect(('localhost', 5678))
debugpy.wait_for_client()
debugpy.breakpoint()  # 첫 번째 중단점

# ComplexClass 인스턴스 생성
comp_class = ComplexClass(name="Example", values=[1, 2, 3])
comp_class.add_value(4)
comp_class.add_tag("example")
comp_class.add_child(ComplexClass(name="Child", values=[5, 6]))

# 테스트 변수
a = 11
b = 22
c = ['a', 'b']
d = {'name': 'test', 'value': 123}

# 디버거 중단점 없이도 상태 캡처
debug_tracer.capture_state(reason="변수 초기화 후")

# 단순 계산 함수
def calculate(x, y):
    """간단한 계산 수행"""
    result = x * y
    debugpy.breakpoint()  # 함수 내 중단점
    return result + random.randint(1, 10)

# 재귀 함수
def factorial(n):
    """재귀 함수 예제"""
    if n <= 1:
        return 1
    
    # n이 3일 때 중단점
    if n == 3:
        debugpy.breakpoint()
    
    return n * factorial(n - 1)

# 예외 발생 함수
def divide(x, y):
    """0으로 나누기 시 예외 발생"""
    try:
        result = x / y
        return result
    except ZeroDivisionError as e:
        # 예외 발생 시 상태 캡처
        debug_tracer.capture_state(reason="divide_by_zero")
        raise e

# 함수 호출
print("계산 함수 호출...")
result = calculate(a, b)
print(f"계산 결과: {result}")

# 재귀 함수 호출
print("\n재귀 함수 호출...")
factorial_result = factorial(5)
print(f"factorial(5) = {factorial_result}")

# 반복문 실행
print("\n반복문 실행...")
for i in range(3):
    print(f"반복 {i+1}/3")
    c.append(f"item_{i}")
    time.sleep(0.5)
    debugpy.breakpoint()  # 반복문 내 중단점

# 예외 처리 테스트
print("\n예외 처리 테스트...")
try:
    # 0으로 나누기 시도
    divide(10, 0)
except ZeroDivisionError:
    print("0으로 나눌 수 없습니다.")

# 세션 정보 출력
print("\n디버그 세션 정보:")
summary = debug_tracer.get_debug_summary()
if summary:
    print(f"시작 시간: {summary['start_time']}")
    print(f"저장된 상태 수: {summary['total_states']}")
    print(f"최신 상태 파일: {summary['latest_state_file']}")

# 디버깅 추적 중지
debug_tracer.stop_tracing()
print("\n디버깅 완료!")
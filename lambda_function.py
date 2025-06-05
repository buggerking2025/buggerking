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


        def calculate(a, b):
            """두 숫자를 더하는 간단한 함수입니다."""
            result = a + b
            return result

        a = 10
        b = 20  

        debugpy.breakpoint()  # 중단점 설정
        result = calculate(a, b)
        debugpy.breakpoint()  # 중단점 설정 후 계산 결과 출력
        print(f"계산 결과: {result}")


        for i in range(10):
            print(f"[루프 {i}] 중단점 진입 전")
            debugpy.breakpoint()
            print(f"[루프 {i}] 중단점 통과 후")
            time.sleep(1)

        return {
            "statusCode": 500,
            "body": json.dumps("디버깅 진입"),
        }
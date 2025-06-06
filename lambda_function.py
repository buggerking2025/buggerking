import debugpy
import random
import json
import socket


class Car:
    def __init__(self, make, model):
        self.make = make
        self.model = model

    def start(self):
        print(f"{self.make} {self.model} is starting.")

def calculate_sum(x, y):
    my_car2 = Car("test", "test2")

    def test_function():
        print("This is a test function.")

    if random.randint(0, 10) > 5:
        s = 1

    h = 4
    test_function()
    return x + y

def calculate_product(x, y):
    calculate_sum(x, y)
    return x * y

def lambda_handler(event, context):
    debugpy.breakpoint()
    
    x = 11
    y = 22

    debugpy.connect(("165.194.27.213", 7789))
    debugpy.wait_for_client()
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


    debugpy.breakpoint()

    my_car = Car("Toyota", "Corolla")

    test_var = 42
    test_str = "hello"
    test_list = [1, 2, 3]

    a = 11
    b = 22
    c = a + b
    cdk = calculate_product(11, 22)

    x = random.randint(0, 10)
    if x > 5:
        print("x is greater than 5")
        c = calculate_sum(a, b)

    d = [a, b]

    for i in range(5):
        print(f"Hello, world! {i}")
        debugpy.breakpoint()
        my_car.start()

# Lambda에서 직접 실행되므로 main()은 필요 없음
import debugpy
import json
import random    

class Car:
    def __init__(self, make, model, engine=None): # Added engine=None parameter
        self.make = make
        self.model = model # Model name (string)
        self.engine = engine # Engine object (or None)
        self.__private_variable = "sexy guy"

    def start(self):
        if self.engine:
            print(f"{self.make} {self.model} with {self.engine.fuel} engine is starting.")
        else:
            print(f"{self.make} {self.model} is starting.")
        
class Engine:
    def __init__(self, cc, fuel):
        self.cc = cc
        self.fuel = fuel
        self.__private_variable = "sexy engine"
    

def tempCal(x, y):
    z = x + y
    Ans = z * 10
    return Ans

# global variables
car1 = Car(make="Hyundai", model="Sonata", engine=Engine(2000, "gasoline"))
car2 = Car(make="Kia", model="K5", engine=Engine(1800, "diesel"))
car3 = Car(make="Tesla", model="Model S", engine=Engine(0, "electric")) # Electric car with 0 cc engine
carlist = [car1, car2, car3]


def lambda_handler(event, context):
    print("🚀 Lambda 핸들러 시작!")

    # 재실행일 때
    if (event.get("queryStringParameters", {}) or {}).get("reinvoked") == "true":
        debugpy.connect(("165.194.27.213", 7789))
        debugpy.wait_for_client(context=context, restart=((event.get("queryStringParameters", {}) or {}).get("reinvoked") == "true"))
        debugpy.breakpoint()
        print("Debugpy 재연결 완료!")

    # main Logic
    try:
        my_engine = Engine(1000, "diesel")
        my_car = Car("KIA", "Vehicle", engine=my_engine) # Updated instantiation
        
        testlist = [1, 2, 3, 4, 5]

        a = 11
        b = 22
        c = a - b
        ddd = 0
        cdk = tempCal(11, 22)

        x = random.randint(0, 10)
        if x > 5:
            print("x is greater than 5")
            c = tempCal(a, b)

        d = [a, b]

        x = 1 / ddd   #  예외 발생!

        f = tempCal(a, b)

        new_engine = Engine(1500, "hybrid")
        car4 = Car(make="BMW", model="X5", engine=new_engine) # New car with hybrid engine

        car5 = Car(make="Audi", model="A6", engine=Engine(2000, "gasoline")) # Another car with gasoline engine
        carlist.append(car4)
        carlist.append(car5)
        print(f"Car list: {[f'{car.make} {car.model}' for car in carlist]}")

        print("Lambda 핸들러 로직 완료 1!")
        print("Lambda 핸들러 로직 완료 2!")
        print("Lambda 핸들러 로직 완료 3!")

    except Exception as e:
        debugpy.connect(("165.194.27.213", 7789))
        debugpy.wait_for_client(exception=e, context=context, restart=((event.get("queryStringParameters", {}) or {}).get("reinvoked") == "true"))
        debugpy.breakpoint()

        print("Exception occurred! Debug Mode starts...")
        # pass

    return {
        "statusCode": 200,
        "body": json.dumps("DAP JSON 전송 테스트 완료"),
    }
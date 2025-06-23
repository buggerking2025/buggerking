import debugpy


def debug_decorator(func):
    def _wrapper(event, context):
        # 재실행일 때
        if (event.get("queryStringParameters", {}) or {}).get("reinvoked") == "true":
            debugpy.connect(("165.194.27.222", 7789))
            debugpy.wait_for_client(context=context, event=event)
            debugpy.breakpoint()
            print("Debugpy 재연결 완료!")
        
        try:
            func(event, context)          # debugpy 원본 호출
            
        except Exception as e:
            debugpy.connect(("165.194.27.222", 7789))
            debugpy.wait_for_client(exception=e, context=context, event=event)
            debugpy.breakpoint()

            print("Exception occurred! Debug Mode starts...")
            
    return _wrapper
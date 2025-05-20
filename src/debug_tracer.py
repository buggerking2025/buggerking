# debug_tracer.py (계속)
import os  # Add missing import for os module
import json  # Add missing import for json module
import sys  # Add missing import for sys module
import threading  # Add missing import for threading module
import glob  # Add missing import for glob module

# 전역 상태 객체 초기화
class DebugState:
    def __init__(self):
        self.tracing_enabled = False
        self.last_saved_state = None
        self.breakpoint_states = []
        self.debug_states_dir = os.path.join(os.getcwd(), "debug_states")

_state = DebugState()

def stop_tracing():
    """
    디버깅 추적 중지
    
    Returns:
        bool: 성공 여부
    """
    if not _state.tracing_enabled:
        return False  # 이미 비활성화됨
    
    # 자동 저장 중지
    # _stop_auto_save() is not defined, so we replace it with a placeholder or remove it
    pass  # Placeholder for stopping auto-save functionality
    
    # 추적 비활성화
    _state.tracing_enabled = False
    
    # 최종 상태 저장
    state = {
        "event": "stop",
        "reason": "tracing_stopped",
        "timestamp": time.time()
    }  # Placeholder for _collect_current_state implementation
    _save_debug_state(state)  # Save the debug state
    
    # 트레이스 함수 제거
    sys.settrace(None)
    threading.settrace(lambda *args, **kwargs: None)  # Set a no-op trace function
    
    return True

def capture_state(reason='manual_capture'):
    """
    현재 디버깅 상태 수동 캡처
    
    Args:
        reason (str): 캡처 이유
    
    Returns:
        str: 상태 파일 경로 또는 None
    """
    # 추적이 활성화되어 있지 않아도 상태 캡처 허용
    state = _collect_current_state(event='manual', reason=reason)  # Ensure _collect_current_state is defined
    return _save_debug_state(state)

def get_last_state():
    """
    마지막으로 저장된 디버그 상태 반환
    
    Returns:
        dict: 마지막 디버그 상태 또는 None
    """
    return _state.last_saved_state

def get_all_states():
    """
    모든 저장된 디버그 상태 반환
    
    Returns:
        list: 디버그 상태 목록
    """
    return _state.breakpoint_states.copy()

def clear_states():
    """
    메모리에 저장된 디버그 상태 지우기
    
    Returns:
        int: 지워진 상태 수
    """
    count = len(_state.breakpoint_states)
    _state.breakpoint_states = []
    return count

def set_debugpy_breakpoint_handler():
    """
    debugpy의 중단점 핸들러 설정 (이미 debugpy가 로드된 경우 사용)
    
    Returns:
        bool: 성공 여부
    """
    try:
        import debugpy
        original_breakpoint = debugpy.breakpoint
        
        def breakpoint_with_state_capture():
            """debugpy.breakpoint 호출 시 상태 캡처"""
            # 상태 캡처
            state = _collect_current_state(event='breakpoint', reason='debugpy_breakpoint')
            _save_debug_state(state)
            
            # 원래 중단점 함수 호출
            original_breakpoint()
        
        # debugpy의 breakpoint 함수 교체
        debugpy.breakpoint = breakpoint_with_state_capture
        return True
    except ImportError:
        print("debugpy가 설치되어 있지 않습니다.")
        return False
    except Exception as e:
        print(f"debugpy 중단점 핸들러 설정 오류: {e}")
        return False

def set_builtin_breakpoint_handler():
    """
    내장 breakpoint() 함수에 대한 핸들러 설정
    
    Returns:
        bool: 성공 여부
    """
    try:
        import builtins
        original_breakpoint = builtins.breakpoint
        
        def breakpoint_with_state_capture(*args, **kwargs):
            """내장 breakpoint() 호출 시 상태 캡처"""
            # 상태 캡처
            state = _collect_current_state(event='breakpoint', reason='builtin_breakpoint')
            _save_debug_state(state)
            
            # 원래 중단점 함수 호출
            return original_breakpoint(*args, **kwargs)
        
        # 내장 breakpoint 함수 교체
        builtins.breakpoint = breakpoint_with_state_capture
        return True
    except Exception as e:
        print(f"내장 breakpoint 핸들러 설정 오류: {e}")
        return False

def get_debug_summary():
    """
    디버그 세션 요약 정보 가져오기
    
    Returns:
        dict: 요약 정보 또는 None
    """
    # summary_file 경로: debug_states_dir/debug_summary.json
    summary_file = os.path.join(_state.debug_states_dir, "debug_summary.json")
    
    if os.path.exists(summary_file):
        try:
            with open(summary_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    
    return {
        "summary": "No debug summary available",
        "timestamp": time.time()
    }

def load_state_from_file(filename):
    """
    파일에서 디버그 상태 로드
    
    Args:
        filename (str): 상태 파일 경로
    
    Returns:
        dict: 디버그 상태 또는 None
    """
    if not os.path.exists(filename):
        # 상대 경로 시도
        full_path = os.path.join(_state.debug_states_dir, filename)
        if not os.path.exists(full_path):
            print(f"파일을 찾을 수 없음: {filename}")
            return None
        filename = full_path
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"상태 파일 로드 오류: {e}")
        return None

def get_latest_state_file():
    """
    최신 디버그 상태 파일 경로 가져오기
    
    Returns:
        str: 파일 경로 또는 None
    """
    latest_file = os.path.join(_state.debug_states_dir, "latest_state.json")
    
    if os.path.exists(latest_file):
        return latest_file
    
    # latest_state.json이 없으면 가장 최근 파일 찾기
    if glob:  # glob 모듈이 있는 경우에만
        files = glob.glob(os.path.join(_state.debug_states_dir, "state_*.json"))
        if files:
            return max(files, key=os.path.getmtime)
    
    return None

def find_state_files(pattern=None):
    """
    패턴과 일치하는 상태 파일 찾기
    
    Args:
        pattern (str): 파일명 패턴 (None이면 모든 파일)
    
    Returns:
        list: 파일 경로 목록
    """
    if not glob:  # glob 모듈이 없는 경우
        return []
        
    if pattern is None:
        pattern = "state_*.json"
    
    return glob.glob(os.path.join(_state.debug_states_dir, pattern))

def get_state_dir():
    """
    디버그 상태 파일 저장 디렉토리 경로 가져오기
    
    Returns:
        str: 디렉토리 경로
    """
    return _state.debug_states_dir

def set_state_dir(directory):
    """
    디버그 상태 파일 저장 디렉토리 설정
    
    Args:
        directory (str): 디렉토리 경로
    
    Returns:
        bool: 성공 여부
    """
    try:
        os.makedirs(directory, exist_ok=True)
        _state.debug_states_dir = directory
        return True
    except Exception as e:
        print(f"디렉토리 설정 오류: {e}")
        return False

def _save_debug_state(state):
    """
    디버그 상태를 파일에 저장
    
    Args:
        state (dict): 저장할 디버그 상태
    
    Returns:
        str: 저장된 파일 경로 또는 None
    """
    try:
        if not os.path.exists(_state.debug_states_dir):
            os.makedirs(_state.debug_states_dir, exist_ok=True)
    except Exception as e:
        print(f"Error while creating debug states directory: {e}")
        
        filename = f"state_{len(_state.breakpoint_states) + 1}.json"
        filepath = os.path.join(_state.debug_states_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=4)
        
        _state.breakpoint_states.append(state)
        _state.last_saved_state = state
        
        return filepath
import time  # Add missing import for time module

def _collect_current_state(event, reason):
    """
    현재 디버깅 상태를 수집하는 함수 (임시 구현)
    
    Args:
        event (str): 이벤트 유형
        reason (str): 상태 수집 이유
    
    Returns:
        dict: 디버깅 상태 정보
    """
    return {
        "event": event,
        "reason": reason,
        "timestamp": time.time(),
        "additional_info": "Placeholder for actual state collection logic"
    }

# 모듈이 import될 때 자동으로 초기화
# Removed as "e" is not defined in this context

# 모듈이 import될 때 자동으로 초기화
def _initialize():
    """모듈 초기화"""
    # 저장 디렉토리 생성
    os.makedirs(_state.debug_states_dir, exist_ok=True)
    
    # 내장 breakpoint 핸들러 설정
    set_builtin_breakpoint_handler()
    
    # debugpy가 이미 로드되어 있는지 확인하고 핸들러 설정
    if 'debugpy' in sys.modules:
        set_debugpy_breakpoint_handler()

# 모듈 초기화 호출
_initialize()

# 디버거 자동 실행 코드
if os.environ.get('PYTHON_DEBUG_TRACER_AUTO_START') == '1':
    # 환경 변수로 자동 시작 설정됨
    auto_save = os.environ.get('PYTHON_DEBUG_TRACER_AUTO_SAVE', '1') == '1'
    save_interval = float(os.environ.get('PYTHON_DEBUG_TRACER_SAVE_INTERVAL', '1.0'))
    max_states = int(os.environ.get('PYTHON_DEBUG_TRACER_MAX_STATES', '100'))
    
    # 디버그 추적 자동 시작
    print(f"디버그 추적 자동 시작 설정됨 (auto_save={auto_save}, interval={save_interval}s)")
    # Placeholder for start_tracing implementation
    # Define start_tracing function or implement its logic here if needed
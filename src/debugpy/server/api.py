# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

import codecs
import inspect
import os
import pydevd
import socket
import sys
import threading
import traceback # Ensure traceback is imported for the custom logic block
# for changing variable 기능
import json as std_json
import importlib.util
from ast import literal_eval

# for struct.pack
import struct

# This class and functions are used to parse the capture_data json file
from dataclasses import dataclass, field
from typing import List, Optional, Any

import debugpy
from debugpy import adapter
from debugpy.common import json, log, sockets
from _pydevd_bundle.pydevd_constants import get_global_debugger
from _pydevd_bundle.pydevd_net_command import NetCommand # Ensure this import is present
from pydevd_file_utils import absolute_path
from debugpy.common.util import hide_debugpy_internals
from _pydevd_bundle.pydevd_vars import change_attr_expression
from _pydevd_bundle.pydevd_process_net_command_json import PyDevJsonCommandProcessor
from _pydevd_bundle._debug_adapter import pydevd_schema
from _pydevd_bundle.pydevd_constants import get_thread_id
            

_tls = threading.local()

# TODO: "gevent", if possible.
_config = {
    "qt": "none",
    "subProcess": True,
    "python": sys.executable,
    "pythonEnv": {},
}

_config_valid_values = {
    # If property is not listed here, any value is considered valid, so long as
    # its type matches that of the default value in _config.
    "qt": ["auto", "none", "pyside", "pyside2", "pyqt4", "pyqt5"],
}

# This must be a global to prevent it from being garbage collected and triggering
# https://bugs.python.org/issue37380.
_adapter_process = None


def _settrace(*args, **kwargs):
    log.debug("pydevd.settrace(*{0!r}, **{1!r})", args, kwargs)
    # The stdin in notification is not acted upon in debugpy, so, disable it.
    kwargs.setdefault("notify_stdin", False)
    try:
        pydevd.settrace(*args, **kwargs)
    except Exception:
        raise


def ensure_logging():
    """Starts logging to log.log_dir, if it hasn't already been done."""
    if ensure_logging.ensured:
        return
    ensure_logging.ensured = True
    log.to_file(prefix="debugpy.server")
    log.describe_environment("Initial environment:")
    if log.log_dir is not None:
        pydevd.log_to(log.log_dir + "/debugpy.pydevd.log")


ensure_logging.ensured = False


def log_to(path):
    if ensure_logging.ensured:
        raise RuntimeError("logging has already begun")

    log.debug("log_to{0!r}", (path,))
    if path is sys.stderr:
        log.stderr.levels |= set(log.LEVELS)
    else:
        log.log_dir = path


def configure(properties=None, **kwargs):
    ensure_logging()
    log.debug("configure{0!r}", (properties, kwargs))

    if properties is None:
        properties = kwargs
    else:
        properties = dict(properties)
        properties.update(kwargs)

    for k, v in properties.items():
        if k not in _config:
            raise ValueError("Unknown property {0!r}".format(k))
        expected_type = type(_config[k])
        if type(v) is not expected_type:
            raise ValueError("{0!r} must be a {1}".format(k, expected_type.__name__))
        valid_values = _config_valid_values.get(k)
        if (valid_values is not None) and (v not in valid_values):
            raise ValueError("{0!r} must be one of: {1!r}".format(k, valid_values))
        _config[k] = v


def _starts_debugging(func):
    def debug(address, **kwargs):
        try:
            _, port = address
        except Exception:
            port = address
            address = ("127.0.0.1", port)
        try:
            port.__index__()  # ensure it's int-like
        except Exception:
            raise ValueError("expected port or (host, port)")
        if not (0 <= port < 2**16):
            raise ValueError("invalid port number")

        ensure_logging()
        log.debug("{0}({1!r}, **{2!r})", func.__name__, address, kwargs)
        log.info("Initial debug configuration: {0}", json.repr(_config))

        qt_mode = _config.get("qt", "none")
        if qt_mode != "none":
            pydevd.enable_qt_support(qt_mode)

        settrace_kwargs = {
            "suspend": False,
            "patch_multiprocessing": _config.get("subProcess", True),
        }

        if hide_debugpy_internals():
            debugpy_path = os.path.dirname(absolute_path(debugpy.__file__))
            settrace_kwargs["dont_trace_start_patterns"] = (debugpy_path,)
            settrace_kwargs["dont_trace_end_patterns"] = (str("debugpy_launcher.py"),)

        try:
            return func(address, settrace_kwargs, **kwargs)
        except Exception:
            log.reraise_exception("{0}() failed:", func.__name__, level="info")

    return debug


@_starts_debugging
def listen(address, settrace_kwargs, in_process_debug_adapter=False):
    # Errors below are logged with level="info", because the caller might be catching
    # and handling exceptions, and we don't want to spam their stderr unnecessarily.

    if listen.called:
        # Multiple calls to listen() cause the debuggee to hang
        raise RuntimeError("debugpy.listen() has already been called on this process")

    if in_process_debug_adapter:
        host, port = address
        log.info("Listening: pydevd without debugpy adapter: {0}:{1}", host, port)
        settrace_kwargs["patch_multiprocessing"] = False
        _settrace(
            host=host,
            port=port,
            wait_for_ready_to_run=False,
            block_until_connected=False,
            **settrace_kwargs
        )
        return

    import subprocess

    server_access_token = codecs.encode(os.urandom(32), "hex").decode("ascii")

    try:
        endpoints_listener = sockets.create_server("127.0.0.1", 0, timeout=30)
    except Exception as exc:
        log.swallow_exception("Can't listen for adapter endpoints:")
        raise RuntimeError("can't listen for adapter endpoints: " + str(exc))

    try:
        endpoints_host, endpoints_port = endpoints_listener.getsockname()
        log.info(
            "Waiting for adapter endpoints on {0}:{1}...",
            endpoints_host,
            endpoints_port,
        )

        host, port = address
        adapter_args = [
            _config.get("python", sys.executable),
            os.path.dirname(adapter.__file__),
            "--for-server",
            str(endpoints_port),
            "--host",
            host,
            "--port",
            str(port),
            "--server-access-token",
            server_access_token,
        ]
        if log.log_dir is not None:
            adapter_args += ["--log-dir", log.log_dir]
        log.info("debugpy.listen() spawning adapter: {0}", json.repr(adapter_args))

        # On Windows, detach the adapter from our console, if any, so that it doesn't
        # receive Ctrl+C from it, and doesn't keep it open once we exit.
        creationflags = 0
        if sys.platform == "win32":
            creationflags |= 0x08000000  # CREATE_NO_WINDOW
            creationflags |= 0x00000200  # CREATE_NEW_PROCESS_GROUP

        # On embedded applications, environment variables might not contain
        # Python environment settings.
        python_env = _config.get("pythonEnv")
        if not bool(python_env):
            python_env = None

        # Adapter will outlive this process, so we shouldn't wait for it. However, we
        # need to ensure that the Popen instance for it doesn't get garbage-collected
        # by holding a reference to it in a non-local variable, to avoid triggering
        # https://bugs.python.org/issue37380.
        try:
            global _adapter_process
            _adapter_process = subprocess.Popen(
                adapter_args,
                close_fds=True,
                creationflags=creationflags,
                env=python_env,
            )
            if os.name == "posix":
                # It's going to fork again to daemonize, so we need to wait on it to
                # clean it up properly.
                _adapter_process.wait()
            else:
                # Suppress misleading warning about child process still being alive when
                # this process exits (https://bugs.python.org/issue38890).
                _adapter_process.returncode = 0
                pydevd.add_dont_terminate_child_pid(_adapter_process.pid)
        except Exception as exc:
            log.swallow_exception("Error spawning debug adapter:", level="info")
            raise RuntimeError("error spawning debug adapter: " + str(exc))

        try:
            sock, _ = endpoints_listener.accept()
            try:
                sock.settimeout(None)
                sock_io = sock.makefile("rb", 0)
                try:
                    endpoints = json.loads(sock_io.read().decode("utf-8"))
                finally:
                    sock_io.close()
            finally:
                sockets.close_socket(sock)
        except socket.timeout:
            log.swallow_exception(
                "Timed out waiting for adapter to connect:", level="info"
            )
            raise RuntimeError("timed out waiting for adapter to connect")
        except Exception as exc:
            log.swallow_exception("Error retrieving adapter endpoints:", level="info")
            raise RuntimeError("error retrieving adapter endpoints: " + str(exc))

    finally:
        endpoints_listener.close()

    log.info("Endpoints received from adapter: {0}", json.repr(endpoints))

    if "error" in endpoints:
        raise RuntimeError(str(endpoints["error"]))

    try:
        server_host = str(endpoints["server"]["host"])
        server_port = int(endpoints["server"]["port"])
        client_host = str(endpoints["client"]["host"])
        client_port = int(endpoints["client"]["port"])
    except Exception as exc:
        log.swallow_exception(
            "Error parsing adapter endpoints:\n{0}\n",
            json.repr(endpoints),
            level="info",
        )
        raise RuntimeError("error parsing adapter endpoints: " + str(exc))
    log.info(
        "Adapter is accepting incoming client connections on {0}:{1}",
        client_host,
        client_port,
    )

    _settrace(
        host=server_host,
        port=server_port,
        wait_for_ready_to_run=False,
        block_until_connected=True,
        access_token=server_access_token,
        **settrace_kwargs
    )
    log.info("pydevd is connected to adapter at {0}:{1}", server_host, server_port)
    listen.called = True
    return client_host, client_port

listen.called = False


@_starts_debugging
def connect(address, settrace_kwargs, access_token=None):
    host, port = address
    _settrace(host=host, port=port, client_access_token=access_token, **settrace_kwargs)


# jump_to function for buggerking
def jump_to(pydb, file_path, target_line):
    
    """
    Jump to a specific line in a file using the debugger.
    
    :param pydb: The debugger instance.
    :param file_path: The path to the file.
    :param target_line: The line number to jump to.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    # Ensure the debugger is set up correctly
    if pydb is None:
        raise RuntimeError("Debugger instance is not available. Ensure settrace() was called.")
    
    
    print("Custom logic: Starting jump to cursor test.")
    try:
        # JSON 명령 처리용 프로세서
        json_processor = PyDevJsonCommandProcessor(
            from_json=lambda data, cls=None: cls(**data) if cls else data
        )
        print("Custom logic: Using a new instance of PyDevJsonCommandProcessor for test.")
        
        # GotoTargets 요청 시퀀스 증가
        NetCommand.next_seq += 2
        seq_gototargets = NetCommand.next_seq

        # GotoTargetsArguments 생성
        gototargets_args = pydevd_schema.GotoTargetsArguments(
            source=pydevd_schema.Source(
                path=file_path,
                name=os.path.basename(file_path)
            ),
            line=target_line
        )
        gototargets_request = pydevd_schema.GotoTargetsRequest(
            arguments=gototargets_args,
            seq=seq_gototargets,
            type='request',
            command='gotoTargets'
        )
        print(f"Custom logic: Calling on_gototargets_request (seq: {seq_gototargets}) "
              f"for {file_path} line {target_line}")
        
        # 실제 요청 실행
        net_command_gototargets = json_processor.on_gototargets_request(pydb, gototargets_request)

        # 응답 확인
        if not (net_command_gototargets and hasattr(net_command_gototargets, 'as_dict')):
            print(f"Custom logic: No valid response object from on_gototargets_request. "
                  f"Command object: {net_command_gototargets}")
            return

        response_payload = net_command_gototargets.as_dict
        success = response_payload.get('success', False)
        if not success:
            print(f"Custom logic: on_gototargets_request failed. "
                  f"Success: {success}, Message: {response_payload.get('message')}")
            return

        body = response_payload.get('body')
        if not body or not body.get('targets'):
            print(f"Custom logic: No goto targets found in response body: {body}")
            return

        # 첫 번째 타겟 정보 꺼내기
        first_target: dict = body['targets'][0]
        target_obj = pydevd_schema.GotoTarget(**first_target)
        target_id = target_obj.id
        print(f"Custom logic: Got goto target ID: {target_id} "
              f"(label: '{target_obj.label}', line: {target_obj.line})")

        # 현재 스레드의 DAP threadId 얻기
        dap_thread_id = get_thread_id(threading.current_thread())
        if dap_thread_id is None:
            print("Custom logic: Critical - DAP thread ID not found. Cannot proceed with on_goto_request.")
            return
        print(f"Custom logic: DAP thread ID finally obtained: {dap_thread_id}. Proceeding with on_goto_request.")

        # Goto 요청 생성 및 전송
        NetCommand.next_seq += 2
        seq_goto = NetCommand.next_seq
        goto_args = pydevd_schema.GotoArguments(
            threadId=dap_thread_id,
            targetId=target_id
        )
        goto_request = pydevd_schema.GotoRequest(
            arguments=goto_args,
            seq=seq_goto,
            type='request',
            command='goto'
        )
        print(f"Custom logic: Calling on_goto_request (seq: {seq_goto}) for target ID {target_id} "
              f"on DAP thread {dap_thread_id}")
        net_command_goto = json_processor.on_goto_request(pydb, goto_request)

        if not (net_command_goto and hasattr(net_command_goto, 'as_dict')):
            print(f"Custom logic: No valid response dict from on_goto_request. Command object: {net_command_goto}")
            return

        response_goto = net_command_goto.as_dict
        if response_goto.get('success'):
            print("Custom logic: Jump to cursor successfully initiated by on_goto_request.")
        else:
            print(f"Custom logic: on_goto_request indicates failure: {response_goto.get('message')}")

    except ImportError as imp_err:
        print(f"Custom logic: ImportError during jump to cursor test: {imp_err}\n{traceback.format_exc()}")
    except Exception as gen_err:
        print(f"Custom logic: General exception in jump to cursor test block: {gen_err}\n{traceback.format_exc()}")

def parse(variable_data: dict) -> list:
    if variable_data['name'] == 'function variables':
        return []

    if variable_data['type'] in ('int', 'float', 'bool', 'str'):
        return [[variable_data['evaluateName'], variable_data['value']]]
    
    elif variable_data['type'] in ('list', 'tuple', 'set'):
        return [
            [variable_data['evaluateName'], f'{variable_data["type"]}(range({len(variable_data["value"][1:-1].split(","))}))'],
            *[
                item
                for var in variable_data['recursive_children']
                for item in parse(var)
                if var['name'] not in ('function variables', 'len()', 'keys()', 'values()', 'items()')
            ]
        ]
    
    elif variable_data['type'] == 'dict':
        return [
            [variable_data['evaluateName'], f'{variable_data["type"]}()'],
            *[
                item
                for var in variable_data['recursive_children']
                for item in parse(var)
                if var['name'] not in ('function variables', 'len()', 'keys()', 'values()', 'items()')
            ]
        ]
    
    else:  # Class
        return [
            [variable_data['evaluateName'], f'{variable_data["type"]}.__new__({variable_data["type"]})'],
            *[
                item
                for var in variable_data['recursive_children']
                for item in parse(var)
            ]
        ]
    
def send_dap_message(sock, data, message_type_str: str):
    """
    지정된 타입과 데이터를 사용하여 고정 크기 헤더와 가변 크기 바디로 구성된 메시지를 전송합니다.
    헤더는 4바이트 메시지 타입 문자열과 4바이트 바디 크기 정수로 구성됩니다. (총 8바이트 헤더)
    수신측에서는 이 헤더를 먼저 읽고 파싱하여 바디의 크기를 알아낸 후, 해당 크기만큼 바디를 읽습니다.

    :param sock: 소켓 객체
    :param data: 전송할 데이터 (dict만 지원 - 자동으로 JSON 변환됨)
    :param message_type_str: 메시지 타입을 나타내는 4자리 문자열 (예: "TIME", "SHUT", "CAPT").
                             4자보다 짧으면 공백으로 패딩되고, 길면 4자로 절단됩니다.
    :return: 성공 시 True, 실패 시 False
    """
    try:
        # 모든 데이터는 dict → JSON으로 처리 (프로토콜 단순화)
        if isinstance(data, dict):
            body_bytes = std_json.dumps(data).encode('utf-8')
        else:
            error_msg = f"Unsupported data type: {type(data)}. Only dict is supported (automatically converted to JSON)."
            print(f"❌ [DAP-SEND] 데이터 타입 오류 ({message_type_str}): {error_msg}")
            raise TypeError(error_msg)

        body_length = len(body_bytes)

        # 헤더 생성 (총 8바이트)
        # 1. 메시지 타입 (4바이트 ASCII)
        type_str_fixed_length = message_type_str.ljust(4)[:4]
        type_bytes_for_header = type_str_fixed_length.encode('ascii')

        # 2. 바디 길이 (4바이트 big-endian unsigned integer)
        body_length_bytes = struct.pack('>I', body_length)

        header_bytes = type_bytes_for_header + body_length_bytes
        
        message_to_send = header_bytes + body_bytes
        sock.sendall(message_to_send)
        
        total_sent = len(message_to_send)
        print(f"📤 [DAP-SEND] '{message_type_str}' 전송 완료: header={len(header_bytes)}B, body={body_length}B. 총 {total_sent}B.")
        return True
        
    except TypeError: 
        return False 
    except Exception as e:
        print(f"❌ [DAP-SEND] '{message_type_str}' 전송 실패 (오류: {type(e).__name__}): {e}")
        return False

def receive_dap_message(conn):
    """
    고정 크기 헤더와 가변 크기 바디로 구성된 메시지를 수신합니다.
    헤더는 4바이트 메시지 타입 문자열과 4바이트 바디 크기 정수로 구성됩니다. (총 8바이트 헤더)
    
    :param conn: 소켓 연결 객체
    :return: 성공 시 (message_type, data) 튜플, 실패 시 None
    """
    try:
        # 1단계: 헤더 8바이트 수신
        header_bytes = _receive_exact_bytes(conn, 8)
        if header_bytes is None:
            print("❌ [DAP-RECV] 헤더 수신 실패")
            return None
        
        # 2단계: 헤더 파싱
        # 메시지 타입 (4바이트 ASCII)
        type_bytes = header_bytes[:4]
        message_type = type_bytes.decode('ascii').rstrip()  # 오른쪽 공백 제거
        
        # 바디 길이 (4바이트 big-endian unsigned integer)
        body_length_bytes = header_bytes[4:8]
        body_length = struct.unpack('>I', body_length_bytes)[0]
        
        print(f"📥 [DAP-RECV] 헤더 파싱 완료: type='{message_type}', body_length={body_length}B")
        
        # 3단계: 바디 수신 (길이가 0이면 빈 바이트)
        if body_length == 0:
            body_bytes = b''
        else:
            body_bytes = _receive_exact_bytes(conn, body_length)
            if body_bytes is None:
                print(f"❌ [DAP-RECV] 바디 수신 실패 (예상: {body_length}B)")
                return None
        
        # 4단계: JSON 데이터 변환
        json_str = body_bytes.decode('utf-8')
        data = json.loads(json_str)
        
        total_received = 8 + body_length
        print(f"📥 [DAP-RECV] '{message_type}' 수신 완료: header=8B, body={body_length}B. 총 {total_received}B.")
        
        return (message_type, data)
        
    except std_json.JSONDecodeError as e:
        print(f"❌ [DAP-RECV] JSON 파싱 실패: {e}")
        return None
    except UnicodeDecodeError as e:
        print(f"❌ [DAP-RECV] UTF-8 디코딩 실패: {e}")
        return None
    except Exception as e:
        print(f"❌ [DAP-RECV] 수신 실패 (오류: {type(e).__name__}): {e}")
        return None

def _receive_exact_bytes(sock, num_bytes):
    """
    소켓에서 정확히 지정된 바이트 수만큼 데이터를 수신합니다.
    TCP의 특성상 한 번의 recv() 호출로 모든 데이터가 오지 않을 수 있으므로 반복해서 수신합니다.
    
    :param sock: 소켓 객체
    :param num_bytes: 수신할 바이트 수
    :return: 성공 시 바이트 데이터, 실패 시 None
    """
    received_data = b''
    remaining_bytes = num_bytes
    
    while remaining_bytes > 0:
        try:
            chunk = sock.recv(remaining_bytes)
            if not chunk:  # 연결이 닫힌 경우
                print(f"❌ [DAP-RECV] 연결 종료됨 (수신된: {len(received_data)}B, 예상: {num_bytes}B)")
                return None
            
            received_data += chunk
            remaining_bytes -= len(chunk)
            
        except Exception as e:
            print(f"❌ [DAP-RECV] 바이트 수신 오류: {e}")
            return None
    
    return received_data

def _decode_message_data(message_type, body_bytes):
    """
    메시지 타입에 따라 바이트 데이터를 적절한 Python 객체로 변환합니다.
    
    :param message_type: 메시지 타입 문자열
    :param body_bytes: 바디 바이트 데이터
    :return: 변환된 데이터 또는 None (실패 시)
    """
    try:
        # JSON 형태의 데이터
        if message_type.upper() in ['JSON', 'JSNO', 'JSN']:  # 오타 허용
            if len(body_bytes) == 0:
                return {}
            json_str = body_bytes.decode('utf-8')
            return std_json.loads(json_str)
        
        # 텍스트 데이터
        elif message_type.upper() in ['TEXT', 'TXT', 'STR']:
            return body_bytes.decode('utf-8')
        
        # 바이너리 데이터 (그대로 반환)
        elif message_type.upper() in ['BIN', 'BYTE', 'RAW']:
            return body_bytes
        
        # 기본값: UTF-8 텍스트로 시도, 실패하면 바이너리로 반환
        else:
            try:
                return body_bytes.decode('utf-8')
            except UnicodeDecodeError:
                print(f"⚠️ [DAP-RECV] '{message_type}' UTF-8 디코딩 실패, 바이너리로 반환")
                return body_bytes
                
    except std_json.JSONDecodeError as e:
        print(f"❌ [DAP-RECV] JSON 파싱 실패: {e}")
        return None
    except UnicodeDecodeError as e:
        print(f"❌ [DAP-RECV] UTF-8 디코딩 실패: {e}")
        return None
    except Exception as e:
        print(f"❌ [DAP-RECV] 데이터 변환 실패: {e}")
        return None

def request_previous_state(reinvoked=False):
    """개발자 PC에서 이전 디버깅 상태 요청 - DAP 방식"""
    print("🔄 [REQUEST-STATE] 이전 디버깅 상태 요청 시작...")
    if reinvoked:
        print("🔁 [REQUEST-STATE] 재호출로 인한 상태 복구 시도")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)  # 10초 타임아웃
            
            print("🔄 [REQUEST-STATE] 개발자 PC에 연결 중... (165.194.27.213:6689)")
            sock.connect(("165.194.27.213", 6689))
            print("✅ [REQUEST-STATE] 연결 성공!")
            
            # DAP 방식으로 응답 수신
            response_data = receive_dap_message(sock)
            
            sock.close()
            
            if response_data:
                print(f"🎉 [REQUEST-STATE] 총 {len(response_data)} bytes 수신 완료!")
                
                try:
                    response_json = std_json.loads(response_data.decode('utf-8'))
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
        

class wait_for_client:
    def __call__(self, exception=None, context=None, restart=False):
        ensure_logging()
        log.debug("wait_for_client()")
        print("wait_for_client() called with exception:", exception, "and restart:", restart)

        # <--- 여기에 사용자 정의 기능 추가 (대기 시작 전) --- >
        # 예시: 특정 조건 확인 또는 로그 기록
        print("Custom logic: wait_for_client() is about to start waiting.")
        # <--- 사용자 정의 기능 끝 --- >

        pydb = get_global_debugger()
        if pydb is None:
            # This means that settrace() was not called, so there's nothing to wait for.
            # This can happen if listen() or connect() failed.
            log.info("wait_for_client() ignored - settrace() not called")
            return

        cancel_event = threading.Event()
        self.cancel = cancel_event.set
        pydevd._wait_for_attach(cancel=cancel_event)

        # <--- 여기에 사용자 정의 기능 추가 (대기 종료 후) --- >
        # 예시: 클라이언트 연결 성공/실패 또는 취소 시 로그 기록
        if is_client_connected():
            # ---------------------- Custom Logic: send remaining time to developer PC & if restart, receive the capture_data ----------------------
            remaining = context.get_remaining_time_in_millis()
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(30.0)  # 30초 타임아웃 설정
                sock.connect(("165.194.27.213", 6689))  # 개발자 PC IP + 수신 포트
                # 1) remaining_ms 전송 (DAP 방식)
                timeout_data = {"remaining_ms": remaining}
                success = send_dap_message(sock, timeout_data, 'TIME')

                if success:
                    print(f"📤 timeout = {remaining} ms 전송 완료 (DAP)")
                else:
                    print("❌ timeout 전송 실패 (DAP)")
                    sock.close()
                    raise RuntimeError("Timeout 전송 실패 (DAP)")

                
                # 재디버깅인 경우, apply process 시작
                if restart:
                    # capture_data를 수신
                    print("🔄 이전 상태 JSON 응답 대기 중...")
            
                    # DAP 방식으로 JSON 수신
                    result = receive_dap_message(sock)
    
                    if result is not None:
                        data_type, json_data = result
                        print(f"📥 수신 성공: 타입={data_type}")
                    else:
                        print("JSON 수신 실패")
                        sock.close()
                        raise RuntimeError("JSON 수신 실패")
                    
                    # 잘 받았는지 테스트
                    if json_data:
                        # JSON 데이터가 dict 타입인 경우
                        print(f"📥 JSON 데이터 수신 완료: {len(json_data)} bytes ({data_type}), 데이터 타입: {type(json_data)}")
                        
                        try:
                            # parsed_json = std_json.loads(json_data.decode('utf-8'))
                            
                            # print(f"📊 JSON 구조 확인:")
                            # print(f"  - 최상위 키: {list(parsed_json.keys())}")

                            parsed_json = json_data
                            
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
                            
                            print("🎉 DAP JSON 파일 전송 성공!")
                            
                        except std_json.JSONDecodeError as e:
                            print(f"❌ JSON 파싱 실패: {e}")
                            print(f"📋 받은 데이터 (처음 200자): {json_data[:200]}")
                            
                    else:
                        print("❌ JSON 수신 실패")
                        
                    # apply process 시작
                    # json_data를 std_json.load()로 파싱하려면 BytesIO로 감싸야 함
                    # import io
                    # with io.BytesIO(json_data) as f:
                    #     data = std_json.load(f)
                       
                    received_callstacks = json_data.get("callstacks", [])

                    # 원치않은 call stack 프레임 제거
                    capture_callstacks = [frame for frame in received_callstacks if frame.get("line") != -1]                    
                    
                    for i in range(len(capture_callstacks)):
                        function_name = capture_callstacks[i].get("function", "unknown_function")
                        file_name = capture_callstacks[i].get("file", "unknown_file.py")
                        print(f"🔄 복원할 프레임: {function_name} in {file_name}")
                        # ---------------------- jump_to()를 사용하여 각 프레임으로 이동 ---------------------- 
                        try:
                            jump_to(pydb,
                                    file_path=os.path.normpath(os.path.join("/var/task", file_name)),  # 실제 파일 경로로 변경
                                    target_line=capture_callstacks[i].get("line", 1))  # 기본값 1로 설정
                        except Exception as e:
                            print(f"❗ jump_to 실패: {e}")
                            
                        # ---------------------- 각 프레임 변수 복원 ----------------------
                        function_name_from_capture = capture_callstacks[i].get("function")    # lambda_handler
                        if not function_name_from_capture:
                            print(f"Custom logic: Skipping a captured frame because it has no function name. Frame ID: {capture_callstacks[i].get('frame_id')}")
                            continue

                        print(f"Custom logic: Processing captured frame: {function_name_from_capture}")
                        
                        frame_file_name_from_capture = capture_callstacks[i].get('file', 'unknown_file.py')   # lambda_function.py
                        

                        target_frame = None
                        expected_filename = None
                        if frame_file_name_from_capture:
                            # Assuming /var/task is the root for lambda execution environment or similar
                            # This path might need to be made more robust or configurable
                            expected_filename = os.path.normpath(os.path.join("/var/task", frame_file_name_from_capture))
                            print(f"Custom logic: Using inspect.stack(). Expected file for '{function_name_from_capture}': {expected_filename}")

                            # 복원할 프레임을 찾기 위해 현재 스택을 검사
                            try:
                                current_stack = inspect.stack()
                                for frame_info in current_stack:
                                    frame_obj = frame_info.frame if hasattr(frame_info, 'frame') else frame_info[0]
                                    filename_live = os.path.normpath(frame_obj.f_code.co_filename)
                                    func_name_live = frame_obj.f_code.co_name

                                    if func_name_live == function_name_from_capture and filename_live == expected_filename:
                                        print(f"Custom logic: Found target '{function_name_from_capture}' stack frame in '{filename_live}'.")
                                        target_frame = frame_obj
                                        break 
                                    del frame_obj # Clean up frame_obj if not the target
                                del current_stack # Clean up stack
                            except Exception as e:
                                print(f"Custom logic: Error inspecting stack for {function_name_from_capture}: {e}")
                            
                            # target_frame이 발견되면 캡처 데이터를 적용
                            if target_frame:
                                try:
                                    for _var_data in capture_callstacks[i].get('variables', {}).get('locals', []):
                                        for item in parse(_var_data):
                                            print(f"Custom logic: Attempting to change '{item[0]}' to '{item[1]}' in frame {target_frame.f_code.co_name if hasattr(target_frame, 'f_code') else target_frame}.")
                                            change_attr_expression(
                                                target_frame,
                                                item[0],
                                                item[1],
                                                pydb
                                            )
                                    # for _var_data in capture_callstacks[i].get('variables', {}).get('globals', []):
                                    #     for item in parse(_var_data):
                                    #         print(f"Custom logic: Attempting to change global '{item[0]}' to '{item[1]}' in frame {target_frame.f_code.co_name if hasattr(target_frame, 'f_code') else target_frame}.")
                                    #         change_attr_expression(
                                    #             target_frame,
                                    #             item[0],
                                    #             item[1],
                                    #             pydb,
                                    #             is_global=True
                                    #         )
                                except Exception as e:
                                    print(f"Custom logic: Failed to apply capture data to frame {function_name_from_capture}: {e}")
                        else:
                            print(f"Custom logic: frame_file_name_from_capture is not set for {function_name_from_capture}, cannot determine expected_filename.")
                        
                        # ---------------------- 다음 callstack이 있으면 step_in()을 사용하여 다음 프레임으로 이동 ----------------------
                        if i < len(capture_callstacks) - 1:
                            print("Custom logic: Finished jump to cursor test.")                
                            print("Custom logic: Starting step-in test.")

                            try:
                                json_processor = PyDevJsonCommandProcessor(from_json=lambda data, cls=None: cls(**data) if cls else data)
                                print("Custom logic: Using PyDevJsonCommandProcessor for step-in test.")

                                # 현재 스레드의 DAP thread ID 가져오기
                                dap_thread_id = get_thread_id(threading.current_thread())
                                print(f"Custom logic: Current DAP thread ID: {dap_thread_id}")

                                if dap_thread_id is not None:
                                    # Step Into 요청 생성
                                    NetCommand.next_seq += 2
                                    seq_step_in = NetCommand.next_seq
                                    
                                    step_in_args = pydevd_schema.StepInArguments(
                                        threadId=dap_thread_id,
                                        singleThread=True  # 단일 스레드에서만 step
                                    )
                                    
                                    step_in_request = pydevd_schema.StepInRequest(
                                        arguments=step_in_args,
                                        seq=seq_step_in,
                                        type='request',
                                        command='stepIn'
                                    )
                                    
                                    print(f"Custom logic: Calling on_stepin_request (seq: {seq_step_in}) for thread {dap_thread_id}")
                                    net_command_step_in = json_processor.on_stepin_request(pydb, step_in_request)

                                    if net_command_step_in and hasattr(net_command_step_in, 'as_dict'):
                                        response_payload_step_in = net_command_step_in.as_dict
                                        print(f"Custom logic: on_stepin_request response: {std_json.dumps(response_payload_step_in)}")
                                        if response_payload_step_in and response_payload_step_in.get('success'):
                                            print("Custom logic: Step-in command successfully initiated.")
                                        else:
                                            print(f"Custom logic: Step-in failed: {response_payload_step_in.get('message') if response_payload_step_in else 'No response'}")
                                    else:
                                        print(f"Custom logic: No valid response from on_stepin_request. Command object: {net_command_step_in}")
                                else:
                                    print("Custom logic: DAP thread ID not available for step-in test.")

                            except ImportError as e_step_imp:
                                print(f"Custom logic: ImportError during step-in test: {e_step_imp}\\n{traceback.format_exc()}")
                            except Exception as e_step:
                                print(f"Custom logic: Exception during step-in test: {e_step}\\n{traceback.format_exc()}")
                            
                            print("Custom logic: Finished step-in test.")
                        
                
                # if not restart -> jump to the line Exception occurred    
                else:
                    print("🔄 재디버깅이 아니므로 예외 발생 위치로 이동합니다.")
                    # ---------------------- Custom logic: Test jump to cursor ----------------------
                    try: 
                        last = list(traceback.walk_tb(exception.__traceback__))[-1]
                        target_line = last[1]
                        print("라인 번호:", target_line)
                        jump_to(pydb, 
                                file_path=os.path.normpath("/var/task/lambda_function.py"),  # 실제 파일 경로로 변경
                                target_line=target_line)
                    except Exception as e:
                        print(f"❗ jump_to 실패: {e}")
                    
                    # Runtime 시, Exception 객체에서 target_line과 file_path를 추출하여 jump_to() 실행
                    # try:
                    #     if exception is None or not hasattr(exception, '__traceback__') or exception.__traceback__ is None:
                    #         print("❗ 예외 객체 또는 트레이스백 정보가 없어 jump_to를 실행할 수 없습니다.")
                    #     else:
                    #         tb_frames = list(traceback.walk_tb(exception.__traceback__))
                    #         if not tb_frames:
                    #             print("❗ 트레이스백 프레임 정보를 가져올 수 없어 jump_to를 실행할 수 없습니다.")
                    #         else:
                    #             last_frame_info = tb_frames[-1]
                    #             frame_obj = last_frame_info[0]
                    #             target_line = last_frame_info[1]
                    #             print("라인 번호:", target_line)

                    #             # 트레이스백에서 실제 파일 이름 가져오기
                    #             actual_filename_from_tb = frame_obj.f_code.co_filename
                    #             if not actual_filename_from_tb:
                    #                 print("❗ 트레이스백에서 유효한 파일 이름을 얻지 못했습니다.")
                    #             else:
                    #                 # /var/task/를 기준으로 파일 경로 구성 (Lambda 환경 가정)
                    #                 # os.path.basename을 사용하여 co_filename이 절대 경로이거나 상대 경로인 경우에도 올바르게 처리
                    #                 file_basename = os.path.basename(actual_filename_from_tb)
                    #                 file_path_for_jump = os.path.normpath(os.path.join("/var/task", file_basename))
                                    
                    #                 print(f"🔄 예외 발생 위치로 점프 시도: {file_path_for_jump}:{target_line}")
                    #                 jump_to(pydb,
                    #                         file_path=file_path_for_jump,
                    #                         target_line=target_line)
                    # except AttributeError as ae:
                    #     print(f"❗ 트레이스백 정보 접근 중 오류 발생 (jump_to): {ae}")
                    # except IndexError as ie:
                    #     print(f"❗ 트레이스백 프레임 분석 중 오류 발생 (jump_to): {ie}")
                    # except Exception as e: # jump_to 내부 또는 기타 예외 처리
                    #     print(f"❗ jump_to 실패 또는 예외 발생 위치 처리 중 오류: {e}")
                        
                # restart이든 첫 실행이든, 소켓 닫기        
                sock.close()                
                        
            except Exception as e:
                print(f"❗ 전송 실패: {e}")

        else: # This is the else for if is_client_connected()
            print("Custom logic: wait_for_client() finished, but no client connected (possibly cancelled).")        
        # <--- 사용자 정의 기능 끝 --- >
        # debugpy.breakpoint()

    @staticmethod
    def cancel():
        raise RuntimeError("wait_for_client() must be called first")


wait_for_client = wait_for_client()


def is_client_connected():
    return pydevd._is_attached()


def breakpoint():
    ensure_logging()
    if not is_client_connected():
        log.info("breakpoint() ignored - debugger not attached")
        return
    log.debug("breakpoint()")

    # Get the first frame in the stack that's not an internal frame.
    pydb = get_global_debugger()
    stop_at_frame = sys._getframe().f_back
    while (
        stop_at_frame is not None
        and pydb.get_file_type(stop_at_frame) == pydb.PYDEV_FILE
    ):
        stop_at_frame = stop_at_frame.f_back

    _settrace(
        suspend=True,
        trace_only_current_thread=True,
        patch_multiprocessing=False,
        stop_at_frame=stop_at_frame,
    )
    stop_at_frame = None


def debug_this_thread():
    ensure_logging()
    log.debug("debug_this_thread()")

    _settrace(suspend=False)


def trace_this_thread(should_trace):
    ensure_logging()
    log.debug("trace_this_thread({0!r})", should_trace)

    pydb = get_global_debugger()
    if should_trace:
        pydb.enable_tracing()
    else:
        pydb.disable_tracing()

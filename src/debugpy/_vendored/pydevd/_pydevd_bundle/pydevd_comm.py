""" pydevd - a debugging daemon
This is the daemon you launch for python remote debugging.

Protocol:
each command has a format:
    id\tsequence-num\ttext
    id: protocol command number
    sequence-num: each request has a sequence number. Sequence numbers
    originating at the debugger are odd, sequence numbers originating
    at the daemon are even. Every response uses the same sequence number
    as the request.
    payload: it is protocol dependent. When response is a complex structure, it
    is returned as XML. Each attribute value is urlencoded, and then the whole
    payload is urlencoded again to prevent stray characters corrupting protocol/xml encodings

    Commands:

    NUMBER   NAME                     FROM*     ARGUMENTS                     RESPONSE      NOTE
100 series: program execution
    101      RUN                      JAVA      -                             -
    102      LIST_THREADS             JAVA                                    RETURN with XML listing of all threads
    103      THREAD_CREATE            PYDB      -                             XML with thread information
    104      THREAD_KILL              JAVA      id (or * to exit)             kills the thread
                                      PYDB      id                            nofies JAVA that thread was killed
    105      THREAD_SUSPEND           JAVA      XML of the stack,             suspends the thread
                                                reason for suspension
                                      PYDB      id                            notifies JAVA that thread was suspended

    106      CMD_THREAD_RUN           JAVA      id                            resume the thread
                                      PYDB      id \t reason                  notifies JAVA that thread was resumed

    107      STEP_INTO                JAVA      thread_id
    108      STEP_OVER                JAVA      thread_id
    109      STEP_RETURN              JAVA      thread_id

    110      GET_VARIABLE             JAVA      thread_id \t frame_id \t      GET_VARIABLE with XML of var content
                                                FRAME|GLOBAL \t attributes*

    111      SET_BREAK                JAVA      file/line of the breakpoint
    112      REMOVE_BREAK             JAVA      file/line of the return
    113      CMD_EVALUATE_EXPRESSION  JAVA      expression                    result of evaluating the expression
    114      CMD_GET_FRAME            JAVA                                    request for frame contents
    115      CMD_EXEC_EXPRESSION      JAVA
    116      CMD_WRITE_TO_CONSOLE     PYDB
    117      CMD_CHANGE_VARIABLE
    118      CMD_RUN_TO_LINE
    119      CMD_RELOAD_CODE
    120      CMD_GET_COMPLETIONS      JAVA

    200      CMD_REDIRECT_OUTPUT      JAVA      streams to redirect as string -
                                                'STDOUT' (redirect only STDOUT)
                                                'STDERR' (redirect only STDERR)
                                                'STDOUT STDERR' (redirect both streams)

500 series diagnostics/ok
    501      VERSION                  either      Version string (1.0)        Currently just used at startup
    502      RETURN                   either      Depends on caller    -

900 series: errors
    901      ERROR                    either      -                           This is reserved for unexpected errors.

    * JAVA - remote debugger, the java end
    * PYDB - pydevd, the python end
"""

import linecache
import os

from _pydev_bundle.pydev_imports import _queue
from _pydev_bundle._pydev_saved_modules import time, ThreadingEvent
from _pydev_bundle._pydev_saved_modules import socket as socket_module
from _pydevd_bundle.pydevd_constants import (
    DebugInfoHolder,
    IS_WINDOWS,
    IS_JYTHON,
    IS_WASM,
    IS_PY36_OR_GREATER,
    STATE_RUN,
    ASYNC_EVAL_TIMEOUT_SEC,
    get_global_debugger,
    GetGlobalDebugger,
    set_global_debugger,  # Keep for backward compatibility @UnusedImport
    silence_warnings_decorator,
    filter_all_warnings,
    IS_PY311_OR_GREATER,
)
from _pydev_bundle.pydev_override import overrides
import weakref
from _pydev_bundle._pydev_completer import extract_token_and_qualifier
from _pydevd_bundle._debug_adapter.pydevd_schema import (
    VariablesResponseBody,
    SetVariableResponseBody,
    StepInTarget,
    StepInTargetsResponseBody,
)
from _pydevd_bundle._debug_adapter import pydevd_base_schema, pydevd_schema
from _pydevd_bundle.pydevd_net_command import NetCommand
from _pydevd_bundle.pydevd_xml import ExceptionOnEvaluate
from _pydevd_bundle.pydevd_constants import ForkSafeLock, NULL
from _pydevd_bundle.pydevd_daemon_thread import PyDBDaemonThread
from _pydevd_bundle.pydevd_thread_lifecycle import pydevd_find_thread_by_id, resume_threads
from _pydevd_bundle.pydevd_dont_trace_files import PYDEV_FILE
import dis
import pydevd_file_utils
import itertools
from urllib.parse import quote_plus, unquote_plus
import pydevconsole
from _pydevd_bundle import pydevd_vars, pydevd_io, pydevd_reload
from _pydevd_bundle import pydevd_bytecode_utils
from _pydevd_bundle import pydevd_xml
from _pydevd_bundle import pydevd_vm_type
import sys
import traceback
from _pydevd_bundle.pydevd_utils import (
    quote_smart as quote,
    compare_object_attrs_key,
    notify_about_gevent_if_needed,
    isinstance_checked,
    ScopeRequest,
    getattr_checked,
    Timer,
    is_current_thread_main_thread,
)
from _pydev_bundle import pydev_log, fsnotify
from _pydev_bundle.pydev_log import exception as pydev_log_exception
from _pydev_bundle import _pydev_completer

from pydevd_tracing import get_exception_traceback_str
from _pydevd_bundle import pydevd_console
from _pydev_bundle.pydev_monkey import disable_trace_thread_modules, enable_trace_thread_modules
from io import StringIO

# CMD_XXX constants imported for backward compatibility
from _pydevd_bundle.pydevd_comm_constants import *  # @UnusedWildImport

import json
from datetime import datetime
import struct
import threading

# Socket import aliases:
AF_INET, AF_INET6, SOCK_STREAM, SHUT_WR, SOL_SOCKET, IPPROTO_TCP, socket = (
    socket_module.AF_INET,
    socket_module.AF_INET6,
    socket_module.SOCK_STREAM,
    socket_module.SHUT_WR,
    socket_module.SOL_SOCKET,
    socket_module.IPPROTO_TCP,
    socket_module.socket,
)

if IS_WINDOWS and not IS_JYTHON:
    SO_EXCLUSIVEADDRUSE = socket_module.SO_EXCLUSIVEADDRUSE
if not IS_WASM:
    SO_REUSEADDR = socket_module.SO_REUSEADDR


class ReaderThread(PyDBDaemonThread):
    """reader thread reads and dispatches commands in an infinite loop"""

    def __init__(self, sock, py_db, PyDevJsonCommandProcessor, process_net_command, terminate_on_socket_close=True):
        assert sock is not None
        PyDBDaemonThread.__init__(self, py_db)
        self.__terminate_on_socket_close = terminate_on_socket_close

        self.sock = sock
        self._buffer = b""
        self.name = "pydevd.Reader"
        self.process_net_command = process_net_command
        self.process_net_command_json = PyDevJsonCommandProcessor(self._from_json).process_net_command_json

    def _from_json(self, json_msg, update_ids_from_dap=False):
        return pydevd_base_schema.from_json(json_msg, update_ids_from_dap, on_dict_loaded=self._on_dict_loaded)

    def _on_dict_loaded(self, dct):
        for listener in self.py_db.dap_messages_listeners:
            listener.after_receive(dct)

    @overrides(PyDBDaemonThread.do_kill_pydev_thread)
    def do_kill_pydev_thread(self):
        PyDBDaemonThread.do_kill_pydev_thread(self)
        # Note that we no longer shutdown the reader, just the writer. The idea is that we shutdown
        # the writer to send that the communication has finished, then, the client will shutdown its
        # own writer when it receives an empty read, at which point this reader will also shutdown.

        # That way, we can *almost* guarantee that all messages have been properly sent -- it's not
        # completely guaranteed because it's possible that the process exits before the whole
        # message was sent as having this thread alive won't stop the process from exiting -- we
        # have a timeout when exiting the process waiting for this thread to finish -- see:
        # PyDB.dispose_and_kill_all_pydevd_threads()).

        # try:
        #    self.sock.shutdown(SHUT_RD)
        # except:
        #    pass
        # try:
        #    self.sock.close()
        # except:
        #    pass

    def _read(self, size):
        while True:
            buffer_len = len(self._buffer)
            if buffer_len == size:
                ret = self._buffer
                self._buffer = b""
                return ret

            if buffer_len > size:
                ret = self._buffer[:size]
                self._buffer = self._buffer[size:]
                return ret

            try:
                r = self.sock.recv(max(size - buffer_len, 1024))
            except OSError:
                return b""
            if not r:
                return b""
            self._buffer += r

    def _read_line(self):
        while True:
            i = self._buffer.find(b"\n")
            if i != -1:
                i += 1  # Add the newline to the return
                ret = self._buffer[:i]
                self._buffer = self._buffer[i:]
                return ret
            else:
                try:
                    r = self.sock.recv(1024)
                except OSError:
                    return b""
                if not r:
                    return b""
                self._buffer += r

    @overrides(PyDBDaemonThread._on_run)
    def _on_run(self):
        try:
            content_len = -1

            while True:
                # i.e.: even if we received a kill, we should only exit the ReaderThread when the
                # client itself closes the connection (although on kill received we stop actually
                # processing anything read).
                try:
                    notify_about_gevent_if_needed()
                    line = self._read_line()

                    if len(line) == 0:
                        pydev_log.debug("ReaderThread: empty contents received (len(line) == 0).")
                        self._terminate_on_socket_close()
                        return  # Finished communication.

                    if self._kill_received:
                        continue

                    if line.startswith(b"Content-Length:"):
                        content_len = int(line.strip().split(b":", 1)[1])
                        continue

                    if content_len != -1:
                        # If we previously received a content length, read until a '\r\n'.
                        if line == b"\r\n":
                            json_contents = self._read(content_len)

                            content_len = -1

                            if len(json_contents) == 0:
                                pydev_log.debug("ReaderThread: empty contents received (len(json_contents) == 0).")
                                self._terminate_on_socket_close()
                                return  # Finished communication.

                            if self._kill_received:
                                continue

                            # We just received a json message, let's process it.
                            self.process_net_command_json(self.py_db, json_contents)

                        continue
                    else:
                        # No content len, regular line-based protocol message (remove trailing new-line).
                        if line.endswith(b"\n\n"):
                            line = line[:-2]

                        elif line.endswith(b"\n"):
                            line = line[:-1]

                        elif line.endswith(b"\r"):
                            line = line[:-1]
                except:
                    if not self._kill_received:
                        pydev_log_exception()
                        self._terminate_on_socket_close()
                    return  # Finished communication.

                # Note: the java backend is always expected to pass utf-8 encoded strings. We now work with str
                # internally and thus, we may need to convert to the actual encoding where needed (i.e.: filenames
                # on python 2 may need to be converted to the filesystem encoding).
                if hasattr(line, "decode"):
                    line = line.decode("utf-8")

                if DebugInfoHolder.DEBUG_TRACE_LEVEL >= 3:
                    pydev_log.debug("debugger: received >>%s<<\n", line)

                args = line.split("\t", 2)
                try:
                    cmd_id = int(args[0])
                    if DebugInfoHolder.DEBUG_TRACE_LEVEL >= 3:
                        pydev_log.debug("Received command: %s %s\n", ID_TO_MEANING.get(str(cmd_id), "???"), line)
                    self.process_command(cmd_id, int(args[1]), args[2])
                except:
                    if sys is not None and pydev_log_exception is not None:  # Could happen at interpreter shutdown
                        pydev_log_exception("Can't process net command: %s.", line)

        except:
            if not self._kill_received:
                if sys is not None and pydev_log_exception is not None:  # Could happen at interpreter shutdown
                    pydev_log_exception()

            self._terminate_on_socket_close()
        finally:
            pydev_log.debug("ReaderThread: exit")

    def _terminate_on_socket_close(self):
        if self.__terminate_on_socket_close:
            self.py_db.dispose_and_kill_all_pydevd_threads()

    def process_command(self, cmd_id, seq, text):
        self.process_net_command(self.py_db, cmd_id, seq, text)


class FSNotifyThread(PyDBDaemonThread):
    def __init__(self, py_db, api, watch_dirs):
        PyDBDaemonThread.__init__(self, py_db)
        self.api = api
        self.name = "pydevd.FSNotifyThread"
        self.watcher = fsnotify.Watcher()
        self.watch_dirs = watch_dirs

    @overrides(PyDBDaemonThread._on_run)
    def _on_run(self):
        try:
            pydev_log.info("Watching directories for code reload:\n---\n%s\n---" % ("\n".join(sorted(self.watch_dirs))))

            # i.e.: The first call to set_tracked_paths will do a full scan, so, do it in the thread
            # too (after everything is configured).
            self.watcher.set_tracked_paths(self.watch_dirs)
            while not self._kill_received:
                for change_enum, change_path in self.watcher.iter_changes():
                    # We're only interested in modified events
                    if change_enum == fsnotify.Change.modified:
                        pydev_log.info("Modified: %s", change_path)
                        self.api.request_reload_code(self.py_db, -1, None, change_path)
                    else:
                        pydev_log.info("Ignored (add or remove) change in: %s", change_path)
        except:
            pydev_log.exception("Error when waiting for filesystem changes in FSNotifyThread.")

    @overrides(PyDBDaemonThread.do_kill_pydev_thread)
    def do_kill_pydev_thread(self):
        self.watcher.dispose()
        PyDBDaemonThread.do_kill_pydev_thread(self)


class WriterThread(PyDBDaemonThread):
    """writer thread writes out the commands in an infinite loop"""

    def __init__(self, sock, py_db, terminate_on_socket_close=True):
        PyDBDaemonThread.__init__(self, py_db)
        self.sock = sock
        self.__terminate_on_socket_close = terminate_on_socket_close
        self.name = "pydevd.Writer"
        self._cmd_queue = _queue.Queue()
        if pydevd_vm_type.get_vm_type() == "python":
            self.timeout = 0
        else:
            self.timeout = 0.1

    def add_command(self, cmd):
        """cmd is NetCommand"""
        if not self._kill_received:  # we don't take new data after everybody die
            self._cmd_queue.put(cmd, False)

    @overrides(PyDBDaemonThread._on_run)
    def _on_run(self):
        """just loop and write responses"""

        try:
            while True:
                try:
                    try:
                        cmd = self._cmd_queue.get(True, 0.1)
                    except _queue.Empty:
                        if self._kill_received:
                            pydev_log.debug("WriterThread: kill_received (sock.shutdown(SHUT_WR))")
                            try:
                                self.sock.shutdown(SHUT_WR)
                            except:
                                pass
                            # Note: don't close the socket, just send the shutdown,
                            # then, when no data is received on the reader, it can close
                            # the socket.
                            # See: https://blog.netherlabs.nl/articles/2009/01/18/the-ultimate-so_linger-page-or-why-is-my-tcp-not-reliable

                            # try:
                            #     self.sock.close()
                            # except:
                            #     pass

                            return  # break if queue is empty and _kill_received
                        else:
                            continue
                except:
                    # pydev_log.info('Finishing debug communication...(1)')
                    # when liberating the thread here, we could have errors because we were shutting down
                    # but the thread was still not liberated
                    return

                if cmd.as_dict is not None:
                    for listener in self.py_db.dap_messages_listeners:
                        listener.before_send(cmd.as_dict)

                notify_about_gevent_if_needed()
                cmd.send(self.sock)

                if cmd.id == CMD_EXIT:
                    pydev_log.debug("WriterThread: CMD_EXIT received")
                    break
                if time is None:
                    break  # interpreter shutdown
                time.sleep(self.timeout)
        except Exception:
            if self.__terminate_on_socket_close:
                self.py_db.dispose_and_kill_all_pydevd_threads()
                if DebugInfoHolder.DEBUG_TRACE_LEVEL > 0:
                    pydev_log_exception()
        finally:
            pydev_log.debug("WriterThread: exit")

    def empty(self):
        return self._cmd_queue.empty()

    @overrides(PyDBDaemonThread.do_kill_pydev_thread)
    def do_kill_pydev_thread(self):
        if not self._kill_received:
            # Add command before setting the kill flag (otherwise the command may not be added).
            exit_cmd = self.py_db.cmd_factory.make_exit_command(self.py_db)
            self.add_command(exit_cmd)

        PyDBDaemonThread.do_kill_pydev_thread(self)


def create_server_socket(host, port):
    try:
        server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP)
        if IS_WINDOWS and not IS_JYTHON:
            server.setsockopt(SOL_SOCKET, SO_EXCLUSIVEADDRUSE, 1)
        elif not IS_WASM:
            server.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)

        server.bind((host, port))
        server.settimeout(None)
    except Exception:
        server.close()
        raise

    return server


def start_server(port):
    """binds to a port, waits for the debugger to connect"""
    s = create_server_socket(host="", port=port)

    try:
        s.listen(1)
        # Let the user know it's halted waiting for the connection.
        host, port = s.getsockname()
        msg = f"pydevd: waiting for connection at: {host}:{port}"
        print(msg, file=sys.stderr)
        pydev_log.info(msg)

        new_socket, _addr = s.accept()
        pydev_log.info("Connection accepted")
        # closing server socket is not necessary but we don't need it
        s.close()
        return new_socket
    except:
        pydev_log.exception("Could not bind to port: %s\n", port)
        raise


def start_client(host, port):
    """connects to a host/port"""
    pydev_log.info("Connecting to %s:%s", host, port)

    address_family = AF_INET
    for res in socket_module.getaddrinfo(host, port, 0, SOCK_STREAM):
        if res[0] == AF_INET:
            address_family = res[0]
            # Prefer IPv4 addresses for backward compat.
            break
        if res[0] == AF_INET6:
            # Don't break after this - if the socket is dual-stack prefer IPv4.
            address_family = res[0]

    s = socket(address_family, SOCK_STREAM)

    #  Set TCP keepalive on an open socket.
    #  It activates after 1 second (TCP_KEEPIDLE,) of idleness,
    #  then sends a keepalive ping once every 3 seconds (TCP_KEEPINTVL),
    #  and closes the connection after 5 failed ping (TCP_KEEPCNT), or 15 seconds
    try:
        s.setsockopt(SOL_SOCKET, socket_module.SO_KEEPALIVE, 1)
    except (AttributeError, OSError):
        pass  # May not be available everywhere.
    try:
        s.setsockopt(socket_module.IPPROTO_TCP, socket_module.TCP_KEEPIDLE, 1)
    except (AttributeError, OSError):
        pass  # May not be available everywhere.
    try:
        s.setsockopt(socket_module.IPPROTO_TCP, socket_module.TCP_KEEPINTVL, 3)
    except (AttributeError, OSError):
        pass  # May not be available everywhere.
    try:
        s.setsockopt(socket_module.IPPROTO_TCP, socket_module.TCP_KEEPCNT, 5)
    except (AttributeError, OSError):
        pass  # May not be available everywhere.

    try:
        # 10 seconds default timeout
        timeout = int(os.environ.get("PYDEVD_CONNECT_TIMEOUT", 10))
        s.settimeout(timeout)
        s.connect((host, port))
        s.settimeout(None)  # no timeout after connected
        pydev_log.info(f"Connected to: {s}.")
        return s
    except:
        pydev_log.exception("Could not connect to %s: %s", host, port)
        raise


INTERNAL_TERMINATE_THREAD = 1
INTERNAL_SUSPEND_THREAD = 2


class InternalThreadCommand(object):
    """internal commands are generated/executed by the debugger.

    The reason for their existence is that some commands have to be executed
    on specific threads. These are the InternalThreadCommands that get
    get posted to PyDB.
    """

    def __init__(self, thread_id, method=None, *args, **kwargs):
        self.thread_id = thread_id
        self.method = method
        self.args = args
        self.kwargs = kwargs

    def can_be_executed_by(self, thread_id):
        """By default, it must be in the same thread to be executed"""
        return self.thread_id == thread_id or self.thread_id.endswith("|" + thread_id)

    def do_it(self, dbg):
        try:
            if self.method is not None:
                self.method(dbg, *self.args, **self.kwargs)
            else:
                raise NotImplementedError("you have to override do_it")
        finally:
            self.args = None
            self.kwargs = None

    def __str__(self):
        return "InternalThreadCommands(%s, %s, %s)" % (self.method, self.args, self.kwargs)

    __repr__ = __str__


class InternalThreadCommandForAnyThread(InternalThreadCommand):
    def __init__(self, thread_id, method=None, *args, **kwargs):
        assert thread_id == "*"

        InternalThreadCommand.__init__(self, thread_id, method, *args, **kwargs)

        self.executed = False
        self.lock = ForkSafeLock()

    def can_be_executed_by(self, thread_id):
        return True  # Can be executed by any thread.

    def do_it(self, dbg):
        with self.lock:
            if self.executed:
                return
            self.executed = True

        InternalThreadCommand.do_it(self, dbg)


def _send_io_message(py_db, s):
    cmd = py_db.cmd_factory.make_io_message(s, 2)
    if py_db.writer is not None:
        py_db.writer.add_command(cmd)


def internal_reload_code(dbg, seq, module_name, filename):
    try:
        found_module_to_reload = False
        if module_name is not None:
            module_name = module_name
            if module_name not in sys.modules:
                if "." in module_name:
                    new_module_name = module_name.split(".")[-1]
                    if new_module_name in sys.modules:
                        module_name = new_module_name

        modules_to_reload = {}
        module = sys.modules.get(module_name)
        if module is not None:
            modules_to_reload[id(module)] = (module, module_name)

        if filename:
            filename = pydevd_file_utils.normcase(filename)
            for module_name, module in sys.modules.copy().items():
                f = getattr_checked(module, "__file__")
                if f is not None:
                    if f.endswith((".pyc", ".pyo")):
                        f = f[:-1]

                    if pydevd_file_utils.normcase(f) == filename:
                        modules_to_reload[id(module)] = (module, module_name)

        if not modules_to_reload:
            if filename and module_name:
                _send_io_message(dbg, "code reload: Unable to find module %s to reload for path: %s\n" % (module_name, filename))
            elif filename:
                _send_io_message(dbg, "code reload: Unable to find module to reload for path: %s\n" % (filename,))
            elif module_name:
                _send_io_message(dbg, "code reload: Unable to find module to reload: %s\n" % (module_name,))

        else:
            # Too much info...
            # _send_io_message(dbg, 'code reload: This usually means you are trying to reload the __main__ module (which cannot be reloaded).\n')
            for module, module_name in modules_to_reload.values():
                _send_io_message(dbg, 'code reload: Start reloading module: "' + module_name + '" ... \n')
                found_module_to_reload = True

                if pydevd_reload.xreload(module):
                    _send_io_message(dbg, "code reload: reload finished\n")
                else:
                    _send_io_message(dbg, "code reload: reload finished without applying any change\n")

        cmd = dbg.cmd_factory.make_reloaded_code_message(seq, found_module_to_reload)
        dbg.writer.add_command(cmd)
    except:
        pydev_log.exception("Error reloading code")


class InternalGetThreadStack(InternalThreadCommand):
    """
    This command will either wait for a given thread to be paused to get its stack or will provide
    it anyways after a timeout (in which case the stack will be gotten but local variables won't
    be available and it'll not be possible to interact with the frame as it's not actually
    stopped in a breakpoint).
    """

    def __init__(self, seq, thread_id, py_db, set_additional_thread_info, fmt, timeout=0.5, start_frame=0, levels=0):
        InternalThreadCommand.__init__(self, thread_id)
        self._py_db = weakref.ref(py_db)
        self._timeout = time.time() + timeout
        self.seq = seq
        self._cmd = None
        self._fmt = fmt
        self._start_frame = start_frame
        self._levels = levels

        # Note: receives set_additional_thread_info to avoid a circular import
        # in this module.
        self._set_additional_thread_info = set_additional_thread_info

    @overrides(InternalThreadCommand.can_be_executed_by)
    def can_be_executed_by(self, _thread_id):
        timed_out = time.time() >= self._timeout

        py_db = self._py_db()
        t = pydevd_find_thread_by_id(self.thread_id)
        frame = None
        if t and not getattr(t, "pydev_do_not_trace", None):
            additional_info = self._set_additional_thread_info(t)
            frame = additional_info.get_topmost_frame(t)
        try:
            self._cmd = py_db.cmd_factory.make_get_thread_stack_message(
                py_db,
                self.seq,
                self.thread_id,
                frame,
                self._fmt,
                must_be_suspended=not timed_out,
                start_frame=self._start_frame,
                levels=self._levels,
            )
        finally:
            frame = None
            t = None

        return self._cmd is not None or timed_out

    @overrides(InternalThreadCommand.do_it)
    def do_it(self, dbg):
        if self._cmd is not None:
            dbg.writer.add_command(self._cmd)
            self._cmd = None

def extract_json_from_cmd(cmd):
    import xml.etree.ElementTree as ET
    result = {"stackFrames": []}
    try:
        root = ET.fromstring(cmd.text)
        for frame in root.findall("frame"):
            frame_info = {
                "file": frame.attrib.get("file"),
                "line": int(frame.attrib.get("line", 0)),
                "name": frame.attrib.get("name"),
                "obj": frame.attrib.get("obj"),
            }
            result["stackFrames"].append(frame_info)
    except Exception as e:
        result["error"] = f"Failed to parse stack XML: {str(e)}"
    return result

def internal_step_in_thread(py_db, thread_id, cmd_id, set_additional_thread_info):
    thread_to_step = pydevd_find_thread_by_id(thread_id)
    if thread_to_step is not None:
        info = set_additional_thread_info(thread_to_step)
        info.pydev_original_step_cmd = cmd_id
        info.pydev_step_cmd = cmd_id
        info.pydev_step_stop = None
        info.pydev_state = STATE_RUN
        info.update_stepping_info()

    if py_db.stepping_resumes_all_threads:
        resume_threads("*", except_thread=thread_to_step)


def internal_smart_step_into(py_db, thread_id, offset, child_offset, set_additional_thread_info):
    thread_to_step = pydevd_find_thread_by_id(thread_id)
    if thread_to_step is not None:
        info = set_additional_thread_info(thread_to_step)
        info.pydev_original_step_cmd = CMD_SMART_STEP_INTO
        info.pydev_step_cmd = CMD_SMART_STEP_INTO
        info.pydev_step_stop = None
        info.pydev_smart_parent_offset = int(offset)
        info.pydev_smart_child_offset = int(child_offset)
        info.pydev_state = STATE_RUN
        info.update_stepping_info()

    if py_db.stepping_resumes_all_threads:
        resume_threads("*", except_thread=thread_to_step)


class InternalSetNextStatementThread(InternalThreadCommand):
    def __init__(self, thread_id, cmd_id, line, func_name, seq=0):
        """
        cmd_id may actually be one of:

        CMD_RUN_TO_LINE
        CMD_SET_NEXT_STATEMENT
        CMD_SMART_STEP_INTO
        """
        self.thread_id = thread_id
        self.cmd_id = cmd_id
        self.line = line
        self.seq = seq

        self.func_name = func_name

    def do_it(self, dbg):
        t = pydevd_find_thread_by_id(self.thread_id)
        if t is not None:
            info = t.additional_info
            info.pydev_original_step_cmd = self.cmd_id
            info.pydev_step_cmd = self.cmd_id
            info.pydev_step_stop = None
            info.pydev_next_line = int(self.line)
            info.pydev_func_name = self.func_name
            info.pydev_message = str(self.seq)
            info.pydev_smart_parent_offset = -1
            info.pydev_smart_child_offset = -1
            info.pydev_state = STATE_RUN
            info.update_stepping_info()

# def log_variable(f, var, depth=0, max_depth=3, py_db=None, request=None, processed_refs=None):
#     """
#     실제 SuspendedFramesManager 구조에 맞춰 완전히 수정된 버전
#     """
#     if processed_refs is None:
#         processed_refs = set()
    
#     # depth에 따른 indent (4칸씩)
#     indent = "    " * depth
#     var_name = var.get("name", "unknown")
#     var_value = str(var.get("value", ""))[:150]
#     var_type = var.get("type", "unknown")
#     variables_reference = var.get("variablesReference", 0)
    
#     try:
#         f.write(f"{indent}[DEPTH {depth}] {var_name} = {var_value} ({var_type}) [ref: {variables_reference}]\n")
#     except UnicodeEncodeError:
#         safe_name = var_name.encode('ascii', 'replace').decode('ascii')
#         safe_value = var_value.encode('ascii', 'replace').decode('ascii')
#         safe_type = var_type.encode('ascii', 'replace').decode('ascii')
#         f.write(f"{indent}[DEPTH {depth}] {safe_name} = {safe_value} ({safe_type}) [ref: {variables_reference}]\n")

#     # 재귀 종료 조건들
#     if depth >= max_depth:
#         f.write(f"{indent}    +-- [MAX DEPTH {max_depth} REACHED]\n")
#         return
        
#     if variables_reference == 0:
#         f.write(f"{indent}    +-- [NO CHILDREN - ref is 0]\n")
#         return
        
#     if py_db is None:
#         f.write(f"{indent}    +-- [NO py_db PROVIDED]\n")
#         return
        
#     # 순환 참조 확인
#     if variables_reference in processed_refs:
#         f.write(f"{indent}    +-- [CIRCULAR REFERENCE - ref {variables_reference}]\n")
#         return
    
#     processed_refs.add(variables_reference)

#     # 올바른 방법으로 변수 접근
#     try:
#         sfm = py_db.suspended_frames_manager
#         f.write(f"{indent}    +-- [ACCESSING VARIABLE {variables_reference}]\n")
        
#         # 1. _get_tracker_for_variable_reference를 통해 tracker 찾기
#         try:
#             frames_tracker = sfm._get_tracker_for_variable_reference(variables_reference)
#             if frames_tracker is None:
#                 f.write(f"{indent}    +-- [NO TRACKER FOUND for ref {variables_reference}]\n")
                
#                 # 디버깅: 사용 가능한 tracker들 확인
#                 available_trackers = list(sfm._thread_id_to_tracker.keys())
#                 f.write(f"{indent}    +-- [AVAILABLE TRACKERS]: {available_trackers}\n")
                
#                 # 각 tracker에서 변수 찾기 시도
#                 for thread_id, tracker in sfm._thread_id_to_tracker.items():
#                     try:
#                         test_var = tracker.get_variable(variables_reference)
#                         f.write(f"{indent}    +-- [FOUND in tracker {thread_id}!]\n")
#                         frames_tracker = tracker
#                         break
#                     except KeyError:
#                         continue
#                     except Exception as tracker_error:
#                         f.write(f"{indent}    +-- [TRACKER {thread_id} ERROR]: {tracker_error}\n")
                
#                 if frames_tracker is None:
#                     f.write(f"{indent}    +-- [VARIABLE NOT FOUND IN ANY TRACKER]\n")
#                     return
#             else:
#                 f.write(f"{indent}    +-- [FOUND TRACKER: {type(frames_tracker).__name__}]\n")
            
#             # 2. tracker에서 variable 가져오기
#             variable = frames_tracker.get_variable(variables_reference)
#             f.write(f"{indent}    +-- [GOT VARIABLE: {type(variable).__name__}]\n")
            
#             # 3. format 정보 추출
#             fmt = {}
#             if request and hasattr(request, 'arguments') and hasattr(request.arguments, 'format'):
#                 fmt = request.arguments.format
#                 if hasattr(fmt, "to_dict"):
#                     fmt = fmt.to_dict()
            
#             # 4. scope 정보 처리 (필요한 경우)
#             scope = None
            
#             # 5. children 가져오기
#             try:
#                 f.write(f"{indent}    +-- [GETTING CHILDREN...]\n")
#                 children = variable.get_children_variables(fmt=fmt, scope=scope)
#                 f.write(f"{indent}    +-- [FOUND {len(children)} CHILDREN]\n")
                
#                 if len(children) == 0:
#                     f.write(f"{indent}    +-- [NO CHILDREN TO PROCESS]\n")
#                 else:
#                     # 처음 50개 children만 처리 (성능상 이유)
#                     max_children = min(50, len(children))
#                     for i in range(max_children):
#                         try:
#                             child_var = children[i]
#                             f.write(f"{indent}    +-- [PROCESSING CHILD {i+1}/{len(children)}]\n")
                            
#                             child_data = child_var.get_var_data(fmt=fmt)
                            
#                             # 재귀 호출
#                             log_variable(f, child_data, depth + 1, max_depth, py_db, request, processed_refs.copy())
                            
#                         except Exception as child_error:
#                             f.write(f"{indent}    +-- [CHILD {i+1} ERROR]: {type(child_error).__name__}: {str(child_error)[:80]}\n")
                    
#                     if len(children) > max_children:
#                         f.write(f"{indent}    +-- [... {len(children) - max_children} more children not shown]\n")
                        
#             except Exception as children_error:
#                 f.write(f"{indent}    +-- [CHILDREN ERROR]: {type(children_error).__name__}: {str(children_error)[:100]}\n")
                
#                 # 대안: 기본 파라미터로 시도
#                 try:
#                     f.write(f"{indent}    +-- [TRYING BASIC CHILDREN ACCESS...]\n")
#                     children = variable.get_children_variables()
#                     f.write(f"{indent}    +-- [BASIC: found {len(children)} children]\n")
                    
#                     # 처음 3개만 테스트
#                     for i in range(min(3, len(children))):
#                         try:
#                             child_var = children[i]
#                             child_data = child_var.get_var_data()
#                             child_name = child_data.get('name', 'unknown')
#                             f.write(f"{indent}    +-- [BASIC CHILD {i+1}: {child_name}]\n")
                            
#                             # 재귀 호출 (깊이 제한)
#                             if depth < max_depth - 1:
#                                 log_variable(f, child_data, depth + 1, max_depth, py_db, request, processed_refs.copy())
                            
#                         except Exception as basic_child_error:
#                             f.write(f"{indent}    +-- [BASIC CHILD {i+1} ERROR]: {basic_child_error}\n")
                            
#                 except Exception as basic_error:
#                     f.write(f"{indent}    +-- [BASIC ACCESS FAILED]: {type(basic_error).__name__}\n")
                    
#         except KeyError:
#             f.write(f"{indent}    +-- [KEYERROR: Variable {variables_reference} not found]\n")
            
#             # 디버깅 정보 추가
#             try:
#                 # tracker별 변수 수 확인
#                 for thread_id, tracker in sfm._thread_id_to_tracker.items():
#                     try:
#                         if hasattr(tracker, '_variable_reference_to_variable'):
#                             var_count = len(tracker._variable_reference_to_variable)
#                             f.write(f"{indent}    +-- [TRACKER {thread_id}: {var_count} variables]\n")
#                         elif hasattr(tracker, 'get_all_variable_references'):
#                             refs = tracker.get_all_variable_references()
#                             f.write(f"{indent}    +-- [TRACKER {thread_id}: refs {refs[:5]}...]\n")
#                     except Exception as debug_error:
#                         f.write(f"{indent}    +-- [TRACKER {thread_id}: debug error {debug_error}]\n")
#             except Exception as debug_main_error:
#                 f.write(f"{indent}    +-- [DEBUG ERROR]: {debug_main_error}\n")
                
#         except Exception as tracker_error:
#             f.write(f"{indent}    +-- [TRACKER ERROR]: {type(tracker_error).__name__}: {str(tracker_error)[:100]}\n")
            
#     except Exception as main_error:
#         f.write(f"{indent}    +-- [MAIN ERROR]: {type(main_error).__name__}: {str(main_error)[:100]}\n")

#     # processed_refs에서 제거
#     processed_refs.discard(variables_reference)

#새로 추가한 debug_all_trackers
# def debug_all_trackers(py_db, log_f):
#     """
#     모든 tracker의 상태를 디버깅하는 함수
#     """
#     try:
#         log_f.write("=== DEBUGGING ALL TRACKERS ===\n")
        
#         sfm = py_db.suspended_frames_manager
        
#         # 기본 정보
#         log_f.write(f"_thread_id_to_tracker: {len(sfm._thread_id_to_tracker)} trackers\n")
#         log_f.write(f"_variable_reference_to_frames_tracker: {len(sfm._variable_reference_to_frames_tracker)} mappings\n")
        
#         # 각 tracker 상세 정보
#         for thread_id, tracker in sfm._thread_id_to_tracker.items():
#             log_f.write(f"\nTRACKER {thread_id}:\n")
#             log_f.write(f"  Type: {type(tracker).__name__}\n")
            
#             # tracker의 속성들 확인
#             tracker_attrs = [attr for attr in dir(tracker) if not attr.startswith('__')]
#             log_f.write(f"  Attributes: {tracker_attrs}\n")
            
#             # 변수 관련 속성들 확인
#             var_attrs = [attr for attr in tracker_attrs if 'variable' in attr.lower()]
#             for attr in var_attrs:
#                 try:
#                     value = getattr(tracker, attr)
#                     if hasattr(value, '__len__'):
#                         log_f.write(f"  {attr}: {len(value)} items\n")
#                         if hasattr(value, 'keys'):
#                             keys = list(value.keys())[:10]
#                             log_f.write(f"    Keys: {keys}\n")
#                     else:
#                         log_f.write(f"  {attr}: {type(value).__name__}\n")
#                 except Exception as attr_error:
#                     log_f.write(f"  {attr}: ERROR {attr_error}\n")
        
#         # _variable_reference_to_frames_tracker 내용 확인
#         log_f.write(f"\nVARIABLE REFERENCE MAPPINGS:\n")
#         for var_ref, tracker in list(sfm._variable_reference_to_frames_tracker.items())[:10]:
#             log_f.write(f"  {var_ref} -> {type(tracker).__name__}\n")
            
#     except Exception as e:
#         log_f.write(f"ERROR in debug_all_trackers: {e}\n")

#변수 저장할 때 callstack 정보 추출
def get_callstack_info(py_db, variables_reference):
    """
    실시간 callstack 정보를 추출하는 개선된 함수
    """
    try:
        print(f"\n[CALLSTACK-INFO] Extracting callstack info for variables_reference: {variables_reference}")
        
        # 방법 1: suspended_frames_manager를 통한 정확한 정보 추출
        try:
            thread_id = py_db.suspended_frames_manager.get_thread_id_for_variable_reference(variables_reference)
            if thread_id is None:
                print(f"[CALLSTACK-INFO] thread_id is None")
                return {
                    "thread_id": None,
                    "frame_id": None,
                    "callstack_level": -1,
                    "function_name": "unknown",
                    "filename": "unknown",
                    "line_number": -1,
                    "error": "thread_id not found"
                }
            
            print(f"[CALLSTACK-INFO] Found thread_id: {thread_id}")
            
            # py_db.find_frame으로 실제 frame 객체 가져오기
            frame = py_db.find_frame(thread_id, variables_reference)
            if frame is not None:
                filename = frame.f_code.co_filename
                lineno = frame.f_lineno  # 실시간 라인!
                function_name = frame.f_code.co_name
                
                print(f"[CALLSTACK-INFO] SUCCESS via find_frame!")
                print(f"[CALLSTACK-INFO] File: {filename}")
                print(f"[CALLSTACK-INFO] Current Line: {lineno}")
                print(f"[CALLSTACK-INFO] Function: {function_name}")
                
                return {
                    "thread_id": thread_id,
                    "frame_id": variables_reference,
                    "callstack_level": 0,  # 현재 프레임이므로 0
                    "function_name": function_name,
                    "filename": os.path.basename(filename),
                    "line_number": lineno,
                    "error": None
                }
            else:
                print(f"[CALLSTACK-INFO] find_frame returned None")
                
        except Exception as e:
            print(f"[CALLSTACK-INFO] Method 1 failed: {e}")
        
        # 방법 2: frames_list를 통한 정보 추출
        try:
            thread_id = py_db.suspended_frames_manager.get_thread_id_for_variable_reference(variables_reference)
            if thread_id and hasattr(py_db.suspended_frames_manager, 'get_frames_list'):
                frames_list = py_db.suspended_frames_manager.get_frames_list(thread_id)
                if frames_list:
                    print(f"[CALLSTACK-INFO] Processing frames_list with {len(frames_list) if hasattr(frames_list, '__len__') else 'unknown'} frames")
                    
                    for level, frame_data in enumerate(frames_list):
                        try:
                            if isinstance(frame_data, (tuple, list)) and len(frame_data) >= 6:
                                f_id, frame_obj, method_name, original_filename, filename_in_utf8, lineno = frame_data[:6]
                                
                                if f_id == variables_reference or (hasattr(frame_obj, 'f_lineno') and id(frame_obj) == variables_reference):
                                    # 실시간 정보 우선 사용
                                    if hasattr(frame_obj, 'f_lineno'):
                                        actual_filename = frame_obj.f_code.co_filename
                                        actual_lineno = frame_obj.f_lineno  # 실시간!
                                        actual_function = frame_obj.f_code.co_name
                                    else:
                                        actual_filename = filename_in_utf8 or original_filename
                                        actual_lineno = lineno
                                        actual_function = method_name
                                    
                                    print(f"[CALLSTACK-INFO] SUCCESS via frames_list!")
                                    print(f"[CALLSTACK-INFO] Level: {level}")
                                    print(f"[CALLSTACK-INFO] File: {actual_filename}")
                                    print(f"[CALLSTACK-INFO] Current Line: {actual_lineno}")
                                    print(f"[CALLSTACK-INFO] Function: {actual_function}")
                                    
                                    return {
                                        "thread_id": thread_id,
                                        "frame_id": variables_reference,
                                        "callstack_level": level,
                                        "function_name": actual_function or "unknown",
                                        "filename": os.path.basename(actual_filename) if actual_filename else "unknown",
                                        "line_number": actual_lineno or -1,
                                        "error": None
                                    }
                                    
                        except Exception as frame_error:
                            print(f"[CALLSTACK-INFO] Frame processing error: {frame_error}")
                            continue
                            
        except Exception as e:
            print(f"[CALLSTACK-INFO] Method 2 failed: {e}")
        
        # 모든 방법 실패
        print(f"[CALLSTACK-INFO] All methods failed")
        return {
            "thread_id": thread_id if 'thread_id' in locals() else None,
            "frame_id": variables_reference,
            "callstack_level": -1,
            "function_name": "unknown",
            "filename": "unknown",
            "line_number": -1,
            "error": "unable to extract callstack info"
        }
        
    except Exception as main_error:
        print(f"[CALLSTACK-INFO ERROR] Critical error: {main_error}")
        return {
            "thread_id": None,
            "frame_id": None,
            "callstack_level": -1,
            "function_name": "unknown",
            "filename": "unknown",
            "line_number": -1,
            "error": str(main_error)
        }

def should_filter_special_variable(var_name, var_type, var_value):
    """Enhanced Special variable 필터링 - 더 포괄적인 필터링"""
    if not var_name:
        return True
    
    # 1. Special categories that VSCode shows
    special_categories = {
        "special variables", "class variables",
        "protected variables", "private variables"
    }
    if var_name.lower() in special_categories:
        return True
    
    lambda_parameters = {"event", "context"}
    if var_name in lambda_parameters:
        return True
    
    # 2. System and built-in variables (확장)
    system_variables = {
        "__builtins__", "__cached__", "__loader__", "__spec__", "__package__",
        "__path__", "__file__", "__annotations__", "__dict__", "__module__",
        "__qualname__", "__slots__", "__weakref__", "__orig_bases__",
        "__parameters__", "__origin__", "__args__", "__mro_entries__"
    }
    
    # 중요한 dunder 변수들만 유지
    important_dunders = {"__name__", "__doc__", "__class__"}
    
    if (var_name.startswith("__") and var_name.endswith("__") and 
        var_name not in important_dunders):
        if var_name in system_variables:
            return True
    
    # 3. 디버깅 관련 변수들
    debug_variables = {
        "__traceback__", "__context__", "__cause__", "__suppress_context__",
        "__frame__", "__locals__", "__globals__", "__code__"
    }
    if var_name in debug_variables:
        return True
    
    # 4. Built-in 모듈들 (대폭 확장)
    if var_type == "module":
        builtin_modules = {
            "sys", "os", "builtins", "types", "collections", "itertools",
            "functools", "operator", "weakref", "gc", "inspect", "linecache",
            "threading", "traceback", "warnings", "importlib", "re", "json",
            "time", "datetime", "math", "random", "socket", "urllib", "http",
            "pickle", "copy", "io", "contextlib", "enum", "abc", "typing",
            "dataclasses", "pathlib", "shutil", "subprocess", "signal",
            "platform", "locale", "calendar", "decimal", "fractions",
            "statistics", "zlib", "gzip", "tarfile", "zipfile", "csv",
            "xml", "html", "email", "base64", "binascii", "hashlib",
            "hmac", "secrets", "ssl", "asyncio", "concurrent", "multiprocessing",
            "queue", "sched", "select", "selectors", "sqlite3", "dbm",
            "unittest", "doctest", "pdb", "profile", "cProfile", "timeit",
            "trace", "dis", "py_compile", "compileall", "keyword", "token",
            "tokenize", "ast", "symtable", "code", "codeop", "runpy",
            "pkgutil", "modulefinder", "imp", "zipimport", "encodings"
        }
        
        # 모듈 이름 추출 시도
        module_name = None
        if "'" in var_value and "module" in var_value:
            try:
                parts = var_value.split("'")
                if len(parts) >= 2:
                    module_name = parts[1]
            except:
                pass
        
        if module_name in builtin_modules:
            return True
    
    # 5. Name mangling 감지 함수
    def is_name_mangled_private_field(name):
        """Name mangling된 private field인지 확인 (_ClassName__fieldname 패턴)"""
        if not name.startswith("_"):
            return False
        
        # _ClassName__fieldname 패턴 확인
        parts = name[1:].split("__", 1)  # 첫 번째 _를 제거하고 __로 분할
        if len(parts) == 2:
            class_name, field_name = parts
            # 클래스명이 비어있지 않고, 필드명도 비어있지 않아야 함
            if class_name and field_name and class_name[0].isupper():
                return True
        return False
    
    # 5. Private 변수들 (name mangling된 private field는 제외)
    if var_name.startswith("_") and not var_name.startswith("__"):
        # Name mangling된 private field는 필터링하지 않음
        if is_name_mangled_private_field(var_name):
            return False
        
        important_privates = {"_", "_1", "_2", "_3", "_last_traceback"}
        if var_name not in important_privates and len(var_name) > 2:
            return True
    
    # 6. 타입별 특수 필터링
    if var_type in ["method", "builtin_function_or_method", "wrapper_descriptor", 
                    "method_descriptor", "classmethod_descriptor", "staticmethod"]:
        common_methods = {"__init__", "__str__", "__repr__", "append", "extend", 
                         "insert", "remove", "pop", "clear", "index", "count",
                         "get", "keys", "values", "items", "update"}
        if var_name not in common_methods and var_name.startswith("__"):
            return True
    
    # 7. 큰 컬렉션의 내부 구현
    if var_type in ["dict_keys", "dict_values", "dict_items", "range",
                    "enumerate", "zip", "filter", "map"]:
        return True
    
    return False

def should_filter_by_context(var_name, var_type, var_value, current_depth, parent_type=None):
    """컨텍스트 기반 필터링 - depth와 부모 타입에 따른 스마트 필터링"""
    
    # 1. Depth 기반 필터링
    if current_depth >= 5:  # 깊이 5 이상에서는 더 엄격하게
        # 원시 타입이 아닌 것들은 더 제한적으로
        if var_type not in ["int", "float", "str", "bool", "NoneType"]:
            # 컬렉션이라면 크기 제한
            if var_type in ["list", "tuple", "dict", "set"]:
                if "length" in var_value or len(var_value) > 100:
                    return True
            else:
                return True
    
    # 2. 부모 타입에 따른 필터링
    if parent_type:
        # 모듈의 자식들은 더 엄격하게
        if parent_type == "module":
            if var_name.startswith("_") or var_type in ["function", "type", "module"]:
                return True
        
        # 클래스의 자식들
        elif parent_type == "type":
            if var_name.startswith("__") and var_name not in ["__init__", "__str__", "__repr__"]:
                return True
    
    # 3. 순환 참조 가능성이 높은 것들
    circular_prone = ["__class__", "__dict__", "__module__", "im_class", "im_self"]
    if var_name in circular_prone:
        return True
    
    return False

# def categorize_variable_by_scope(py_db, variables_reference, child_var, fmt):
#     """변수를 locals/globals로 분류"""
#     try:
#         var_data = child_var.get_var_data(fmt=fmt)
#         var_name = var_data.get("name", "")
#         var_type = var_data.get("type", "")
#         var_value = str(var_data.get("value", ""))
        
#         if should_filter_special_variable(var_name, var_type, var_value):
#             return None, None
        
#         from _pydevd_bundle.pydevd_utils import ScopeRequest
        
#         if hasattr(py_db.suspended_frames_manager, '_variable_to_scope'):
#             scope_info = py_db.suspended_frames_manager._variable_to_scope.get(variables_reference)
#             if scope_info and hasattr(scope_info, 'scope'):
#                 if scope_info.scope == "locals":
#                     return "locals", var_data
#                 elif scope_info.scope == "globals":
#                     return "globals", var_data
        
#         if var_type in ["module", "type", "function", "builtin_function_or_method"]:
#             return "globals", var_data
        
#         if var_type == "module" or (var_type == "type" and "<class" in var_value):
#             return "globals", var_data
        
#         return "locals", var_data
        
#     except Exception as e:
#         print(f"Error categorizing variable: {e}")
#         return None, None
    
def get_stacks_accurate_callstack(py_db, thread_id=None):
    """
    ✅ stacks.py의 dump 함수 로직을 완전히 활용한 정확한 callstack 추출
    """
    try:
        current_tid = threading.current_thread().ident
        target_tid = current_tid
        
        # 특정 thread_id 찾기
        if thread_id:
            for t in threading.enumerate():
                if (str(t.ident) in str(thread_id) or 
                    t.name in str(thread_id) or
                    str(t.ident) == str(thread_id).split('_')[-1]):
                    target_tid = t.ident
                    break
        
        print(f"[STACKS] Extracting callstack for thread {target_tid}")
        
        # sys._current_frames()에서 프레임 가져오기 (stacks.py와 동일)
        current_frames = sys._current_frames()
        frame = current_frames.get(target_tid)
        
        if frame is None:
            print(f"[STACKS] No frame found for thread {target_tid}")
            return []
        
        # 스레드 정보 (stacks.py와 동일)
        thread_name = "<unknown>"
        thread_daemon = False
        for t in threading.enumerate():
            if t.ident == target_tid:
                thread_name = t.name
                thread_daemon = t.daemon
                break
        
        # traceback.format_stack() 사용 (stacks.py와 동일)
        stack = traceback.format_stack(frame)
        parsed_stack = []
        
        for entry in stack:
            try:
                parts = entry.strip().split('\n')
                if len(parts) < 2:
                    continue
                    
                location_part = parts[0].strip()
                code_line = parts[1].strip()
                
                # stacks.py와 동일한 파싱 로직
                file_info = location_part.split(', ')
                if len(file_info) >= 3:
                    # 파일 경로
                    file_part = file_info[0].strip()
                    if '"' in file_part:
                        file_path = file_part.split('"')[1]
                    else:
                        continue
                    
                    # 라인 번호
                    line_part = file_info[1].strip()
                    if "line " in line_part:
                        try:
                            line_number = int(line_part.replace("line ", ""))
                        except ValueError:
                            line_number = -1
                    else:
                        line_number = -1
                    
                    # 함수명
                    func_part = file_info[2].strip()
                    if "in " in func_part:
                        function_name = func_part.replace("in ", "")
                    else:
                        function_name = "unknown"
                    
                    # 사용자 코드만 필터링 (stacks.py 스타일)
                    exclude_patterns = [
                        'pydevd', 'debugpy', '_pydev', 'site-packages',
                        '<string>', '<stdin>', '<console>', 'runpy.py',
                        'threading.py', 'queue.py', 'importlib'
                    ]
                    
                    should_include = True
                    for pattern in exclude_patterns:
                        if pattern in file_path.lower():
                            should_include = False
                            break
                    
                    if file_path.startswith('<') and file_path.endswith('>'):
                        should_include = False
                    
                    if should_include:
                        parsed_frame = {
                            'file': file_path,
                            'line': line_number,
                            'function': function_name,
                            'code': code_line,
                            'thread_id': target_tid,
                            'thread_name': thread_name,
                            'thread_daemon': thread_daemon
                        }
                        parsed_stack.append(parsed_frame)
                        
            except Exception as parse_error:
                print(f"[STACKS] Error parsing entry: {parse_error}")
                continue
        
        # 역순 정렬 (최상위 호출부터)
        parsed_stack.reverse()
        
        print(f"[STACKS] Extracted {len(parsed_stack)} accurate frames")
        return parsed_stack
        
    except Exception as e:
        print(f"[STACKS] Error in get_stacks_accurate_callstack: {e}")
        import traceback
        traceback.print_exc()
        return []

# def create_accurate_callstacks_with_variables(py_db, thread_id, current_variables_reference, current_locals, current_globals, max_levels=5):
#     """
#     ✅ stacks.py 로직으로 정확한 callstack과 변수 정보 결합
#     """
#     try:
#         callstacks = []
        
#         # stacks.py 로직으로 정확한 프레임들 가져오기
#         accurate_frames = get_stacks_accurate_callstack(py_db, thread_id)
        
#         if not accurate_frames:
#             print("[STACKS] No accurate frames, creating single frame from current data")
#             # fallback: 현재 정보로라도 하나의 프레임 생성
#             try:
#                 current_info = get_callstack_info(py_db, current_variables_reference)
#                 fallback_frame = {
#                     "frame_id": current_info.get("frame_id", "fallback_frame"),
#                     "file": current_info.get("filename", "unknown"),
#                     "line": current_info.get("line_number", -1),
#                     "function": current_info.get("function_name", "unknown"),
#                     "code": "# Code not available via stacks.py",
#                     "variables": {
#                         "locals": current_locals,
#                         "globals": current_globals
#                     },
#                     "counts": {
#                         "total_locals": len(current_locals),
#                         "total_globals": len(current_globals),
#                         "total_variables": len(current_locals) + len(current_globals)
#                     },
#                     "extraction_method": "fallback"
#                 }
#                 callstacks.append(fallback_frame)
#             except Exception as fallback_error:
#                 print(f"[STACKS] Fallback also failed: {fallback_error}")
            
#             return callstacks
        
#         # 정확한 프레임들을 callstack 구조로 변환
#         for level, frame_info in enumerate(accurate_frames[:max_levels]):
#             try:
#                 # ✅ stacks.py에서 가져온 정확한 정보 사용
#                 callstack_entry = {
#                     "frame_id": f"stacks_frame_{level}_{hash(frame_info['file'] + str(frame_info['line']))}",
#                     "file": frame_info['file'],
#                     "line": frame_info['line'], 
#                     "function": frame_info['function'],
#                     "code": frame_info['code'],
#                     "variables": {
#                         "locals": [],
#                         "globals": []
#                     },
#                     "counts": {
#                         "total_locals": 0,
#                         "total_globals": 0,
#                         "total_variables": 0
#                     },
#                     "thread_info": {
#                         "thread_id": frame_info['thread_id'],
#                         "thread_name": frame_info['thread_name'],
#                         "thread_daemon": frame_info['thread_daemon']
#                     },
#                     "extraction_method": "stacks.py"
#                 }
                
#                 # 첫 번째 프레임(현재 중단 위치)에 실제 변수 정보 추가
#                 if level == 0:
#                     callstack_entry["variables"]["locals"] = current_locals
#                     callstack_entry["variables"]["globals"] = current_globals
#                     callstack_entry["counts"]["total_locals"] = len(current_locals)
#                     callstack_entry["counts"]["total_globals"] = len(current_globals)
#                     callstack_entry["counts"]["total_variables"] = len(current_locals) + len(current_globals)
                    
#                     # 원본 frame_id도 보존
#                     try:
#                         original_info = get_callstack_info(py_db, current_variables_reference)
#                         if original_info.get("frame_id"):
#                             callstack_entry["original_frame_id"] = original_info["frame_id"]
#                             callstack_entry["frame_id"] = original_info["frame_id"]  # 원본 사용
#                     except Exception as original_error:
#                         print(f"[STACKS] Could not get original frame_id: {original_error}")
                
#                 callstacks.append(callstack_entry)
                
#             except Exception as frame_error:
#                 print(f"[STACKS] Error processing frame {level}: {frame_error}")
        
#         print(f"[STACKS] Created {len(callstacks)} callstack entries")
#         return callstacks
        
#     except Exception as e:
#         print(f"[STACKS] Error in create_accurate_callstacks_with_variables: {e}")
#         return []

# def save_stacks_debug_dump(py_db, thread_id, seq, accurate_frames):
#     """
#     ✅ stacks.py 스타일의 디버그 덤프 파일 저장
#     """
#     try:
#         save_dir = "src/debug_data"
#         if not os.path.exists(save_dir):
#             os.makedirs(save_dir)
        
#         timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
#         # JSON 형태 저장
#         stacks_json = f"{save_dir}/stacks_debug_dump_{seq}_{timestamp}.json"
#         stacks_data = {
#             "timestamp": datetime.now().isoformat(),
#             "process_id": os.getpid(),
#             "thread_id": thread_id,
#             "total_frames": len(accurate_frames),
#             "frames": accurate_frames
#         }
        
#         with open(stacks_json, "w", encoding="utf-8") as f:
#             json.dump(stacks_data, f, indent=2, ensure_ascii=False)
        
#         # 텍스트 형태 저장 (읽기 쉬움)
#         stacks_txt = f"{save_dir}/stacks_debug_dump_{seq}_{timestamp}.txt"
        
#         with open(stacks_txt, "w", encoding="utf-8") as f:
#             f.write(f"Stack Debug Dump (stacks.py style)\n")
#             f.write(f"{'='*70}\n")
#             f.write(f"Process ID: {os.getpid()}\n")
#             f.write(f"Thread ID: {thread_id}\n")
#             f.write(f"Timestamp: {stacks_data['timestamp']}\n")
#             f.write(f"Total Frames: {len(accurate_frames)}\n")
#             f.write(f"{'='*70}\n\n")
            
#             for i, frame in enumerate(accurate_frames):
#                 f.write(f"Frame {i}:\n")
#                 f.write(f"  File: {frame['file']}\n")
#                 f.write(f"  Line: {frame['line']}\n")
#                 f.write(f"  Function: {frame['function']}\n")
#                 f.write(f"  Code: {frame['code']}\n")
#                 f.write(f"  Thread: {frame['thread_name']} (ID: {frame['thread_id']})\n")
#                 f.write(f"\n")
        
#         print(f"[STACKS] Debug dump saved to {stacks_json} and {stacks_txt}")
#         return stacks_json
        
#     except Exception as e:
#         print(f"[STACKS] Error saving debug dump: {e}")
#         return None
  
def send_file_to_local(file_path):
    """람다에서 로컬 PC로 파일 전송 (DAP 방식으로 수정)"""
    try:
        print(f"📤 [FILE-SEND] 파일 전송 시작: {os.path.basename(file_path)}")
        
        # 파일 내용 읽기
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
        
        try:
            # 파일 내용을 JSON으로 파싱하여 딕셔너리로 변환
            file_content_dict = json.loads(file_content)
            print(f"ℹ️ [FILE-SEND] 파일 내용을 JSON 딕셔너리로 변환 성공.")
        except Exception as e:
            print(f"⚠️ [FILE-SEND] 파일 내용 변환 중 예기치 않은 오류: {e}. 원본 문자열로 전송합니다.")
            file_content_dict = {"error": f"Unexpected error during content conversion: {e}", "raw_content": file_content}
    
        # 소켓 연결 및 전송
        sock = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
        
        try:
            sock.settimeout(15.0)  # 15초 타임아웃 (더 여유있게)
            print(f"📤 [FILE-SEND] 연결 시도...")
            sock.connect(("165.194.27.213", 6689))
            print(f"📤 [FILE-SEND] 연결 성공!")
            
            # DAP 방식으로 전송
            success = send_dap_message(sock, file_content_dict, "CAPT")
            
            if success:
                print(f"📤 [FILE-SEND] DAP 전송 완료: {os.path.basename(file_path)}")
                return True
            else:
                print(f"❌ [FILE-SEND] DAP 전송 실패")
                return False
            
        finally:
            try:
                sock.close()
                print(f"📤 [FILE-SEND] 소켓 닫음")
            except:
                pass
        
    except Exception as e:
        print(f"❗ [FILE-SEND] 파일 전송 실패 ({file_path}): {e}")
        import traceback
        print(f"❗ [FILE-SEND] 상세 에러: {traceback.format_exc()}")
        return False
    
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
            body_bytes = json.dumps(data).encode('utf-8')
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


@silence_warnings_decorator
def internal_get_variable_json(py_db, request):
    """
    람다용 변수 수집 + 재귀적 자식 변수 탐색 + 통합 파일 저장
    """
    
    arguments = request.arguments
    variables_reference = arguments.variablesReference
    scope = None
    scope_type = None
    
    from _pydevd_bundle.pydevd_utils import ScopeRequest
    if isinstance(variables_reference, ScopeRequest):
        scope = variables_reference
        scope_type = scope.scope
        variables_reference = variables_reference.variable_reference

    fmt = arguments.format
    if hasattr(fmt, "to_dict"):
        fmt = fmt.to_dict()

    variables = []  # 기존 DAP 응답용
    
    print(f"[LAMBDA-DEBUG] Processing variables for reference: {variables_reference}, scope: {scope_type}")

    # 변수 수집
    try:
        try:
            variable = py_db.suspended_frames_manager.get_variable(variables_reference)
            print(f"[LAMBDA-DEBUG] ✅ Variable access successful: {type(variable).__name__} (scope: {scope_type})")
                
        except Exception as main_access_error:
            print(f"[LAMBDA-DEBUG] ❌ Variable access failed: {main_access_error}")
            pass
        else:
            # children 변수들 가져오기
            try:
                children = variable.get_children_variables(fmt=fmt, scope=scope)
                print(f"[LAMBDA-DEBUG] Got {len(children)} variables for frame {variables_reference} scope '{scope_type}'")
                
                # 변수 처리 (VSCode용은 필터링 없음, JSON용만 필터링)
                for i in range(len(children)):
                    try:
                        child_var = children[i]
                        var_data = child_var.get_var_data(fmt=fmt)
                        
                        # VSCode DAP 응답용: 모든 변수 포함 (필터링 없음)
                        variables.append(var_data)
                            
                    except Exception as child_processing_error:
                        print(f"[LAMBDA-DEBUG] Error processing frame {variables_reference} {scope_type} child {i+1}: {child_processing_error}")
                
                print(f"[LAMBDA-DEBUG] [{scope_type.upper() if scope_type else 'UNKNOWN'}] Collected {len(variables)} variables for VSCode")
                        
            except Exception as children_get_error:
                print(f"[LAMBDA-DEBUG] Error getting frame {variables_reference} {scope_type} children: {children_get_error}")
                    
    except Exception as main_error:
        # 에러 처리
        print(f"[LAMBDA-DEBUG] Main exception for frame {variables_reference} {scope_type}: {type(main_error).__name__}: {main_error}")
        
        try:
            import sys
            import traceback
            exc, exc_type, tb = sys.exc_info()
            err = "".join(traceback.format_exception(exc, exc_type, tb))
            variables = [{"name": "<error>", "value": err, "type": "<error>", "variablesReference": 0}]
        except:
            err = "<Internal error - unable to get traceback when getting variables>"
            variables = []

    # 재귀적 자식 변수 탐색 (JSON 저장용만 필터링 적용)
    variables_with_recursive_children = []
    try:
        print(f"[LAMBDA-DEBUG] Starting recursive collection for JSON storage (filtering applied)...")
        
        for i, var_data in enumerate(variables):
            try:
                # JSON 저장용: 필터링 적용하여 재귀 수집
                var_name = var_data.get("name", "")
                var_type = var_data.get("type", "")
                var_value = str(var_data.get("value", ""))
                
                # 최상위 변수도 JSON용으로는 필터링
                if should_filter_special_variable(var_name, var_type, var_value):
                    print(f"[JSON-FILTER] Skipped top-level: {var_name} ({var_type})")
                    continue
                
                # 각 변수에 대해 재귀적으로 자식들 수집 (필터링 적용)
                enhanced_var_data = collect_recursive_children(py_db, var_data)
                
                print(f"[LAMBDA-DEBUG] JSON Variable {len(variables_with_recursive_children)+1}: {var_name} (filtered & processed)")
                
                variables_with_recursive_children.append(enhanced_var_data)
                
            except Exception as recursive_error:
                print(f"[LAMBDA-DEBUG] Error processing JSON variable {i+1}: {recursive_error}")
                # JSON용은 에러 발생해도 스킵
        
        print(f"[LAMBDA-DEBUG] Completed JSON collection: {len(variables_with_recursive_children)} variables (filtered from {len(variables)} total)")
        
    except Exception as recursive_main_error:
        print(f"[LAMBDA-DEBUG] Main recursive collection error: {recursive_main_error}")
        # 재귀 수집 실패해도 기존 변수들은 fallback으로 저장
        variables_with_recursive_children = [
            {**var_data, "recursive_children": [], "recursive_error": "Collection failed"}
            for var_data in variables
        ]

    # 프레임별 정확한 콜스택 정보 추출
    try:
        thread_id = py_db.suspended_frames_manager.get_thread_id_for_variable_reference(variables_reference)
        frame_info = extract_frame_info_improved(py_db, thread_id, variables_reference)
        
        print(f"[LAMBDA-DEBUG] Frame info: {frame_info}")
        
    except Exception as frame_info_error:
        print(f"[LAMBDA-DEBUG] Error extracting frame info: {frame_info_error}")
        frame_info = {
            "frame_id": variables_reference,
            "file": "unknown",
            "line": -1,
            "function": "unknown",
            "code": "# Frame info extraction failed"
        }

    # ✅ frame_id 기반 callstack 관리 (하나의 JSON 파일에 모든 프레임)
    try:
        save_dir = "/tmp"  # 람다 전용
        thread_id = py_db.suspended_frames_manager.get_thread_id_for_variable_reference(variables_reference)
        
        # 🔥 thread_id만으로 파일명 생성 (frame_id 제거)
        session_filename = f"{save_dir}/unified_callstack_variables_{thread_id}.json"
        
        # 기존 파일이 있으면 읽기, 없으면 새로 생성
        if os.path.exists(session_filename):
            try:
                with open(session_filename, "r", encoding="utf-8") as f:
                    session_data = json.load(f)
                print(f"[LAMBDA-DEBUG] 📁 Loading existing callstack file for frame {variables_reference}")
            except Exception as read_error:
                print(f"[LAMBDA-DEBUG] Failed to read existing file: {read_error}")
                session_data = create_empty_session_data(thread_id)
        else:
            print(f"[LAMBDA-DEBUG] 🆕 Creating new callstack file for thread {thread_id}")
            session_data = create_empty_session_data(thread_id)

        # callstacks 배열에서 frame_id로 기존 프레임 찾기
        target_frame = None
        for frame in session_data["callstacks"]:
            if frame["frame_id"] == variables_reference:
                target_frame = frame
                print(f"[LAMBDA-DEBUG] 🔄 Found existing frame {variables_reference} at index {session_data['callstacks'].index(frame)}")
                break
        
        # 프레임이 없으면 새로 생성
        if target_frame is None:
            target_frame = {
                "frame_id": variables_reference,
                "file": frame_info.get("file", "unknown"),
                "line": frame_info.get("line", -1),
                "function": frame_info.get("function", "unknown"),
                "code": frame_info.get("code", "# Code not available"),
                "variables": {
                    "locals": [],
                    "globals": []
                },
                "counts": {
                    "total_locals": 0,
                    "total_globals": 0,
                    "total_variables": 0
                },
                "recursive_stats": {}
            }
            session_data["callstacks"].append(target_frame)
            print(f"[LAMBDA-DEBUG] ➕ Added new frame {variables_reference} at index {len(session_data['callstacks'])-1}")
        
        # scope_type에 따라 데이터 추가/업데이트
        if scope_type == "locals":
            target_frame["variables"]["locals"] = variables_with_recursive_children
            print(f"[LAMBDA-DEBUG] 📥 Added LOCALS to frame {variables_reference}: {len(variables_with_recursive_children)} variables")
        elif scope_type == "globals":
            target_frame["variables"]["globals"] = variables_with_recursive_children
            print(f"[LAMBDA-DEBUG] 📥 Added GLOBALS to frame {variables_reference}: {len(variables_with_recursive_children)} variables")
        else:
            target_frame["variables"]["unknown_scope"] = variables_with_recursive_children
            print(f"[LAMBDA-DEBUG] 📥 Added UNKNOWN_SCOPE to frame {variables_reference}: {len(variables_with_recursive_children)} variables")
        
        # 프레임 메타데이터 최신화
        target_frame["file"] = frame_info.get("file", "unknown")
        target_frame["line"] = frame_info.get("line", -1)
        target_frame["function"] = frame_info.get("function", "unknown")
        target_frame["code"] = frame_info.get("code", "# Code not available")
        
        # 카운트 및 통계 재계산
        target_frame["counts"] = {
            "total_locals": len(target_frame["variables"]["locals"]),
            "total_globals": len(target_frame["variables"]["globals"]),
            "total_variables": len(target_frame["variables"]["locals"]) + len(target_frame["variables"]["globals"])
        }
        
        target_frame["recursive_stats"][scope_type or "unknown"] = calculate_recursive_stats(variables_with_recursive_children)
        
        # 전체 세션 메타데이터 업데이트
        total_frames = len(session_data["callstacks"])
        total_variables = sum(frame["counts"]["total_variables"] for frame in session_data["callstacks"])
        frames_with_both_scopes = len([f for f in session_data["callstacks"] 
                                     if len(f["variables"]["locals"]) > 0 and len(f["variables"]["globals"]) > 0])
        
        session_data["last_updated"] = datetime.now().isoformat()
        session_data["last_updated_scope"] = scope_type
        session_data["last_updated_frame"] = variables_reference
        session_data["summary"] = {
            "total_frames": total_frames,
            "total_variables": total_variables,
            "frames_with_both_scopes": frames_with_both_scopes,
            "callstack_complete": frames_with_both_scopes > 0  # 적어도 하나의 프레임에 locals+globals가 있으면
        }
        
        # 파일 덮어쓰기 저장
        with open(session_filename, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2, ensure_ascii=False)

        print(f"[LAMBDA-DEBUG] 💾 Callstack file updated: {session_filename}")
        print(f"[LAMBDA-DEBUG] 📊 Current callstack state:")
        for i, frame in enumerate(session_data["callstacks"]):
            locals_count = len(frame["variables"]["locals"])
            globals_count = len(frame["variables"]["globals"])
            complete = "✅" if locals_count > 0 and globals_count > 0 else "❌"
            file_info = f"{frame['file']}:{frame['line']}"
            code_preview = frame['code'][:50] + "..." if len(frame['code']) > 50 else frame['code']
            print(f"  [{i}] Frame {frame['frame_id']}: {frame['function']} | {file_info}")
            print(f"      Code: {code_preview}")
            print(f"      Variables: L:{locals_count} G:{globals_count} {complete}")
            print()  # 빈 줄로 구분

        # 🚀 현재 프레임이 완전해졌을 때 전송
        current_frame_complete = (len(target_frame["variables"]["locals"]) > 0 and 
                                len(target_frame["variables"]["globals"]) > 0)
        
        if scope_type == "globals" and current_frame_complete:
            print(f"[LAMBDA-DEBUG] 🚀 Frame {variables_reference} complete! Sending callstack file...")
            success = send_file_to_local(session_filename)
            if success:
                print(f"[LAMBDA-DEBUG] ✅ Callstack file sent successfully!")
                # 전송 후에는 파일 유지 (다른 프레임이 추가될 수 있음)
            else:
                print(f"[LAMBDA-DEBUG] ❌ Callstack file send failed!")
        else:
            print(f"[LAMBDA-DEBUG] ⏳ Frame {variables_reference} waiting for complete data... (current: {scope_type})")

    except Exception as e:
        print(f"[LAMBDA-DEBUG] Failed to save/transfer callstack session: {e}")
        import traceback
        print(f"[LAMBDA-DEBUG] Traceback: {traceback.format_exc()}")

    except Exception as e:
        print(f"[LAMBDA-DEBUG] Failed to save/transfer unified session: {e}")
        import traceback
        print(f"[LAMBDA-DEBUG] Traceback: {traceback.format_exc()}")

    print(f"[LAMBDA-DEBUG] Callstack session completed for frame {variables_reference} {scope_type}")

    # DAP 응답 생성 (완전히 동일하게 유지)
    from _pydevd_bundle._debug_adapter.pydevd_schema import VariablesResponseBody
    from _pydevd_bundle._debug_adapter import pydevd_base_schema
    from _pydevd_bundle.pydevd_net_command import NetCommand
    from _pydevd_bundle.pydevd_comm_constants import CMD_RETURN
    
    body = VariablesResponseBody(variables)  # 기존 variables 사용 (재귀 정보 없는)
    variables_response = pydevd_base_schema.build_response(request, kwargs={"body": body})
    py_db.writer.add_command(NetCommand(CMD_RETURN, 0, variables_response, is_json=True))

def create_empty_session_data(thread_id):
    """빈 callstack 세션 데이터 구조 생성"""
    return {
        "timestamp": datetime.now().isoformat(),
        "thread_id": thread_id,
        "extraction_method": "lambda_debugger_callstack_array",
        "callstacks": [],  # 빈 배열로 시작, frame_id별로 추가됨
        "summary": {
            "total_frames": 0,
            "total_variables": 0,
            "frames_with_both_scopes": 0,
            "callstack_complete": False
        },
        "debug_info": {
            "session_created": datetime.now().isoformat(),
            "callstack_mode": True,
            "note": "Each index in callstacks array represents depth (0=bottom/current)"
        }
    }

def extract_frame_info_improved(py_db, thread_id, variables_reference):
    """프레임 정보 추출 (람다 환경 최적화)"""
    try:
        print(f"[LAMBDA-DEBUG] Extracting frame info for {variables_reference}")
        
        # 방법 1: py_db.find_frame() 
        try:
            frame = py_db.find_frame(thread_id, variables_reference)
            if frame is not None:
                filename = frame.f_code.co_filename
                lineno = frame.f_lineno
                function_name = frame.f_code.co_name
                
                # 코드 확인
                try:
                    import linecache
                    line_text = linecache.getline(filename, lineno)
                    code = line_text.strip() if line_text else "# Code not available"
                except Exception:
                    code = "# Code extraction failed"
                
                return {
                    "frame_id": variables_reference,
                    "file": os.path.basename(filename),
                    "full_file": filename,
                    "line": lineno,
                    "function": function_name,
                    "code": code
                }
        except Exception as e:
            print(f"[LAMBDA-DEBUG] find_frame failed: {e}")
        
        # 방법 2: frames_list 직접 접근
        try:
            actual_thread_id = py_db.suspended_frames_manager.get_thread_id_for_variable_reference(variables_reference)
            if actual_thread_id and hasattr(py_db.suspended_frames_manager, 'get_frames_list'):
                frames_list = py_db.suspended_frames_manager.get_frames_list(actual_thread_id)
                
                if frames_list:
                    for frame_data in frames_list[:3]:  # 처음 3개만
                        try:
                            if isinstance(frame_data, (tuple, list)) and len(frame_data) >= 6:
                                f_id, frame_obj, method_name, original_filename, filename_in_utf8, lineno = frame_data[:6]
                                
                                if f_id == variables_reference:
                                    # 실시간 정보 우선
                                    if hasattr(frame_obj, 'f_lineno'):
                                        current_filename = frame_obj.f_code.co_filename
                                        current_lineno = frame_obj.f_lineno
                                        current_function = frame_obj.f_code.co_name
                                    else:
                                        current_filename = filename_in_utf8 or original_filename
                                        current_lineno = lineno
                                        current_function = method_name
                                    
                                    import linecache
                                    line_text = linecache.getline(current_filename, current_lineno)
                                    code = line_text.strip() if line_text else "# Code not available"
                                    
                                    return {
                                        "frame_id": variables_reference,
                                        "file": os.path.basename(current_filename),
                                        "full_file": current_filename,
                                        "line": current_lineno,
                                        "function": current_function,
                                        "code": code
                                    }
                        except Exception:
                            continue
        except Exception as e:
            print(f"[LAMBDA-DEBUG] frames_list failed: {e}")
        
        # 기본 반환값
        return {
            "frame_id": variables_reference,
            "file": "lambda_function",
            "line": -1,
            "function": "lambda_handler",
            "code": "# Frame info extraction failed"
        }
        
    except Exception as e:
        print(f"[LAMBDA-DEBUG] Critical error: {e}")
        return {
            "frame_id": variables_reference,
            "file": "error",
            "line": -1,
            "function": "unknown",
            "code": f"# Critical error: {str(e)}"
        }

# def collect_recursive_children(py_db, var_data, current_depth=0, processed_refs=None, parent_type=None):
#     """
#     제한을 제거하고 스마트 필터링을 적용한 재귀적 자식 수집
#     """
#     if processed_refs is None:
#         processed_refs = set()
    
#     # 기본 변수 정보 복사
#     enhanced_var = var_data.copy()
#     enhanced_var["recursive_children"] = []
#     enhanced_var["recursive_depth"] = current_depth
#     enhanced_var["recursive_collection_time"] = datetime.now().isoformat()
    
#     variables_reference = var_data.get("variablesReference", 0)
#     var_name = var_data.get("name", "")
#     var_type = var_data.get("type", "")
#     var_value = str(var_data.get("value", ""))
    
#     # 재귀 종료 조건 (제한 완화)
#     max_reasonable_depth = 10  # 기존 3에서 10으로 증가
    
#     if (current_depth >= max_reasonable_depth or 
#         variables_reference == 0 or 
#         variables_reference in processed_refs):
        
#         if current_depth >= max_reasonable_depth:
#             enhanced_var["recursive_truncated"] = f"max_depth_reached_{max_reasonable_depth}"
#         elif variables_reference == 0:
#             enhanced_var["recursive_truncated"] = "no_children"
#         elif variables_reference in processed_refs:
#             enhanced_var["recursive_truncated"] = "circular_reference"
        
#         return enhanced_var
    
#     # 순환 참조 방지
#     processed_refs.add(variables_reference)
    
#     try:
#         # SuspendedFramesManager를 통해 자식 변수들 가져오기
#         sfm = py_db.suspended_frames_manager
#         frames_tracker = sfm._get_tracker_for_variable_reference(variables_reference)
        
#         if frames_tracker is not None:
#             variable = frames_tracker.get_variable(variables_reference)
#             children = variable.get_children_variables()
            
#             print(f"[FILTER-DEBUG] Depth {current_depth}: {var_name} ({var_type}) has {len(children)} children")
            
#             # 필터링된 자식들만 처리
#             filtered_children = []
#             for i, child_var in enumerate(children):
#                 try:
#                     child_data = child_var.get_var_data()
#                     child_name = child_data.get("name", "")
#                     child_type = child_data.get("type", "")
#                     child_value = str(child_data.get("value", ""))
                    
#                     # 기본 필터링
#                     if should_filter_special_variable(child_name, child_type, child_value):
#                         continue
                    
#                     # 컨텍스트 기반 필터링
#                     if should_filter_by_context(child_name, child_type, child_value, current_depth, var_type):
#                         continue
                    
#                     filtered_children.append(child_var)
                    
#                 except Exception as child_error:
#                     print(f"[FILTER-ERROR] Error checking child {i}: {child_error}")
#                     continue
            
#             print(f"[FILTER-DEBUG] Filtered {len(children)} -> {len(filtered_children)} children for {var_name}")
            
#             # 필터링된 자식들을 재귀적으로 처리
#             for i, child_var in enumerate(filtered_children):
#                 try:
#                     child_data = child_var.get_var_data()
                    
#                     # 🔥 재귀 호출 (올바른 매개변수 순서)
#                     recursive_child = collect_recursive_children(
#                         py_db, child_data, current_depth + 1, processed_refs.copy(), var_type
#                     )
                    
#                     enhanced_var["recursive_children"].append(recursive_child)
                    
#                 except Exception as child_error:
#                     error_child = {
#                         "name": f"<error_child_{i}>",
#                         "value": str(child_error)[:100],
#                         "type": "<error>",
#                         "variablesReference": 0,
#                         "recursive_children": [],
#                         "recursive_depth": current_depth + 1,
#                         "error": str(child_error)
#                     }
#                     enhanced_var["recursive_children"].append(error_child)
            
#             # 자식 통계 추가
#             enhanced_var["recursive_stats"] = {
#                 "original_children": len(children),
#                 "filtered_children": len(filtered_children),
#                 "displayed_children": len(enhanced_var["recursive_children"]),
#                 "filter_ratio": f"{len(filtered_children)}/{len(children)}" if len(children) > 0 else "0/0"
#             }
                
#     except Exception as e:
#         enhanced_var["recursive_error"] = str(e)
#         enhanced_var["recursive_children"] = []
    
#     # processed_refs에서 제거
#     processed_refs.discard(variables_reference)
    
#     return enhanced_var
def collect_recursive_children(py_db, var_data, current_depth=0, processed_refs=None, parent_type=None):
    """
    제한을 제거하고 스마트 필터링을 적용한 재귀적 자식 수집
    """
    if processed_refs is None:
        processed_refs = set()
    
    # 기본 변수 정보 복사
    enhanced_var = var_data.copy()
    enhanced_var["recursive_children"] = []
    enhanced_var["recursive_depth"] = current_depth
    enhanced_var["recursive_collection_time"] = datetime.now().isoformat()
    
    variables_reference = var_data.get("variablesReference", 0)
    var_name = var_data.get("name", "")
    var_type = var_data.get("type", "")
    var_value = str(var_data.get("value", ""))
    
    # 재귀 종료 조건 (depth 제한 완전 제거)
    if (variables_reference == 0 or 
        variables_reference in processed_refs):
        
        if variables_reference == 0:
            enhanced_var["recursive_truncated"] = "no_children"
        elif variables_reference in processed_refs:
            enhanced_var["recursive_truncated"] = "circular_reference"
        
        return enhanced_var
    
    # 순환 참조 방지
    processed_refs.add(variables_reference)
    
    try:
        # SuspendedFramesManager를 통해 자식 변수들 가져오기
        sfm = py_db.suspended_frames_manager
        frames_tracker = sfm._get_tracker_for_variable_reference(variables_reference)
        
        if frames_tracker is not None:
            variable = frames_tracker.get_variable(variables_reference)
            children = variable.get_children_variables()
            
            # print(f"[FILTER-DEBUG] Depth {current_depth}: {var_name} ({var_type}) has {len(children)} children")
            
            # 필터링된 자식들만 처리
            filtered_children = []
            for i, child_var in enumerate(children):
                try:
                    child_data = child_var.get_var_data()
                    child_name = child_data.get("name", "")
                    child_type = child_data.get("type", "")
                    child_value = str(child_data.get("value", ""))
                    
                    # 기본 필터링
                    if should_filter_special_variable(child_name, child_type, child_value):
                        continue
                    
                    # 컨텍스트 기반 필터링
                    if should_filter_by_context(child_name, child_type, child_value, current_depth, var_type):
                        continue
                    
                    filtered_children.append(child_var)
                    
                except Exception as child_error:
                    print(f"[FILTER-ERROR] Error checking child {i}: {child_error}")
                    continue
            
            # print(f"[FILTER-DEBUG] Filtered {len(children)} -> {len(filtered_children)} children for {var_name}")
            
            # 필터링된 자식들을 재귀적으로 처리
            for i, child_var in enumerate(filtered_children):
                try:
                    child_data = child_var.get_var_data()
                    
                    # 🔥 재귀 호출 (올바른 매개변수 순서)
                    recursive_child = collect_recursive_children(
                        py_db, child_data, current_depth + 1, processed_refs.copy(), var_type
                    )
                    
                    enhanced_var["recursive_children"].append(recursive_child)
                    
                except Exception as child_error:
                    error_child = {
                        "name": f"<error_child_{i}>",
                        "value": str(child_error)[:100],
                        "type": "<error>",
                        "variablesReference": 0,
                        "recursive_children": [],
                        "recursive_depth": current_depth + 1,
                        "error": str(child_error)
                    }
                    enhanced_var["recursive_children"].append(error_child)
            
            # 자식 통계 추가
            enhanced_var["recursive_stats"] = {
                "original_children": len(children),
                "filtered_children": len(filtered_children),
                "displayed_children": len(enhanced_var["recursive_children"]),
                "filter_ratio": f"{len(filtered_children)}/{len(children)}" if len(children) > 0 else "0/0"
            }
                
    except Exception as e:
        enhanced_var["recursive_error"] = str(e)
        enhanced_var["recursive_children"] = []
    
    # processed_refs에서 제거
    processed_refs.discard(variables_reference)
    
    return enhanced_var

def calculate_recursive_stats(variables_with_children):
    """재귀적 수집 통계 계산"""
    total_nodes = 0
    max_depth = 0
    variables_with_children_count = 0
    
    def count_nodes(var):
        nonlocal total_nodes, max_depth
        total_nodes += 1
        current_depth = var.get("recursive_depth", 0)
        max_depth = max(max_depth, current_depth)
        
        for child in var.get("recursive_children", []):
            count_nodes(child)
    
    for var in variables_with_children:
        if var.get("recursive_children"):
            variables_with_children_count += 1
        count_nodes(var)
    
    return {
        "total_variables": len(variables_with_children),
        "variables_with_children": variables_with_children_count,
        "total_nodes_collected": total_nodes,
        "max_recursive_depth": max_depth,
        "collection_timestamp": datetime.now().isoformat()
    }

class InternalGetVariable(InternalThreadCommand):
    """gets the value of a variable"""

    def __init__(self, seq, thread_id, frame_id, scope, attrs):
        self.sequence = seq
        self.thread_id = thread_id
        self.frame_id = frame_id
        self.scope = scope
        self.attributes = attrs

    @silence_warnings_decorator
    def do_it(self, dbg):
        """Converts request into python variable"""
        try:
            xml = StringIO()
            xml.write("<xml>")
            type_name, val_dict = pydevd_vars.resolve_compound_variable_fields(
                dbg, self.thread_id, self.frame_id, self.scope, self.attributes
            )
            if val_dict is None:
                val_dict = {}

            # assume properly ordered if resolver returns 'OrderedDict'
            # check type as string to support OrderedDict backport for older Python
            keys = list(val_dict)
            if not (type_name == "OrderedDict" or val_dict.__class__.__name__ == "OrderedDict" or IS_PY36_OR_GREATER):
                keys = sorted(keys, key=compare_object_attrs_key)

            timer = Timer()
            for k in keys:
                val = val_dict[k]
                evaluate_full_value = pydevd_xml.should_evaluate_full_value(val)
                xml.write(pydevd_xml.var_to_xml(val, k, evaluate_full_value=evaluate_full_value))
                timer.report_if_compute_repr_attr_slow(self.attributes, k, type(val))

            xml.write("</xml>")
            cmd = dbg.cmd_factory.make_get_variable_message(self.sequence, xml.getvalue())
            xml.close()
            dbg.writer.add_command(cmd)
        except Exception:
            cmd = dbg.cmd_factory.make_error_message(self.sequence, "Error resolving variables %s" % (get_exception_traceback_str(),))
            dbg.writer.add_command(cmd)


class InternalGetArray(InternalThreadCommand):
    def __init__(self, seq, roffset, coffset, rows, cols, format, thread_id, frame_id, scope, attrs):
        self.sequence = seq
        self.thread_id = thread_id
        self.frame_id = frame_id
        self.scope = scope
        self.name = attrs.split("\t")[-1]
        self.attrs = attrs
        self.roffset = int(roffset)
        self.coffset = int(coffset)
        self.rows = int(rows)
        self.cols = int(cols)
        self.format = format

    def do_it(self, dbg):
        try:
            frame = dbg.find_frame(self.thread_id, self.frame_id)
            var = pydevd_vars.eval_in_context(self.name, frame.f_globals, frame.f_locals, py_db=dbg)
            xml = pydevd_vars.table_like_struct_to_xml(var, self.name, self.roffset, self.coffset, self.rows, self.cols, self.format)
            cmd = dbg.cmd_factory.make_get_array_message(self.sequence, xml)
            dbg.writer.add_command(cmd)
        except:
            cmd = dbg.cmd_factory.make_error_message(self.sequence, "Error resolving array: " + get_exception_traceback_str())
            dbg.writer.add_command(cmd)


def internal_change_variable(dbg, seq, thread_id, frame_id, scope, attr, value):
    """Changes the value of a variable"""
    try:
        # Log function call and arguments
        print(f"internal_change_variable called with: dbg={dbg}, seq={seq}, thread_id={thread_id}, frame_id={frame_id}, scope={scope}, attr='{attr}', value='{value}'")
        frame = dbg.find_frame(thread_id, frame_id)
        if frame is not None:
            result = pydevd_vars.change_attr_expression(frame, attr, value, dbg)
        else:
            result = None
        xml = "<xml>"
        xml += pydevd_xml.var_to_xml(result, "")
        xml += "</xml>"
        cmd = dbg.cmd_factory.make_variable_changed_message(seq, xml)
        dbg.writer.add_command(cmd)
    except Exception:
        cmd = dbg.cmd_factory.make_error_message(
            seq, "Error changing variable attr:%s expression:%s traceback:%s" % (attr, value, get_exception_traceback_str())
        )
        dbg.writer.add_command(cmd)


def internal_change_variable_json(py_db, request):
    """
    The pydevd_vars.change_attr_expression(thread_id, frame_id, attr, value, dbg) can only
    deal with changing at a frame level, so, currently changing the contents of something
    in a different scope is currently not supported.

    :param SetVariableRequest request:
    """
    # : :type arguments: SetVariableArguments
    arguments = request.arguments
    variables_reference = arguments.variablesReference
    variable_name = arguments.name
    new_value = arguments.value
    
    print(f"[CHANGE-DEBUG] 🔧 변수 변경 요청:")
    print(f"[CHANGE-DEBUG]   name: '{variable_name}'")
    print(f"[CHANGE-DEBUG]   value: '{new_value}'")
    print(f"[CHANGE-DEBUG]   variables_reference: {variables_reference}")
    
    scope = None
    original_variables_reference = variables_reference
    if isinstance_checked(variables_reference, ScopeRequest):
        scope = variables_reference
        variables_reference = variables_reference.variable_reference
        print(f"[CHANGE-DEBUG]   scope: {scope.scope if scope else None}")

    fmt = arguments.format
    if hasattr(fmt, "to_dict"):
        fmt = fmt.to_dict()

    try:
        variable = py_db.suspended_frames_manager.get_variable(variables_reference)
        print(f"[CHANGE-DEBUG] ✅ Variable container found: {type(variable).__name__}")
    except KeyError:
        variable = None
        print(f"[CHANGE-DEBUG] ❌ Variable container not found: KeyError")

    if variable is None:
        print(f"[CHANGE-DEBUG] ❌ Writing error response: variable container not found")
        _write_variable_response(
            py_db, request, value="", success=False, 
            message="Unable to find variable container to change: %s." % (variables_reference,)
        )
        return

    # 🔍 변경 전 현재 값 확인
    try:
        current_var_data = variable.get_var_data(fmt=fmt)
        current_value = current_var_data.get("value", "N/A")
        print(f"[CHANGE-DEBUG] 📊 Current value before change: '{current_value}'")
    except Exception as e:
        print(f"[CHANGE-DEBUG] ⚠️ Could not get current value: {e}")
        current_value = "unknown"

    print(f"[CHANGE-DEBUG] 🔄 Attempting to change variable...")
    
    # 🚀 핵심 개선: 다단계 변수 변경 시도
    child_var = None
    success = False
    final_var_data = None
    
    # 방법 1: 기존 change_variable 시도
    try:
        child_var = variable.change_variable(variable_name, new_value, py_db, fmt=fmt, scope=scope)
        
        if child_var is not None:
            # 변경 후 값 즉시 확인
            test_data = child_var.get_var_data(fmt=fmt)
            actual_new_value = test_data.get("value", "")
            
            print(f"[CHANGE-DEBUG] 📊 Value after change_variable: '{actual_new_value}'")
            
            # 🔍 변경이 실제로 적용되었는지 검증
            if str(actual_new_value) == str(new_value):
                success = True
                final_var_data = test_data
                print(f"[CHANGE-DEBUG] ✅ change_variable successful: '{current_value}' → '{actual_new_value}'")
            else:
                print(f"[CHANGE-DEBUG] ⚠️ change_variable may have failed: expected '{new_value}', got '{actual_new_value}'")
                child_var = None  # 재시도를 위해 None으로 설정
        else:
            print(f"[CHANGE-DEBUG] ❌ change_variable returned None")
    
    except Exception as change_error:
        print(f"[CHANGE-DEBUG] ❌ change_variable failed with exception: {change_error}")
        child_var = None

    # 방법 2: 직접 프레임 레벨 변경 시도 (change_variable 실패 시)
    if not success and child_var is None:
        print(f"[CHANGE-DEBUG] 🔧 Attempting direct frame-level change...")
        
        try:
            # 스레드 및 프레임 ID 확인
            thread_id = py_db.suspended_frames_manager.get_thread_id_for_variable_reference(variables_reference)
            
            if thread_id:
                frame = py_db.find_frame(thread_id, variables_reference)
                
                if frame is not None:
                    print(f"[CHANGE-DEBUG] 🎯 Frame found, attempting direct change...")
                    
                    # 값을 안전하게 평가
                    try:
                        # 문자열인 경우 따옴표 처리
                        if isinstance(new_value, str) and not new_value.startswith(("'", '"')):
                            # 숫자나 불린 값인지 확인
                            try:
                                # 정수 시도
                                evaluated_value = int(new_value)
                            except ValueError:
                                try:
                                    # 실수 시도
                                    evaluated_value = float(new_value)
                                except ValueError:
                                    # 불린 시도
                                    if new_value.lower() in ('true', 'false'):
                                        evaluated_value = new_value.lower() == 'true'
                                    else:
                                        # 문자열로 처리
                                        evaluated_value = new_value
                        else:
                            # eval을 사용하여 평가 (안전한 컨텍스트에서)
                            evaluated_value = eval(new_value, frame.f_globals, frame.f_locals)
                        
                        print(f"[CHANGE-DEBUG] 🔄 Evaluated value: {evaluated_value} (type: {type(evaluated_value).__name__})")
                        
                        # Scope에 따라 적절한 namespace에 값 설정
                        if scope and hasattr(scope, 'scope') and scope.scope == "globals":
                            frame.f_globals[variable_name] = evaluated_value
                            verification_value = frame.f_globals.get(variable_name)
                            print(f"[CHANGE-DEBUG] 🔧 Direct globals change applied")
                        else:
                            frame.f_locals[variable_name] = evaluated_value
                            verification_value = frame.f_locals.get(variable_name)
                            print(f"[CHANGE-DEBUG] 🔧 Direct locals change applied")
                        
                        print(f"[CHANGE-DEBUG] 📊 Verification value: '{verification_value}'")
                        
                        # 변경 성공 확인
                        if str(verification_value) == str(evaluated_value):
                            success = True
                            print(f"[CHANGE-DEBUG] ✅ Direct change successful!")
                            
                            # final_var_data 직접 생성
                            final_var_data = {
                                "value": str(verification_value),
                                "type": type(verification_value).__name__,
                                "variablesReference": 0
                            }
                            
                            # Mock child_var 생성
                            class MockChildVar:
                                def get_var_data(self, fmt=None):
                                    return final_var_data
                            
                            child_var = MockChildVar()
                        else:
                            print(f"[CHANGE-DEBUG] ❌ Direct change verification failed")
                            
                    except Exception as eval_error:
                        print(f"[CHANGE-DEBUG] ❌ Value evaluation failed: {eval_error}")
                        
                else:
                    print(f"[CHANGE-DEBUG] ❌ Frame not found for direct change")
            else:
                print(f"[CHANGE-DEBUG] ❌ Thread ID not found for direct change")
                
        except Exception as direct_error:
            print(f"[CHANGE-DEBUG] ❌ Direct change failed: {direct_error}")

    # 🚨 모든 방법 실패 시 오류 응답
    if not success or child_var is None:
        print(f"[CHANGE-DEBUG] ❌ All change methods failed")
        _write_variable_response(
            py_db, request, value="", success=False, 
            message="Unable to change: %s." % (variable_name,)
        )
        return

    # ✅ 성공 시 응답 생성
    print(f"[CHANGE-DEBUG] ✅ change_variable returned: {type(child_var).__name__}")
    
    # 최종 변수 데이터 가져오기
    if final_var_data is None:
        try:
            final_var_data = child_var.get_var_data(fmt=fmt)
        except Exception as data_error:
            print(f"[CHANGE-DEBUG] ❌ get_var_data failed: {data_error}")
            _write_variable_response(
                py_db, request, value="", success=False,
                message="Failed to get updated variable data: %s" % str(data_error)
            )
            return
    
    print(f"[CHANGE-DEBUG] 📊 Getting updated variable data...")
    print(f"[CHANGE-DEBUG] ✅ var_data retrieved:")
    print(f"[CHANGE-DEBUG]   value: '{final_var_data.get('value', 'N/A')}'")
    print(f"[CHANGE-DEBUG]   type: '{final_var_data.get('type', 'N/A')}'")
    print(f"[CHANGE-DEBUG]   variablesReference: {final_var_data.get('variablesReference', 0)}")
    
    # 🚀 UI 새로고침을 위한 응답 구조
    print(f"[CHANGE-DEBUG] 🔄 Applying container-specific UI refresh...")
    
    response_value = final_var_data.get("value", "")
    response_type = final_var_data.get("type") or "unknown"  # null 방지
    response_ref = final_var_data.get("variablesReference", 0)
    
    body_kwargs = {
        "value": str(response_value),
        "type": response_type,
        "variablesReference": response_ref,
    }
    
    # 선택적 필드들 추가
    if final_var_data.get("namedVariables") is not None:
        body_kwargs["namedVariables"] = final_var_data["namedVariables"]
    if final_var_data.get("indexedVariables") is not None:
        body_kwargs["indexedVariables"] = final_var_data["indexedVariables"]
    
    body = SetVariableResponseBody(**body_kwargs)
    
    print(f"[CHANGE-DEBUG] 📤 Creating container-optimized response...")
    variables_response = pydevd_base_schema.build_response(request, kwargs={"body": body})
    
    # 표준 응답 전송
    py_db.writer.add_command(NetCommand(CMD_RETURN, 0, variables_response, is_json=True))
    
    # 🚀 UI 새로고침 이벤트 전송 (핵심!)
    try:
        thread_id = py_db.suspended_frames_manager.get_thread_id_for_variable_reference(variables_reference)
        if thread_id:
            # Variables 창 무효화 이벤트
            invalidate_event = {
                "type": "event",
                "event": "invalidated",
                "body": {
                    "areas": ["variables"],
                    "threadId": thread_id,
                    "stackFrameId": variables_reference
                }
            }
            py_db.writer.add_command(NetCommand(CMD_RETURN, 0, invalidate_event, is_json=True))
            print(f"[UI-REFRESH] 🔄 Variables invalidation event sent for thread {thread_id}")
            
            # Console 알림 이벤트
            output_event = {
                "type": "event",
                "event": "output",
                "body": {
                    "category": "console",
                    "output": f"✅ {variable_name} = {response_value}\n"
                }
            }
            py_db.writer.add_command(NetCommand(CMD_RETURN, 0, output_event, is_json=True))
            print(f"[UI-REFRESH] 📢 Output notification sent: {variable_name} = {response_value}")
            
        else:
            print(f"[UI-REFRESH] ⚠️ No thread_id found for UI refresh")
            
    except Exception as refresh_error:
        print(f"[UI-REFRESH] ⚠️ UI refresh events failed (not critical): {refresh_error}")
    
    print(f"[CHANGE-DEBUG] ✅ Container-specific UI refresh applied")
    print(f"[CHANGE-SUCCESS] 🎉 Variable '{variable_name}' ({type(variable).__name__}) changed to '{response_value}'")
    print(f"[CHANGE-SUCCESS] 📱 Container-specific UI refresh applied")


def _write_variable_response(py_db, request, value, success, message):
    """SetVariable 오류 응답 (개선된 버전)"""
    print(f"[CHANGE-DEBUG] ❌ Writing error response:")
    print(f"[CHANGE-DEBUG]   success: {success}")
    print(f"[CHANGE-DEBUG]   message: '{message}'")
    print(f"[CHANGE-DEBUG]   value: '{value}'")
    
    body = SetVariableResponseBody(value=value, type="<e>", variablesReference=0)
    variables_response = pydevd_base_schema.build_response(
        request, 
        kwargs={
            "body": body, 
            "success": success, 
            "message": message
        }
    )
    py_db.writer.add_command(NetCommand(CMD_RETURN, 0, variables_response, is_json=True))
    
    # 오류도 사용자에게 알림
    if not success:
        error_output = {
            "type": "event",
            "event": "output",
            "body": {
                "category": "stderr", 
                "output": f"❌ Variable change failed: {message}\n"
            }
        }
        py_db.writer.add_command(NetCommand(CMD_RETURN, 0, error_output, is_json=True))
        print(f"[CHANGE-DEBUG] ❌ Error response sent to VS Code")


def _create_presentation_hint_for_changed_variable(variable_name, var_type, scope):
    """변경된 변수를 위한 presentationHint 생성"""
    hint = {
        "kind": "data",
        "attributes": ["modified"]  # 수정됨을 명시
    }
    
    # 변수 타입별 힌트
    if var_type in ["list", "dict", "set", "tuple"]:
        hint["attributes"].append("hasObjectId")
    elif var_type in ["int", "float", "str", "bool"]:
        hint["kind"] = "data"
    elif var_type in ["function", "method"]:
        hint["kind"] = "method"
        hint["attributes"].append("readOnly")
    
    # Scope별 가시성
    if scope and hasattr(scope, 'scope'):
        if scope.scope == "locals":
            hint["visibility"] = "public"
        elif scope.scope == "globals":
            hint["visibility"] = "internal"
    else:
        hint["visibility"] = "public"
    
    return hint


def _apply_frame_variable_ui_refresh(py_db, variables_reference, variable_name, var_data):
    """_FrameVariable 컨테이너의 UI 새로고침"""
    print(f"[FRAME-REFRESH] Applying frame variable refresh for '{variable_name}'")
    
    # Frame 변수는 일반적으로 locals/globals scope
    thread_id = py_db.suspended_frames_manager.get_thread_id_for_variable_reference(variables_reference)
    if thread_id:
        # 특정 프레임의 변수 영역 무효화
        invalidate_event = {
            "type": "event",
            "event": "invalidated", 
            "body": {
                "areas": ["variables"],
                "threadId": thread_id,
                "stackFrameId": variables_reference
            }
        }
        py_db.writer.add_command(NetCommand(CMD_RETURN, 0, invalidate_event, is_json=True))
        print(f"[FRAME-REFRESH] Frame invalidation event sent")


def _apply_object_variable_ui_refresh(py_db, variables_reference, variable_name, var_data):
    """_ObjectVariable 컨테이너의 UI 새로고침"""
    print(f"[OBJECT-REFRESH] Applying object variable refresh for '{variable_name}'")
    
    # 객체 변수는 부모 컨테이너도 새로고침 필요할 수 있음
    thread_id = py_db.suspended_frames_manager.get_thread_id_for_variable_reference(variables_reference)
    if thread_id:
        # 현재 변수 참조 영역 무효화
        invalidate_event = {
            "type": "event",
            "event": "invalidated",
            "body": {
                "areas": ["variables"],
                "threadId": thread_id
            }
        }
        py_db.writer.add_command(NetCommand(CMD_RETURN, 0, invalidate_event, is_json=True))
        print(f"[OBJECT-REFRESH] Object invalidation event sent")


def _apply_generic_variable_ui_refresh(py_db, variables_reference, variable_name, var_data):
    """일반 변수 컨테이너의 UI 새로고침"""
    print(f"[GENERIC-REFRESH] Applying generic variable refresh for '{variable_name}'")
    
    thread_id = py_db.suspended_frames_manager.get_thread_id_for_variable_reference(variables_reference)
    if thread_id:
        invalidate_event = {
            "type": "event",
            "event": "invalidated",
            "body": {
                "areas": ["variables"],
                "threadId": thread_id
            }
        }
        py_db.writer.add_command(NetCommand(CMD_RETURN, 0, invalidate_event, is_json=True))
        print(f"[GENERIC-REFRESH] Generic invalidation event sent")


def _trigger_ui_refresh_events(py_db, variables_reference, variable_name, var_data):
    """VS Code UI 새로고침을 위한 이벤트들 전송"""
    try:
        thread_id = py_db.suspended_frames_manager.get_thread_id_for_variable_reference(variables_reference)
        if not thread_id:
            print(f"[UI-REFRESH] ⚠️ No thread_id found for variables_reference {variables_reference}")
            return
        
        # 🚀 방법 1: Variables 영역 무효화 (가장 효과적)
        invalidate_event = {
            "type": "event",
            "event": "invalidated",
            "body": {
                "areas": ["variables"],  # Variables 창 새로고침
                "threadId": thread_id,
                "stackFrameId": variables_reference
            }
        }
        
        py_db.writer.add_command(NetCommand(CMD_RETURN, 0, invalidate_event, is_json=True))
        print(f"[UI-REFRESH] 🔄 Variables invalidation event sent for thread {thread_id}")
        
        # 🚀 방법 2: Output 이벤트로 사용자 알림
        value_preview = str(var_data.get("value", ""))[:50]
        if len(str(var_data.get("value", ""))) > 50:
            value_preview += "..."
            
        output_event = {
            "type": "event",
            "event": "output",
            "body": {
                "category": "console",
                "output": f"✅ {variable_name} = {value_preview}\n"
            }
        }
        
        py_db.writer.add_command(NetCommand(CMD_RETURN, 0, output_event, is_json=True))
        print(f"[UI-REFRESH] 📢 Output notification sent: {variable_name} = {value_preview}")
        
    except Exception as refresh_error:
        print(f"[UI-REFRESH] ❌ UI refresh events failed: {refresh_error}")

@silence_warnings_decorator
def internal_get_frame(dbg, seq, thread_id, frame_id):
    """Converts request into python variable"""
    try:
        frame = dbg.find_frame(thread_id, frame_id)
        if frame is not None:
            hidden_ns = pydevconsole.get_ipython_hidden_vars()
            xml = "<xml>"
            xml += pydevd_xml.frame_vars_to_xml(frame.f_locals, hidden_ns)
            del frame
            xml += "</xml>"
            cmd = dbg.cmd_factory.make_get_frame_message(seq, xml)
            dbg.writer.add_command(cmd)
        else:
            # pydevd_vars.dump_frames(thread_id)
            # don't print this error: frame not found: means that the client is not synchronized (but that's ok)
            cmd = dbg.cmd_factory.make_error_message(seq, "Frame not found: %s from thread: %s" % (frame_id, thread_id))
            dbg.writer.add_command(cmd)
    except:
        cmd = dbg.cmd_factory.make_error_message(seq, "Error resolving frame: %s from thread: %s" % (frame_id, thread_id))
        dbg.writer.add_command(cmd)


def internal_get_smart_step_into_variants(dbg, seq, thread_id, frame_id, start_line, end_line, set_additional_thread_info):
    try:
        thread = pydevd_find_thread_by_id(thread_id)
        frame = dbg.find_frame(thread_id, frame_id)

        if thread is None or frame is None:
            cmd = dbg.cmd_factory.make_error_message(seq, "Frame not found: %s from thread: %s" % (frame_id, thread_id))
            dbg.writer.add_command(cmd)
            return

        if pydevd_bytecode_utils is None:
            variants = []
        else:
            variants = pydevd_bytecode_utils.calculate_smart_step_into_variants(frame, int(start_line), int(end_line))

        info = set_additional_thread_info(thread)

        # Store the last request (may be used afterwards when stepping).
        info.pydev_smart_step_into_variants = tuple(variants)
        xml = "<xml>"

        for variant in variants:
            if variant.children_variants:
                for child_variant in variant.children_variants:
                    # If there are child variants, the current one is just an intermediary, so,
                    # just create variants for the child (notifying properly about the parent too).
                    xml += (
                        '<variant name="%s" isVisited="%s" line="%s" offset="%s" childOffset="%s" callOrder="%s" endlineno="%s" startcol="%s" endcol="%s"/>'
                        % (
                            quote(child_variant.name),
                            str(child_variant.is_visited).lower(),
                            child_variant.line,
                            variant.offset,
                            child_variant.offset,
                            child_variant.call_order,
                            variant.endlineno,
                            variant.startcol,
                            variant.endcol,
                        )
                    )
            else:
                xml += (
                    '<variant name="%s" isVisited="%s" line="%s" offset="%s" childOffset="-1" callOrder="%s" endlineno="%s" startcol="%s" endcol="%s"/>'
                    % (
                        quote(variant.name),
                        str(variant.is_visited).lower(),
                        variant.line,
                        variant.offset,
                        variant.call_order,
                        variant.endlineno,
                        variant.startcol,
                        variant.endcol,
                    )
                )

        xml += "</xml>"
        cmd = NetCommand(CMD_GET_SMART_STEP_INTO_VARIANTS, seq, xml)
        dbg.writer.add_command(cmd)
    except:
        # Error is expected (if `dis` module cannot be used -- i.e.: Jython).
        pydev_log.exception("Error calculating Smart Step Into Variants.")
        cmd = dbg.cmd_factory.make_error_message(
            seq, "Error getting smart step into variants for frame: %s from thread: %s" % (frame_id, thread_id)
        )
        dbg.writer.add_command(cmd)


def internal_get_step_in_targets_json(dbg, seq, thread_id, frame_id, request, set_additional_thread_info):
    try:
        thread = pydevd_find_thread_by_id(thread_id)
        frame = dbg.find_frame(thread_id, frame_id)

        if thread is None or frame is None:
            body = StepInTargetsResponseBody([])
            variables_response = pydevd_base_schema.build_response(
                request, kwargs={"body": body, "success": False, "message": "Thread to get step in targets seems to have resumed already."}
            )
            cmd = NetCommand(CMD_RETURN, 0, variables_response, is_json=True)
            dbg.writer.add_command(cmd)
            return

        start_line = 0
        end_line = 99999999
        if pydevd_bytecode_utils is None:
            variants = []
        else:
            variants = pydevd_bytecode_utils.calculate_smart_step_into_variants(frame, start_line, end_line)

        info = set_additional_thread_info(thread)
        targets = []
        counter = itertools.count(0)
        target_id_to_variant = {}
        for variant in variants:
            if not variant.is_visited:
                if variant.children_variants:
                    for child_variant in variant.children_variants:
                        target_id = next(counter)

                        if child_variant.call_order > 1:
                            targets.append(
                                StepInTarget(
                                    id=target_id,
                                    label="%s (call %s)" % (child_variant.name, child_variant.call_order),
                                )
                            )
                        else:
                            targets.append(StepInTarget(id=target_id, label=child_variant.name))
                        target_id_to_variant[target_id] = child_variant

                        if len(targets) >= 15:  # Show at most 15 targets.
                            break
                else:
                    target_id = next(counter)
                    if variant.call_order > 1:
                        targets.append(
                            StepInTarget(
                                id=target_id,
                                label="%s (call %s)" % (variant.name, variant.call_order),
                            )
                        )
                    else:
                        targets.append(StepInTarget(id=target_id, label=variant.name))
                    target_id_to_variant[target_id] = variant

                    if len(targets) >= 15:  # Show at most 15 targets.
                        break

        # Store the last request (may be used afterwards when stepping).
        info.pydev_smart_step_into_variants = tuple(variants)
        info.target_id_to_smart_step_into_variant = target_id_to_variant

        body = StepInTargetsResponseBody(targets=targets)
        response = pydevd_base_schema.build_response(request, kwargs={"body": body})
        cmd = NetCommand(CMD_RETURN, 0, response, is_json=True)
        dbg.writer.add_command(cmd)
    except Exception as e:
        # Error is expected (if `dis` module cannot be used -- i.e.: Jython).
        pydev_log.exception("Error calculating Smart Step Into Variants.")
        body = StepInTargetsResponseBody([])
        variables_response = pydevd_base_schema.build_response(request, kwargs={"body": body, "success": False, "message": str(e)})
        cmd = NetCommand(CMD_RETURN, 0, variables_response, is_json=True)
        dbg.writer.add_command(cmd)


def internal_get_next_statement_targets(dbg, seq, thread_id, frame_id):
    """gets the valid line numbers for use with set next statement"""
    try:
        frame = dbg.find_frame(thread_id, frame_id)
        if frame is not None:
            code = frame.f_code
            xml = "<xml>"
            try:
                linestarts = dis.findlinestarts(code)
            except:
                # i.e.: jython doesn't provide co_lnotab, so, we can only keep at the current line.
                xml += "<line>%d</line>" % (frame.f_lineno,)
            else:
                for _, line in linestarts:
                    if line is not None:
                        xml += "<line>%d</line>" % (line,)
            del frame
            xml += "</xml>"
            cmd = dbg.cmd_factory.make_get_next_statement_targets_message(seq, xml)
            dbg.writer.add_command(cmd)
        else:
            cmd = dbg.cmd_factory.make_error_message(seq, "Frame not found: %s from thread: %s" % (frame_id, thread_id))
            dbg.writer.add_command(cmd)
    except:
        cmd = dbg.cmd_factory.make_error_message(seq, "Error resolving frame: %s from thread: %s" % (frame_id, thread_id))
        dbg.writer.add_command(cmd)


def _evaluate_response(py_db, request, result, error_message=""):
    is_error = isinstance(result, ExceptionOnEvaluate)
    if is_error:
        result = result.result
    if not error_message:
        body = pydevd_schema.EvaluateResponseBody(result=result, variablesReference=0)
        variables_response = pydevd_base_schema.build_response(request, kwargs={"body": body})
        py_db.writer.add_command(NetCommand(CMD_RETURN, 0, variables_response, is_json=True))
    else:
        body = pydevd_schema.EvaluateResponseBody(result=result, variablesReference=0)
        variables_response = pydevd_base_schema.build_response(request, kwargs={"body": body, "success": False, "message": error_message})
        py_db.writer.add_command(NetCommand(CMD_RETURN, 0, variables_response, is_json=True))


_global_frame = None


def internal_evaluate_expression_json(py_db, request, thread_id):
    """
    :param EvaluateRequest request:
    """
    global _global_frame
    # : :type arguments: EvaluateArguments

    arguments = request.arguments
    expression = arguments.expression
    frame_id = arguments.frameId
    context = arguments.context
    fmt = arguments.format
    if hasattr(fmt, "to_dict"):
        fmt = fmt.to_dict()

    ctx = NULL
    if context == "repl":
        if not py_db.is_output_redirected:
            ctx = pydevd_io.redirect_stream_to_pydb_io_messages_context()
    else:
        # If we're not in a repl (watch, hover, ...) don't show warnings.
        ctx = filter_all_warnings()

    with ctx:
        try_exec = False
        if frame_id is None:
            if _global_frame is None:
                # Lazily create a frame to be used for evaluation with no frame id.

                def __create_frame():
                    yield sys._getframe()

                _global_frame = next(__create_frame())

            frame = _global_frame
            try_exec = True  # Always exec in this case
            eval_result = None
        else:
            frame = py_db.find_frame(thread_id, frame_id)

            eval_result = pydevd_vars.evaluate_expression(py_db, frame, expression, is_exec=False)
            is_error = isinstance_checked(eval_result, ExceptionOnEvaluate)
            if is_error:
                if context == "hover":  # In a hover it doesn't make sense to do an exec.
                    _evaluate_response(py_db, request, result="", error_message="Exception occurred during evaluation.")
                    return
                elif context == "watch":
                    # If it's a watch, don't show it as an exception object, rather, format
                    # it and show it as a string (with success=False).
                    msg = "%s: %s" % (
                        eval_result.result.__class__.__name__,
                        eval_result.result,
                    )
                    _evaluate_response(py_db, request, result=msg, error_message=msg)
                    return
                else:
                    # We only try the exec if the failure we had was due to not being able
                    # to evaluate the expression.
                    try:
                        pydevd_vars.compile_as_eval(expression)
                    except Exception:
                        try_exec = context == "repl"
                    else:
                        try_exec = False
                        if context == "repl":
                            # In the repl we should show the exception to the user.
                            _evaluate_response_return_exception(py_db, request, eval_result.etype, eval_result.result, eval_result.tb)
                            return

        if try_exec:
            try:
                pydevd_vars.evaluate_expression(py_db, frame, expression, is_exec=True)
            except (Exception, KeyboardInterrupt):
                _evaluate_response_return_exception(py_db, request, *sys.exc_info())
                return
            # No result on exec.
            _evaluate_response(py_db, request, result="")
            return

        # Ok, we have the result (could be an error), let's put it into the saved variables.
        frame_tracker = py_db.suspended_frames_manager.get_frame_tracker(thread_id)
        if frame_tracker is None:
            # This is not really expected.
            _evaluate_response(py_db, request, result="", error_message="Thread id: %s is not current thread id." % (thread_id,))
            return

        safe_repr_custom_attrs = {}
        if context == "clipboard":
            safe_repr_custom_attrs = dict(
                maxstring_outer=2**64,
                maxstring_inner=2**64,
                maxother_outer=2**64,
                maxother_inner=2**64,
            )

        if context == "repl" and eval_result is None:
            # We don't want "None" to appear when typing in the repl.
            body = pydevd_schema.EvaluateResponseBody(
                result="",
                variablesReference=0,
            )

        else:
            variable = frame_tracker.obtain_as_variable(expression, eval_result, frame=frame)
            var_data = variable.get_var_data(fmt=fmt, context=context, **safe_repr_custom_attrs)

            body = pydevd_schema.EvaluateResponseBody(
                result=var_data["value"],
                variablesReference=var_data.get("variablesReference", 0),
                type=var_data.get("type"),
                presentationHint=var_data.get("presentationHint"),
                namedVariables=var_data.get("namedVariables"),
                indexedVariables=var_data.get("indexedVariables"),
            )
        variables_response = pydevd_base_schema.build_response(request, kwargs={"body": body})
        py_db.writer.add_command(NetCommand(CMD_RETURN, 0, variables_response, is_json=True))


def _evaluate_response_return_exception(py_db, request, exc_type, exc, initial_tb):
    try:
        tb = initial_tb

        # Show the traceback without pydevd frames.
        temp_tb = tb
        while temp_tb:
            if py_db.get_file_type(temp_tb.tb_frame) == PYDEV_FILE:
                tb = temp_tb.tb_next
            temp_tb = temp_tb.tb_next

        if tb is None:
            tb = initial_tb
        err = "".join(traceback.format_exception(exc_type, exc, tb))

        # Make sure we don't keep references to them.
        exc = None
        exc_type = None
        tb = None
        temp_tb = None
        initial_tb = None
    except:
        err = "<Internal error - unable to get traceback when evaluating expression>"
        pydev_log.exception(err)

    # Currently there is an issue in VSC where returning success=false for an
    # eval request, in repl context, VSC does not show the error response in
    # the debug console. So return the error message in result as well.
    _evaluate_response(py_db, request, result=err, error_message=err)


@silence_warnings_decorator
def internal_evaluate_expression(dbg, seq, thread_id, frame_id, expression, is_exec, trim_if_too_big, attr_to_set_result):
    """gets the value of a variable"""
    try:
        frame = dbg.find_frame(thread_id, frame_id)
        if frame is not None:
            result = pydevd_vars.evaluate_expression(dbg, frame, expression, is_exec)
            if attr_to_set_result != "":
                pydevd_vars.change_attr_expression(frame, attr_to_set_result, expression, dbg, result)
        else:
            result = None

        xml = "<xml>"
        xml += pydevd_xml.var_to_xml(result, expression, trim_if_too_big)
        xml += "</xml>"
        cmd = dbg.cmd_factory.make_evaluate_expression_message(seq, xml)
        dbg.writer.add_command(cmd)
    except:
        exc = get_exception_traceback_str()
        cmd = dbg.cmd_factory.make_error_message(seq, "Error evaluating expression " + exc)
        dbg.writer.add_command(cmd)


def _set_expression_response(py_db, request, error_message):
    body = pydevd_schema.SetExpressionResponseBody(value="")
    variables_response = pydevd_base_schema.build_response(request, kwargs={"body": body, "success": False, "message": error_message})
    py_db.writer.add_command(NetCommand(CMD_RETURN, 0, variables_response, is_json=True))


def internal_set_expression_json(py_db, request, thread_id):
    # : :type arguments: SetExpressionArguments

    arguments = request.arguments
    expression = arguments.expression
    frame_id = arguments.frameId
    value = arguments.value
    fmt = arguments.format
    if hasattr(fmt, "to_dict"):
        fmt = fmt.to_dict()

    frame = py_db.find_frame(thread_id, frame_id)
    exec_code = "%s = (%s)" % (expression, value)
    try:
        pydevd_vars.evaluate_expression(py_db, frame, exec_code, is_exec=True)
    except (Exception, KeyboardInterrupt):
        _set_expression_response(py_db, request, error_message="Error executing: %s" % (exec_code,))
        return

    # Ok, we have the result (could be an error), let's put it into the saved variables.
    frame_tracker = py_db.suspended_frames_manager.get_frame_tracker(thread_id)
    if frame_tracker is None:
        # This is not really expected.
        _set_expression_response(py_db, request, error_message="Thread id: %s is not current thread id." % (thread_id,))
        return

    # Now that the exec is done, get the actual value changed to return.
    result = pydevd_vars.evaluate_expression(py_db, frame, expression, is_exec=False)
    variable = frame_tracker.obtain_as_variable(expression, result, frame=frame)
    var_data = variable.get_var_data(fmt=fmt)

    body = pydevd_schema.SetExpressionResponseBody(
        value=var_data["value"],
        variablesReference=var_data.get("variablesReference", 0),
        type=var_data.get("type"),
        presentationHint=var_data.get("presentationHint"),
        namedVariables=var_data.get("namedVariables"),
        indexedVariables=var_data.get("indexedVariables"),
    )
    variables_response = pydevd_base_schema.build_response(request, kwargs={"body": body})
    py_db.writer.add_command(NetCommand(CMD_RETURN, 0, variables_response, is_json=True))


def internal_get_completions(dbg, seq, thread_id, frame_id, act_tok, line=-1, column=-1):
    """
    Note that if the column is >= 0, the act_tok is considered text and the actual
    activation token/qualifier is computed in this command.
    """
    try:
        remove_path = None
        try:
            qualifier = ""
            if column >= 0:
                token_and_qualifier = extract_token_and_qualifier(act_tok, line, column)
                act_tok = token_and_qualifier[0]
                if act_tok:
                    act_tok += "."
                qualifier = token_and_qualifier[1]

            frame = dbg.find_frame(thread_id, frame_id)
            if frame is not None:
                completions = _pydev_completer.generate_completions(frame, act_tok)

                # Note that qualifier and start are only actually valid for the
                # Debug Adapter Protocol (for the line-based protocol, the IDE
                # is required to filter the completions returned).
                cmd = dbg.cmd_factory.make_get_completions_message(seq, completions, qualifier, start=column - len(qualifier))
                dbg.writer.add_command(cmd)
            else:
                cmd = dbg.cmd_factory.make_error_message(
                    seq, "internal_get_completions: Frame not found: %s from thread: %s" % (frame_id, thread_id)
                )
                dbg.writer.add_command(cmd)

        finally:
            if remove_path is not None:
                sys.path.remove(remove_path)

    except:
        exc = get_exception_traceback_str()
        sys.stderr.write("%s\n" % (exc,))
        cmd = dbg.cmd_factory.make_error_message(seq, "Error evaluating expression " + exc)
        dbg.writer.add_command(cmd)


def internal_get_description(dbg, seq, thread_id, frame_id, expression):
    """Fetch the variable description stub from the debug console"""
    try:
        frame = dbg.find_frame(thread_id, frame_id)
        description = pydevd_console.get_description(frame, thread_id, frame_id, expression)
        description = pydevd_xml.make_valid_xml_value(quote(description, "/>_= \t"))
        description_xml = '<xml><var name="" type="" value="%s"/></xml>' % description
        cmd = dbg.cmd_factory.make_get_description_message(seq, description_xml)
        dbg.writer.add_command(cmd)
    except:
        exc = get_exception_traceback_str()
        cmd = dbg.cmd_factory.make_error_message(seq, "Error in fetching description" + exc)
        dbg.writer.add_command(cmd)


def build_exception_info_response(dbg, thread_id, thread, request_seq, set_additional_thread_info, iter_visible_frames_info, max_frames):
    """
    :return ExceptionInfoResponse
    """
    additional_info = set_additional_thread_info(thread)
    topmost_frame = additional_info.get_topmost_frame(thread)

    current_paused_frame_name = ""

    source_path = ""  # This is an extra bit of data used by Visual Studio
    stack_str_lst = []
    name = None
    description = None

    if topmost_frame is not None:
        try:
            try:
                frames_list = dbg.suspended_frames_manager.get_frames_list(thread_id)
                while frames_list is not None and len(frames_list):
                    frames = []

                    frame = None

                    if not name:
                        exc_type = frames_list.exc_type
                        if exc_type is not None:
                            try:
                                name = exc_type.__qualname__
                            except:
                                try:
                                    name = exc_type.__name__
                                except:
                                    try:
                                        name = str(exc_type)
                                    except:
                                        pass

                    if not description:
                        exc_desc = frames_list.exc_desc
                        if exc_desc is not None:
                            try:
                                description = str(exc_desc)
                            except:
                                pass

                    for (
                        frame_id,
                        frame,
                        method_name,
                        original_filename,
                        filename_in_utf8,
                        lineno,
                        _applied_mapping,
                        show_as_current_frame,
                        line_col_info,
                    ) in iter_visible_frames_info(dbg, frames_list):
                        line_text = linecache.getline(original_filename, lineno)

                        # Never filter out plugin frames!
                        if not getattr(frame, "IS_PLUGIN_FRAME", False):
                            if dbg.is_files_filter_enabled and dbg.apply_files_filter(frame, original_filename, False):
                                continue

                        if show_as_current_frame:
                            current_paused_frame_name = method_name
                            method_name += " (Current frame)"
                        frames.append((filename_in_utf8, lineno, method_name, line_text, line_col_info))

                    if not source_path and frames:
                        source_path = frames[0][0]

                    if IS_PY311_OR_GREATER:
                        stack_summary = traceback.StackSummary()
                        for filename_in_utf8, lineno, method_name, line_text, line_col_info in frames[-max_frames:]:
                            if line_col_info is not None:
                                # End line might mean that we have a multiline statement.
                                if line_col_info.end_lineno is not None and lineno < line_col_info.end_lineno:
                                    line_text = "\n".join(linecache.getlines(filename_in_utf8)[lineno : line_col_info.end_lineno + 1])
                                frame_summary = traceback.FrameSummary(
                                    filename_in_utf8, 
                                    lineno, 
                                    method_name, 
                                    line=line_text, 
                                    end_lineno=line_col_info.end_lineno, 
                                    colno=line_col_info.colno, 
                                    end_colno=line_col_info.end_colno)
                                stack_summary.append(frame_summary)
                            else:
                                frame_summary = traceback.FrameSummary(filename_in_utf8, lineno, method_name, line=line_text)
                                stack_summary.append(frame_summary)

                        stack_str = "".join(stack_summary.format())

                    else:
                        # Note: remove col info (just used in 3.11).
                        stack_str = "".join(traceback.format_list((x[:-1] for x in frames[-max_frames:])))

                    try:
                        stype = frames_list.exc_type.__qualname__
                        smod = frames_list.exc_type.__module__
                        if smod not in ("__main__", "builtins"):
                            if not isinstance(smod, str):
                                smod = "<unknown>"
                            stype = smod + "." + stype
                    except Exception:
                        stype = "<unable to get exception type>"
                        pydev_log.exception("Error getting exception type.")

                    stack_str += "%s: %s\n" % (stype, frames_list.exc_desc)
                    stack_str += frames_list.exc_context_msg
                    stack_str_lst.append(stack_str)

                    frames_list = frames_list.chained_frames_list
                    if frames_list is None or not frames_list:
                        break

            except:
                pydev_log.exception("Error on build_exception_info_response.")
        finally:
            topmost_frame = None
    full_stack_str = "".join(reversed(stack_str_lst))

    if not name:
        name = "exception: type unknown"
    if not description:
        description = "exception: no description"

    if current_paused_frame_name:
        name += "       (note: full exception trace is shown but execution is paused at: %s)" % (current_paused_frame_name,)

    if thread.stop_reason == CMD_STEP_CAUGHT_EXCEPTION:
        break_mode = pydevd_schema.ExceptionBreakMode.ALWAYS
    else:
        break_mode = pydevd_schema.ExceptionBreakMode.UNHANDLED

    response = pydevd_schema.ExceptionInfoResponse(
        request_seq=request_seq,
        success=True,
        command="exceptionInfo",
        body=pydevd_schema.ExceptionInfoResponseBody(
            exceptionId=name,
            description=description,
            breakMode=break_mode,
            details=pydevd_schema.ExceptionDetails(
                message=description,
                typeName=name,
                stackTrace=full_stack_str,
                source=source_path,
                # Note: ExceptionDetails actually accepts an 'innerException', but
                # when passing it, VSCode is not showing the stack trace at all.
            ),
        ),
    )
    return response


def internal_get_exception_details_json(
    dbg, request, thread_id, thread, max_frames, set_additional_thread_info=None, iter_visible_frames_info=None
):
    """Fetch exception details"""
    try:
        response = build_exception_info_response(
            dbg, thread_id, thread, request.seq, set_additional_thread_info, iter_visible_frames_info, max_frames
        )
    except:
        exc = get_exception_traceback_str()
        response = pydevd_base_schema.build_response(request, kwargs={"success": False, "message": exc, "body": {}})
    dbg.writer.add_command(NetCommand(CMD_RETURN, 0, response, is_json=True))


class InternalGetBreakpointException(InternalThreadCommand):
    """Send details of exception raised while evaluating conditional breakpoint"""

    def __init__(self, thread_id, exc_type, stacktrace):
        self.sequence = 0
        self.thread_id = thread_id
        self.stacktrace = stacktrace
        self.exc_type = exc_type

    def do_it(self, dbg):
        try:
            callstack = "<xml>"

            makeValid = pydevd_xml.make_valid_xml_value

            for filename, line, methodname, methodobj in self.stacktrace:
                if not filesystem_encoding_is_utf8 and hasattr(filename, "decode"):
                    # filename is a byte string encoded using the file system encoding
                    # convert it to utf8
                    filename = filename.decode(file_system_encoding).encode("utf-8")

                callstack += '<frame thread_id = "%s" file="%s" line="%s" name="%s" obj="%s" />' % (
                    self.thread_id,
                    makeValid(filename),
                    line,
                    makeValid(methodname),
                    makeValid(methodobj),
                )
            callstack += "</xml>"

            cmd = dbg.cmd_factory.make_send_breakpoint_exception_message(self.sequence, self.exc_type + "\t" + callstack)
            dbg.writer.add_command(cmd)
        except:
            exc = get_exception_traceback_str()
            sys.stderr.write("%s\n" % (exc,))
            cmd = dbg.cmd_factory.make_error_message(self.sequence, "Error Sending Exception: " + exc)
            dbg.writer.add_command(cmd)


class InternalSendCurrExceptionTrace(InternalThreadCommand):
    """Send details of the exception that was caught and where we've broken in."""

    def __init__(self, thread_id, arg, curr_frame_id):
        """
        :param arg: exception type, description, traceback object
        """
        self.sequence = 0
        self.thread_id = thread_id
        self.curr_frame_id = curr_frame_id
        self.arg = arg

    def do_it(self, dbg):
        try:
            cmd = dbg.cmd_factory.make_send_curr_exception_trace_message(dbg, self.sequence, self.thread_id, self.curr_frame_id, *self.arg)
            del self.arg
            dbg.writer.add_command(cmd)
        except:
            exc = get_exception_traceback_str()
            sys.stderr.write("%s\n" % (exc,))
            cmd = dbg.cmd_factory.make_error_message(self.sequence, "Error Sending Current Exception Trace: " + exc)
            dbg.writer.add_command(cmd)


class InternalSendCurrExceptionTraceProceeded(InternalThreadCommand):
    """Send details of the exception that was caught and where we've broken in."""

    def __init__(self, thread_id):
        self.sequence = 0
        self.thread_id = thread_id

    def do_it(self, dbg):
        try:
            cmd = dbg.cmd_factory.make_send_curr_exception_trace_proceeded_message(self.sequence, self.thread_id)
            dbg.writer.add_command(cmd)
        except:
            exc = get_exception_traceback_str()
            sys.stderr.write("%s\n" % (exc,))
            cmd = dbg.cmd_factory.make_error_message(self.sequence, "Error Sending Current Exception Trace Proceeded: " + exc)
            dbg.writer.add_command(cmd)


class InternalEvaluateConsoleExpression(InternalThreadCommand):
    """Execute the given command in the debug console"""

    def __init__(self, seq, thread_id, frame_id, line, buffer_output=True):
        self.sequence = seq
        self.thread_id = thread_id
        self.frame_id = frame_id
        self.line = line
        self.buffer_output = buffer_output

    def do_it(self, dbg):
        """Create an XML for console output, error and more (true/false)
        <xml>
            <output message=output_message></output>
            <error message=error_message></error>
            <more>true/false</more>
        </xml>
        """
        try:
            frame = dbg.find_frame(self.thread_id, self.frame_id)
            if frame is not None:
                console_message = pydevd_console.execute_console_command(
                    frame, self.thread_id, self.frame_id, self.line, self.buffer_output
                )

                cmd = dbg.cmd_factory.make_send_console_message(self.sequence, console_message.to_xml())
            else:
                from _pydevd_bundle.pydevd_console import ConsoleMessage

                console_message = ConsoleMessage()
                console_message.add_console_message(
                    pydevd_console.CONSOLE_ERROR,
                    "Select the valid frame in the debug view (thread: %s, frame: %s invalid)" % (self.thread_id, self.frame_id),
                )
                cmd = dbg.cmd_factory.make_error_message(self.sequence, console_message.to_xml())
        except:
            exc = get_exception_traceback_str()
            cmd = dbg.cmd_factory.make_error_message(self.sequence, "Error evaluating expression " + exc)
        dbg.writer.add_command(cmd)


class InternalRunCustomOperation(InternalThreadCommand):
    """Run a custom command on an expression"""

    def __init__(self, seq, thread_id, frame_id, scope, attrs, style, encoded_code_or_file, fnname):
        self.sequence = seq
        self.thread_id = thread_id
        self.frame_id = frame_id
        self.scope = scope
        self.attrs = attrs
        self.style = style
        self.code_or_file = unquote_plus(encoded_code_or_file)
        self.fnname = fnname

    def do_it(self, dbg):
        try:
            res = pydevd_vars.custom_operation(
                dbg, self.thread_id, self.frame_id, self.scope, self.attrs, self.style, self.code_or_file, self.fnname
            )
            resEncoded = quote_plus(res)
            cmd = dbg.cmd_factory.make_custom_operation_message(self.sequence, resEncoded)
            dbg.writer.add_command(cmd)
        except:
            exc = get_exception_traceback_str()
            cmd = dbg.cmd_factory.make_error_message(self.sequence, "Error in running custom operation" + exc)
            dbg.writer.add_command(cmd)


class InternalConsoleGetCompletions(InternalThreadCommand):
    """Fetch the completions in the debug console"""

    def __init__(self, seq, thread_id, frame_id, act_tok):
        self.sequence = seq
        self.thread_id = thread_id
        self.frame_id = frame_id
        self.act_tok = act_tok

    def do_it(self, dbg):
        """Get completions and write back to the client"""
        try:
            frame = dbg.find_frame(self.thread_id, self.frame_id)
            completions_xml = pydevd_console.get_completions(frame, self.act_tok)
            cmd = dbg.cmd_factory.make_send_console_message(self.sequence, completions_xml)
            dbg.writer.add_command(cmd)
        except:
            exc = get_exception_traceback_str()
            cmd = dbg.cmd_factory.make_error_message(self.sequence, "Error in fetching completions" + exc)
            dbg.writer.add_command(cmd)


class InternalConsoleExec(InternalThreadCommand):
    """gets the value of a variable"""

    def __init__(self, seq, thread_id, frame_id, expression):
        self.sequence = seq
        self.thread_id = thread_id
        self.frame_id = frame_id
        self.expression = expression

    def init_matplotlib_in_debug_console(self, py_db):
        # import hook and patches for matplotlib support in debug console
        from _pydev_bundle.pydev_import_hook import import_hook_manager

        if is_current_thread_main_thread():
            for module in list(py_db.mpl_modules_for_patching):
                import_hook_manager.add_module_name(module, py_db.mpl_modules_for_patching.pop(module))

    def do_it(self, py_db):
        if not py_db.mpl_hooks_in_debug_console and not py_db.gui_in_use:
            # add import hooks for matplotlib patches if only debug console was started
            try:
                self.init_matplotlib_in_debug_console(py_db)
                py_db.gui_in_use = True
            except:
                pydev_log.debug("Matplotlib support in debug console failed", traceback.format_exc())
            py_db.mpl_hooks_in_debug_console = True

        try:
            try:
                # Don't trace new threads created by console command.
                disable_trace_thread_modules()

                result = pydevconsole.console_exec(self.thread_id, self.frame_id, self.expression, py_db)
                xml = "<xml>"
                xml += pydevd_xml.var_to_xml(result, "")
                xml += "</xml>"
                cmd = py_db.cmd_factory.make_evaluate_expression_message(self.sequence, xml)
                py_db.writer.add_command(cmd)
            except:
                exc = get_exception_traceback_str()
                sys.stderr.write("%s\n" % (exc,))
                cmd = py_db.cmd_factory.make_error_message(self.sequence, "Error evaluating console expression " + exc)
                py_db.writer.add_command(cmd)
        finally:
            enable_trace_thread_modules()

            sys.stderr.flush()
            sys.stdout.flush()


class InternalLoadFullValue(InternalThreadCommand):
    """
    Loads values asynchronously
    """

    def __init__(self, seq, thread_id, frame_id, vars):
        self.sequence = seq
        self.thread_id = thread_id
        self.frame_id = frame_id
        self.vars = vars

    @silence_warnings_decorator
    def do_it(self, dbg):
        """Starts a thread that will load values asynchronously"""
        try:
            var_objects = []
            for variable in self.vars:
                variable = variable.strip()
                if len(variable) > 0:
                    if "\t" in variable:  # there are attributes beyond scope
                        scope, attrs = variable.split("\t", 1)
                        name = attrs[0]
                    else:
                        scope, attrs = (variable, None)
                        name = scope
                    var_obj = pydevd_vars.getVariable(dbg, self.thread_id, self.frame_id, scope, attrs)
                    var_objects.append((var_obj, name))

            t = GetValueAsyncThreadDebug(dbg, dbg, self.sequence, var_objects)
            t.start()
        except:
            exc = get_exception_traceback_str()
            sys.stderr.write("%s\n" % (exc,))
            cmd = dbg.cmd_factory.make_error_message(self.sequence, "Error evaluating variable %s " % exc)
            dbg.writer.add_command(cmd)


class AbstractGetValueAsyncThread(PyDBDaemonThread):
    """
    Abstract class for a thread, which evaluates values for async variables
    """

    def __init__(self, py_db, frame_accessor, seq, var_objects):
        PyDBDaemonThread.__init__(self, py_db)
        self.frame_accessor = frame_accessor
        self.seq = seq
        self.var_objs = var_objects
        self.cancel_event = ThreadingEvent()

    def send_result(self, xml):
        raise NotImplementedError()

    @overrides(PyDBDaemonThread._on_run)
    def _on_run(self):
        start = time.time()
        xml = StringIO()
        xml.write("<xml>")
        for var_obj, name in self.var_objs:
            current_time = time.time()
            if current_time - start > ASYNC_EVAL_TIMEOUT_SEC or self.cancel_event.is_set():
                break
            xml.write(pydevd_xml.var_to_xml(var_obj, name, evaluate_full_value=True))
        xml.write("</xml>")
        self.send_result(xml)
        xml.close()


class GetValueAsyncThreadDebug(AbstractGetValueAsyncThread):
    """
    A thread for evaluation async values, which returns result for debugger
    Create message and send it via writer thread
    """

    def send_result(self, xml):
        if self.frame_accessor is not None:
            cmd = self.frame_accessor.cmd_factory.make_load_full_value_message(self.seq, xml.getvalue())
            self.frame_accessor.writer.add_command(cmd)


class GetValueAsyncThreadConsole(AbstractGetValueAsyncThread):
    """
    A thread for evaluation async values, which returns result for Console
    Send result directly to Console's server
    """

    def send_result(self, xml):
        if self.frame_accessor is not None:
            self.frame_accessor.ReturnFullValue(self.seq, xml.getvalue())


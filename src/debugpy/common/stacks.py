# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

"""Provides facilities to dump all stacks of all threads in the process.
"""

import os
import sys
import time
import threading
import traceback
import json

from debugpy.common import log


def dump():
    """Dump stacks of all threads in this process, except for the current thread."""

    tid = threading.current_thread().ident
    pid = os.getpid()

    log.info("Dumping stacks for process {0}...", pid)

    for t_ident, frame in sys._current_frames().items():
        print("Dumping stack for thread {0} (tid={1}, pid={2})...".format(
            threading.current_thread().name, t_ident, pid))

        # if t_ident == tid:
        #     continue

        for t in threading.enumerate():
            if t.ident == tid:
                t_name = t.name
                t_daemon = t.daemon
                break
        else:
            t_name = t_daemon = "<unknown>"

        stack = traceback.format_stack(frame)

        parsed_stack = []

        for entry in stack:
            parts = entry.strip().split('\n')
            if len(parts) < 2:
                continue
            location_part = parts[0]
            code_line = parts[1].strip()
            
            # "  File "C:\\path\\to\\file.py", line 123, in func"
            file_info = location_part.strip().split(', ')
            file_path = file_info[0].split('"')[1]
            line_number = int(file_info[1].replace("line ", ""))
            function_name = file_info[2].replace("in ", "")
            
            parsed_stack.append({
                'file': file_path,
                'line': line_number,
                'function': function_name,
                'code': code_line
            })

        # parsed_stack[-1]
        
        with open("C:/Users/ky0413/Desktop/debugpy-1.8.14/stack_trace.txt", "a") as f:
            # 예시 출력
            for frame in parsed_stack:
                f.write(json.dumps(frame) + "\n")
            f.write("\n\n")

        log.info(
            "Stack of thread {0} (tid={1}, pid={2}, daemon={3}):\n\n{4}",
            t_name,
            t_ident,
            pid,
            t_daemon,
            stack,
        )

    log.info("Finished dumping stacks for process {0}.", pid)


def dump_after(secs):
    """Invokes dump() on a background thread after waiting for the specified time."""

    # print('Dumping stacks after {0} seconds...'.format(secs))

    # dump()

    # print('Finished dumping stacks.')

    def dumper():
        time.sleep(secs)
        try:
            dump()
        except:
            log.swallow_exception()

    thread = threading.Thread(target=dumper)
    thread.daemon = True
    thread.start()

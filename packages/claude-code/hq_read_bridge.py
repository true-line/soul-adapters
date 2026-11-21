"""Fixture-only translation from native Messages to a bound HQ inference client."""

from contextlib import closing
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import time

from initialize import wait_for_process_group_exit


class BridgeError(Exception):
    pass


class ReadBridge:
    def __init__(self, node, hq, read):
        self.read = read
        self.proc = subprocess.Popen(
            [str(node), str(Path(__file__).with_name("hq_read_broker.mjs")), str(hq),
             str(read.path), read.marker, read.tool_id],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, start_new_session=True, env={**os.environ, "HQ_INTERIM_OFFLINE": "1"},
        )
        self.buffer = b""
        try:
            if self._receive() != {"ready": True, "profile": "managed-cooperative"}:
                raise BridgeError("fixture HQ did not bind cooperatively")
        except BaseException:
            self.close(check=False)
            raise

    def _receive(self):
        deadline = time.monotonic() + 30
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                raise BridgeError("fixture HQ did not respond")
            part = os.read(self.proc.stdout.fileno(), 65536)
            if not part:
                raise BridgeError("fixture HQ closed before responding")
            self.buffer += part
            if len(self.buffer) > 1024 * 1024:
                raise BridgeError("fixture HQ frame exceeds the limit")
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def _send(self, value):
        data = memoryview((json.dumps(value) + "\n").encode())
        while data:
            written = self.proc.stdin.write(data)
            if not written:
                raise BridgeError("fixture HQ input closed")
            data = data[written:]

    def begin(self, request):
        # The three omitted vendor controls have no representation in the
        # candidate. The renamed output limit and every modeled value survive.
        self._send({"system": request["system"], "max_output_tokens": request["max_tokens"],
                    "tools": request["tools"], "messages": request["messages"]})

    def receive(self):
        return self._receive()

    def continue_after_observation(self):
        self._send({"continue": True})

    def close(self, check=True):
        # Reserve the leader PID until the whole group has been signaled.
        with closing(select.kqueue()) as events:
            watch = select.kevent(self.proc.pid, filter=select.KQ_FILTER_PROC,
                                  flags=select.KQ_EV_ADD, fflags=select.KQ_NOTE_EXIT)
            events.control([watch], 0, 0)
            try:
                if self.proc.stdin and not self.proc.stdin.closed:
                    self.proc.stdin.close()
                exited = bool(events.control(None, 1, 10))
            finally:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                code = self.proc.wait(timeout=5)
                wait_for_process_group_exit(self.proc.pid)
            if check and (not exited or code):
                raise BridgeError("fixture HQ failed its completed-operation assertions")

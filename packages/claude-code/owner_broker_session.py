"""Probe one stock ACP turn through HQ's owner-only protected Soul start.

This is a disposable-guest component probe. It does not issue physical T credit.
The Machine install packet supplies the exact paths and deterministic fixture.
"""

import argparse
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time

from initialize import PACKAGE, VERSION, check_eof, read_frame, wait_for_process_group_exit


def require_guest():
    if platform.system() != "Darwin" or platform.machine() != "arm64" or os.getuid() == 0:
        raise ValueError("requires an ordinary account in a disposable Darwin arm64 guest")
    model = subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.model"], timeout=5).strip()
    if not re.fullmatch(rb"VirtualMac[0-9]+,[0-9]+", model):
        raise ValueError("requires Apple virtual hardware")


def send(child, frame):
    child.stdin.write(json.dumps({"jsonrpc": "2.0", **frame}, separators=(",", ":")).encode() + b"\n")
    child.stdin.flush()


def reply(child, request_id, notices, transcript):
    for _ in range(256):
        frame = read_frame(child.stdout)
        if frame is None:
            raise ValueError(f"ACP ended before reply {request_id}")
        transcript.update(json.dumps(frame, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        if frame.get("method"):
            if "id" in frame and frame["id"] is not None:
                if frame["method"] == "session/request_permission":
                    send(child, {"id": frame["id"], "result": {"outcome": {"outcome": "cancelled"}}})
                else:
                    send(child, {"id": frame["id"], "error": {"code": -32601, "message": "method not found"}})
            else:
                notices(frame)
            continue
        if frame.get("id") != request_id or type(frame.get("id")) is not int:
            raise ValueError(f"unexpected ACP reply while waiting for {request_id}")
        if "error" in frame:
            error = frame["error"]
            code = error.get("code") if isinstance(error, dict) else None
            raise ValueError(f"ACP request {request_id} refused with code {code if type(code) is int else 'unknown'}")
        if set(frame) != {"jsonrpc", "id", "result"} or not isinstance(frame["result"], dict):
            raise ValueError(f"invalid ACP reply {request_id}")
        return frame["result"]
    raise ValueError(f"ACP response limit exceeded for {request_id}")


def run_protocol(child, cwd, prompt, expected, phase=None, session_out=None):
    transcript = hashlib.sha256()
    chunks = []
    usage = False
    counts = {}
    session_id = None

    def notice(frame):
        nonlocal usage
        method = frame["method"]
        counts[method] = counts.get(method, 0) + 1
        if method != "session/update" or frame.get("params", {}).get("sessionId") != session_id:
            return
        update = frame["params"].get("update", {})
        if update.get("sessionUpdate") == "agent_message_chunk":
            content = update.get("content", {})
            if content.get("type") == "text" and isinstance(content.get("text"), str):
                chunks.append(content["text"])
        elif update.get("sessionUpdate") == "usage_update":
            usage = True

    if phase is not None:
        phase[0] = "initialize"
    send(child, {"id": 1, "method": "initialize", "params": {"protocolVersion": 1,
        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}}})
    initialized = reply(child, 1, notice, transcript)
    info = initialized.get("agentInfo")
    if (initialized.get("protocolVersion") != 1 or not isinstance(info, dict)
            or info.get("name") != PACKAGE or info.get("version") != VERSION):
        raise ValueError("unexpected ACP package identity")
    if phase is not None:
        phase[0] = "session/new"
    send(child, {"id": 2, "method": "session/new", "params": {"cwd": str(cwd), "mcpServers": []}})
    session = reply(child, 2, notice, transcript)
    session_id = session.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session/new did not return a session ID")
    if session_out is not None:
        session_out.append(session_id)
    if phase is not None:
        phase[0] = "session/prompt"
    send(child, {"id": 3, "method": "session/prompt", "params": {"sessionId": session_id,
        "prompt": [{"type": "text", "text": prompt}]}})
    result = reply(child, 3, notice, transcript)
    response = "".join(chunks)
    if result.get("stopReason") != "end_turn" or not usage:
        raise ValueError("ACP prompt did not finish with usage")
    if response != expected:
        raise ValueError("ACP response differs from the pinned provider fixture")
    return {"incoming_transcript_sha256": transcript.hexdigest(),
            "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
            "notification_counts": counts, "stop_reason": result["stopReason"],
            "usage_seen": usage}


def run(args):
    require_guest()
    for path in (args.node, args.hq_main, args.package_root, args.cwd,
                 args.prompt_file, args.expected_response_file):
        if not path.is_absolute():
            raise ValueError(f"expected absolute path: {path}")
    if not args.node.is_file() or not args.hq_main.is_file():
        raise ValueError("Node and the merged HQ entry point must be installed files")
    prompt = args.prompt_file.read_text()
    expected = args.expected_response_file.read_text()
    if not prompt or not expected:
        raise ValueError("the pinned prompt and expected response must be nonempty")
    command = [str(args.node), str(args.hq_main), "soul", "start", "--soul",
               str(args.package_root), "--profile", "managed-protected", "--owner-control"]
    began = time.monotonic()
    phase = ["initialize"]
    limit = [args.turn_timeout]
    session = []
    errors = tempfile.TemporaryFile()
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=errors, start_new_session=True, cwd="/")
    except Exception:
        errors.close()
        raise
    events = select.kqueue()
    try:
        events.control([select.kevent(child.pid, filter=select.KQ_FILTER_PROC,
                       flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                       fflags=select.KQ_NOTE_EXIT)], 0, 0)
        watchable = True
    except ProcessLookupError:
        # A very early HQ refusal can exit before the exit watch is installed.
        watchable = False
    prior = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise TimeoutError(f"{phase[0]}: {limit[0]}s deadline exceeded")
    try:
        signal.signal(signal.SIGALRM, expired)
        signal.alarm(args.turn_timeout)
        result = run_protocol(child, args.cwd, prompt, expected, phase, session)
        signal.alarm(0)
        phase[0] = "stdin EOF"
        limit[0] = args.exit_timeout
        child.stdin.close()
        signal.alarm(args.exit_timeout)
        check_eof(child.stdout, session[0])
        if watchable and not events.control(None, 1, args.exit_timeout):
            raise TimeoutError("HQ owner ACP client did not exit after stdin EOF")
        signal.alarm(0)
    except Exception as error:
        if not isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
            errors.seek(0)
            codes = re.findall(rb'"code"\s*:\s*"([a-z0-9_.-]{1,80})"', errors.read(65536))
            if codes:
                raise ValueError(f"{error}; HQ refusal {codes[-1].decode()}") from error
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prior)
        # Keep the group leader PID reserved until the signal is delivered.
        # A reaped PID could be reused for another process group.
        if child.returncode is None:
            # An already-exited group can yield ESRCH or EPERM on macOS.
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(child.pid, signal.SIGKILL)
        try:
            wait_for_process_group_exit(child.pid)
        finally:
            try:
                child.wait(timeout=5)
            finally:
                # HQ may close its ACP pipe before our buffered stdin closes.
                with suppress(BrokenPipeError, OSError):
                    child.stdin.close()
                child.stdout.close()
                errors.close()
                events.close()
    if child.returncode != 0:
        raise ValueError(f"HQ owner ACP client exited {child.returncode}")
    return {"outcome": "pass", "elapsed_seconds": round(time.monotonic() - began, 3),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "expected_response_sha256": hashlib.sha256(expected.encode()).hexdigest(), **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("node", "hq-main", "package-root", "cwd", "prompt-file", "expected-response-file"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--turn-timeout", type=int, default=120)
    parser.add_argument("--exit-timeout", type=int, default=10)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if not 1 <= args.turn_timeout <= 300 or not 1 <= args.exit_timeout <= 30:
        parser.error("timeouts must be bounded positive seconds")
    if args.receipt:
        if not args.receipt.is_absolute():
            parser.error("--receipt needs an absolute private path")
        parent = args.receipt.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or stat.S_IMODE(parent.st_mode) & 0o077):
            parser.error("--receipt parent must be an owner-private directory")
    began = time.monotonic()
    try:
        receipt = run(args)
    except Exception as error:
        receipt = {"outcome": "timeout" if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) else "fail",
                   "reason": str(error), "elapsed_seconds": round(time.monotonic() - began, 3)}
    encoded = json.dumps(receipt, sort_keys=True) + "\n"
    if args.receipt:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(args.receipt, flags, 0o600), "w") as output:
            output.write(encoded)
    sys.stdout.write(encoded)
    if receipt["outcome"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

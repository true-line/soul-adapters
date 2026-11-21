"""Check stock Claude Code and native ACP streaming on a synthetic Unix endpoint."""

import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import signal
import socketserver
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

from initialize import (PACKAGE, REQUEST, VERSION, check_session, private_state, read_frame,
                        unpack, verify, wait_for_process_group_exit)
from vm_session import PROFILE_SHA256, require_guest


MODEL = "claude-haiku-4-5-20251001"
PLACEHOLDER = "ssh-placeholder"
USAGE = {"input_tokens": 7, "output_tokens": 3}


class FixtureError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise FixtureError(message)


class MessagesFixture(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, endpoint):
        super().__init__(str(endpoint), MessagesHandler)
        self.prompt = "Return the fixture response for " + secrets.token_hex(12)
        self.chunks = ("fixture-" + secrets.token_hex(12), "-complete")
        self.first_seen = threading.Event()
        self.lock = threading.Lock()
        self.prompt_calls = 0
        self.ancillary_messages = 0
        self.policy_checks = 0
        self.failed = threading.Event()

    def handle_error(self, *_):
        self.failed.set()


class MessagesHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if urlsplit(self.path).path != "/api/claude_code/policy_limits":
            self.server.failed.set()
            self.send_error(404)
            return
        with self.server.lock:
            self.server.policy_checks += 1
        data = b'{"policy_limits":{},"restrictions":{},"compliance_taints":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        fixture = self.server
        require(urlsplit(self.path).path == "/v1/messages", "unexpected POST path")
        require(self.headers.get("x-api-key") == PLACEHOLDER and "authorization" not in self.headers,
                "unexpected credential header")
        length = int(self.headers.get("Content-Length", "0"))
        require(0 < length <= 256 * 1024, "unexpected Messages body length")
        request = json.loads(self.rfile.read(length))
        messages = request["messages"]
        last = messages[-1] if messages else {}
        content = last.get("content")
        exact_prompt = last.get("role") == "user" and (
            content == fixture.prompt
            or isinstance(content, list) and any(
                block.get("type") == "text" and block.get("text") == fixture.prompt for block in content
            )
        )
        with fixture.lock:
            if exact_prompt:
                fixture.prompt_calls += 1
                require(fixture.prompt_calls == 1 and request.get("stream") is True
                        and request.get("model") == MODEL, "unexpected prompt Messages request")
            else:
                fixture.ancillary_messages += 1
        if not exact_prompt:
            # Auxiliary requests get neither the canary output nor prompt usage.
            self.send_error(422)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def event(kind, **fields):
            payload = json.dumps({"type": kind, **fields})
            self.wfile.write(f"event: {kind}\ndata: {payload}\n\n".encode())
            self.wfile.flush()

        event("message_start", message={"id": "msg_fixture", "type": "message", "role": "assistant",
                                        "model": MODEL, "content": [], "stop_reason": None,
                                        "stop_sequence": None, "usage": {"input_tokens": 7, "output_tokens": 0}})
        event("content_block_start", index=0, content_block={"type": "text", "text": ""})
        for index, chunk in enumerate(fixture.chunks):
            event("content_block_delta", index=0, delta={"type": "text_delta", "text": chunk})
            if index == 0:
                require(fixture.first_seen.wait(8), "first delta was buffered")
        event("content_block_stop", index=0)
        event("message_delta", delta={"stop_reason": "end_turn", "stop_sequence": None}, usage=USAGE)
        event("message_stop")


def write_rpc(child, frame):
    child.stdin.write(json.dumps({"jsonrpc": "2.0", **frame}).encode() + b"\n")
    child.stdin.flush()


def acp_reply(child, reply_id, notice):
    for _ in range(256):
        frame = read_frame(child.stdout)
        require(frame is not None, "ACP closed before replying")
        if "method" not in frame:
            require(frame.get("id") == reply_id and "result" in frame, "ACP refused the request")
            return frame["result"]
        if "id" not in frame:
            notice(frame)
        elif frame["method"] == "session/request_permission":
            write_rpc(child, {"id": frame["id"], "result": {"outcome": {"outcome": "cancelled"}}})
        else:
            write_rpc(child, {"id": frame["id"], "error": {"code": -32601, "message": "method not found"}})
    raise FixtureError("too many ACP notifications")


def observe_acp(child, cwd, fixture):
    write_rpc(child, REQUEST)
    initialized = acp_reply(child, 1, lambda _: None)
    require(initialized.get("protocolVersion") == 1
            and initialized["agentInfo"]["name"] == PACKAGE and initialized["agentInfo"]["version"] == VERSION,
            "unexpected ACP identity")
    session = check_session(child, cwd)
    chunks = []
    usage_seen = False

    def notice(frame):
        nonlocal usage_seen
        params = frame.get("params", {})
        if frame["method"] != "session/update" or params.get("sessionId") != session:
            return
        update = params["update"]
        if update["sessionUpdate"] == "agent_message_chunk" and update["content"]["type"] == "text":
            chunks.append(update["content"]["text"])
            if chunks == [fixture.chunks[0]]:
                fixture.first_seen.set()
        if update["sessionUpdate"] == "usage_update":
            usage_seen = True

    write_rpc(child, {"id": 3, "method": "session/prompt", "params": {
        "sessionId": session, "prompt": [{"type": "text", "text": fixture.prompt}],
    }})
    result = acp_reply(child, 3, notice)
    require(result.get("stopReason") == "end_turn" and usage_seen, "ACP did not finish with usage")
    require(tuple(chunks) == fixture.chunks, "unexpected ACP text deltas")
    child.stdin.close()


def observe_cli(child, _, fixture):
    chunks = []
    result = {}
    for line in child.stdout:
        frame = json.loads(line)
        event = frame.get("event", {})
        if event.get("type") == "content_block_delta":
            chunks.append(event["delta"]["text"])
            if chunks == [fixture.chunks[0]]:
                fixture.first_seen.set()
        if frame.get("type") == "result":
            result = frame
    require(tuple(chunks) == fixture.chunks, "unexpected CLI text deltas")
    require(result.get("is_error") is False and result.get("result") == "".join(fixture.chunks)
            and all(result["usage"][key] == value for key, value in USAGE.items()),
            "CLI did not finish with the fixture text and usage")


def expired(*_):
    raise FixtureError("client deadline exceeded")


def run_client(command, cwd, env, stdin, observe, fixture):
    with closing(select.kqueue()) as events, subprocess.Popen(
        command, cwd=cwd, env=env, stdin=stdin, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, start_new_session=True,
    ) as child:
        previous = signal.signal(signal.SIGALRM, expired)
        try:
            events.control([select.kevent(child.pid, filter=select.KQ_FILTER_PROC,
                                          flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                          fflags=select.KQ_NOTE_EXIT)], 0, 0)
            signal.alarm(45)
            observe(child, cwd, fixture)
            # Observe exit without reaping the PID before process group cleanup.
            require(events.control(None, 1, 10), "client did not exit")
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # Darwin may report either result for an exited group; verify cleanup below.
                pass
            status = child.wait(timeout=5)
            wait_for_process_group_exit(child.pid)
        require(status == 0, "client did not exit zero")


def trial(root, profile, mode):
    state = root / mode
    state.mkdir()
    _, cwd, env = private_state(state, "member-temp")
    member, broker = state / "member.sock", state / "broker.sock"
    env.update(ANTHROPIC_UNIX_SOCKET=str(broker), ANTHROPIC_API_KEY=PLACEHOLDER,
               ANTHROPIC_BASE_URL="http://fixture.invalid", ANTHROPIC_MODEL=MODEL,
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    policy = ["/usr/bin/sandbox-exec", "-D", f"BROKER_SOCKET={broker}",
              "-D", f"MEMBER_SOCKET={member}", "-f", str(profile)]
    with MessagesFixture(broker) as fixture:
        server = threading.Thread(target=fixture.serve_forever, daemon=True)
        server.start()
        try:
            if mode == "cli":
                command = [str(root / "claude"), "--print", fixture.prompt, "--model", MODEL,
                           "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                           "--no-session-persistence", "--tools", ""]
                run_client(policy + command, cwd, env, subprocess.DEVNULL, observe_cli, fixture)
            else:
                run_client(policy + [str(root / "claude-agent-acp")], cwd, env, subprocess.PIPE,
                           observe_acp, fixture)
        finally:
            fixture.shutdown()
            server.join(timeout=5)
        require(fixture.prompt_calls == 1 and fixture.first_seen.is_set() and not fixture.failed.is_set(),
                "the fixture did not complete exactly one streaming prompt request")
        print(json.dumps({"mode": mode, "model": MODEL, "prompt_messages": fixture.prompt_calls,
                          "ancillary_messages": fixture.ancillary_messages, "policy_checks": fixture.policy_checks,
                          "incremental_deltas": len(fixture.chunks), "usage_observed": True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="pinned v0.109.0 Darwin arm64 release zip")
    parser.add_argument("machine_profile", type=Path, help="pinned Machine harness-egress.sb")
    args = parser.parse_args()
    require_guest()
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="claude-uds-", dir="/private/tmp") as temp:
        root = Path(temp)
        unpack(args.archive, root)
        profile = root / "harness-egress.sb"
        shutil.copyfile(args.machine_profile, profile)
        verify(profile, PROFILE_SHA256)
        for mode in ("cli", "acp"):
            try:
                trial(root, profile, mode)
            except FixtureError as error:
                raise SystemExit(f"{mode}: {error}") from None
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
                raise SystemExit(f"{mode}: invalid client or fixture output") from None


if __name__ == "__main__":
    main()

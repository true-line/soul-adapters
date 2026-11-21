"""Check native Codex ACP persistence in isolated member-shaped state on a Mac guest."""

import argparse
import json
import os
import socket
from pathlib import Path
import subprocess
import tempfile
import threading

from loopback_turn import (ANSWER, PROMPT, FixtureHandler, FixtureServer, configured_state,
                           cooperative_sandbox, response_events)
from session import CODEX_VERSION_OUTPUT, PACKAGE, exchange, initialize, install_packages, read_frame, session
from vm_session import require_guest


HOLD_PROMPT = "Start the separate interrupted fixture turn."
HOLD_TEXT = "Partial text before cancellation."
CONTINUE_PROMPT = "Continue the original local conversation."
CONTINUE_CHUNKS = ("Same conversation ", "continued.")


class InterruptHandler(FixtureHandler):
    def do_POST(self):
        if self.path != "/v1/responses":
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 256 * 1024:
            raise ValueError("unexpected fixture request size")
        body = json.loads(self.rfile.read(size))
        inputs = body.get("input", [])

        def has_message(role, text, items=inputs):
            return any(isinstance(item, dict) and item.get("role") == role and
                       any(isinstance(block, dict) and block.get("text") == text
                           for block in item.get("content", []))
                       for item in items)

        turn = next((name for name, prompt in (("saved", PROMPT), ("interrupted", HOLD_PROMPT),
                                               ("continued", CONTINUE_PROMPT))
                     if has_message("user", prompt, inputs[-1:])), None)
        if not turn:
            self.send_error(422)
            return
        if body.get("stream") is not True:
            raise ValueError("expected a streaming Responses request")
        self.server.requests.append(turn)
        if turn == "continued":
            if not has_message("user", PROMPT) or not has_message("assistant", ANSWER):
                raise ValueError("continued request did not carry the first complete turn")
        chunks = {"saved": ("Fixture response ", "complete."), "interrupted": (HOLD_TEXT,),
                  "continued": CONTINUE_CHUNKS}[turn]
        events = response_events(body["model"], chunks=chunks, tag=turn)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for event in events:
            encoded = json.dumps(event, separators=(",", ":"))
            self.wfile.write(f"event: {event['type']}\ndata: {encoded}\n\n".encode())
            self.wfile.flush()
            if turn == "interrupted" and event["type"] == "response.output_text.delta":
                # Hold the stream open without sending any completion frames.
                # The adapter must interrupt the request while its process is alive.
                self.connection.settimeout(12)
                try:
                    if self.connection.recv(1) == b"":
                        self.server.stream_closed.set()
                except ConnectionResetError:
                    self.server.stream_closed.set()
                except socket.timeout:
                    pass
                return


class InterruptServer(FixtureServer):
    def __init__(self):
        super().__init__(InterruptHandler)
        self.stream_closed = threading.Event()
        self.failed = False

    def handle_error(self, *_):
        # Keep fixture bodies and exception contents out of the receipt.
        self.failed = True


def request_with_updates(child, request_id, method, params, on_update=None):
    request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    child.stdin.write(json.dumps(request).encode() + b"\n")
    child.stdin.flush()
    updates = []
    for _ in range(200):
        frame = read_frame(child.stdout)
        if frame is None:
            break
        if frame.get("method") == "session/update":
            notification = frame.get("params", {})
            if notification.get("sessionId") == params.get("sessionId"):
                update = notification.get("update", {})
                updates.append(update)
                if on_update:
                    on_update(update)
        elif frame.get("id") == request_id:
            if "result" not in frame:
                raise ValueError(f"{method} failed: {frame.get('error', {}).get('code')}")
            return frame["result"], updates
    raise ValueError(f"{method}: missing response")


def text_updates(updates, kind):
    return "".join(update.get("content", {}).get("text", "") for update in updates
                   if update.get("sessionUpdate") == kind and update.get("content", {}).get("type") == "text")


def prompt(child, session_id, text=PROMPT, answer=ANSWER):
    result, updates = request_with_updates(child, 3, "session/prompt", {
        "sessionId": session_id, "prompt": [{"type": "text", "text": text}],
    })
    if result.get("stopReason") != "end_turn" or answer not in text_updates(updates, "agent_message_chunk"):
        raise ValueError("native adapter did not finish the fixture turn")


def interrupt_prompt(child, session_id, server):
    chunks = []
    cancel_sent = False

    def cancel_at_text_boundary(update):
        nonlocal cancel_sent
        chunks.append(text_updates([update], "agent_message_chunk"))
        # Codex can deliver other notices as assistant text. The fixture
        # marker identifies the provider delta independently of those updates.
        if not cancel_sent and HOLD_TEXT in "".join(chunks):
            cancel_sent = True
            child.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "session/cancel",
                                         "params": {"sessionId": session_id}}).encode() + b"\n")
            child.stdin.flush()

    result, updates = request_with_updates(child, 4, "session/prompt", {
        "sessionId": session_id, "prompt": [{"type": "text", "text": HOLD_PROMPT}],
    }, on_update=cancel_at_text_boundary)
    if (not cancel_sent or result.get("stopReason") != "cancelled"
            or text_updates(updates, "agent_message_chunk").count(HOLD_TEXT) != 1):
        raise ValueError("ACP did not cancel the unfinished text turn")
    if not server.stream_closed.wait(5):
        raise ValueError("cancelled turn left its fixture provider stream open")


def check(root, packages, node, base, interruption=None):
    cli = packages / "@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
    adapter = packages / PACKAGE / "dist/index.js"
    member_parent = root / "soul/Contents/home"
    member_parent.mkdir(parents=True, mode=0o700)
    state, env = configured_state(member_parent, "m_lead", cli, node, base)
    command = cooperative_sandbox(root) + [str(node), str(adapter)]
    session_ids = []

    def first(child, wire, workdir):
        initialize(child, wire)
        result = exchange(child, 2, "session/new", {"cwd": str(workdir), "mcpServers": []}).get("result", {})
        session_id = result.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session/new did not return an ID")
        session_ids.append(session_id)
        prompt(child, session_id)
        if interruption:
            interrupt_prompt(child, session_id, interruption)

    def second(child, wire, workdir):
        initialize(child, wire)
        _, updates = request_with_updates(child, 2, "session/load", {
            "sessionId": session_ids[0], "cwd": str(workdir), "mcpServers": [],
        })
        if PROMPT not in text_updates(updates, "user_message_chunk"):
            raise ValueError("session/load did not replay the original user message")
        if ANSWER not in text_updates(updates, "agent_message_chunk"):
            raise ValueError("session/load did not replay the original assistant message")
        if interruption:
            prompt(child, session_ids[0], CONTINUE_PROMPT, "".join(CONTINUE_CHUNKS))
        else:
            prompt(child, session_ids[0])

    session(command, env, state, 1, first)
    rollout_root = Path(env["CODEX_HOME"]) / "sessions"
    rollouts = list(rollout_root.rglob(f"rollout-*-{session_ids[0]}.jsonl"))
    if len(rollouts) != 1 or not rollouts[0].is_file() or not rollouts[0].resolve().is_relative_to(state.resolve()):
        raise ValueError("missing native rollout for the session in private fixture state")
    with rollouts[0].open(encoding="utf-8") as stream:
        metadata = json.loads(stream.readline())
    if metadata.get("type") != "session_meta" or metadata.get("payload", {}).get("id") != session_ids[0]:
        raise ValueError("native rollout metadata does not match the session")
    session(command, env, state, 1, second)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interrupt", action="store_true", help="cancel an unfinished turn before reloading")
    args = parser.parse_args()
    require_guest()
    os.umask(0o077)
    source = Path.cwd()
    node, npm = source / "runtime/bin/node", source / "runtime/bin/npm"
    with tempfile.TemporaryDirectory(prefix="codex-native-resume-", dir="/private/tmp") as temporary:
        root = Path(temporary)
        packages = install_packages(root, str(node), str(npm), source / "cache")
        cli = packages / "@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
        if subprocess.check_output([cli, "--version"], timeout=15).strip() != CODEX_VERSION_OUTPUT:
            raise ValueError("unexpected Codex CLI version")
        server = InterruptServer() if args.interrupt else FixtureServer()
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            check(root, packages, node, f"http://127.0.0.1:{server.server_port}/v1",
                  server if args.interrupt else None)
            if args.interrupt:
                if server.failed or server.requests != ["saved", "interrupted", "continued"]:
                    raise ValueError("unexpected interrupted fixture request sequence")
                print("PASS native ACP: cancelled unfinished stream, reloaded private history, continued same session")
            else:
                if (len(server.requests) != 2 or any(row.get("path") != "/v1/responses" or
                        not row.get("fixture_prompt_present") or row.get("stream") is not True
                        for row in server.requests)):
                    raise ValueError("expected two fixture prompt requests")
                print("PASS native ACP: member-scoped rollout, adapter restart, history replay and second turn")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    main()

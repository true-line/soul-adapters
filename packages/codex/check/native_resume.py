"""Check native Codex ACP persistence in isolated member-shaped state on a Mac guest."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from loopback_turn import ANSWER, PROMPT, FixtureServer, configured_state, cooperative_sandbox
from session import PACKAGE, exchange, initialize, install_packages, read_frame, session
from vm_session import require_guest


def request_with_updates(child, request_id, method, params):
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
                updates.append(notification.get("update", {}))
        elif frame.get("id") == request_id:
            if "result" not in frame:
                raise ValueError(f"{method} failed: {frame.get('error', {}).get('code')}")
            return frame["result"], updates
    raise ValueError(f"{method}: missing response")


def text_updates(updates, kind):
    return "".join(update.get("content", {}).get("text", "") for update in updates
                   if update.get("sessionUpdate") == kind and update.get("content", {}).get("type") == "text")


def prompt(child, session_id):
    result, updates = request_with_updates(child, 3, "session/prompt", {
        "sessionId": session_id, "prompt": [{"type": "text", "text": PROMPT}],
    })
    if result.get("stopReason") != "end_turn" or ANSWER not in text_updates(updates, "agent_message_chunk"):
        raise ValueError("native adapter did not finish the fixture turn")


def check(root, packages, node, base):
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

    def second(child, wire, workdir):
        initialize(child, wire)
        _, updates = request_with_updates(child, 2, "session/load", {
            "sessionId": session_ids[0], "cwd": str(workdir), "mcpServers": [],
        })
        if PROMPT not in text_updates(updates, "user_message_chunk"):
            raise ValueError("session/load did not replay the original user message")
        if ANSWER not in text_updates(updates, "agent_message_chunk"):
            raise ValueError("session/load did not replay the original assistant message")
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
    require_guest()
    os.umask(0o077)
    source = Path.cwd()
    node, npm = source / "runtime/bin/node", source / "runtime/bin/npm"
    with tempfile.TemporaryDirectory(prefix="codex-native-resume-", dir="/private/tmp") as temporary:
        root = Path(temporary)
        packages = install_packages(root, str(node), str(npm), source / "cache")
        cli = packages / "@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
        if subprocess.check_output([cli, "--version"], timeout=15).strip() != b"codex-cli 0.170.0":
            raise ValueError("unexpected Codex CLI version")
        server = FixtureServer()
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            check(root, packages, node, f"http://127.0.0.1:{server.server_port}/v1")
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

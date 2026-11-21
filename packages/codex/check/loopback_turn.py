"""Exercise Codex and ACP against a loopback fixture in a disposable Mac guest."""

import http.server
import json
import os
from pathlib import Path
import socketserver
import subprocess
import threading
import time

from session import CODEX_VERSION_OUTPUT, PACKAGE, exchange, initialize, install_packages, private_env, read_frame, session
from vm_session import require_guest


PROMPT = "Reply with a short greeting."
ANSWER = "Fixture response complete."


class FixtureServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def __init__(self, handler=None):
        super().__init__(("127.0.0.1", 0), handler or FixtureHandler)
        self.requests = []
        self.completions = []


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self.server.requests.append({"method": "GET", "path": self.path})
        body = json.dumps({"object": "list", "data": [{"id": "gpt-5.4", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/v1/responses":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        inputs = body.get("input", [])
        self.server.requests.append({
            "method": "POST", "path": self.path, "keys": sorted(body),
            "input_type": type(inputs).__name__,
            "input_roles": sorted({item.get("role", "") for item in inputs if isinstance(item, dict)}),
            "stream": body.get("stream"), "tools_type": type(body.get("tools")).__name__,
            "fixture_prompt_present": any(
                isinstance(item, dict) and item.get("role") == "user" and
                PROMPT in json.dumps(item.get("content")) for item in inputs
            ),
        })
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for event in response_events(body.get("model", "gpt-5.4")):
            encoded = json.dumps(event, separators=(",", ":"))
            if event["type"] == "response.completed":
                self.server.completions.append(time.monotonic())
            self.wfile.write(f"event: {event['type']}\ndata: {encoded}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.2)


def response_events(model, *, chunks=("Fixture response ", "complete."), tag="fixture"):
    answer = "".join(chunks)
    part = {"type": "output_text", "text": answer, "annotations": []}
    message = {"id": f"msg_{tag}", "type": "message", "status": "completed",
               "role": "assistant", "content": [part]}
    response = {"id": f"resp_{tag}", "object": "response", "created_at": 1,
                "status": "completed", "model": model, "output": [message],
                "usage": {"input_tokens": 1, "output_tokens": 3, "total_tokens": 4}}
    location = {"item_id": message["id"], "output_index": 0, "content_index": 0}
    return [
        {"type": "response.created", "response": dict(response, status="in_progress", output=[])},
        {"type": "response.output_item.added", "output_index": 0,
         "item": dict(message, status="in_progress", content=[])},
        {"type": "response.content_part.added", **location,
         "part": {"type": "output_text", "text": "", "annotations": []}},
        *({"type": "response.output_text.delta", **location, "delta": text} for text in chunks),
        {"type": "response.output_text.done", **location, "text": answer},
        {"type": "response.content_part.done", **location, "part": part},
        {"type": "response.output_item.done", "output_index": 0, "item": message},
        {"type": "response.completed", "response": response},
    ]


def cooperative_sandbox(root):
    profile = root / "cooperative-loopback.sb"
    profile.write_text('(version 1)\n(allow default)\n(deny network*)\n'
                       '(allow network-outbound (remote ip "localhost:*"))\n')
    command = ["/usr/bin/sandbox-exec", "-f", str(profile)]
    canary = ("import errno,socket,sys; s=socket.socket(); s.settimeout(2); "
              "r=s.connect_ex(('203.0.113.1',443)); "
              "sys.exit(0 if r in (errno.EPERM,errno.EACCES) else 1)")
    subprocess.run(command + ["/usr/bin/python3", "-IS", "-c", canary], check=True, timeout=5)
    return command


def configured_state(root, name, cli, node, base_url):
    state = root / name
    state.mkdir(mode=0o700)
    env = private_env(state, cli)
    env.update({"PATH": f"{node.parent}:/usr/bin:/bin", "OPENAI_API_KEY": "fixture-only",
                "NO_PROXY": "127.0.0.1,localhost"})
    (Path(env["CODEX_HOME"]) / "config.toml").write_text(
        'model_provider = "local"\nmodel = "gpt-5.4"\n'
        '[model_providers.local]\nname = "Loopback fixture"\n'
        f'base_url = "{base_url}"\nwire_api = "responses"\nrequires_openai_auth = false\n'
    )
    return state, env


def check_cli(root, cli, node, sandbox, base_url):
    state, env = configured_state(root, "cli", cli, node, base_url)
    proc = subprocess.run(sandbox + [str(cli), "exec", "--skip-git-repo-check", "--json", PROMPT],
                          cwd=state, env=env, capture_output=True, text=True, timeout=50)
    if proc.returncode:
        raise ValueError("Codex CLI failed to complete the fixture turn")
    events = [json.loads(line) for line in proc.stdout.splitlines()]
    messages = [event.get("item", {}) for event in events if event.get("type") == "item.completed"]
    if not any(item.get("type") == "agent_message" and item.get("text") == ANSWER for item in messages):
        raise ValueError("Codex CLI did not emit the expected completed agent message")
    if not any(event.get("type") == "turn.completed" for event in events):
        raise ValueError("Codex CLI did not emit turn.completed")
    print("PASS CLI: completed agent message and turn")


def check_acp(root, cli, node, adapter, sandbox, base_url, server):
    state, env = configured_state(root, "acp", cli, node, base_url)

    def handshake(child, wire, workdir):
        initialize(child, wire, gateway=True)
        configured = exchange(child, 4, "providers/set", {
            "providerId": "openai", "apiType": "openai", "baseUrl": base_url, "headers": {},
        })
        if "result" not in configured:
            raise ValueError("ACP gateway discovery failed")
        reply = exchange(child, 2, "session/new", {"cwd": str(workdir), "mcpServers": []})
        session_id = reply.get("result", {}).get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("ACP session/new did not return a session ID")
        request = {"jsonrpc": "2.0", "id": 3, "method": "session/prompt", "params": {
            "sessionId": session_id, "prompt": [{"type": "text", "text": PROMPT}],
        }}
        completion_count = len(server.completions)
        child.stdin.write(json.dumps(request).encode() + b"\n")
        child.stdin.flush()
        chunks = []
        chunk_times = []
        result = None
        for _ in range(120):
            frame = read_frame(child.stdout)
            if frame is None:
                break
            if frame.get("method") == "session/update":
                update = frame.get("params", {}).get("update", {})
                content = update.get("content", {})
                if update.get("sessionUpdate") == "agent_message_chunk" and content.get("type") == "text":
                    text = content.get("text")
                    if isinstance(text, str) and text:
                        chunks.append(text)
                        chunk_times.append(time.monotonic())
            elif frame.get("id") == 3:
                result = frame
                break
        if not result or result.get("result", {}).get("stopReason") != "end_turn":
            raise ValueError("ACP prompt did not return end_turn")
        text = "".join(chunks)
        start = text.find(ANSWER)
        if start < 0 or len(chunks) < 2:
            raise ValueError(f"ACP expected incremental text: {len(chunks)} nonempty chunks")
        offset = 0
        first_answer_at = None
        for chunk, received_at in zip(chunks, chunk_times):
            offset += len(chunk)
            if offset > start:
                first_answer_at = received_at
                break
        if (first_answer_at is None or len(server.completions) <= completion_count or
                first_answer_at >= server.completions[completion_count]):
            raise ValueError("ACP did not emit an agent message chunk before response.completed")
        print("PASS ACP: discovery, incremental agent message and end_turn")

    session(sandbox + [str(node), str(adapter)], env, state, 1, handshake)


def main():
    require_guest()
    os.umask(0o077)
    root = Path.cwd()
    node, npm = root / "runtime/bin/node", root / "runtime/bin/npm"
    packages = install_packages(root, str(node), str(npm), root / "cache")
    cli = packages / "@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
    adapter = packages / PACKAGE / "dist/index.js"
    if subprocess.check_output([cli, "--version"], timeout=15).strip() != CODEX_VERSION_OUTPUT:
        raise ValueError("unexpected Codex CLI version")
    sandbox = cooperative_sandbox(root)
    server = FixtureServer()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        check_cli(root, cli, node, sandbox, base_url)
        check_acp(root, cli, node, adapter, sandbox, base_url, server)
        requests = server.requests
        if [request["path"] for request in requests] != ["/v1/responses", "/v1/models", "/v1/responses"]:
            raise ValueError("unexpected fixture request sequence")
        if any(request.get("stream") is not True for request in requests if request["method"] == "POST"):
            raise ValueError("Codex did not request streaming responses")
        print("Sanitized request shapes:", json.dumps(requests, sort_keys=True))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()

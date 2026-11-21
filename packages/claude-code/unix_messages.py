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
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit
from uuid import UUID

from initialize import (PACKAGE, REQUEST, VERSION, check_session, private_state, read_frame,
                        unpack, verify, wait_for_process_group_exit)
from vm_session import PROFILE_SHA256, require_guest
from hq_read_bridge import BridgeError, ReadBridge


MODEL = "claude-haiku-4-5-20251001"
PLACEHOLDER = "ssh-placeholder"
USAGE = {"input_tokens": 7, "output_tokens": 3}


class FixtureError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise FixtureError(message)


def content_blocks(message):
    content = message.get("content")
    return content if isinstance(content, list) else [{"type": "text", "text": content}]


def has_prompt(message, prompt):
    return message.get("role") == "user" and any(
        block.get("type") == "text" and block.get("text") == prompt for block in content_blocks(message)
    )


def contains_turn(messages, prompt, answer):
    for index, message in enumerate(messages):
        if has_prompt(message, prompt):
            text = []
            for reply in messages[index + 1:]:
                if reply.get("role") != "assistant":
                    break
                text.extend(block.get("text", "") for block in content_blocks(reply)
                            if block.get("type") == "text")
            if "".join(text) == answer:
                return True
    return False


class ReadFixture:
    def __init__(self, cwd):
        self.path = cwd / ("fixture-" + secrets.token_hex(12) + ".txt")
        self.marker = "local-fixture-" + secrets.token_hex(12)
        self.path.write_text(self.marker + "\n")
        self.tool_id = "toolu_fixture_" + secrets.token_hex(12)
        self.tool_input = {"file_path": str(self.path)}
        self.settings = {"permissions": {
            "allow": [f"Read(/{self.path})"],
            "ask": ["Read(./**)", f"Read(!{self.path.name})"],
            "blockReadsOutsideWorkingDirectories": True,
            "defaultMode": "dontAsk",
        }}
        self.calls = 0
        self.shape = {}

    def inspect(self, request, prompt, exact_prompt):
        messages = request["messages"]
        calls = [block for message in messages if message.get("role") == "assistant"
                 for block in content_blocks(message) if block["type"] == "tool_use"]
        results = [block for message in messages if message.get("role") == "user"
                   for block in content_blocks(message) if block["type"] == "tool_result"]
        if not exact_prompt and not results and not calls:
            return None
        require(request.get("stream") is True and request.get("model") == MODEL,
                "unexpected tool request model or stream")
        names = [tool.get("name") for tool in request.get("tools", [])]
        require(names == ["Read"], "expected only the fixture Read tool")
        if exact_prompt and not results:
            schema = request["tools"][0]["input_schema"]
            require(self.calls == 0 and not calls and schema.get("type") == "object"
                    and schema.get("properties", {}).get("file_path", {}).get("type") == "string"
                    and "file_path" in schema.get("required", []), "invalid Read tool schema or initial history")
            self.calls = 1
            self.shape = {
                "tool_schema": True,
                "system_present": isinstance(request.get("system"), (str, list)) and bool(request["system"]),
                "output_cap_present": type(request.get("max_tokens")) is int and request["max_tokens"] > 0,
            }
            return "tool_use"
        require(self.calls == 1 and len(calls) == len(results) == 1,
                "expected one Read and its result")
        call, result = calls[0], results[0]
        require(call.get("id") == result.get("tool_use_id") == self.tool_id
                and call.get("name") == "Read" and call.get("input") == self.tool_input,
                "Read call and result did not match the fixture")
        require(not result.get("is_error") and any(
            block.get("type") == "text" and self.marker in (block.get("text") or "")
            for block in content_blocks(result)
        ), "Read result did not contain the private file marker")
        require(has_prompt(messages[0], prompt) and messages[-2]["role"] == "assistant"
                and call in content_blocks(messages[-2]) and messages[-1]["role"] == "user"
                and result in content_blocks(messages[-1]), "the Read history is out of order")
        self.calls = 2
        self.shape.update(tool_result_id_matches=True, private_file_result=True,
                          ordered_read_history=True, previous_messages=len(messages) - 1)
        return "end_turn"


class MessagesFixture(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, endpoint, read=None):
        super().__init__(str(endpoint), MessagesHandler)
        self.prompt = "Return the fixture response for " + secrets.token_hex(12)
        self.chunks = ("fixture-" + secrets.token_hex(12), "-complete")
        self.first_seen = threading.Event()
        self.lock = threading.Lock()
        self.prompt_calls = 0
        self.ancillary_messages = 0
        self.policy_checks = 0
        self.error = None
        self.read = read
        self.resume_history = None
        self.bridge = None

    def handle_error(self, *_):
        error = sys.exc_info()[1]
        self.error = error if isinstance(error, FixtureError) else FixtureError("invalid fixture request")


class DirectCanary(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), socketserver.BaseRequestHandler)
        self.connections = 0
        self.lock = threading.Lock()

    def get_request(self):
        client, address = super().get_request()
        with self.lock:
            self.connections += 1
        return client, address


class MessagesHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if urlsplit(self.path).path != "/api/claude_code/policy_limits":
            self.server.error = FixtureError("unexpected GET path")
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
        exact_prompt = bool(messages) and has_prompt(messages[-1], fixture.prompt)
        with fixture.lock:
            if fixture.read:
                stop = fixture.read.inspect(request, fixture.prompt, exact_prompt)
            elif exact_prompt:
                require(fixture.prompt_calls == int(fixture.resume_history is not None)
                        and request.get("stream") is True and request.get("model") == MODEL,
                        "unexpected prompt Messages request")
                if fixture.resume_history:
                    require(contains_turn(messages[:-1], *fixture.resume_history),
                            "resumed request did not include the private first turn")
                stop = "end_turn"
            else:
                stop = None
            if stop:
                fixture.prompt_calls += 1
            else:
                fixture.ancillary_messages += 1
        if stop is None:
            # Auxiliary requests get neither the canary output nor prompt usage.
            self.send_error(422)
            return
        if fixture.bridge:
            fixture.bridge.begin(request)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def event(kind, **fields):
            payload = json.dumps({"type": kind, **fields})
            self.wfile.write(f"event: {kind}\ndata: {payload}\n\n".encode())
            self.wfile.flush()

        event("message_start", message={"id": f"msg_fixture_{fixture.prompt_calls}", "type": "message",
                                        "role": "assistant", "model": MODEL, "content": [], "stop_reason": None,
                                        "stop_sequence": None,
                                        "usage": {"input_tokens": USAGE["input_tokens"], "output_tokens": 0}})
        if fixture.bridge:
            tool_id = None
            arguments = ""
            tool_frames = []
            chunks = []
            while True:
                frame = fixture.bridge.receive()
                if "result" in frame:
                    terminal = frame["result"]
                    require(terminal == {"stop_reason": stop, "usage": USAGE}, "unexpected HQ terminal")
                    break
                chunk = frame["event"]
                kind = chunk.get("type")
                if kind == "response.tool_call.start":
                    require(stop == "tool_use" and tool_id is None and chunk["call_id"] == fixture.read.tool_id
                            and chunk["name"] == "Read", "unexpected HQ tool start")
                    tool_id = chunk["call_id"]
                    tool_frames.append(chunk)
                elif kind == "response.tool_call.arguments.delta":
                    require(tool_id == chunk["call_id"], "unexpected HQ tool arguments")
                    arguments += chunk["delta"]
                    tool_frames.append(chunk)
                elif kind == "response.output_text.delta":
                    require(stop == "end_turn" and len(chunks) < 2 and isinstance(chunk["delta"], str)
                            and chunk["delta"], "unexpected HQ text chunk")
                    chunks.append(chunk["delta"])
                    fixture.chunks = tuple(chunks)
                    if len(chunks) == 1:
                        event("content_block_start", index=0, content_block={"type": "text", "text": ""})
                    event("content_block_delta", index=0, delta={"type": "text_delta", "text": chunk["delta"]})
                    if len(chunks) == 1:
                        require(fixture.first_seen.wait(8), "first HQ delta was buffered")
                        fixture.bridge.continue_after_observation()
                else:
                    raise FixtureError("unexpected HQ stream event")
            if stop == "tool_use":
                require(tool_id == fixture.read.tool_id and json.loads(arguments) == fixture.read.tool_input,
                        "HQ tool input did not match the fixture")
                start = tool_frames[0]
                event("content_block_start", index=0, content_block={"type": "tool_use", "id": start["call_id"],
                      "name": start["name"], "input": {}})
                for fragment in tool_frames[1:]:
                    event("content_block_delta", index=0, delta={"type": "input_json_delta", "partial_json": fragment["delta"]})
            else:
                require(len(chunks) == 2, "HQ did not deliver two text chunks")
            usage = terminal["usage"]
        else:
            if stop == "tool_use":
                event("content_block_start", index=0, content_block={
                    "type": "tool_use", "id": fixture.read.tool_id, "name": "Read", "input": {},
                })
                event("content_block_delta", index=0, delta={
                    "type": "input_json_delta", "partial_json": json.dumps(fixture.read.tool_input),
                })
            else:
                event("content_block_start", index=0, content_block={"type": "text", "text": ""})
                for index, chunk in enumerate(fixture.chunks):
                    event("content_block_delta", index=0, delta={"type": "text_delta", "text": chunk})
                    if index == 0:
                        require(fixture.first_seen.wait(8), "first delta was buffered")
            usage = USAGE
        event("content_block_stop", index=0)
        event("message_delta", delta={"stop_reason": terminal["stop_reason"] if fixture.bridge else stop,
                                      "stop_sequence": None}, usage=usage)
        event("message_stop")


def write_rpc(child, frame):
    child.stdin.write(json.dumps({"jsonrpc": "2.0", **frame}).encode() + b"\n")
    child.stdin.flush()


def acp_reply(child, reply_id, notice, deny_unexpected_tool=False, *, error_code=None):
    for _ in range(256):
        frame = read_frame(child.stdout)
        require(frame is not None, "ACP closed before replying")
        if "method" not in frame:
            require(type(frame.get("id")) is int and frame["id"] == reply_id, "unexpected ACP reply ID")
            if error_code is not None:
                error = frame.get("error")
                require(set(frame) == {"jsonrpc", "id", "error"} and isinstance(error, dict)
                        and type(error.get("code")) is int and error["code"] == error_code,
                        "ACP did not return the expected error")
                return error
            require("result" in frame, "ACP refused the request")
            return frame["result"]
        if "id" not in frame:
            notice(frame)
        elif frame["method"] == "session/request_permission":
            write_rpc(child, {"id": frame["id"], "result": {"outcome": {"outcome": "cancelled"}}})
            require(not deny_unexpected_tool, "Read required consent beyond the one-file rule")
        else:
            write_rpc(child, {"id": frame["id"], "error": {"code": -32601, "message": "method not found"}})
    raise FixtureError("too many ACP notifications")


def initialize_acp(child):
    write_rpc(child, REQUEST)
    initialized = acp_reply(child, 1, lambda _: None)
    require(initialized.get("protocolVersion") == 1
            and initialized["agentInfo"]["name"] == PACKAGE and initialized["agentInfo"]["version"] == VERSION,
            "unexpected ACP identity")
    return initialized


def observe_acp(child, cwd, fixture, *, check_load=False, load_session=None):
    initialized = initialize_acp(child)
    if check_load:
        capabilities = initialized["agentCapabilities"]
        require(capabilities.get("loadSession") is True, "ACP did not advertise session/load")
        require(isinstance(capabilities.get("sessionCapabilities", {}).get("resume"), dict),
                "ACP did not advertise session/resume")
    if load_session:
        replay = {"user_message_chunk": [], "agent_message_chunk": []}

        def history(frame):
            params = frame.get("params", {})
            if frame["method"] == "session/update" and params.get("sessionId") == load_session:
                update = params["update"]
                content = update.get("content", {})
                role = update["sessionUpdate"]
                chunks = replay.get(role)
                if chunks is not None and content.get("type") == "text":
                    if role == "user_message_chunk":
                        require(not replay["agent_message_chunk"], "ACP replayed the reply before the full prompt")
                    chunks.append(content["text"])

        write_rpc(child, {"id": 2, "method": "session/load", "params": {
            "sessionId": load_session, "cwd": str(cwd), "mcpServers": [],
        }})
        acp_reply(child, 2, history)
        require(tuple("".join(chunks) for chunks in replay.values()) == fixture.resume_history,
                "ACP did not replay both messages from the saved session")
        require(fixture.prompt_calls == 1, "ACP issued a prompt while loading the transcript")
        session = load_session
    elif fixture.read:
        write_rpc(child, {"id": 2, "method": "session/new", "params": {
            "cwd": str(cwd), "mcpServers": [],
            "_meta": {"claudeCode": {"options": {
                "tools": ["Read"], "settings": fixture.read.settings,
            }}},
        }})
        session = acp_reply(child, 2, lambda _: None, deny_unexpected_tool=True)["sessionId"]
    else:
        session = check_session(child, cwd)
    chunks = []
    usage_seen = False
    tool_ids = set()

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
        if fixture.read and update["sessionUpdate"] in {"tool_call", "tool_call_update"}:
            tool_ids.add(update["toolCallId"])
            require(tool_ids == {fixture.read.tool_id}, "unexpected ACP tool operation")
        if update["sessionUpdate"] == "usage_update":
            usage_seen = True

    write_rpc(child, {"id": 3, "method": "session/prompt", "params": {
        "sessionId": session, "prompt": [{"type": "text", "text": fixture.prompt}],
    }})
    result = acp_reply(child, 3, notice, deny_unexpected_tool=fixture.read is not None)
    require(result.get("stopReason") == "end_turn" and usage_seen, "ACP did not finish with usage")
    require(tuple(chunks) == fixture.chunks, "unexpected ACP text deltas")
    if fixture.read:
        require(tool_ids == {fixture.read.tool_id}, "ACP did not report the Read operation")
        usage = result.get("usage") or {}
        quota = result.get("_meta", {}).get("quota") or {}
        fixture.read.shape.update(terminal_end_turn=True, acp_tool_id_matches=True,
                                  usage_token_fields=all(type(usage.get(key)) is int
                                                         for key in ("inputTokens", "outputTokens")),
                                  model_usage_present=bool(quota.get("model_usage")))
    child.stdin.close()
    return session


def observe_cli(child, _, fixture):
    chunks = []
    result = {}
    stop_reasons = []
    tool_calls = 0
    for line in child.stdout:
        frame = json.loads(line)
        event = frame.get("event", {})
        if fixture.read and event.get("type") == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                tool_calls += 1
                require(tool_calls == 1 and block.get("name") == "Read" and block.get("id") == fixture.read.tool_id,
                        "unexpected CLI tool operation")
        if fixture.read and event.get("type") == "message_delta":
            stop_reasons.append(event["delta"].get("stop_reason"))
        if event.get("type") == "content_block_delta" and event["delta"].get("type") == "text_delta":
            chunks.append(event["delta"]["text"])
            if chunks == [fixture.chunks[0]]:
                fixture.first_seen.set()
        if frame.get("type") == "result":
            result = frame
    require(tuple(chunks) == fixture.chunks, "unexpected CLI text deltas")
    current_calls = fixture.prompt_calls - int(fixture.resume_history is not None)
    require(result.get("is_error") is False and result.get("result") == "".join(fixture.chunks)
            and all(result["usage"][key] == value * current_calls for key, value in USAGE.items()),
            "CLI did not finish with the fixture text and usage")
    if fixture.read:
        require(tool_calls == 1 and stop_reasons == ["tool_use", "end_turn"], "unexpected CLI tool or stop reasons")
        fixture.read.shape.update(terminal_end_turn=True, usage_summed=True,
                                  model_usage_present=bool(result.get("modelUsage")))
    return result.get("session_id")


def expired(*_):
    raise FixtureError("client deadline exceeded")


def run_client(command, cwd, env, stdin, observe, fixture, *, expected_exit=0):
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
            observation = observe(child, cwd, fixture)
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
        require(status == expected_exit, "client exit did not match the expected result")
        return observation


def check_transcript(home, session):
    project_root = home / ".claude/projects"
    paths = list(project_root.glob(f"*/{session}.jsonl"))
    require(len(paths) == 1 and not paths[0].is_symlink() and paths[0].is_file()
            and paths[0].resolve().is_relative_to(home.resolve()),
            "missing native transcript in the assigned member home")
    with paths[0].open("rb") as source:
        transcript = source.read(512 * 1024 + 1)
    require(0 < len(transcript) <= 512 * 1024, "unexpected native transcript size")
    entries = [json.loads(line) for line in transcript.splitlines()]
    return [entry["message"] for entry in entries
            if entry.get("sessionId") == session and entry.get("type") in ("user", "assistant")]


def resume_session(env, fixture, start, reopen):
    home = Path(env["HOME"])
    require(not (home / ".claude/projects").exists(), "expected a fresh member home")
    session = start()
    require(isinstance(session, str) and str(UUID(session)) == session,
            "client did not return a native session ID")
    first = (fixture.prompt, "".join(fixture.chunks))
    require(contains_turn(check_transcript(home, session), *first),
            "native transcript does not contain the first turn")

    with fixture.lock:
        fixture.resume_history = first
        fixture.prompt = "Return the resumed fixture response for " + secrets.token_hex(12)
        fixture.chunks = ("resumed-" + secrets.token_hex(12), "-complete")
        fixture.first_seen.clear()
    require(reopen(session) == session, "client did not reopen the same session")
    require(contains_turn(check_transcript(home, session), fixture.prompt, "".join(fixture.chunks)),
            "native transcript does not contain the resumed turn")


def resume_cli(command, cwd, env, fixture):
    def invoke(*arguments):
        return run_client(command + ["--print", fixture.prompt, *arguments], cwd, env,
                          subprocess.DEVNULL, observe_cli, fixture)

    resume_session(env, fixture, invoke, lambda session: invoke("--resume", session))


def load_acp(command, cwd, env, fixture):
    def invoke(session=None):
        def observe(child, workdir, model):
            return observe_acp(child, workdir, model, check_load=True, load_session=session)

        return run_client(command, cwd, env, subprocess.PIPE, observe, fixture)

    resume_session(env, fixture, invoke, invoke)


def trial(root, profile, mode, read_fixture, hq_source=None, resume=False):
    state = root / mode
    state.mkdir()
    _, cwd, env = private_state(state, "member-temp")
    member, broker = state / "member.sock", state / "broker.sock"
    env.update(ANTHROPIC_UNIX_SOCKET=str(broker), ANTHROPIC_API_KEY=PLACEHOLDER,
               ANTHROPIC_BASE_URL="http://fixture.invalid", ANTHROPIC_MODEL=MODEL,
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    policy = ["/usr/bin/sandbox-exec", "-D", f"BROKER_SOCKET={broker}",
              "-D", f"MEMBER_SOCKET={member}", "-f", str(profile)]
    with MessagesFixture(broker, ReadFixture(cwd) if read_fixture else None) as fixture:
        if hq_source:
            node = shutil.which("node")
            require(node is not None, "Node.js is required for the HQ fixture")
            fixture.bridge = ReadBridge(node, hq_source, fixture.read)
        server = threading.Thread(target=fixture.serve_forever, daemon=True)
        server.start()
        try:
            if mode == "cli":
                command = [str(root / "claude"), "--model", MODEL, "--output-format", "stream-json",
                           "--verbose", "--include-partial-messages", "--tools", "Read" if read_fixture else ""]
                if resume:
                    resume_cli(policy + command, cwd, env, fixture)
                else:
                    command += ["--print", fixture.prompt, "--no-session-persistence"]
                    if read_fixture:
                        command += ["--settings", json.dumps(fixture.read.settings)]
                    run_client(policy + command, cwd, env, subprocess.DEVNULL, observe_cli, fixture)
            else:
                command = policy + [str(root / "claude-agent-acp")]
                if resume:
                    load_acp(command, cwd, env, fixture)
                else:
                    run_client(command, cwd, env, subprocess.PIPE, observe_acp, fixture)
        except FixtureError:
            if fixture.error is not None:
                raise fixture.error from None
            raise
        finally:
            fixture.shutdown()
            server.join(timeout=5)
            if fixture.bridge:
                fixture.bridge.close()
        if fixture.error is not None:
            raise fixture.error
        require(fixture.prompt_calls == (2 if read_fixture or resume else 1) and fixture.first_seen.is_set(),
                "the fixture did not complete the expected prompt requests")
        print(json.dumps({"mode": mode, "model": MODEL, "prompt_messages": fixture.prompt_calls,
                          "ancillary_messages": fixture.ancillary_messages, "policy_checks": fixture.policy_checks,
                          "incremental_deltas": len(fixture.chunks), "usage_observed": True,
                          **(fixture.read.shape if read_fixture else {}),
                          **({"native_transcript_in_member_home": True, "resumed_in_new_process": True,
                              "resumed_request_carried_history": True} if resume else {}),
                          **({"acp_load_advertised": True, "acp_resume_advertised": True,
                              "acp_load_replayed_history": True} if resume and mode == "acp" else {}),
                          **({"bound_hq_fixture": True, "modeled_turn_equal": True,
                              "completed_zero_cost_operations": 2} if hq_source else {})}))


def observe_direct_cli(child, _cwd, _fixture):
    data = child.stdout.read(256 * 1024 + 1)
    require(0 < len(data) <= 256 * 1024, "missing bounded direct-only CLI result")
    result = json.loads(data)
    require(isinstance(result, dict) and result.get("type") == "result"
            and result.get("is_error") is True and isinstance(result.get("result"), str),
            "direct-only CLI invocation did not report failure")
    return result["result"]


def observe_direct_acp(child, cwd, prompt):
    initialize_acp(child)
    session = check_session(child, cwd)
    write_rpc(child, {"id": 3, "method": "session/prompt", "params": {
        "sessionId": session, "prompt": [{"type": "text", "text": prompt}],
    }})
    error = acp_reply(child, 3, lambda _: None, error_code=-32603)
    child.stdin.close()
    require(len(child.stdout.read(256 * 1024 + 1)) <= 256 * 1024, "excess ACP output after EOF")
    return json.dumps(error)


def direct_control(root, profile, mode):
    state = root / f"direct-{mode}"
    state.mkdir()
    _, cwd, env = private_state(state, "member-temp")
    with DirectCanary() as canary:
        server = threading.Thread(target=canary.serve_forever, daemon=True)
        server.start()
        try:
            def reachable():
                with socket.create_connection(canary.server_address, timeout=3) as client:
                    require(client.recv(1) == b"", "unexpected canary response")

            reachable()
            env.update(ANTHROPIC_API_KEY=PLACEHOLDER,
                       ANTHROPIC_BASE_URL=f"http://127.0.0.1:{canary.server_address[1]}",
                       ANTHROPIC_MODEL=MODEL, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                       CLAUDE_CODE_MAX_RETRIES="0", API_TIMEOUT_MS="3000")
            broker, member = state / "broker.sock", state / "member.sock"
            command = ["/usr/bin/sandbox-exec", "-D", f"BROKER_SOCKET={broker}",
                       "-D", f"MEMBER_SOCKET={member}", "-f", str(profile)]
            prompt = "Return the fixture response."
            if mode == "cli":
                command += [str(root / "claude"), "--model", MODEL, "--output-format", "json", "--tools", "",
                            "--print", prompt, "--no-session-persistence"]
                message = run_client(command, cwd, env, subprocess.DEVNULL, observe_direct_cli, None, expected_exit=1)
            else:
                command.append(str(root / "claude-agent-acp"))
                message = run_client(command, cwd, env, subprocess.PIPE, observe_direct_acp, prompt)
            message = message.lower()
            require("connect" in message and ("permission" in message or "eperm" in message),
                    "direct-only client did not report a permission-related connection failure")
            reachable()
            with canary.lock:
                require(canary.connections == 2, "confined direct client reached the loopback canary")
            print(json.dumps({"mode": "direct-control", "client": mode, "reachable_controls": 2,
                              "confined_direct_connections": 0, "client_reported_error": True,
                              "connection_error": True, "mentions_permission": True,
                              "mentions_eperm": "eperm" in message, "mentions_loopback": "127.0.0.1" in message,
                              **({"acp_session_created": True, "acp_prompt_error": -32603} if mode == "acp" else {}),
                              "scope": "fixture-only; no protected-host qualification"}))
        finally:
            canary.shutdown()
            server.join(timeout=5)
            require(not server.is_alive(), "direct canary did not stop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="pinned v0.109.0 Darwin arm64 release zip")
    parser.add_argument("machine_profile", type=Path, help="pinned Machine harness-egress.sb")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--read-fixture", action="store_true", help="exercise one local Read and follow-up")
    mode.add_argument("--resume-fixture", action="store_true", help="persist and resume a native CLI session")
    mode.add_argument("--acp-load-fixture", action="store_true", help="load a saved ACP session after a restart")
    mode.add_argument("--direct-control", action="store_true", help="compare direct-only and assigned-broker fixture turns")
    parser.add_argument("--hq-source", type=Path, help="pinned HQ draft checkout for the optional bound bridge")
    args = parser.parse_args()
    require_guest()
    if args.hq_source and not args.read_fixture:
        parser.error("--hq-source requires --read-fixture")
    if args.hq_source:
        hq = args.hq_source.resolve()
        revision = subprocess.check_output(["git", "-C", str(hq), "rev-parse", "HEAD"], text=True, timeout=10).strip()
        changed = subprocess.check_output(["git", "-C", str(hq), "status", "--porcelain", "--untracked-files=no"], text=True, timeout=10)
        if revision != "b3c4a6aadfe20abcac547ba17cd4538d3b00a68c" or changed:
            raise SystemExit("HQ fixture checkout must be the clean reviewed draft revision")
        args.hq_source = hq
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="claude-uds-", dir="/private/tmp") as temp:
        root = Path(temp)
        unpack(args.archive, root)
        profile = root / "harness-egress.sb"
        shutil.copyfile(args.machine_profile, profile)
        verify(profile, PROFILE_SHA256)
        modes = ("cli",) if args.resume_fixture else ("acp",) if args.acp_load_fixture else ("cli", "acp")
        for mode in modes:
            try:
                if args.direct_control:
                    direct_control(root, profile, mode)
                trial(root, profile, mode, args.read_fixture, args.hq_source,
                      args.resume_fixture or args.acp_load_fixture)
            except (FixtureError, BridgeError) as error:
                raise SystemExit(f"{mode}: {error}") from None
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
                raise SystemExit(f"{mode}: invalid client or fixture output") from None


if __name__ == "__main__":
    main()

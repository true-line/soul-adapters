"""Signed-out ACP session check for Apple Silicon. Run with a standalone Node distribution."""

import json
import os
from pathlib import Path
import platform
import select
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import closing


HERE = Path(__file__).resolve().parent
PACKAGE = "@agentclientprotocol/codex-acp"


def read_frame(stream):
    data = stream.readline(65537)
    if not data:
        return None
    if len(data) > 65536 or not data.endswith(b"\n"):
        raise ValueError("incomplete or oversized ACP frame")
    frame = json.loads(data)
    if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
        raise ValueError("invalid JSON-RPC frame")
    return frame


def auth_notice(frame):
    return (set(frame) == {"jsonrpc", "method", "params"}
            and frame["method"] == "_auth/status_update")


def exchange(child, request_id, method, params):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    child.stdin.write(json.dumps(message).encode() + b"\n")
    child.stdin.flush()
    for _ in range(32):
        frame = read_frame(child.stdout)
        if frame is None:
            raise ValueError(f"{method}: missing response")
        if auth_notice(frame):
            continue
        if type(frame.get("id")) is not int or frame["id"] != request_id or "method" in frame:
            raise ValueError(f"{method}: unexpected message")
        return frame
    raise ValueError(f"{method}: response limit exceeded")


def initialize(child, wire):
    info = {"name": "signed-out-check", "version": "1.0.0"}
    if wire == 1:
        params = {"protocolVersion": wire, "clientInfo": info, "clientCapabilities": {}}
        info_key, method_key = "agentInfo", "id"
    else:
        params = {"protocolVersion": wire, "info": info, "capabilities": {}}
        info_key, method_key = "info", "methodId"
    reply = exchange(child, 1, "initialize", params)
    result = reply.get("result", {})
    agent = result.get(info_key, {})
    if (set(reply) != {"id", "jsonrpc", "result"} or type(result.get("protocolVersion")) is not int
            or result["protocolVersion"] != wire or agent.get("name") != PACKAGE or agent.get("version") != "2.8.0"):
        raise ValueError("unexpected ACP version or package identity")
    methods = sorted(entry[method_key] for entry in result.get("authMethods", []))
    if methods != ["api-key", "chat-gpt"]:
        raise ValueError("unexpected signed-out authentication methods")


def live_sidecars(leader):
    # Only compare numeric process IDs and state. The leader is still unreaped.
    snapshot = subprocess.check_output(["/bin/ps", "-A", "-o", "pgid=,pid=,stat="],
                                       text=True, timeout=5)
    return any(group == str(leader) and pid != str(leader) and not state.startswith("Z")
               for group, pid, state in (row.split() for row in snapshot.splitlines()))


def session(command, env, state, wire):
    def expired(*_):
        raise TimeoutError("adapter handshake or shutdown timed out")

    with closing(select.kqueue()) as events, subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=state, env=env, start_new_session=True) as child:
        previous = signal.signal(signal.SIGALRM, expired)
        try:
            watch = select.kevent(child.pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD,
                                  fflags=select.KQ_NOTE_EXIT)
            events.control([watch], 0, 0)
            signal.alarm(45)
            initialize(child, wire)
            reply = exchange(child, 2, "session/new", {"cwd": str(state), "mcpServers": []})
            error = reply.get("error", {})
            if (set(reply) != {"id", "jsonrpc", "error"} or error.get("code") != -32000
                    or error.get("message") != "Authentication required"):
                raise ValueError("signed-out session/new did not return the expected authentication error")
            child.stdin.close()
            signal.alarm(10)
            final = read_frame(child.stdout)
            if final is not None and (not auth_notice(final) or read_frame(child.stdout) is not None):
                raise ValueError("unexpected output after stdin EOF")
            if not events.control(None, 1, 10):
                raise TimeoutError("adapter did not exit after EOF")
            if live_sidecars(child.pid):
                raise ValueError("adapter left a live child process after EOF")
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
            # Release every process in our group while its leader's PID is still reserved.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # Darwin may reject a group signal when the only member is a zombie.
                pass
            deadline = time.monotonic() + 5
            while live_sidecars(child.pid):
                if time.monotonic() >= deadline:
                    raise RuntimeError("adapter process group survived cleanup")
                time.sleep(0.05)
            code = child.wait(timeout=5)
        if code != 0:
            raise ValueError("adapter exited unsuccessfully after EOF")


def check(packages, state, node, wire):
    cli = packages / "@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
    adapter = packages / PACKAGE / "dist/index.js"
    sandbox = ["/usr/bin/sandbox-exec", "-D", f"NODE={node}", "-D", f"CODEX={cli}",
               "-D", f"PACKAGES={packages}", "-D", f"STATE={state}", "-f", str(HERE / "session.sb")]
    env = {"PATH": "/usr/bin:/bin", "CODEX_PATH": str(cli)}
    for name in ("HOME", "CODEX_HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                 "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        path = state / name.lower()
        path.mkdir(mode=0o700)
        env[name] = str(path)
    # Distinguish a denied socket from a reachable host with a closed port.
    # The canary is deliberately readable by this user outside the sandbox.
    canary = state.parent / "read-control"
    canary.write_text("fixture")
    control = """
        const fs = require('node:fs');
        const net = require('node:net');
        try { fs.readFileSync(process.argv[1]); process.exit(1); }
        catch (err) { if (!['EACCES', 'EPERM'].includes(err.code)) throw err; }
        const probe = net.connect(9, '127.0.0.1');
        probe.on('connect', () => process.exit(1));
        probe.on('error', err => process.exit(['EACCES', 'EPERM'].includes(err.code) ? 0 : 1));
    """
    subprocess.run(sandbox + [str(node), "-e", control, str(canary)],
                   cwd=state, env=env, check=True, timeout=15)
    version = subprocess.check_output(sandbox + [str(cli), "--version"], cwd=state, env=env, timeout=15)
    if version.strip() != b"codex-cli 0.170.0":
        raise ValueError("unexpected Codex CLI version")
    session(sandbox + [str(node), str(adapter)], env, state, wire)
    print(f"PASS codex-acp 2.8.0 / codex-cli 0.170.0: ACP v{wire} advertised api-key and chat-gpt, "
          "session/new refused (-32000 Authentication required), EOF exit 0")


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64" or os.getuid() == 0:
        raise SystemExit("requires a non-root Apple Silicon macOS account")
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise SystemExit("put the standalone Node.js distribution and npm on PATH")
    with tempfile.TemporaryDirectory(prefix="codex-acp-", dir="/private/tmp") as temporary:
        root = Path(temporary)
        install = root / "install"
        install.mkdir()
        for filename in ("package.json", "package-lock.json"):
            shutil.copyfile(HERE / filename, install / filename)
        env = {"PATH": f"{Path(node).parent}:/usr/bin:/bin", "HOME": str(root),
               "NPM_CONFIG_USERCONFIG": "/dev/null", "NPM_CONFIG_GLOBALCONFIG": str(root / "npmrc"),
               "NPM_CONFIG_UPDATE_NOTIFIER": "false"}
        subprocess.run([npm, "ci", "--include=optional", "--ignore-scripts", "--no-audit", "--no-fund"],
                       check=True, cwd=install, env=env, timeout=180)
        for wire in (1, 2):
            state = root / f"state-v{wire}"
            state.mkdir(mode=0o700)
            check(install / "node_modules", state, Path(node).resolve(), wire)


if __name__ == "__main__":
    main()

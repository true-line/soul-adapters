"""Signed-out ACP session check for Apple Silicon. Run with a standalone Node distribution."""

import ctypes
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
LOCKED_PACKAGES = json.loads((HERE / "package-lock.json").read_text())["packages"]
CODEX_VERSION = LOCKED_PACKAGES[""]["dependencies"]["@openai/codex"]
FALLBACK_CODEX_VERSION = LOCKED_PACKAGES[f"node_modules/{PACKAGE}/node_modules/@openai/codex"]["version"]
NATIVE_CODEX = "@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
CODEX_VERSION_OUTPUT = f"codex-cli {CODEX_VERSION}".encode()


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


def initialize(child, wire, *, gateway=False):
    caps = {"auth": {"_meta": {"gateway": True}}} if gateway else {}
    info = {"name": "signed-out-check", "version": "1.0.0"}
    if wire == 1:
        params = {"protocolVersion": wire, "clientInfo": info, "clientCapabilities": caps}
        info_key, method_key = "agentInfo", "id"
    else:
        params = {"protocolVersion": wire, "info": info, "capabilities": caps}
        info_key, method_key = "info", "methodId"
    reply = exchange(child, 1, "initialize", params)
    result = reply.get("result", {})
    agent = result.get(info_key, {})
    if (set(reply) != {"id", "jsonrpc", "result"} or type(result.get("protocolVersion")) is not int
            or result["protocolVersion"] != wire or agent.get("name") != PACKAGE or agent.get("version") != "2.8.0"):
        raise ValueError("unexpected ACP version or package identity")
    methods = sorted(entry[method_key] for entry in result.get("authMethods", []))
    expected = ["api-key", "chat-gpt", "gateway"] if gateway else ["api-key", "chat-gpt"]
    if methods != expected:
        raise ValueError("unexpected signed-out authentication methods")


def live_sidecars(leader):
    # Only compare numeric process IDs and state. The leader is still unreaped.
    snapshot = subprocess.check_output(["/bin/ps", "-A", "-o", "pgid=,pid=,stat="],
                                       text=True, timeout=5)
    return any(group == str(leader) and pid != str(leader) and not state.startswith("Z")
               for group, pid, state in (row.split() for row in snapshot.splitlines()))


def observe_child_executable(leader, native, wrapper_node=None):
    """Check only live children in the launched fixture's own process group."""
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    child_pids = libproc.proc_listchildpids
    child_pids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    executable_path = libproc.proc_pidpath
    executable_path.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    pids = (ctypes.c_int * 16)()
    # PROC_PIDPATHINFO_MAXSIZE from Darwin's libproc.h.
    path = ctypes.create_string_buffer(4096)

    def children(parent):
        count = child_pids(parent, pids, ctypes.sizeof(pids))
        if not 0 <= count < len(pids):
            raise ValueError("could not observe bounded fixture descendants")
        paths = {}
        for pid in pids[:count]:
            try:
                if pid > 0 and os.getpgid(pid) == leader and executable_path(pid, path, len(path)) > 0:
                    paths[pid] = os.fsdecode(path.value)
            except ProcessLookupError:
                continue
        return paths

    direct = children(leader)
    matches = [1 for path in direct.values() if path == str(native)]
    # Current Node execs the native binary in the wrapper process. Older
    # Node releases can retain the wrapper and spawn one additional child.
    if wrapper_node is not None:
        for pid, path in direct.items():
            if path == str(wrapper_node):
                matches.extend(2 for path in children(pid).values() if path == str(native))
    if len(matches) != 1:
        raise ValueError("expected one live child with the pinned executable path")
    return {"native_executable": str(native), "child_depth": matches[0]}


def check_signed_out(child, wire, state):
    initialize(child, wire)
    reply = exchange(child, 2, "session/new", {"cwd": str(state), "mcpServers": []})
    error = reply.get("error", {})
    if (set(reply) != {"id", "jsonrpc", "error"} or error.get("code") != -32000
            or error.get("message") != "Authentication required"):
        raise ValueError("signed-out session/new did not return the expected authentication error")


def session(command, env, state, wire, handshake=check_signed_out):
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
            observation = handshake(child, wire, state)
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
        return observation


def private_env(state, cli):
    env = {"PATH": "/usr/bin:/bin"}
    if cli is not None:
        env["CODEX_PATH"] = str(cli)
    for name in ("HOME", "CODEX_HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                 "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        path = state / name.lower()
        path.mkdir(mode=0o700)
        env[name] = str(path)
    return env


def check(packages, state, node, wire, *, package_fallback=False):
    explicit = packages / NATIVE_CODEX
    fallback = packages / PACKAGE / "node_modules" / NATIVE_CODEX
    cli, excluded = (fallback, explicit) if package_fallback else (explicit, fallback)
    expected_version = FALLBACK_CODEX_VERSION if package_fallback else CODEX_VERSION
    adapter = packages / PACKAGE / "dist/index.js"
    sandbox = ["/usr/bin/sandbox-exec", "-D", f"NODE={node}", "-D", f"CODEX={cli}",
               "-D", f"PACKAGES={packages}", "-D", f"STATE={state}", "-f", str(HERE / "session.sb")]
    env = private_env(state, None if package_fallback else cli)
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
    if version.strip() != f"codex-cli {expected_version}".encode():
        raise ValueError("unexpected Codex CLI version")
    if wire == 1:
        # The other locked binary works under its own policy. It must not be
        # an executable alternative during this stock ACP handshake.
        control = subprocess.run(sandbox + [str(excluded), "--version"], cwd=state, env=env,
                                 capture_output=True, timeout=15)
        if control.returncode == 0:
            raise ValueError("sandbox allowed the other locked Codex CLI")
    def handshake(child, wire, state):
        check_signed_out(child, wire, state)
        return observe_child_executable(child.pid, cli, node if package_fallback else None)

    observation = session(sandbox + [str(node), str(adapter)], env, state, wire, handshake)
    selection = "package fallback" if package_fallback else "explicit CODEX_PATH"
    print(f"PASS codex-acp 2.8.0 / codex-cli {expected_version} ({selection}): ACP v{wire} advertised api-key and chat-gpt, "
          "session/new refused (-32000 Authentication required), native child path observed, EOF exit 0")
    return observation


def install_packages(root, node, npm, cache=None):
    install = root / "install"
    install.mkdir()
    for filename in ("package.json", "package-lock.json"):
        shutil.copyfile(HERE / filename, install / filename)
    env = {"PATH": f"{Path(node).parent}:/usr/bin:/bin", "HOME": str(root),
           "NPM_CONFIG_USERCONFIG": "/dev/null", "NPM_CONFIG_GLOBALCONFIG": str(root / "npmrc"),
           "NPM_CONFIG_UPDATE_NOTIFIER": "false"}
    args = [npm, "ci", "--include=optional", "--ignore-scripts", "--no-audit", "--no-fund"]
    if cache is not None:
        args.extend(["--offline", "--cache", str(cache)])
    subprocess.run(args,
                   check=True, cwd=install, env=env, timeout=180)
    return install / "node_modules"


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64" or os.getuid() == 0:
        raise SystemExit("requires a non-root Apple Silicon macOS account")
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise SystemExit("put the standalone Node.js distribution and npm on PATH")
    with tempfile.TemporaryDirectory(prefix="codex-acp-", dir="/private/tmp") as temporary:
        root = Path(temporary)
        packages = install_packages(root, node, npm)
        for wire in (1, 2):
            state = root / f"state-v{wire}"
            state.mkdir(mode=0o700)
            check(packages, state, Path(node).resolve(), wire)
        state = root / "state-package-fallback"
        state.mkdir(mode=0o700)
        check(packages, state, Path(node).resolve(), 1, package_fallback=True)


if __name__ == "__main__":
    main()

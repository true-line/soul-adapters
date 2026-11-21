"""Hosted-only ACP v1 handshake against a pinned public release."""

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import select
import time
import shutil
import signal
import subprocess
import tempfile
import zipfile


VERSION = "0.109.0"
PACKAGE = "@agentclientprotocol/claude-agent-acp"
ASSET = "claude-agent-acp-darwin-arm64.zip"
RELEASE = f"https://github.com/agentclientprotocol/claude-agent-acp/releases/download/v{VERSION}"
ARCHIVE_SHA = "603fdb593f00335a996986f9ed9f8a84928dad4b20e08b25862782fd770b2a28"
BINARIES = {
    "claude-agent-acp": "2d48a3f9ee728def1365e89bbd0571141a607ae281344999b0349ab209bc4d39",
    "claude": "e85547190ef8cd983033ba61b17194045a6fea00035da349efeabed86badacc9",
}
REQUEST = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": 1, "clientCapabilities": {}}}


def verify(path, digest):
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(chunk)
    if sha.hexdigest() != digest:
        raise ValueError(f"unexpected digest for {path.name}")


def unpack(archive, root):
    verify(archive, ARCHIVE_SHA)
    with zipfile.ZipFile(archive) as package:
        if sorted(package.namelist()) != sorted(BINARIES):
            raise ValueError("unexpected release members")
        for name, digest in BINARIES.items():
            target = root / name
            with package.open(name) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            verify(target, digest)
            target.chmod(0o500)


def read_frame(stream):
    raw = stream.readline(65537)
    if not raw:
        return None
    if len(raw) > 65536 or not raw.endswith(b"\n"):
        raise ValueError("invalid ACP line")
    frame = json.loads(raw)
    if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
        raise ValueError("invalid JSON-RPC frame")
    return frame


class SessionRefused(Exception):
    pass


def check_session(child, cwd):
    request = {"jsonrpc": "2.0", "id": 2, "method": "session/new",
               "params": {"cwd": str(cwd), "mcpServers": []}}
    child.stdin.write(json.dumps(request).encode() + b"\n")
    child.stdin.flush()
    # Souls ignores unrelated notifications. Its only request handler cancels
    # every permission request; other requests receive method-not-found.
    for _ in range(32):
        frame = read_frame(child.stdout)
        if frame is None:
            raise SessionRefused("session/new: stdout ended")
        if isinstance(frame.get("method"), str):
            if "id" in frame and frame["id"] is not None:
                response = {"jsonrpc": "2.0", "id": frame["id"]}
                if frame["method"] == "session/request_permission":
                    response["result"] = {"outcome": {"outcome": "cancelled"}}
                else:
                    response["error"] = {"code": -32601, "message": "method not found"}
                child.stdin.write(json.dumps(response).encode() + b"\n")
                child.stdin.flush()
            continue
        if type(frame.get("id")) is not int or frame["id"] != 2:
            raise SessionRefused("session/new: unexpected reply")
        if "error" in frame:
            error = frame["error"]
            code = error.get("code") if isinstance(error, dict) else None
            # Vendor errors can contain host paths and arbitrary text.
            label = str(code) if type(code) is int else "unknown"
            detail = json.dumps(error)
            if re.search(r"mkdir [^/]{1,3}(?:/private)?/tmp/claude(?:-[0-9]+)?(?:/|[^A-Za-z0-9_-])", detail):
                label += " (mkdir outside member-private temp)"
            raise SessionRefused(f"session/new: JSON-RPC refusal {label}")
        result = frame.get("result")
        if set(frame) != {"jsonrpc", "id", "result"} or not isinstance(result, dict) or not isinstance(result.get("sessionId"), str) or not result["sessionId"]:
            raise SessionRefused("session/new: missing session ID")
        return
    raise SessionRefused("session/new: response limit exceeded")


def handshake(root, session_mode=None):
    state = root / "state"
    state.mkdir(mode=0o700)
    env = {"PATH": "/usr/bin:/bin"}
    for name in ("HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                 "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        directory = state / name.lower()
        directory.mkdir(mode=0o700)
        env[name] = str(directory)
    cwd = state
    if session_mode is not None:
        cwd = state / "work"
        cwd.mkdir(mode=0o700)
        env.update(TMP=env["TMPDIR"], TEMP=env["TMPDIR"], XDG_CONFIG_DIRS=env["XDG_CONFIG_HOME"],
                   XDG_DATA_DIRS=env["XDG_DATA_HOME"])
        if session_mode == "member-temp":
            env["CLAUDE_CODE_TMPDIR"] = env["TMPDIR"]
    adapter = root / "claude-agent-acp"
    command = ["/usr/bin/sandbox-exec", "-D", f"ADAPTER={adapter}",
               "-D", f"CLI={root / 'claude'}", "-D", f"STATE={state}",
               "-f", str(Path(__file__).with_suffix(".sb").resolve()), str(adapter)]
    with subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as child:
        phase = "initialize"
        def expired(*_):
            raise TimeoutError(f"{phase}: deadline exceeded")

        previous = signal.signal(signal.SIGALRM, expired)
        events = select.kqueue()
        try:
            watch = select.kevent(child.pid, filter=select.KQ_FILTER_PROC,
                                  flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                  fflags=select.KQ_NOTE_EXIT)
            events.control([watch], 0, 0)
            signal.alarm(30)
            initialize = REQUEST
            if session_mode is not None:
                initialize = {**REQUEST, "params": {"protocolVersion": 1, "clientCapabilities":
                              {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}}}
            child.stdin.write(json.dumps(initialize).encode() + b"\n")
            child.stdin.flush()
            reply = read_frame(child.stdout)
            if reply is None:
                raise ValueError("stdout closed before initialize response")
            if (set(reply) != {"id", "jsonrpc", "result"}
                    or type(reply["id"]) is not int or reply["id"] != 1):
                raise ValueError("missing initialize response for request 1")
            result = reply["result"]
            if not isinstance(result, dict) or not isinstance(result.get("agentInfo"), dict):
                raise ValueError("missing initialize result or package identity")
            info = result["agentInfo"]
            if (type(result.get("protocolVersion")) is not int or result["protocolVersion"] != 1
                    or info.get("name") != PACKAGE or info.get("version") != VERSION
                    or not isinstance(result.get("agentCapabilities"), dict)
                    or not isinstance(result.get("authMethods"), list)):
                raise ValueError("unexpected ACP v1 package identity or capabilities")

            if session_mode is not None:
                phase = "session/new"
                signal.alarm(60)
                check_session(child, cwd)
            phase = "stdin EOF"
            child.stdin.close()
            signal.alarm(10)
            # Upstream can emit one asynchronous auth status notification after initialize.
            extra = read_frame(child.stdout)
            if extra is not None:
                if (set(extra) != {"jsonrpc", "method", "params"}
                        or extra["method"] != "_auth/status_update" or read_frame(child.stdout) is not None):
                    raise ValueError("unexpected frame after initialize")
            # Observe exit without reaping the PID. Cleanup only signals our
            # own process group while its leader PID is still reserved.
            if not events.control(None, 1, 10):
                raise TimeoutError("adapter did not exit after stdin EOF")
        finally:
            events.close()
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
            # Kill the isolated process group on failure or if the probe outlives its parent.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # An exited leader alone no longer accepts a group signal.
                # Require positive disappearance before reporting success.
                pass
            code = child.wait(timeout=5)
            deadline = time.monotonic() + 5
            while True:
                snapshot = subprocess.run(["/bin/ps", "-A", "-o", "pgid=,stat="],
                                          capture_output=True, text=True, check=True,
                                          timeout=5, env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"})
                group = [stat for pgid, stat in (line.split() for line in snapshot.stdout.splitlines())
                         if pgid == str(child.pid)]
                if not any(not stat.startswith("Z") for stat in group):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("adapter process group did not exit")
                time.sleep(0.05)
        if code != 0:
            raise ValueError("adapter did not exit zero after stdin EOF")


def main():
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_KIND") != "github-hosted"
            or platform.system() != "Darwin" or platform.machine() != "arm64" or os.getuid() == 0):
        raise SystemExit("requires a non-root GitHub-hosted Darwin arm64 runner")
    os.umask(0o077)
    with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"]) as temp:
        root = Path(temp).resolve()
        archive = root / ASSET
        subprocess.run(["/usr/bin/curl", "--disable", "--fail", "--silent", "--show-error",
                        "--location", "--proto", "=https", "--proto-redir", "=https",
                        "--max-time", "180", "--output", str(archive), f"{RELEASE}/{ASSET}"], check=True)
        unpack(archive, root)
        handshake(root)
    print(f"ACP v1 initialize passed for {PACKAGE} {VERSION}; stdin EOF exited zero")


if __name__ == "__main__":
    main()

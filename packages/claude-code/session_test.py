"""The offline probe implements the entry point's session/new response handling."""

from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
import platform
import re
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import initialize
import session

from initialize import check_eof, check_session, SessionRefused


def child_with(*frames):
    stream = b"".join(json.dumps({"jsonrpc": "2.0", **frame}).encode() + b"\n" for frame in frames)
    return SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(stream))


class SessionProbe(unittest.TestCase):
    def test_post_session_command_notification(self):
        commands = {"method": "session/update", "params": {"sessionId": "mine", "update":
                    {"sessionUpdate": "available_commands_update", "availableCommands": []}}}
        auth = {"method": "auth/status_update", "params": {"state": {}}}
        check_eof(child_with(commands, auth).stdout, "mine")
        check_eof(child_with(auth, commands).stdout, "mine")
        for frames, session_id in (([commands], "other"), ([commands], None), ([auth], None),
                                   ([commands, commands], "mine"), ([{"id": 2, "result": {}}], "mine"),
                                   ([{"method": "other", "params": {}}], "mine"),
                                   ([{"method": "session/update", "params": {"sessionId": "mine", "update":
                                      {"sessionUpdate": "agent_message_chunk", "content": {}}}}], "mine")):
            with self.subTest(frames=frames, session_id=session_id), self.assertRaises(ValueError):
                check_eof(child_with(*frames).stdout, session_id)

    def test_existing_client_semantics(self):
        child = child_with(
            {"method": "session/update", "params": {}},
            {"id": 4, "method": "session/request_permission"},
            {"id": 5, "method": "unimplemented"},
            {"id": 2, "result": {"sessionId": "fixture-session"}},
        )
        check_session(child, Path("/fixture/member"))
        request, cancelled, unavailable = map(json.loads, child.stdin.getvalue().splitlines())
        self.assertEqual(request["method"], "session/new")
        self.assertEqual(request["params"], {"cwd": "/fixture/member", "mcpServers": []})
        self.assertEqual(cancelled["result"], {"outcome": {"outcome": "cancelled"}})
        self.assertEqual(unavailable["error"]["code"], -32601)

    def test_refusal_is_sanitized(self):
        child = child_with({"id": 2, "error": {"code": -32603, "message": "EACCES, mkdir '/tmp/claude-123/fixture'"} })
        with self.assertRaisesRegex(SessionRefused, r"^session/new: JSON-RPC refusal -32603 \(mkdir outside member-private temp\)$"):
            check_session(child, Path("/fixture/member"))

    def test_missing_session(self):
        for response in [{"id": 1, "result": {}}, {"id": 2, "result": {}}, {"method": "session/update"}]:
            with self.subTest(response=response), self.assertRaises(SessionRefused):
                check_session(child_with(response), Path("/fixture/member"))


class StagedBundleProbe(unittest.TestCase):
    def setUp(self):
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        self.bundle = Path(contexts.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.expected = {}
        for name in ("claude-agent-acp", "claude"):
            content = f"fixture {name}".encode()
            binary = self.bundle / name
            binary.write_bytes(content)
            binary.chmod(0o500)
            self.expected[name] = hashlib.sha256(content).hexdigest()
        contexts.enter_context(patch.object(session, "BINARIES", self.expected))
        contexts.enter_context(patch.object(session.platform, "system", return_value="Darwin"))
        contexts.enter_context(patch.object(session.platform, "machine", return_value="arm64"))
        contexts.enter_context(patch.object(session.os, "getuid", return_value=502))

    def run_probe(self):
        previous_umask = os.umask(0o077)
        try:
            with patch.object(sys, "argv", ["session.py", "--staged-bundle", str(self.bundle)]), redirect_stdout(io.StringIO()):
                session.main()
        finally:
            os.umask(previous_umask)

    def test_existing_bundle_is_verified_and_uses_separate_private_state(self):
        scratch = []

        def check_acp(command, cwd, env, session_mode):
            scratch.append(cwd.parent.parent)
            self.assertEqual(session_mode, "member-temp")
            self.assertIn(f"ADAPTER={self.bundle / 'claude-agent-acp'}", command)
            self.assertIn(f"CLI={self.bundle / 'claude'}", command)
            self.assertEqual(command[-1], str(self.bundle / "claude-agent-acp"))
            self.assertEqual(Path(command[command.index("-f") + 1]), Path(initialize.__file__).with_suffix(".sb"))
            self.assertNotIn(self.bundle, cwd.parents)
            self.assertEqual(env["CLAUDE_CODE_TMPDIR"], env["TMPDIR"])
            self.assertNotIn("CLAUDE_CODE_EXECUTABLE", env)
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            for directory in [cwd, *(Path(value) for key, value in env.items() if key != "PATH")]:
                self.assertIn(cwd.parent, directory.parents)
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

        with patch.object(initialize, "check_acp", side_effect=check_acp) as probe, patch.dict(os.environ, {
            "CLAUDE_CODE_EXECUTABLE": "/fixture/ambient/claude",
            "ANTHROPIC_API_KEY": "only-a-test-placeholder",
        }):
            self.run_probe()
        probe.assert_called_once()
        self.assertFalse(scratch[0].exists())
        self.assertEqual(set(os.listdir(self.bundle)), set(self.expected))

    def test_rejects_changed_missing_or_linked_release_members_before_startup(self):
        for name, change, message in (
            ("claude-agent-acp", "changed", "unexpected digest for claude-agent-acp"),
            ("claude", "changed", "unexpected digest for claude"),
            ("claude", "missing", "missing staged release member: claude"),
            ("claude", "linked", "staged release member must be a regular executable: claude"),
        ):
            with self.subTest(name=name, change=change):
                member = self.bundle / name
                original = self.bundle / "original"
                member.rename(original)
                try:
                    if change == "changed":
                        member.write_bytes(b"substituted content")
                        member.chmod(0o500)
                    elif change == "linked":
                        member.symlink_to(original)
                    with patch.object(session, "handshake") as launch, patch.object(session.tempfile, "TemporaryDirectory") as scratch:
                        with self.assertRaisesRegex(SystemExit, message):
                            self.run_probe()
                    launch.assert_not_called()
                    scratch.assert_not_called()
                finally:
                    member.unlink(missing_ok=True)
                    original.rename(member)


@unittest.skipUnless(sys.platform == "darwin" and platform.machine() == "arm64", "Darwin arm64 profile")
class OfflinePolicyProbe(unittest.TestCase):
    def test_system_icu_data_readable_and_unrelated_reads_and_network_denied(self):
        icu_dir = Path("/usr/share/icu")
        candidates = sorted(path for path in icu_dir.iterdir()
                            if re.fullmatch(r"icudt[0-9]+l[.]dat", path.name))
        self.assertTrue(candidates, "no versioned macOS ICU data file")
        # Apply the same source and parameters after Python starts. This keeps
        # the checked policy's executable list limited to the two bundle files.
        probe = """
import ctypes
import errno
import socket
import sys

profile, state, icu, *unrelated = sys.argv[1:]
with open(profile, "rb") as source:
    rules = source.read()
lib = ctypes.CDLL("/usr/lib/libsandbox.1.dylib")
init = lib.sandbox_init_with_parameters
init.argtypes = (ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_char_p),
                 ctypes.POINTER(ctypes.c_char_p))
init.restype = ctypes.c_int
params = (ctypes.c_char_p * 7)(
    b"ADAPTER", b"/fixture/claude-agent-acp", b"CLI", b"/fixture/claude",
    b"STATE", state.encode(), None)
error = ctypes.c_char_p()
if init(rules, 0, params, ctypes.byref(error)):
    raise SystemExit(error.value)

with open(icu, "rb") as source:
    if not source.read(1):
        raise SystemExit("empty ICU data file")

def require_denied(operation):
    try:
        operation()
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EPERM):
            return
        raise
    raise SystemExit("unexpectedly permitted operation")

for path in unrelated:
    require_denied(lambda: open(path, "rb").close())

def connect():
    with socket.socket() as client:
        client.settimeout(2)
        client.connect(("127.0.0.1", 9))

require_denied(connect)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = root / "state"
            state.mkdir(mode=0o700)
            canary = root / "unrelated.dat"
            canary.write_bytes(b"outside the permitted state")
            other = icu_dir / "icutzformat.txt"
            unrelated = [canary, other] if other.is_file() else [canary]
            result = subprocess.run([sys.executable, "-IS", "-c", probe,
                                     str(Path(initialize.__file__).with_suffix(".sb")), str(state),
                                     str(candidates[-1]), *map(str, unrelated)],
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

"""Inputs and observable results of the disposable member startup check."""
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from vm_session import member_inputs, verify_member


class MemberSessionCheck(unittest.TestCase):
    def test_assigned_endpoints_and_only_member_bootstrap(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory, socket.socket(socket.AF_UNIX) as member, socket.socket(socket.AF_UNIX) as broker:
            root = Path(directory)
            member.bind(str(root / "member"))
            broker.bind(str(root / "broker"))
            fields = dict(AGENT_BOT_SOULS_SOCKET=str(root / "member"), AGENT_BOT_SOULS_BROKER=str(root / "broker"),
                          AGENT_BOT_SOULS_MEMBER_TOKEN="test-only")
            first, second, bootstrap = member_inputs({**fields, "OTHER_SETTING": "ignored"})
            self.assertEqual((first, second, bootstrap), (root / "member", root / "broker", fields))
            for name in fields:
                with self.assertRaises(ValueError):
                    member_inputs({**fields, name: ""})
            with self.assertRaises(ValueError):
                member_inputs({**fields, "AGENT_BOT_SOULS_BROKER": str(first)})

    def test_cli_refusal_prevents_positive_result(self):
        command = [Path(__file__).resolve()] * 2
        with patch("vm_session.subprocess.run", return_value=subprocess.CompletedProcess([], 1, b"", b"")):
            with self.assertRaisesRegex(ValueError, "runtime check failed"):
                verify_member(command, [], Path.cwd(), {}, {})

    def test_public_runtime_requires_epoch_and_lease(self):
        good = {"runtime_id": "rt_test", "installation_epoch": 1, "lease_generation": 1}
        command = [Path(__file__).resolve()] * 2
        with patch("vm_session.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, json.dumps(good))
            self.assertEqual(verify_member(command, [], Path.cwd(), {}, {}), good)
            self.assertEqual(run.call_args.args[0][-2:], ["member", "runtime"])
            for override in ({"lease_generation": False}, {"installation_epoch": 0}, {"channel_binding": "test-only"}):
                run.return_value = subprocess.CompletedProcess([], 0, json.dumps({**good, **override}))
                with self.assertRaisesRegex(ValueError, "unexpected public metadata"):
                    verify_member(command, [], Path.cwd(), {}, {})

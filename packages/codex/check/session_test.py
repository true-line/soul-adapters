from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import session


@unittest.skipUnless(sys.platform == "darwin", "uses the macOS process exit notification")
class ShutdownTest(unittest.TestCase):
    def check_failure(self, root, program, message):
        # The real package covers the handshake. These short peers exercise EOF.
        refused = {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "Authentication required"}}
        with patch.object(session, "initialize"), patch.object(session, "exchange", return_value=refused):
            with self.assertRaisesRegex(ValueError, message):
                session.session([sys.executable, "-IS", "-c", "import sys; sys.stdin.read()\n" + program],
                                {"PATH": "/usr/bin:/bin"}, root, 1)

    def test_unexpected_final_response(self):
        with tempfile.TemporaryDirectory() as root:
            self.check_failure(root, """print('{"jsonrpc":"2.0","id":2,"result":{}}')""",
                               "unexpected output after stdin EOF")

    def test_live_child_after_clean_exit(self):
        with tempfile.TemporaryDirectory() as root:
            self.check_failure(root, """
import subprocess
from pathlib import Path
child = subprocess.Popen(['/bin/sleep', '60'], stdout=subprocess.DEVNULL)
Path('fixture.pid').write_text(str(child.pid))
""", "left a live child process")
            pid = Path(root, "fixture.pid").read_text()
            proc = subprocess.run(["/bin/ps", "-p", pid, "-o", "stat="], text=True, capture_output=True)
            self.assertIn(proc.returncode, (0, 1))
            self.assertTrue(proc.returncode == 1 or proc.stdout.strip().startswith("Z"))


if __name__ == "__main__":
    unittest.main()

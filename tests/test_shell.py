import subprocess
from pathlib import Path
from unittest.mock import patch

from pyralph.shell import Shell


class TestShell:
    def test_run_basic(self):
        stdout, stderr, code = Shell.run("echo hello")
        assert "hello" in stdout
        assert code == 0
        _, _, code = Shell.run("exit 1")
        assert code == 1

    def test_run_timeout(self):
        with patch('pyralph.shell.subprocess.run', side_effect=subprocess.TimeoutExpired(cmd="t", timeout=1)):
            stdout, stderr, code = Shell.run("cmd", timeout=1)
        assert stdout == ""
        assert "Timed Out" in stderr

    def test_get_file_tree(self):
        result = Shell.get_file_tree()
        assert isinstance(result, str)
        for excluded in [".git", ".ralph", "__pycache__"]:
            for line in result.split('\n'):
                cleaned = line.strip().lstrip('├─└│ ')
                entry = Path(cleaned).name if cleaned else ''
                assert entry != excluded

    def test_get_file_tree_params(self):
        with patch('pyralph.shell.Shell.run', return_value=("out", "", 0)) as mock:
            Shell.get_file_tree(depth=5, ignore=['build'])
            call_args = mock.call_args[0][0]
            assert "-L 5" in call_args
            assert "-I 'build'" in call_args

import tempfile
import unittest
from pathlib import Path

from ai_terminal.policy import classify_tool_call
from ai_terminal.tools import build_registry


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state = {"cwd": Path(self.temp_dir.name)}
        self.registry = build_registry()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_and_info_tools_do_not_require_approval(self):
        for call in [
            {"tool": "get_cwd", "args": {}},
            {"tool": "system_info", "args": {}},
            {"tool": "project_status", "args": {"path": "."}},
            {"tool": "list_projects", "args": {}},
            {"tool": "list_processes", "args": {}},
            {"tool": "env_get", "args": {"names": ["PATH"]}},
            {"tool": "run_task", "args": {"task": "python_unittest"}},
            {"tool": "open_in_vscode", "args": {"path": "."}},
            {"tool": "run_shell", "args": {"command": "Get-ChildItem"}},
        ]:
            with self.subTest(call=call):
                decision = classify_tool_call(call, self.state, self.registry)
                self.assertFalse(decision.requires_approval)

    def test_file_mutating_tools_require_approval(self):
        for tool in [
            "write_file",
            "append_file",
            "apply_patch",
            "copy_path",
            "move_path",
            "delete_path",
            "git_add",
            "git_commit",
        ]:
            with self.subTest(tool=tool):
                decision = classify_tool_call({"tool": tool, "args": {}}, self.state, self.registry)
                self.assertTrue(decision.requires_approval)

    def test_mutating_shell_commands_require_approval(self):
        for command in [
            "Set-Content file.txt hi",
            "Remove-Item file.txt",
            "git add .",
            "git clone https://github.com/example/repo.git",
            ["git", "clone", "https://github.com/example/repo.git"],
            "npm install",
            "echo hi > file.txt",
        ]:
            with self.subTest(command=command):
                decision = classify_tool_call(
                    {"tool": "run_shell", "args": {"command": command}},
                    self.state,
                    self.registry,
                )
                self.assertTrue(decision.requires_approval)


if __name__ == "__main__":
    unittest.main()

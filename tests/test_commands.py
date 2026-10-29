from unittest import mock
import os
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ai_terminal.commands import (
    handle_command,
    is_direct_shell_command,
    parse_git_clone_command,
    parse_git_clone_request,
    resolve_model_choice,
)
from ai_terminal.tools import build_registry


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()  # macOS: /var is really /private/var
        # Some commands act differently inside an SSH session; tests shouldn't depend on how they're run.
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
            os.environ.pop(name, None)
        (self.root / "ai_terminal").mkdir()
        (self.root / "ai_terminal" / "cli.py").write_text("def main():\n    pass\n", encoding="utf-8")
        (self.root / "tools.md").write_text("tool docs\n", encoding="utf-8")
        (self.root / "start.sh").write_text("#!/usr/bin/env bash\nnpm install\nnpm run dev\n", encoding="utf-8")

        self.registry = build_registry()
        self.state = {
            "active_model": "phi4-mini:latest",
            "orchestrator_model": "phi4-mini:latest",
            "models": ["phi4-mini:latest", "llama3.2:latest"],
            "history": [{"role": "user", "content": "hello"}],
            "cwd": self.root,
            "notes": {},
            "tool_log": [{"tool": "get_cwd", "ok": True}],
        }
        self.helpers = {
            "list_models": lambda: self.state["models"],
            "execute_tool_call": lambda call: {"ok": True, "tool": call["tool"]},
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_command(self, prompt):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = handle_command(prompt, self.state, self.registry, self.helpers)
        return result, buffer.getvalue()

    def test_exit_commands(self):
        self.assertEqual(self.run_command("/bye")[0], "exit")
        self.assertEqual(self.run_command("quit")[0], "exit")

    def test_model_switch_by_number_and_name(self):
        self.assertEqual(resolve_model_choice("2", self.state["models"]), "llama3.2:latest")
        self.assertEqual(resolve_model_choice("/model 2", self.state["models"]), "llama3.2:latest")
        self.assertEqual(resolve_model_choice("model 2", self.state["models"]), "llama3.2:latest")
        self.assertEqual(resolve_model_choice("llama", self.state["models"]), "llama3.2:latest")
        self.assertEqual(resolve_model_choice("/orchestrator 2", self.state["models"]), "llama3.2:latest")

        result, output = self.run_command("/model 2")

        self.assertEqual(result, "handled")
        self.assertEqual(self.state["active_model"], "llama3.2:latest")
        self.assertIn("llama3.2:latest", output)

    def test_orchestrator_switch_by_name(self):
        result, _output = self.run_command("/orchestrator llama")

        self.assertEqual(result, "handled")
        self.assertEqual(self.state["orchestrator_model"], "llama3.2:latest")

    def test_readonly_commands(self):
        for command in [
            "/models",
            "/tools",
            "/tool-log",
            "pwd",
            "where am i",
            "dir",
            "ls",
            "/files",
            "/tree",
            "show the project structure",
            "list_dir(\".\")",
            "just all the files in the current directory",
            "list the files in this directory",
        ]:
            with self.subTest(command=command):
                result, output = self.run_command(command)
                self.assertEqual(result, "handled")
                self.assertTrue(output.strip())

    def test_account_commands_manage_web_accounts(self):
        auth_file = self.root / "web_auth.json"

        with patch("ai_terminal.web_auth.AUTH_FILE", auth_file), patch(
            "ai_terminal.web_auth.AUTH_DIR",
            self.root,
        ), patch("ai_terminal.commands.AUTH_FILE", auth_file, create=True):
            result, output = self.run_command('/account create "dev" "secret"')
            self.assertEqual(result, "handled")
            self.assertIn("Created web account", output)

            result, output = self.run_command("/account list")
            self.assertEqual(result, "handled")
            self.assertIn("dev", output)

            result, output = self.run_command('/account passwd "dev" "new-secret"')
            self.assertEqual(result, "handled")
            self.assertIn("Updated web password", output)

            result, output = self.run_command('/account delete "dev"')
            self.assertEqual(result, "handled")
            self.assertIn("Deleted web account", output)

            result, output = self.run_command("/account list")
            self.assertEqual(result, "handled")
            self.assertIn("No web accounts", output)

    def test_account_without_args_shows_all_account_commands(self):
        result, output = self.run_command("/account")

        self.assertEqual(result, "handled")
        self.assertIn("Web account commands", output)
        self.assertIn("/account list", output)
        self.assertIn('/account create "username" "password"', output)
        self.assertIn('/account delete "username"', output)
        self.assertIn('/account passwd "username" "new-password"', output)

    def test_project_listing_is_handled_without_model(self):
        (self.root / "Sports-Better").mkdir()

        for prompt in ["what projects do i currently have", "show my repos", "which codebases are available"]:
            with self.subTest(prompt=prompt), patch("ai_terminal.commands.PROJECTS_DIR", self.root):
                result, output = self.run_command(prompt)

            self.assertEqual(result, "handled")
            self.assertIn("1.", output)
            self.assertIn("Sports-Better/", output)
            self.assertIn("project(s)", output)
            self.assertIn("Which project would you like to open?", output)
            self.assertEqual(len(self.state["pending_project_choices"]), 2)

    def test_project_listing_choice_opens_project_by_number(self):
        (self.root / "Sports-Better").mkdir()
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()

        with patch("ai_terminal.commands.PROJECTS_DIR", self.root):
            self.run_command("what projects do i currently have")

        with patch("ai_terminal.tools.shutil.which", return_value="code.cmd"), patch(
            "ai_terminal.tools.subprocess.run",
            return_value=completed,
        ) as run:
            result, output = self.run_command("project 2")

        self.assertEqual(result, "handled")
        self.assertIn("Opened", output)
        self.assertEqual(run.call_args.args[0][1], str((self.root / "Sports-Better").resolve()))
        self.assertEqual(self.state["cwd"], (self.root / "Sports-Better").resolve())
        self.assertNotIn("pending_project_choices", self.state)

    def test_project_listing_number_choice_opens_project(self):
        (self.root / "Sports-Better").mkdir()
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()

        with patch("ai_terminal.commands.PROJECTS_DIR", self.root):
            self.run_command("what projects do i currently have")

        with patch("ai_terminal.tools.shutil.which", return_value="code.cmd"), patch(
            "ai_terminal.tools.subprocess.run",
            return_value=completed,
        ) as run:
            result, output = self.run_command("2")

        self.assertEqual(result, "handled")
        self.assertIn("Opened", output)
        self.assertEqual(run.call_args.args[0][1], str((self.root / "Sports-Better").resolve()))
        self.assertEqual(self.state["cwd"], (self.root / "Sports-Better").resolve())

    def test_project_listing_normal_chat_is_not_restricted(self):
        (self.root / "Sports-Better").mkdir()

        with patch("ai_terminal.commands.PROJECTS_DIR", self.root):
            self.run_command("what projects do i currently have")

        result, _output = self.run_command("tell me what python is")

        self.assertIsNone(result)
        self.assertNotIn("pending_project_choices", self.state)

    def test_project_listing_followup_lists_pending_projects_again(self):
        (self.root / "Sports-Better").mkdir()

        with patch("ai_terminal.commands.PROJECTS_DIR", self.root):
            self.run_command("what projects do i currently have")
            result, output = self.run_command("can you list them")

        self.assertEqual(result, "handled")
        self.assertIn("ai_terminal/", output)
        self.assertIn("Sports-Better/", output)
        self.assertIn("Which project would you like to open?", output)
        self.assertIn("pending_project_choices", self.state)

    def test_project_listing_followup_question_summarizes_named_project(self):
        project = self.root / "Sports-Better"
        project.mkdir()
        (project / "README.md").write_text("# SportsBetter\n\nA betting dashboard.\n", encoding="utf-8")
        (project / "start.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

        with patch("ai_terminal.commands.PROJECTS_DIR", self.root):
            self.run_command("what projects do i have")
            result, output = self.run_command("what does the sports better project do?")

        self.assertEqual(result, "handled")
        self.assertIn("Sports-Better/", output)
        self.assertIn("SportsBetter", output)
        self.assertIn("start.sh", output)

    def test_project_listing_followup_start_starts_last_named_project(self):
        project = self.root / "Sports-Better"
        project.mkdir()
        (project / "README.md").write_text("# SportsBetter\n", encoding="utf-8")

        start_result = {
            "project": "Sports-Better",
            "path": str(project),
            "script": "start.ps1",
            "pid": 1234,
            "urls": ["http://127.0.0.1:5173", "http://100.64.0.10:5173"],
            "stdout_log": str(project / "data" / "logs" / "devserver-start.out.log"),
            "stderr_log": str(project / "data" / "logs" / "devserver-start.err.log"),
        }

        with patch("ai_terminal.commands.PROJECTS_DIR", self.root), patch(
            "ai_terminal.commands.start_project",
            return_value=start_result,
        ) as start:
            self.run_command("what projects do i have")
            self.run_command("what is the sports better project about")
            result, output = self.run_command("can you start that")

        self.assertEqual(result, "handled")
        self.assertIn("Started", output)
        self.assertIn("http://127.0.0.1:5173", output)
        self.assertIn("http://100.64.0.10:5173", output)
        self.assertEqual(start.call_args.args[0]["path"], str(project.resolve()))

    def test_start_this_project_prefers_current_directory_over_last_project(self):
        sports = self.root / "Sports-Better"
        internship = self.root / "Internship-Scraper"
        sports.mkdir()
        internship.mkdir()
        self.state["cwd"] = internship
        self.state["last_project_path"] = sports

        start_result = {
            "project": "Internship-Scraper",
            "path": str(internship),
            "script": "docker-compose.yml",
            "pid": 1235,
            "urls": [],
            "stdout_log": str(internship / "data" / "logs" / "devserver-start.out.log"),
            "stderr_log": str(internship / "data" / "logs" / "devserver-start.err.log"),
        }

        with patch("ai_terminal.commands.start_project", return_value=start_result) as start:
            result, output = self.run_command("can you start this project")

        self.assertEqual(result, "handled")
        self.assertIn("Internship-Scraper", output)
        self.assertEqual(start.call_args.args[0]["path"], str(internship.resolve()))

    def test_start_named_project_prefers_explicit_name_over_last_project(self):
        sports = self.root / "Sports-Better"
        internship = self.root / "Internship-Scraper"
        sports.mkdir()
        internship.mkdir()
        self.state["cwd"] = internship
        self.state["last_project_path"] = sports

        start_result = {
            "project": "Internship-Scraper",
            "path": str(internship),
            "script": "docker-compose.yml",
            "pid": 1236,
            "urls": [],
            "stdout_log": str(internship / "data" / "logs" / "devserver-start.out.log"),
            "stderr_log": str(internship / "data" / "logs" / "devserver-start.err.log"),
        }

        with patch("ai_terminal.commands.PROJECTS_DIR", self.root), patch(
            "ai_terminal.tools.PROJECTS_DIR",
            self.root,
        ), patch("ai_terminal.commands.start_project", return_value=start_result) as start:
            result, output = self.run_command("i want to start the internship scraper project")

        self.assertEqual(result, "handled")
        self.assertIn("Internship-Scraper", output)
        self.assertEqual(start.call_args.args[0]["path"], str(internship.resolve()))

    def test_dockerize_request_runs_high_level_tool_directly(self):
        project = self.root / "YoutubeTranscriber"
        project.mkdir()
        dockerize_result = {
            "project": "YoutubeTranscriber",
            "path": str(project),
            "services": [
                {
                    "name": "app",
                    "type": "python",
                    "kind": "fastapi",
                    "host_port": 8001,
                    "container_port": 8001,
                }
            ],
            "files_written": ["Dockerfile", ".dockerignore", "docker-compose.yml"],
            "validation": {"exit_code": 0},
            "build": None,
            "next_commands": ["docker compose up -d --build"],
        }

        with patch("ai_terminal.commands.PROJECTS_DIR", self.root), patch(
            "ai_terminal.tools.PROJECTS_DIR",
            self.root,
        ), patch("ai_terminal.commands.dockerize_project", return_value=dockerize_result) as dockerize:
            result, output = self.run_command("dockerize youtube transcriber")

        self.assertEqual(result, "handled")
        self.assertIn("Docker ready", output)
        self.assertIn("docker compose config passed", output)
        self.assertEqual(dockerize.call_args.args[0]["path"], str(project.resolve()))

    def test_project_listing_followup_name_selects_project_directory(self):
        project = self.root / "Sports-Better"
        project.mkdir()

        with patch("ai_terminal.commands.PROJECTS_DIR", self.root):
            self.run_command("what projects do i have")
            result, output = self.run_command("sports better")

        self.assertEqual(result, "handled")
        self.assertEqual(self.state["cwd"], project.resolve())
        self.assertIn("Directory", output)
        self.assertNotIn("pending_project_choices", self.state)

    def test_start_script_question_is_handled_without_model(self):
        result, output = self.run_command("how can i start this project with start.sh?")

        self.assertEqual(result, "handled")
        self.assertIn("start.sh", output)
        self.assertIn("bash start.sh", output)
        self.assertIn("npm run dev", output)

    def test_complex_file_work_is_left_for_agent_tools(self):
        prompts = [
            "can you review start.sh and end.sh and convert them so they work on windows",
            "read start.sh and end.sh and create new ps1 files that work",
            "can you conver the start.sh and the end.sh files into ps1 files i can run to start this project",
            "check config.py and app.py then fix whatever needs to change",
        ]

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                result, output = self.run_command(prompt)

                self.assertIsNone(result)
                self.assertEqual(output, "")

    def test_doctor_runs_diagnostics_without_model(self):
        with patch("ai_terminal.commands.ollama_is_reachable", return_value=True), patch(
            "ai_terminal.commands.get_models",
            return_value=["phi4-mini:latest"],
        ), patch(
            "ai_terminal.commands.shutil.which",
            return_value="code.cmd",
        ), patch(
            "ai_terminal.commands.list_accounts",
            return_value=["dev"],
        ), patch(
            "ai_terminal.commands.run_task",
            side_effect=[
                {"exit_code": 0, "stdout": "tests ok", "stderr": ""},
                {"exit_code": 0, "stdout": "compose ok", "stderr": ""},
            ],
        ) as run_task:
            result, output = self.run_command("fix devserver")

        self.assertEqual(result, "handled")
        self.assertIn("DevServer diagnostics", output)
        self.assertIn("Tests passed", output)
        self.assertIn("Models", output)
        self.assertIn("Web auth", output)
        self.assertTrue(run_task.called)

    def test_cd_changes_current_directory(self):
        result, output = self.run_command("cd ai_terminal")

        self.assertEqual(result, "handled")
        self.assertEqual(self.state["cwd"], self.root / "ai_terminal")
        self.assertIn("ai_terminal", output)
        self.assertEqual(
            self.state["history"][-1],
            {
                "role": "system",
                "content": f"Current directory changed to: {self.root / 'ai_terminal'}",
            },
        )

    def test_search_and_read_commands(self):
        result, search_output = self.run_command("/search main ai_terminal")
        self.assertEqual(result, "handled")
        self.assertIn("cli.py", search_output)

        result, read_output = self.run_command("/read ai_terminal/cli.py")
        self.assertEqual(result, "handled")
        self.assertIn("Loaded", read_output)
        self.assertIn("File loaded:", self.state["history"][-1]["content"])

        result, natural_read_output = self.run_command("read ai_terminal/cli.py")
        self.assertEqual(result, "handled")
        self.assertIn("def main", natural_read_output)

        result, natural_search_output = self.run_command("search for main")
        self.assertEqual(result, "handled")
        self.assertIn("cli.py", natural_search_output)

    def test_natural_project_open_requests_are_left_for_agent_tools(self):
        for prompt in [
            "open this project",
            "can you open my Sports Better project in visual studio code",
            "access my sports better repo",
        ]:
            with self.subTest(prompt=prompt):
                result, output = self.run_command(prompt)

                self.assertIsNone(result)
                self.assertEqual(output, "")

    def test_missing_args_do_not_crash(self):
        for command in ["/search", "/read"]:
            with self.subTest(command=command):
                result, output = self.run_command(command)
                self.assertEqual(result, "handled")
                self.assertIn("Usage:", output)

    def test_shell_escape_command_uses_helper(self):
        result, output = self.run_command("!git status --short")

        self.assertEqual(result, "handled")
        self.assertIn("run_shell", output)

    def test_common_dev_commands_run_directly(self):
        calls = []

        def execute(call):
            calls.append(call)
            return {
                "ok": True,
                "tool": "run_shell",
                "result": {"stdout": "ok\n", "stderr": "", "exit_code": 0},
            }

        self.helpers["execute_tool_call"] = execute

        for command in [
            "docker compose ps",
            "node --version",
            "npm run dev",
            "python --version",
            "py -m pip --version",
            "git status --short",
            "gh repo list",
            "uv run pytest",
            "kubectl get pods",
            ".\\start.ps1",
        ]:
            with self.subTest(command=command):
                result, output = self.run_command(command)
                self.assertEqual(result, "handled")
                self.assertIn("ok", output)

        self.assertEqual(calls[0]["tool"], "run_shell")
        self.assertEqual(calls[0]["args"]["command"], "docker compose ps")

    def test_direct_shell_detection_avoids_normal_chat(self):
        self.assertTrue(is_direct_shell_command("docker compose up -d"))
        self.assertTrue(is_direct_shell_command("npm install"))
        self.assertTrue(is_direct_shell_command(".\\start.ps1"))
        self.assertFalse(is_direct_shell_command("can you run docker for me"))
        self.assertFalse(is_direct_shell_command("/model 2"))
        self.assertFalse(is_direct_shell_command("!git status"))

    def test_git_clone_command_is_handled_directly(self):
        self.assertEqual(
            parse_git_clone_command("git clone https://github.com/example/repo.git"),
            ("https://github.com/example/repo.git", ""),
        )
        self.assertEqual(
            parse_git_clone_command("git clone https://github.com/example/repo.git is the repo"),
            ("https://github.com/example/repo.git", ""),
        )
        self.assertEqual(
            parse_git_clone_command("git clone https://github.com/example/repo.git custom-name"),
            ("https://github.com/example/repo.git", "custom-name"),
        )
        self.assertEqual(
            parse_git_clone_request(
                "https://github.com/example/repo.git\n\n"
                "can you clone that into my projects folder, make the name the same as the repo name"
            ),
            ("https://github.com/example/repo.git", ""),
        )

        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "done", "stderr": ""},
        )()

        with patch("ai_terminal.commands.PROJECTS_DIR", self.root), patch(
            "builtins.input", return_value="y"
        ), patch(
            "ai_terminal.commands.subprocess.run",
            return_value=completed,
        ) as run:
            result, output = self.run_command(
                "https://github.com/example/repo.git\n\n"
                "can you clone that into my projects folder, make the name the same as the repo name"
            )

        self.assertEqual(result, "handled")
        self.assertIn("Cloned", output)
        self.assertEqual(run.call_args.kwargs["cwd"], self.root)

    def test_unknown_command_is_not_handled(self):
        result, _output = self.run_command("hello")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

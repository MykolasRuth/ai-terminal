import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_terminal.tools import (
    create_file,
    dockerize_project,
    edit_file,
    list_projects,
    open_in_vscode,
    open_project,
    project_snapshot,
    project_status,
    read_many_files,
    replace_in_file,
    run_task,
    run_shell,
    set_cwd,
    start_project,
    windows_launcher_scripts,
    write_many_files,
    git_status,
)


class ToolTests(unittest.TestCase):
    def test_run_shell_accepts_list_command(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "ok", "stderr": ""},
        )()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_terminal.tools.subprocess.run",
            return_value=completed,
        ) as run:
            result = run_shell(
                {"command": ["git", "status"], "timeout_seconds": 30},
                {"cwd": Path(temp_dir)},
            )

        self.assertEqual(result["command"], "git status")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(run.call_args.args[0][-1], "git status")
        self.assertIn(str(Path(sys.executable).resolve().parent), run.call_args.kwargs["env"]["PATH"])

    def test_run_task_lists_available_tasks_without_task_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_task({}, {"cwd": Path(temp_dir)})

        task_names = [item["task"] for item in result["available_tasks"]]
        self.assertIn("python_unittest", task_names)
        self.assertIn("python_compile", task_names)

    def test_run_task_runs_allowlisted_command(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "tests ok", "stderr": ""},
        )()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_terminal.tools.subprocess.run",
            return_value=completed,
        ) as run:
            root = Path(temp_dir)
            (root / "tests").mkdir()
            result = run_task({"task": "python_unittest"}, {"cwd": root})

        self.assertEqual(result["task"], "python_unittest")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(run.call_args.args[0][:3], [sys.executable, "-m", "unittest"])

    def test_run_task_accepts_typed_git_diff_args(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "diff", "stderr": ""},
        )()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_terminal.tools.subprocess.run",
            return_value=completed,
        ) as run:
            result = run_task(
                {"task": "git_diff", "path": "ai_terminal/tools.py", "staged": True, "max_chars": 10},
                {"cwd": Path(temp_dir)},
            )

        self.assertEqual(result["stdout"], "diff")
        self.assertEqual(
            run.call_args.args[0],
            ["git", "-c", "safe.directory=*", "diff", "--staged", "--", "ai_terminal/tools.py"],
        )

    def test_run_task_rejects_unknown_or_invalid_args(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "tests").mkdir()
            state = {"cwd": root}
            with self.assertRaises(ValueError):
                run_task({"task": "npm_dev"}, state)
            with self.assertRaises(ValueError):
                run_task({"task": "python_unittest", "args": ["-k", "one"]}, state)
            with self.assertRaises(ValueError):
                run_task({"task": "python_unittest", "pattern": "../test_*.py"}, state)

    def test_git_status_uses_safe_directory_config(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": " M file.py\n", "stderr": ""},
        )()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_terminal.tools.subprocess.run",
            return_value=completed,
        ) as run:
            result = git_status({}, {"cwd": Path(temp_dir)})

        self.assertEqual(result["stdout"], " M file.py\n")
        self.assertEqual(run.call_args.args[0][:3], ["git", "-c", "safe.directory=*"])

    def test_start_project_requires_docker_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "start.ps1").write_text("$FrontendPort = 5173\n", encoding="utf-8")

            with self.assertRaises(FileNotFoundError) as context:
                start_project({"path": ".", "settle_seconds": 0}, {"cwd": root})

        self.assertIn("No Docker start method", str(context.exception))

    def test_start_project_can_use_docker_compose(self):
        process = type("Process", (), {"pid": 4321})()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_terminal.tools.shutil.which",
            side_effect=lambda name: "docker" if name == "docker" else None,
        ), patch(
            "ai_terminal.tools.subprocess.Popen",
            return_value=process,
        ) as popen, patch(
            "ai_terminal.tools.tailscale_ip",
            return_value="100.64.0.10",
        ), patch(
            "ai_terminal.tools.time.sleep",
        ):
            root = Path(temp_dir)
            (root / "docker-compose.yml").write_text('ports:\n  - "5173:5173"\n', encoding="utf-8")
            result = start_project({"path": ".", "settle_seconds": 0}, {"cwd": root})

        self.assertEqual(result["project"], root.name)
        self.assertEqual(result["script"], "docker-compose.yml")
        self.assertEqual(result["pid"], 4321)
        self.assertIn("http://127.0.0.1:5173", result["urls"])
        self.assertIn("http://100.64.0.10:5173", result["urls"])
        self.assertEqual(popen.call_args.args[0], ["docker", "compose", "up"])

    def test_list_projects_returns_project_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = Path(temp_dir) / "ai-terminal"
            workspace.mkdir()
            (root / "Sports-Better").mkdir()

            with patch("ai_terminal.tools.PROJECTS_DIR", root):
                result = list_projects({}, {"cwd": workspace})

        self.assertEqual(result["path"], str(Path(temp_dir)))
        self.assertIn("Sports-Better", [entry["name"] for entry in result["entries"]])

    def test_open_in_vscode_uses_code_cli(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_terminal.tools.shutil.which",
            return_value="code.cmd",
        ), patch(
            "ai_terminal.tools.is_plain_ssh_session",
            return_value=False,
        ), patch(
            "ai_terminal.tools.subprocess.run",
            return_value=completed,
        ) as run:
            result = open_in_vscode({"path": "."}, {"cwd": Path(temp_dir)})

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(run.call_args.args[0][0], "code.cmd")
        self.assertEqual(run.call_args.args[0][1], str(Path(temp_dir).resolve()))

    def test_open_in_vscode_finds_loose_directory_match(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Sports-Better").mkdir()

            with patch("ai_terminal.tools.shutil.which", return_value="code.cmd"), patch(
                "ai_terminal.tools.is_plain_ssh_session",
                return_value=False,
            ), patch(
                "ai_terminal.tools.subprocess.run",
                return_value=completed,
            ) as run:
                result = open_in_vscode({"path": "SportsBetter"}, {"cwd": root})

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(run.call_args.args[0][1], str((root / "Sports-Better").resolve()))

    def test_open_in_vscode_finds_project_from_natural_phrase(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Internship-Scraper").mkdir()

            with patch("ai_terminal.tools.PROJECTS_DIR", root), patch(
                "ai_terminal.tools.shutil.which",
                return_value="code.cmd",
            ), patch(
                "ai_terminal.tools.is_plain_ssh_session",
                return_value=False,
            ), patch(
                "ai_terminal.tools.subprocess.run",
                return_value=completed,
            ) as run:
                result = open_in_vscode({"path": "my internship scraper project"}, {"cwd": root})

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(run.call_args.args[0][1], str((root / "Internship-Scraper").resolve()))

    def test_open_project_resolves_named_project_before_opening(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "ai-terminal"
            workspace.mkdir()
            (root / "Internship-Scraper").mkdir()

            with patch("ai_terminal.tools.PROJECTS_DIR", root), patch(
                "ai_terminal.tools.shutil.which",
                return_value="code.cmd",
            ), patch(
                "ai_terminal.tools.is_plain_ssh_session",
                return_value=False,
            ), patch(
                "ai_terminal.tools.subprocess.run",
                return_value=completed,
            ) as run:
                result = open_project(
                    {"query": "can you open my internship scraper project"},
                    {"cwd": workspace},
                )

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["selected_project"], "Internship-Scraper")
        self.assertEqual(run.call_args.args[0][1], str((root / "Internship-Scraper").resolve()))

    def test_open_project_current_query_uses_current_directory(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_terminal.tools.shutil.which",
            return_value="code.cmd",
        ), patch(
            "ai_terminal.tools.is_plain_ssh_session",
            return_value=False,
        ), patch(
            "ai_terminal.tools.subprocess.run",
            return_value=completed,
        ) as run:
            cwd = Path(temp_dir)
            result = open_project({"query": "."}, {"cwd": cwd})

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(run.call_args.args[0][1], str(cwd.resolve()))

    def test_open_in_vscode_explains_plain_ssh_limitation(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_terminal.tools.is_plain_ssh_session",
            return_value=True,
        ), patch("ai_terminal.tools.subprocess.run") as run:
            result = open_in_vscode({"path": "."}, {"cwd": Path(temp_dir)})

        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["visible"])
        self.assertIn("plain SSH session", result["stderr"])
        run.assert_not_called()

    def test_cd_finds_loose_directory_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "Sports-Better"
            target.mkdir()
            state = {"cwd": root}

            result = set_cwd({"path": "SportsBetter"}, state)

        self.assertEqual(result["cwd"], str(target.resolve()))

    def test_create_file_refuses_overwrite_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "start.ps1"
            target.write_text("old", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                create_file({"path": "start.ps1", "content": "new"}, {"cwd": root})

            self.assertEqual(target.read_text(encoding="utf-8"), "old")

    def test_create_file_writes_new_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            result = create_file({"path": "scripts/start.ps1", "content": "Write-Host hi"}, {"cwd": root})

            self.assertEqual(Path(result["path"]).read_text(encoding="utf-8"), "Write-Host hi")

    def test_edit_file_can_guard_expected_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "end.ps1"
            target.write_text("old", encoding="utf-8")

            with self.assertRaises(ValueError):
                edit_file(
                    {"path": "end.ps1", "expected_content": "different", "content": "new"},
                    {"cwd": root},
                )

            result = edit_file(
                {"path": "end.ps1", "expected_content": "old", "content": "new"},
                {"cwd": root},
            )

            self.assertTrue(result["edited"])
            self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_replace_in_file_replaces_exact_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "start.ps1"
            target.write_text("python app.py\npython app.py\n", encoding="utf-8")

            result = replace_in_file(
                {"path": "start.ps1", "old_text": "python app.py", "new_text": "py app.py", "count": 1},
                {"cwd": root},
            )

            self.assertEqual(result["replacements"], 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "py app.py\npython app.py\n")

    def test_read_many_files_reports_each_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "start.sh").write_text("start", encoding="utf-8")

            result = read_many_files({"paths": ["start.sh", "missing.sh"]}, {"cwd": root})

        self.assertEqual(result["files"][0]["content"], "start")
        self.assertFalse(result["files"][1]["ok"])

    def test_project_snapshot_reads_key_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("readme", encoding="utf-8")
            (root / "requirements.txt").write_text("flask", encoding="utf-8")

            result = project_snapshot({"path": "."}, {"cwd": root})

        key_paths = [Path(item["path"]).name for item in result["key_files"]]
        self.assertIn("README.md", key_paths)
        self.assertIn("requirements.txt", key_paths)

    def test_project_status_returns_key_files_git_and_tasks(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": " M tools.py\n", "stderr": ""},
        )()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_terminal.tools.subprocess.run",
            return_value=completed,
        ):
            root = Path(temp_dir)
            (root / "tests").mkdir()
            (root / "README.md").write_text("readme", encoding="utf-8")
            (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (root / "package.json").write_text('{"scripts":{"test":"node test.js","dev":"vite --port 5173"}}', encoding="utf-8")

            result = project_status({"path": "."}, {"cwd": root})

        self.assertIn("README.md", [item["name"] for item in result["key_files"]])
        self.assertIn("node", result["project_types"])
        self.assertIn("docker", result["project_types"])
        self.assertIn(5173, result["ports"])
        self.assertIn("npm run dev", [item["command"] for item in result["start_methods"]])
        self.assertEqual(result["git"]["stdout"], " M tools.py\n")
        task_names = [item["task"] for item in result["available_tasks"]]
        self.assertIn("git_status", task_names)
        self.assertIn("python_unittest", task_names)
        self.assertIn("docker_compose_config", task_names)
        self.assertIn("npm_test", task_names)

    def test_write_many_files_prechecks_overwrites(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.txt").write_text("old", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                write_many_files(
                    {
                        "files": [
                            {"path": "a.txt", "content": "new"},
                            {"path": "b.txt", "content": "new"},
                        ]
                    },
                    {"cwd": root},
                )

            self.assertFalse((root / "b.txt").exists())

    def test_windows_launcher_scripts_creates_start_and_end_ps1(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "start.sh").write_text("#!/usr/bin/env bash\npython app.py\n", encoding="utf-8")
            (root / "end.sh").write_text("#!/usr/bin/env bash\npkill -f app.py\n", encoding="utf-8")
            (root / "requirements.txt").write_text("flask\n", encoding="utf-8")
            (root / "app.py").write_text("print('hi')\n", encoding="utf-8")

            result = windows_launcher_scripts({"path": ".", "overwrite": False}, {"cwd": root})

            self.assertTrue((root / "start.ps1").exists())
            self.assertTrue((root / "end.ps1").exists())
            self.assertIn("start.sh", result["source_scripts_found"])
            self.assertIn("requirements.txt", (root / "start.ps1").read_text(encoding="utf-8"))

    def test_dockerize_project_generates_python_compose_files(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "services:\n", "stderr": ""},
        )()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_terminal.tools.shutil.which",
            return_value="docker",
        ), patch(
            "ai_terminal.tools.subprocess.run",
            return_value=completed,
        ) as run:
            root = Path(temp_dir)
            (root / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
            (root / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")

            result = dockerize_project({"path": ".", "validate": True}, {"cwd": root})

            self.assertTrue((root / "Dockerfile").exists())
            self.assertTrue((root / "docker-compose.yml").exists())
            self.assertTrue((root / ".dockerignore").exists())
            self.assertEqual(result["services"][0]["type"], "python")
            self.assertEqual(result["services"][0]["container_port"], 8000)
            self.assertIn("docker-compose.yml", result["files_written"])
            self.assertEqual(run.call_args.args[0], ["docker", "compose", "config"])

    def test_dockerize_project_detects_frontend_backend_monorepo(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ai_terminal.tools.shutil.which",
            return_value=None,
        ):
            root = Path(temp_dir)
            backend = root / "backend"
            frontend = root / "frontend"
            backend.mkdir()
            frontend.mkdir()
            (backend / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
            (backend / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
            (frontend / "package.json").write_text(
                '{"scripts":{"dev":"vite --host 0.0.0.0 --port 5173"}}',
                encoding="utf-8",
            )

            result = dockerize_project({"path": ".", "validate": True}, {"cwd": root})

            services = {service["name"]: service for service in result["services"]}
            self.assertIn("backend", services)
            self.assertIn("frontend", services)
            self.assertTrue((backend / "Dockerfile").exists())
            self.assertTrue((frontend / "Dockerfile").exists())
            compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
            self.assertIn("backend:", compose)
            self.assertIn("frontend:", compose)


if __name__ == "__main__":
    unittest.main()

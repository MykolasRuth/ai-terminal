import unittest
from pathlib import Path

from ai_terminal.agent import (
    asks_about_local_files,
    coerce_project_open_call,
    execute_tool_call,
    named_files_in_prompt,
    is_user_denied_result,
    is_complex_execution_task,
    needs_local_access_retry,
    needs_file_mutation_retry,
    needs_named_file_verification_retry,
    needs_project_open_retry,
    open_vscode_result_message,
    prompt_requires_file_mutation,
    task_execution_instruction,
    tool_progress_label,
)
from ai_terminal.tools import Tool


class AgentTests(unittest.TestCase):
    def test_file_access_refusal_retries_with_tools(self):
        history = [{"role": "user", "content": "list all the files in the current directory"}]
        answer = "I can't actually list all your project files, but you can execute list_dir('.') manually."

        self.assertTrue(needs_local_access_retry(history, answer))

    def test_non_file_refusal_does_not_retry(self):
        history = [{"role": "user", "content": "hello"}]
        answer = "I can't access that."

        self.assertFalse(needs_local_access_retry(history, answer))

    def test_filename_prompt_counts_as_local_file_request(self):
        self.assertTrue(
            asks_about_local_files(
                "review config.yaml and settings.json and convert them"
            )
        )
        self.assertEqual(
            named_files_in_prompt("review config.yaml and settings.json and config.yaml again"),
            ["config.yaml", "settings.json"],
        )

    def test_asking_user_to_provide_script_contents_retries_with_tools(self):
        history = [
            {
                "role": "user",
                "content": "review config.yaml and settings.json and convert them",
            }
        ]
        answer = "Please provide me either the contents from your original files."

        self.assertTrue(needs_local_access_retry(history, answer))

    def test_named_file_missing_claim_retries_without_tool_coverage(self):
        history = [
            {
                "role": "user",
                "content": "read my config.yaml and settings.json files and tell me if they are compatible",
            }
        ]
        answer = "It seems that there are no YAML or JSON files in the current directory."

        self.assertTrue(needs_named_file_verification_retry(history, answer))

    def test_named_file_missing_claim_does_not_retry_after_tool_coverage(self):
        history = [
            {
                "role": "user",
                "content": "read my config.yaml and settings.json files and tell me if they are compatible",
            },
            {
                "role": "user",
                "content": 'Tool result:\n{"tool":"read_file","result":{"path":"config.yaml","content":"x"}}',
            },
            {
                "role": "user",
                "content": 'Tool result:\n{"tool":"read_file","result":{"path":"settings.json","content":"x"}}',
            },
        ]
        answer = "There are no matching files."

        self.assertFalse(needs_named_file_verification_retry(history, answer))

    def test_named_file_content_request_retries_after_listing_only(self):
        history = [
            {
                "role": "user",
                "content": "review config.yaml and settings.json and convert them to toml",
            },
            {
                "role": "user",
                "content": 'Tool result:\n{"tool":"list_dir","result":{"entries":[{"name":"config.yaml"},{"name":"settings.json"}]}}',
            },
        ]
        answer = "Please provide the contents of config.yaml and settings.json."

        self.assertTrue(needs_named_file_verification_retry(history, answer))

    def test_complex_execution_task_gets_instruction(self):
        prompt = "review config.yaml and settings.json and convert them to toml"

        self.assertTrue(is_complex_execution_task(prompt))
        self.assertIn("multi-step execution task", task_execution_instruction(prompt))

    def test_file_mutation_claim_retries_without_write_tool(self):
        history = [
            {
                "role": "user",
                "content": "review config.yaml and settings.json and convert them to toml",
            },
            {
                "role": "user",
                "content": 'Tool result:\n{"tool":"read_file","result":{"path":"config.yaml","content":"x"}}',
            },
            {
                "role": "user",
                "content": 'Tool result:\n{"tool":"read_file","result":{"path":"settings.json","content":"x"}}',
            },
        ]
        answer = "I have reviewed the contents and converted them into a TOML-compatible format."

        self.assertTrue(needs_file_mutation_retry(history, answer))

    def test_file_mutation_recipe_retries_without_write_tool(self):
        history = [
            {
                "role": "user",
                "content": (
                    "review config.yaml and settings.json and convert them to toml. "
                    "make these files on my machine"
                ),
            }
        ]
        answer = """
        Here is the converted file:
        ### config.toml
        ```toml
        name = "demo"
        ```
        Run these commands:
        touch config.toml
        copy converted-output config.toml
        """

        self.assertTrue(needs_file_mutation_retry(history, answer))

    def test_followup_make_them_files_requires_mutation(self):
        self.assertTrue(prompt_requires_file_mutation("can you make them ps1 files"))
        self.assertTrue(prompt_requires_file_mutation("make these config files"))

    def test_file_mutation_retry_stops_after_write_tool(self):
        history = [
            {
                "role": "user",
                "content": "review config.yaml and settings.json and convert them to toml",
            },
            {
                "role": "user",
                "content": 'Tool result:\n{"tool":"create_file","ok":true,"result":{"path":"config.toml"}}',
            },
        ]
        answer = "Created config.toml."

        self.assertFalse(needs_file_mutation_retry(history, answer))

    def test_user_denied_tool_result_is_terminal(self):
        result = {
            "tool": "write_file",
            "ok": False,
            "error": {"type": "UserDenied", "message": "User denied tool execution."},
        }

        self.assertTrue(is_user_denied_result(result))
        self.assertFalse(is_user_denied_result({"ok": False, "error": {"type": "ValueError"}}))

    def test_open_in_vscode_tool_updates_current_directory(self):
        opened_path = Path("C:/Users/devserver/Projects/Internship-Scraper")
        state = {"cwd": Path("C:/Users/devserver/Projects"), "tool_log": []}
        registry = {
            "open_in_vscode": Tool(
                "open_in_vscode",
                "Open a folder in VS Code.",
                lambda args, state: {
                    "path": str(opened_path),
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                },
            )
        }

        result = execute_tool_call(
            {"tool": "open_in_vscode", "args": {"path": str(opened_path)}},
            state,
            registry,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(state["cwd"], opened_path)

    def test_execute_tool_call_reports_progress(self):
        state = {"cwd": Path.home(), "tool_log": []}  # a real folder on any OS (macOS temp dirs sit under /private)
        registry = {
            "read_file": Tool(
                "read_file",
                "Read file.",
                lambda args, state: {"path": args["path"], "content": "ok"},
            )
        }
        updates = []

        result = execute_tool_call(
            {"tool": "read_file", "args": {"path": "README.md"}},
            state,
            registry,
            progress=updates.append,
        )

        self.assertTrue(result["ok"])
        self.assertIn("reading file: README.md", updates)
        self.assertIn("finished read_file", updates)
        self.assertNotIn("_progress", state)

    def test_tool_progress_label_names_dockerize_phase(self):
        self.assertEqual(
            tool_progress_label({"tool": "dockerize_project", "args": {"path": "."}}),
            "preparing Docker project files: .",
        )

    def test_open_vscode_result_message_reports_failure(self):
        result = {
            "tool": "open_in_vscode",
            "ok": True,
            "result": {
                "path": "C:/Users/devserver/Projects/Internship-Scraper",
                "exit_code": 1,
                "stdout": "",
                "stderr": "code command failed",
            },
        }

        message = open_vscode_result_message(result)

        self.assertIn("exited with code 1", message)
        self.assertIn("code command failed", message)

    def test_open_vscode_result_message_reports_plain_ssh_limitation(self):
        result = {
            "tool": "open_in_vscode",
            "ok": True,
            "result": {
                "path": "C:/Users/devserver/Projects/Internship-Scraper",
                "exit_code": 2,
                "stdout": "",
                "stderr": "This is a plain SSH session.",
                "visible": False,
            },
        }

        message = open_vscode_result_message(result)

        self.assertIn("exited with code 2", message)
        self.assertIn("plain SSH session", message)

    def test_project_open_retry_catches_fake_tool_success(self):
        history = [{"role": "user", "content": "can you open my internship scraper projec"}]
        answer = """
        I am opening it now:
        ``json
        {
          "tool": "open_in_vscode",
          "args": {path: ".\\C:\\Users\\devserver\\Projects\\Internship-Scraper"}
        }
        ``
        Done! Your internship scraper project is ready.
        """

        self.assertTrue(needs_project_open_retry(history, answer))

    def test_project_open_retry_accepts_named_project_without_project_word(self):
        history = [{"role": "user", "content": "can you open my internship scraper"}]
        answer = """
        I have found it:
        ``json
        {
          "tool": "open_in_vscode",
          "args": {path: "C:\\Users\\devserver\\Projects\\Internship-Scraper"}
        }
        ``
        Please wait while the system executes this command.
        """

        self.assertTrue(needs_project_open_retry(history, answer))

    def test_project_open_retry_stops_after_open_tool_result(self):
        history = [
            {"role": "user", "content": "can you open my internship scraper projec"},
            {
                "role": "user",
                "content": 'Tool result:\n{"tool": "open_in_vscode", "ok": true}',
            },
        ]
        answer = "Opened C:/Users/devserver/Projects/Internship-Scraper in VS Code."

        self.assertFalse(needs_project_open_retry(history, answer))

    def test_named_project_open_call_is_coerced_to_project_tool(self):
        history = [{"role": "user", "content": "can you open my internship scraper project"}]
        call = {"tool": "open_in_vscode", "args": {"path": "."}}

        coerced = coerce_project_open_call(call, history)

        self.assertEqual(coerced["tool"], "open_project")
        self.assertEqual(coerced["args"]["query"], "can you open my internship scraper project")

    def test_current_project_open_call_uses_current_query(self):
        history = [{"role": "user", "content": "can you open this project"}]
        call = {"tool": "open_in_vscode", "args": {"path": "."}}

        coerced = coerce_project_open_call(call, history)

        self.assertEqual(coerced["tool"], "open_project")
        self.assertEqual(coerced["args"]["query"], ".")


if __name__ == "__main__":
    unittest.main()

import unittest
import io
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from ai_terminal.cli import handle_cli_args, help_text, main, sanitize_user_prompt, select_turn_model, simple_chat_response


class CliTests(unittest.TestCase):
    def test_simple_greetings_are_handled_without_agent_tools(self):
        self.assertEqual(simple_chat_response("hello"), "Hi. What would you like to work on?")
        self.assertEqual(simple_chat_response("Hi!"), "Hi. What would you like to work on?")
        self.assertEqual(simple_chat_response("hi:"), "Hi. What would you like to work on?")
        self.assertEqual(simple_chat_response("thanks"), "You're welcome.")

    def test_real_requests_are_not_short_circuited(self):
        self.assertIsNone(simple_chat_response("read the files in this folder"))
        self.assertIsNone(simple_chat_response("start my sports betting project"))

    def test_answer_decision_keeps_active_model(self):
        decision = {"action": "answer", "model": "phi4-mini:latest"}

        selected_model, is_handoff = select_turn_model(decision, "gemma4:e4b")

        self.assertEqual(selected_model, "gemma4:e4b")
        self.assertFalse(is_handoff)

    def test_handoff_decision_uses_handoff_model_for_turn(self):
        decision = {"action": "handoff", "model": "llama3.1:8b"}

        selected_model, is_handoff = select_turn_model(decision, "gemma4:e4b")

        self.assertEqual(selected_model, "llama3.1:8b")
        self.assertTrue(is_handoff)

    def test_sanitize_user_prompt_removes_pasted_ui_chrome(self):
        prompt = "\n".join(
            [
                "phi default · ~\\Projects\\ai-terminal",
                "> can you clone this",
                "Phi:",
            ]
        )

        self.assertEqual(sanitize_user_prompt(prompt), "can you clone this")

    def test_sanitize_user_prompt_ignores_only_ui_chrome(self):
        prompt = "\n".join(["phi default · ~\\Projects", "Phi:"])

        self.assertEqual(sanitize_user_prompt(prompt), "")

    def test_sanitize_user_prompt_removes_ansi_prompt_codes(self):
        prompt = "phi default - ~\\Projects\\ai-terminal\n^[[32m>^[[0m hi"

        self.assertEqual(sanitize_user_prompt(prompt), "hi")

    def test_help_text_documents_common_commands(self):
        text = help_text()

        self.assertIn("Usage:", text)
        self.assertIn("/doctor", text)
        self.assertIn("--version", text)

    @patch("builtins.print")
    def test_cli_help_is_handled_without_startup(self, print_mock):
        self.assertEqual(handle_cli_args(["--help"]), 0)
        self.assertIn("Usage:", print_mock.call_args.args[0])

    @patch("builtins.print")
    def test_cli_version_is_handled_without_startup(self, print_mock):
        self.assertEqual(handle_cli_args(["--version"]), 0)
        self.assertRegex(print_mock.call_args.args[0], r"\d+\.\d+\.\d+")

    def test_cli_unknown_option_returns_error_code(self):
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            self.assertEqual(handle_cli_args(["--bad-option"]), 2)
        self.assertIn("Unknown option", buffer.getvalue())

    @patch("ai_terminal.cli.list_models", return_value=["phi4-mini:latest"])
    @patch("ai_terminal.cli.get_default_orchestrator", return_value="phi4-mini:latest")
    @patch("ai_terminal.cli.print_startup_banner")
    @patch("ai_terminal.cli.create_prompt_session", return_value=None)
    @patch("ai_terminal.cli.read_user_prompt", side_effect=EOFError)
    def test_main_uses_session_start_dir_for_initial_cwd(
        self,
        _read_prompt,
        _create_prompt_session,
        print_banner,
        _default_orchestrator,
        _list_models,
    ):
        with patch("ai_terminal.cli.SESSION_START_DIR", Path("C:/Projects/Sports-Better")):
            main()

        self.assertEqual(print_banner.call_args.args[1], Path("C:/Projects/Sports-Better"))


if __name__ == "__main__":
    unittest.main()

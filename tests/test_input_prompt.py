import unittest
from unittest.mock import patch

from ai_terminal.input_prompt import create_prompt_session, read_user_prompt


class InputPromptTests(unittest.TestCase):
    def test_read_user_prompt_uses_plain_input_without_session(self):
        with patch("builtins.input", return_value="hello"):
            self.assertEqual(read_user_prompt("phi4-mini:latest", None), "hello")

    def test_create_prompt_session_does_not_crash(self):
        session = create_prompt_session()

        self.assertTrue(session is None or hasattr(session, "prompt"))


if __name__ == "__main__":
    unittest.main()

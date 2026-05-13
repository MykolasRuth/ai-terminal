import unittest

from ai_terminal.model_router import (
    deterministic_model_for_prompt,
    preferred_qwen_model,
    prompt_is_file_task,
)


class ModelRouterTests(unittest.TestCase):
    def test_preferred_qwen_model_uses_coder_when_available(self):
        models = ["phi4-mini:latest", "qwen2.5:7b", "qwen2.5-coder:7b"]

        self.assertEqual(preferred_qwen_model(models), "qwen2.5-coder:7b")

    def test_file_tasks_route_to_qwen(self):
        models = ["phi4-mini:latest", "gemma4:e4b", "qwen2.5-coder:7b"]

        for prompt in [
            "read start.sh and end.sh",
            "can you dockerize this project",
            "review the files in this folder",
            "rewrite app.py",
            "what does this project do",
        ]:
            with self.subTest(prompt=prompt):
                decision = deterministic_model_for_prompt(prompt, models)
                self.assertEqual(decision["action"], "handoff")
                self.assertEqual(decision["model"], "qwen2.5-coder:7b")

    def test_non_file_chat_does_not_route_to_qwen(self):
        self.assertFalse(prompt_is_file_task("hello"))
        self.assertIsNone(deterministic_model_for_prompt("how are you", ["phi4-mini:latest", "qwen2.5-coder:7b"]))

    def test_no_qwen_keeps_normal_routing(self):
        self.assertIsNone(deterministic_model_for_prompt("read app.py", ["phi4-mini:latest", "llama3.1:8b"]))


if __name__ == "__main__":
    unittest.main()

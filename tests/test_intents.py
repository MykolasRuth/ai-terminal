import unittest

from ai_terminal.intents import (
    extract_project_reference,
    is_project_listing_request,
    is_project_open_request,
)


class IntentTests(unittest.TestCase):
    def test_project_listing_accepts_varied_wording(self):
        for prompt in [
            "what projects do i currently have",
            "show my repos",
            "which codebases are available",
            "list apps",
        ]:
            with self.subTest(prompt=prompt):
                self.assertTrue(is_project_listing_request(prompt))

    def test_project_open_accepts_varied_wording(self):
        for prompt in [
            "open sports better project",
            "launch the sports better repo",
            "access Sports-Better in VS Code",
            "edit my sports better codebase",
        ]:
            with self.subTest(prompt=prompt):
                self.assertTrue(is_project_open_request(prompt))

    def test_project_reference_strips_intent_words(self):
        self.assertEqual(extract_project_reference("can you open my Sports Better project"), "sports better")
        self.assertEqual(extract_project_reference("access Sports-Better in VS Code"), "sports-better")

    def test_use_project_question_is_not_open_project_intent(self):
        prompt = "how can i use tailscale and docker to run this project and access it from my laptop"

        self.assertFalse(is_project_open_request(prompt))


if __name__ == "__main__":
    unittest.main()

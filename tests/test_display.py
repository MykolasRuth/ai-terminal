import unittest

from pathlib import Path

from ai_terminal.display import (
    Style,
    compact_path,
    directory_name,
    format_footer,
    format_prompt,
    highlight_text,
    wrap_text,
)


class DisplayTests(unittest.TestCase):
    def test_markdown_bold_is_rendered_without_asterisks(self):
        rendered = highlight_text("**Note:** hello")

        self.assertIn(Style.BOLD, rendered)
        self.assertIn(Style.WHITE, rendered)
        self.assertIn("Note:", rendered)
        self.assertNotIn("**", rendered)

    def test_markdown_bold_handles_function_signature(self):
        rendered = highlight_text("* **search_web(query: str):** Search the web")

        self.assertIn(Style.BOLD, rendered)
        self.assertIn(Style.WHITE, rendered)
        self.assertIn("search_web(query: str):", rendered)
        self.assertNotIn("**", rendered)

    def test_markdown_bold_handles_wrap_text_signature(self):
        rendered = highlight_text("* **wrap_text(text, width):** Wraps long strings")

        self.assertIn(Style.BOLD, rendered)
        self.assertIn(Style.WHITE, rendered)
        self.assertIn("wrap_text(text, width):", rendered)
        self.assertNotIn("**", rendered)

    def test_inline_code_still_highlights_without_backticks(self):
        rendered = highlight_text("Open `ai_terminal/cli.py`")

        self.assertIn("ai_terminal/cli.py", rendered)
        self.assertNotIn("`", rendered)

    def test_directory_color_is_not_blue(self):
        rendered = directory_name("ai_terminal/")

        self.assertIn(Style.YELLOW, rendered)
        self.assertNotIn(Style.BLUE, rendered)

    def test_wrap_text_breaks_on_words(self):
        rendered = wrap_text("alpha beta gamma delta", width=12)

        self.assertEqual(rendered.splitlines(), ["alpha beta", "gamma delta"])

    def test_prompt_is_just_chevron(self):
        rendered = format_prompt("phi4-mini:latest")

        self.assertIn(">", rendered)
        self.assertNotIn("Phi", rendered)

    def test_footer_has_model_and_path(self):
        rendered = format_footer("phi4-mini:latest", Path.cwd())

        self.assertIn("phi", rendered)
        self.assertIn("default", rendered)

    def test_compact_path_uses_home_prefix_when_possible(self):
        rendered = compact_path(Path.home() / "Projects" / "ai-terminal")

        self.assertTrue(rendered.startswith("~\\"))


if __name__ == "__main__":
    unittest.main()

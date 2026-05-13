import unittest
import io
from contextlib import redirect_stdout
from unittest.mock import patch

from ai_terminal.display import WorkingStatus


class WorkingStatusTests(unittest.TestCase):
    def test_update_detail_concatenates_small_chunks(self):
        status = WorkingStatus()

        status.update_detail("checking")
        status.update_detail("files")
        status.update_detail("now")

        self.assertEqual(status.detail, "checking files now")

    def test_update_detail_replaces_prefix_expansion(self):
        status = WorkingStatus()

        status.update_detail("checking")
        status.update_detail("checking files")

        self.assertEqual(status.detail, "checking files")

    def test_update_detail_keeps_full_thinking_text(self):
        status = WorkingStatus()
        long_text = " ".join(f"word{i}" for i in range(120))

        status.update_detail(long_text)

        self.assertEqual(status.detail, long_text)
        self.assertGreater(len(status.wrap_detail(status.detail)), 1)

    def test_web_terminal_emits_status_control_lines(self):
        buffer = io.StringIO()

        with patch.dict("os.environ", {"DEVSERVER_WEB_TERMINAL": "1"}), redirect_stdout(buffer):
            with WorkingStatus() as status:
                status.update_detail("scanning project files")

        output = buffer.getvalue()
        self.assertIn("DEVSERVER_STATUS: working", output)
        self.assertIn("DEVSERVER_STATUS: scanning project files", output)
        self.assertIn("DEVSERVER_STATUS_CLEAR", output)


if __name__ == "__main__":
    unittest.main()

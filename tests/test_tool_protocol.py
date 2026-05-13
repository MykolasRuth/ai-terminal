import json
import unittest

from ai_terminal.tool_protocol import looks_like_tool_attempt, parse_tool_calls


class ToolProtocolTests(unittest.TestCase):
    def test_standard_tool_call(self):
        calls = parse_tool_calls('{"tool":"list_dir","args":{"path":"."}}')
        self.assertEqual(calls, [{"tool": "list_dir", "args": {"path": "."}}])

    def test_array_of_tool_calls(self):
        calls = parse_tool_calls(
            '[{"tool":"get_cwd","args":{}},{"tool":"list_dir","args":{"path":"."}}]'
        )
        self.assertEqual(len(calls), 2)

    def test_legacy_list_shape_is_normalized(self):
        calls = parse_tool_calls('{"tool":["list_dir", {"path":"."}]}')
        self.assertEqual(calls, [{"tool": "list_dir", "args": {"path": "."}}])

    def test_flat_args_are_normalized(self):
        calls = parse_tool_calls('{"tool":"list_dir","path":".","include_hidden":false}')
        self.assertEqual(calls, [{"tool": "list_dir", "args": {"path": ".", "include_hidden": False}}])

    def test_malformed_tool_attempt_is_detected(self):
        self.assertTrue(looks_like_tool_attempt('{"tool": "list_dir", path: "."}'))
        with self.assertRaises(json.JSONDecodeError):
            parse_tool_calls('{"tool": "list_dir", path: "."}')

    def test_pseudo_tool_command_is_detected(self):
        self.assertTrue(looks_like_tool_attempt("```sh\nlist_dir path=\"./\" include_hidden=false\n```"))
        self.assertTrue(looks_like_tool_attempt("read_file path=\"start.sh\""))
        self.assertTrue(looks_like_tool_attempt("run_task task=\"python_unittest\""))
        self.assertTrue(looks_like_tool_attempt("project_status path=\".\""))


if __name__ == "__main__":
    unittest.main()

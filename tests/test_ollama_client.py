import unittest
from unittest.mock import patch

import requests

from ai_terminal.config import (
    OLLAMA_CONNECT_TIMEOUT_SECONDS,
    OLLAMA_ORCHESTRATOR_READ_TIMEOUT_SECONDS,
    OLLAMA_READ_TIMEOUT_SECONDS,
)
from ai_terminal.ollama_client import (
    ask_stream_with_stats,
    chat,
    ask_no_stream_with_stats,
    get_models,
    latest_thinking_text,
    remove_thinking_blocks,
    stream_timed_out,
)


class OllamaClientTests(unittest.TestCase):
    def test_remove_closed_thinking_block(self):
        text = "<think>I should inspect files.</think>\nFinal answer."

        self.assertEqual(remove_thinking_blocks(text), "Final answer.")

    def test_remove_open_thinking_block(self):
        text = "<think>I am still thinking"

        self.assertEqual(remove_thinking_blocks(text), "")

    def test_latest_thinking_text_closed_block(self):
        text = "<think>first</think>answer<think>second</think>"

        self.assertEqual(latest_thinking_text(text), "second")

    def test_latest_thinking_text_open_block(self):
        text = "before <think>looking at directory"

        self.assertEqual(latest_thinking_text(text), "looking at directory")

    def test_stream_timed_out_detects_stall_and_max(self):
        with patch("ai_terminal.ollama_client.time.time", return_value=100):
            with patch("ai_terminal.ollama_client.OLLAMA_STREAM_STALL_TIMEOUT_SECONDS", 10), patch(
                "ai_terminal.ollama_client.OLLAMA_STREAM_MAX_SECONDS",
                200,
            ):
                self.assertEqual(stream_timed_out(0, 80), "stall")

            with patch("ai_terminal.ollama_client.OLLAMA_STREAM_STALL_TIMEOUT_SECONDS", 200), patch(
                "ai_terminal.ollama_client.OLLAMA_STREAM_MAX_SECONDS",
                50,
            ):
                self.assertEqual(stream_timed_out(0, 95), "max")

    @patch("ai_terminal.ollama_client.chat")
    def test_stream_stall_discards_partial_response(self, mock_chat):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return None

            def iter_lines(self, chunk_size=1):
                yield b'{"message":{"content":"partial"}}'
                yield b""
                yield b""

            def close(self):
                self.closed = True

        times = iter([0, 0, 1, 1, 50, 50])
        mock_chat.return_value = FakeResponse()

        with patch("ai_terminal.ollama_client.time.time", side_effect=lambda: next(times)), patch(
            "ai_terminal.ollama_client.OLLAMA_STREAM_STALL_TIMEOUT_SECONDS",
            10,
        ), patch("ai_terminal.ollama_client.OLLAMA_STREAM_MAX_SECONDS", 200):
            text, stats = ask_stream_with_stats("model", [])

        self.assertEqual(text, "")
        self.assertEqual(stats["stop_reason"], "timeout_stall")
        self.assertGreaterEqual(stats["elapsed_seconds"], 0)

    @patch("ai_terminal.ollama_client.requests.get")
    @patch("ai_terminal.ollama_client.ollama_is_reachable", return_value=True)
    def test_get_models_handles_connection_error(self, _reachable, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")

        self.assertEqual(get_models(), [])

    @patch("ai_terminal.ollama_client.requests.get")
    @patch("ai_terminal.ollama_client.ollama_is_reachable", return_value=True)
    def test_get_models_uses_timeout(self, _reachable, mock_get):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {"models": [{"name": "phi4-mini:latest"}]}

        self.assertEqual(get_models(), ["phi4-mini:latest"])
        self.assertIn("timeout", mock_get.call_args.kwargs)

    @patch("ai_terminal.ollama_client.ollama_is_reachable", return_value=False)
    def test_get_models_returns_empty_when_ollama_is_offline(self, _reachable):
        self.assertEqual(get_models(), [])

    @patch("ai_terminal.ollama_client.requests.post")
    def test_chat_uses_long_generation_read_timeout(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None

        chat("gemma4:e4b", [{"role": "user", "content": "hello"}])

        self.assertEqual(
            mock_post.call_args.kwargs["timeout"],
            (OLLAMA_CONNECT_TIMEOUT_SECONDS, OLLAMA_READ_TIMEOUT_SECONDS),
        )

    @patch("ai_terminal.ollama_client.requests.post")
    def test_no_stream_uses_shorter_orchestrator_timeout(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"message": {"content": "{}"}}

        ask_no_stream_with_stats("phi4-mini:latest", [{"role": "user", "content": "route"}])

        self.assertEqual(
            mock_post.call_args.kwargs["timeout"],
            (OLLAMA_CONNECT_TIMEOUT_SECONDS, OLLAMA_ORCHESTRATOR_READ_TIMEOUT_SECONDS),
        )


if __name__ == "__main__":
    unittest.main()

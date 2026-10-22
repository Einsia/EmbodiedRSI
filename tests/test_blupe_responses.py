"""Response framing must not release incomplete robot tool calls."""

import io
import json
import unittest

from integrations.blupe.responses import CompletedBody, completed_response


def stream(*events):
    return io.BytesIO(
        b"".join(
            b"event: "
            + event["type"].encode()
            + b"\r\ndata: "
            + json.dumps(event).encode()
            + b"\r\n\r\n"
            for event in events
        )
    )


class ResponsesTests(unittest.TestCase):
    def completion(self):
        return {
            "type": "response.completed",
            "response": {
                "id": "resp_test",
                "status": "completed",
                "error": None,
                "output": [
                    {
                        "type": "function_call",
                        "name": "move_to",
                        "arguments": '{"targets":{"left_z":0.21}}',
                    }
                ],
                "usage": {"output_tokens": 20},
            },
        }

    def test_only_final_response_exposes_complete_tool_arguments_and_usage(self):
        partial = {"type": "response.function_call_arguments.delta", "delta": '{"targets":'}
        complete = self.completion()
        source = stream(
            {"type": "response.created", "response": {"id": "resp_test"}}, partial, complete
        )
        self.assertEqual(completed_response(source, lambda: False), complete["response"])

    def test_truncation_error_and_incomplete_cannot_become_a_tool_call(self):
        for event in (
            {"type": "response.function_call_arguments.done", "arguments": "{}"},
            {"type": "response.failed"},
            {"type": "response.incomplete"},
            {"type": "error"},
        ):
            with self.subTest(kind=event["type"]), self.assertRaises(ValueError):
                completed_response(stream(event), lambda: False)

    def test_cancellation_size_limit_and_changed_identity_reject(self):
        with self.assertRaises(RuntimeError):
            completed_response(stream(self.completion()), lambda: True)
        with self.assertRaises(ValueError):
            completed_response(stream(self.completion()), lambda: False, max_bytes=12)
        with self.assertRaises(ValueError):
            completed_response(
                stream(
                    {"type": "response.created", "response": {"id": "other"}}, self.completion()
                ),
                lambda: False,
            )

    def test_completion_requires_successful_status(self):
        event = self.completion()
        event["response"]["status"] = "incomplete"
        with self.assertRaises(ValueError):
            completed_response(stream(event), lambda: False)

    def test_body_adapter_retains_context_cleanup_and_partial_reads(self):
        source = stream(self.completion())
        with CompletedBody(source, lambda: False) as body:
            output = body.read1(12) + body.read()
            self.assertEqual(json.loads(output), self.completion()["response"])
            self.assertEqual(body.read(), b"")
        self.assertTrue(source.closed)


if __name__ == "__main__":
    unittest.main()

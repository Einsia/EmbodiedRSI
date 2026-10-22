"""Normalize a Responses SSE completion for the pinned runner's JSON transport."""

import io
import json
import time
from urllib import request

from integrations.blupe.probe import NoRedirect


def completed_response(response, cancelled, *, max_bytes=8 * 1024 * 1024, seconds=45):
    """Never expose a partial tool call; require the final completed response."""
    started = time.monotonic()
    total = 0
    data = []
    response_id = None
    while True:
        if cancelled() or time.monotonic() - started > seconds:
            raise RuntimeError("Responses stream cancelled or exceeded its deadline")
        line = response.readline(max_bytes - total + 1)
        if not line:
            raise ValueError("Responses stream ended without response.completed")
        total += len(line)
        if total > max_bytes:
            raise ValueError("Responses stream exceeds its size limit")
        line = line.rstrip(b"\r\n")
        if line.startswith(b"data:"):
            data.append(line[5:].lstrip(b" "))
        if line or not data:
            continue
        body, data = b"\n".join(data), []
        if body == b"[DONE]":
            raise ValueError("Responses stream has no completed response")
        event = json.loads(body)
        if not isinstance(event, dict):
            raise ValueError("Malformed Responses event")
        kind = event.get("type")
        value = event.get("response")
        if isinstance(value, dict) and value.get("id"):
            if response_id is not None and value["id"] != response_id:
                raise ValueError("Responses stream changed response identity")
            response_id = value["id"]
        if kind in {"error", "response.failed", "response.incomplete"}:
            raise ValueError("Responses stream reported " + kind)
        if kind == "response.completed":
            if (
                not isinstance(value, dict)
                or value.get("status") != "completed"
                or value.get("error") is not None
                or not isinstance(value.get("output"), list)
            ):
                raise ValueError("Responses completion is not successful")
            if cancelled():
                raise RuntimeError("Responses stream cancelled")
            return value


class CompletedBody:
    """Keep the original response/socket available to the runner's cancellation."""

    def __init__(self, response, cancelled):
        self.response, self.cancelled = response, cancelled
        self.body = None

    def __enter__(self):
        self.response.__enter__()
        return self

    def __exit__(self, *args):
        return self.response.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.response, name)

    def read(self, size=-1):
        if self.body is None:
            value = completed_response(self.response, self.cancelled)
            self.body = io.BytesIO(json.dumps(value).encode("utf-8"))
        return self.body.read(size)

    read1 = read


def compatible_opener(cancelled):
    transport = request.build_opener(NoRedirect()).open

    def open_response(req, **kwargs):
        response = transport(req, **kwargs)
        if response.headers.get_content_type() == "text/event-stream":
            return CompletedBody(response, cancelled)
        return response

    return open_response

"""helpers/pi_client.py against a real local HTTP server (not a mock) —
verifies NDJSON framing (including a line split across two TCP chunks),
non-200 handling, and unreachable-sidecar handling.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import config
from helpers.pi_client import stream_chat, PiSidecarError


class _NDJSONHandler(BaseHTTPRequestHandler):
    events = []          # set by the test before starting the server
    status = 200
    split_mid_line = False

    def log_message(self, *a):
        pass  # keep test output quiet

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        self.rfile.read(length)  # drain the request body

        if self.status != 200:
            self.send_response(self.status)
            self.end_headers()
            self.wfile.write(b"upstream error detail")
            return

        self.send_response(200)
        self.send_header("content-type", "application/x-ndjson")
        self.end_headers()
        for event in self.events:
            line = json.dumps(event) + "\n"
            if self.split_mid_line:
                mid = len(line) // 2
                self.wfile.write(line[:mid].encode())
                self.wfile.flush()
                time.sleep(0.01)
                self.wfile.write(line[mid:].encode())
            else:
                self.wfile.write(line.encode())
            self.wfile.flush()


@pytest.fixture
def ndjson_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _NDJSONHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    old_url = config.PI_SIDECAR_URL
    old_secret = config.PI_SIDECAR_SECRET
    config.PI_SIDECAR_URL = f"http://127.0.0.1:{server.server_port}"
    config.PI_SIDECAR_SECRET = "test-secret"
    try:
        yield _NDJSONHandler
    finally:
        server.shutdown()
        config.PI_SIDECAR_URL = old_url
        config.PI_SIDECAR_SECRET = old_secret


def _consume(**kwargs):
    return list(stream_chat(
        scope="global", chat_id="c1", model="qwen3", system_prompt="test",
        message="hi", tool_token="tok", **kwargs,
    ))


class TestStreamChat:
    def test_parses_multiple_ndjson_lines(self, ndjson_server):
        ndjson_server.events = [{"type": "agent_start"}, {"type": "token", "token": "hi"}]
        ndjson_server.status = 200
        ndjson_server.split_mid_line = False
        events = _consume()
        assert events == ndjson_server.events

    def test_parses_a_line_split_across_two_tcp_chunks(self, ndjson_server):
        ndjson_server.events = [{"type": "token", "token": "a fairly long token value to force a split"}]
        ndjson_server.status = 200
        ndjson_server.split_mid_line = True
        events = _consume()
        assert events == ndjson_server.events

    def test_non_200_raises_PiSidecarError(self, ndjson_server):
        ndjson_server.status = 500
        with pytest.raises(PiSidecarError, match="500"):
            _consume()

    def test_unreachable_sidecar_raises_PiSidecarError(self, ndjson_server):
        # Point at a port nothing is listening on.
        config.PI_SIDECAR_URL = "http://127.0.0.1:1"
        with pytest.raises(PiSidecarError):
            _consume()

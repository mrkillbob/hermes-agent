"""Bound framing overhead before Requests yields a body to tools' size caps.

Regression for the public urllib3 advisory GHSA-vxq7-64xx-v4gw.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests


@pytest.mark.parametrize("chunk_size", [b"1", b"0"])
def test_streaming_rejects_oversized_chunk_framing(chunk_size):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if self.path == "/normal":
                body = b"1;ok=yes\r\nx\r\n0\r\n\r\n"
            else:
                # Finite input: exercise the protocol boundary without a DoS.
                body = chunk_size + b";" + b"x" * 70_000 + b"\r\n"
                body += b"x\r\n0\r\n\r\n" if chunk_size == b"1" else b"\r\n"
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Rejection may close the connection before this write.

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url + "/normal", stream=True, timeout=5) as response:
                assert b"".join(response.iter_content(1)) == b"x"
            with session.get(url + "/oversized", stream=True, timeout=5) as response:
                with pytest.raises(requests.exceptions.ChunkedEncodingError):
                    list(response.iter_content(1))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

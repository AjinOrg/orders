import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from wsgiref.util import setup_testing_defaults

import pytest

from orders.app import application


def call(path, method="GET"):
    """Call the WSGI app directly (no server): returns status code, headers, parsed JSON body."""
    environ = {"PATH_INFO": path, "REQUEST_METHOD": method}
    setup_testing_defaults(environ)
    result = {}

    def start_response(status, headers):
        result["status"] = int(status.split()[0])
        result["headers"] = dict(headers)

    body = b"".join(application(environ, start_response))
    return result["status"], result["headers"], json.loads(body)


@pytest.fixture
def fake_my_app():
    """A stand-in for my-app on a random local port; yields its URL."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/broken"):
                data = b"not json"
            else:
                data = json.dumps({"app": "my-app", "version": "abc1234", "environment": "test"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_VERSION", "1234567")
    monkeypatch.delenv("MY_APP_URL", raising=False)


def test_probes_answer_200():
    for probe in ("/healthz", "/readyz"):
        assert call(probe)[0] == 200, probe


def test_info_has_contract_fields():
    status, _, body = call("/dev/orders")
    assert status == 200
    assert body["app"] == "orders"
    assert body["environment"] == "test"
    assert body["version"] == "1234567"
    assert body["path"] == "/dev/orders"


def test_list_orders_behind_the_load_balancer_prefix():
    status, _, body = call("/dev/orders/api/orders")
    assert status == 200
    assert body["count"] == 3
    assert [o["id"] for o in body["orders"]] == ["1001", "1002", "1003"]


def test_one_order():
    status, _, body = call("/dev/orders/api/orders/1002")
    assert status == 200
    assert body["item"] == "monitor"


def test_unknown_order_is_404():
    status, _, body = call("/api/orders/9999")
    assert status == 404
    assert "9999" in body["error"]


def test_only_get_and_head_are_allowed():
    status, headers, _ = call("/", method="POST")
    assert status == 405
    assert headers["Allow"] == "GET, HEAD"


def test_security_headers():
    _, headers, _ = call("/")
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"


def test_my_app_not_configured():
    assert call("/")[2]["dependencies"]["my-app"] == {"configured": False}


def test_my_app_reachable(monkeypatch, fake_my_app):
    monkeypatch.setenv("MY_APP_URL", fake_my_app)
    dep = call("/")[2]["dependencies"]["my-app"]
    assert dep == {"configured": True, "reachable": True, "version": "abc1234", "environment": "test"}


def test_my_app_down_does_not_break_orders(monkeypatch):
    monkeypatch.setenv("MY_APP_URL", "http://127.0.0.1:1")  # nothing listens on port 1
    status, _, body = call("/")
    assert status == 200
    assert body["dependencies"]["my-app"]["reachable"] is False


def test_my_app_bad_answer_is_reported(monkeypatch, fake_my_app):
    monkeypatch.setenv("MY_APP_URL", fake_my_app + "/broken")
    assert call("/")[2]["dependencies"]["my-app"]["error"] == "JSONDecodeError"


def test_only_http_urls_are_called(monkeypatch):
    monkeypatch.setenv("MY_APP_URL", "file:///etc/passwd")
    dep = call("/")[2]["dependencies"]["my-app"]
    assert dep["reachable"] is False
    assert "http://" in dep["error"]

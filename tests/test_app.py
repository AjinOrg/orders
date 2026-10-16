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
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_VERSION", "1234567")
    monkeypatch.delenv("MY_APP_URL", raising=False)
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path / "secrets"))  # empty: no secret mounted unless a test writes one


@pytest.fixture
def database_url(tmp_path):
    """Writes DATABASE_URL like the kubelet mounts it (one file per key, trailing newline)."""

    def write(value):
        secrets = tmp_path / "secrets"
        secrets.mkdir(exist_ok=True)
        (secrets / "DATABASE_URL").write_text(value + "\n", encoding="utf-8")

    return write


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


def test_database_not_mounted():
    db = call("/")[2]["database"]
    assert db == {"configured": False, "error": "DATABASE_URL not found in the mounted secret"}


def test_database_url_password_is_masked(database_url):
    database_url("postgresql://orders_app:S3cr3t!pw@orders-db.dev.internal:5432/ordersdb?schema=orders&sslmode=require")
    status, _, body = call("/dev/orders")
    assert status == 200
    assert body["database"] == {
        "configured": True,
        "valid": True,
        "url": "postgresql://orders_app:########@orders-db.dev.internal:5432/ordersdb?schema=orders&sslmode=require",
        "user": "orders_app",
        "password": "########",
        "host": "orders-db.dev.internal",
        "port": 5432,
        "database": "ordersdb",
        "schema": "orders",
    }


def test_password_never_in_the_answer(database_url):
    database_url("postgres://u:topsecret@db:6543/d?password=alsosecret")
    _, _, body = call("/")
    raw = json.dumps(body)
    assert "topsecret" not in raw
    assert "alsosecret" not in raw
    assert body["database"]["port"] == 6543
    assert body["database"]["schema"] == "public"  # no schema given: PostgreSQL's default


def test_database_url_new_value_without_restart(database_url):
    database_url("postgresql://a:x@db1/one")
    assert call("/")[2]["database"]["database"] == "one"
    database_url("postgresql://a:x@db2/two")  # what the kubelet does after a change in Secrets Manager
    assert call("/")[2]["database"]["host"] == "db2"


@pytest.mark.parametrize(
    "value",
    ["https://user:pw@host/db", "not a url", "postgresql://user:pw@host:notaport/db"],
)
def test_invalid_database_url_is_reported(database_url, value):
    database_url(value)
    db = call("/")[2]["database"]
    assert db["configured"] is True
    assert db["valid"] is False
    assert "pw" not in json.dumps(db)

"""WSGI application for orders: sample orders, and a call to my-app over the cluster network.

Differs from my-app on purpose: Python instead of Node.js, no AWS access, and it depends on another service
(my-app). Its secret (DATABASE_URL) is shown only with the password masked. Configuration and secrets are read on
every request, so tests can change them without reloading the module.
"""

import json
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlsplit

# Demo data: read-only and in memory, so every replica answers the same and the root filesystem can stay read-only
ORDERS = (
    {"id": "1001", "item": "keyboard", "quantity": 1, "status": "shipped"},
    {"id": "1002", "item": "monitor", "quantity": 2, "status": "processing"},
    {"id": "1003", "item": "usb-c cable", "quantity": 5, "status": "delivered"},
)

UPSTREAM_TIMEOUT_SECONDS = 2

STATUS_TEXT = {200: "200 OK", 404: "404 Not Found", 405: "405 Method Not Allowed"}

# Shown instead of a password: proves the secret arrived without ever returning it
MASK = "########"


def read_secret(name):
    """One key of the service's secret, mounted as a file by the chart (External Secrets → Kubernetes Secret).

    Read on every call: when the value changes in Secrets Manager, the mounted file changes and the new value is
    used without restarting the pod. None if the secret is not mounted (yet) or the key is missing.
    """
    secrets_dir = os.environ.get("SECRETS_DIR", "/etc/orders/secrets")
    try:
        with open(os.path.join(secrets_dir, name), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def describe_database(url):
    """The parts of DATABASE_URL (postgresql://user:password@host:5432/db?schema=s), password masked.

    Only describes the value: there is no database in this demo, so nothing connects. Never raises and never
    returns the password, also not one given as a query parameter (?password=...).
    """
    if url is None:
        return {"configured": False, "error": "DATABASE_URL not found in the mounted secret"}
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return {"configured": True, "valid": False, "error": "DATABASE_URL is not a valid URL"}
    if parts.scheme not in ("postgresql", "postgres") or not parts.hostname:
        return {
            "configured": True,
            "valid": False,
            "error": "DATABASE_URL must look like postgresql://user:password@host:5432/database",
        }

    params = parse_qsl(parts.query)
    query = "&".join(f"{k}={MASK if 'password' in k.lower() else v}" for k, v in params)
    user = parts.username or ""
    credentials = f"{user}:{MASK}@" if parts.password is not None else (f"{user}@" if user else "")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    address = f"{host}:{port}" if port else host
    options = dict(params)
    return {
        "configured": True,
        "valid": True,
        "url": f"{parts.scheme}://{credentials}{address}{parts.path}" + (f"?{query}" if query else ""),
        "user": user or None,
        "password": MASK if parts.password is not None else None,
        "host": parts.hostname,
        "port": port or 5432,
        "database": parts.path.lstrip("/") or None,
        "schema": options.get("schema") or options.get("currentSchema") or "public",
    }


def check_my_app(url):
    """Call my-app and report what it answered. Never raises: a broken dependency must not break orders."""
    if not url:
        return {"configured": False}
    if not url.startswith(("http://", "https://")):
        return {"configured": True, "reachable": False, "error": "MY_APP_URL must start with http:// or https://"}
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/", timeout=UPSTREAM_TIMEOUT_SECONDS) as res:  # noqa: S310 (scheme checked above)
            body = json.load(res)
    except (urllib.error.URLError, OSError, ValueError) as err:
        # Blocked by a NetworkPolicy shows up as a timeout; a missing service as a DNS error
        return {"configured": True, "reachable": False, "error": type(err).__name__}
    return {
        "configured": True,
        "reachable": True,
        "version": body.get("version"),
        "environment": body.get("environment"),
    }


def _respond(start_response, status, body, extra_headers=()):
    data = json.dumps(body).encode("utf-8")
    start_response(
        STATUS_TEXT[status],
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(data))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            *extra_headers,
        ],
    )
    return [data]


def _orders(start_response, rest):
    """rest = what follows /api/orders in the path: "" or "/" lists all, "/<id>" returns one."""
    order_id = rest.strip("/")
    if not order_id:
        return _respond(start_response, 200, {"orders": list(ORDERS), "count": len(ORDERS)})
    for order in ORDERS:
        if order["id"] == order_id:
            return _respond(start_response, 200, order)
    return _respond(start_response, 404, {"error": f"order {order_id} not found"})


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    if method not in ("GET", "HEAD"):
        return _respond(start_response, 405, {"error": "method not allowed"}, [("Allow", "GET, HEAD")])

    path = environ.get("PATH_INFO") or "/"

    # Probes: kubelet and the load balancer health check. Deliberately NOT dependent on my-app: if my-app is down,
    # orders stays in the load balancer and reports the problem in its answer instead of disappearing too.
    if path == "/healthz":
        return _respond(start_response, 200, {"status": "ok"})
    if path == "/readyz":
        return _respond(start_response, 200, {"status": "ready"})

    # The load balancer keeps the prefix (/dev/orders/api/orders/1001), so match on what follows /api/orders
    if "/api/orders" in path:
        return _orders(start_response, path.split("/api/orders", 1)[1])

    # Everything else (e.g. /dev/orders): service info - the contract the helm-chart smoke test relies on
    return _respond(
        start_response,
        200,
        {
            "app": "orders",
            "environment": os.environ.get("APP_ENV", "local"),
            "version": os.environ.get("APP_VERSION", "dev"),
            "pod": socket.gethostname(),
            "path": path,
            "orders": len(ORDERS),
            "dependencies": {"my-app": check_my_app(os.environ.get("MY_APP_URL", ""))},
            "database": describe_database(read_secret("DATABASE_URL")),
        },
    )

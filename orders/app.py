"""WSGI application for orders: sample orders, and a call to my-app over the cluster network.

Differs from my-app on purpose: Python instead of Node.js, no secrets and no AWS access, and it depends on another
service (my-app). Configuration is read on every request, so tests can change it without reloading the module.
"""

import json
import os
import socket
import urllib.error
import urllib.request

# Demo data: read-only and in memory, so every replica answers the same and the root filesystem can stay read-only
ORDERS = (
    {"id": "1001", "item": "keyboard", "quantity": 1, "status": "shipped"},
    {"id": "1002", "item": "monitor", "quantity": 2, "status": "processing"},
    {"id": "1003", "item": "usb-c cable", "quantity": 5, "status": "delivered"},
)

UPSTREAM_TIMEOUT_SECONDS = 2

STATUS_TEXT = {200: "200 OK", 404: "404 Not Found", 405: "405 Method Not Allowed"}


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
        },
    )

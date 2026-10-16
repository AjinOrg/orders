"""Starts orders with waitress (a pure-Python production WSGI server) and stops it cleanly on SIGTERM."""

import json
import logging
import os
import signal

from waitress import create_server

from orders.app import application


def log(**fields):
    print(json.dumps(fields), flush=True)


def _stop(signum, _frame):
    raise SystemExit(0)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    port = int(os.environ.get("PORT", "8080"))
    # All interfaces: in a pod the only way in is the pod IP (IPv4 cluster)
    server = create_server(application, host="0.0.0.0", port=port, threads=8, ident="orders")  # noqa: S104

    # Kubernetes sends SIGTERM before stopping a pod (after the chart's 10 s preStop pause, so the load balancer
    # has already stopped sending new requests): finish open requests, then exit
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log(msg="listening", port=port)
    # run() returns when _stop raises SystemExit: waitress catches it and closes the listening socket
    server.run()
    log(msg="shutting down")
    server.task_dispatcher.shutdown(timeout=10)  # let the worker threads finish the requests they already have
    log(msg="stopped")


if __name__ == "__main__":
    main()

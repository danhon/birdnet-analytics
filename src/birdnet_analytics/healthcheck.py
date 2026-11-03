"""Docker healthcheck probe: exit 0 if this container should get traffic.

docker-compose.yml runs it as `python3 -I -S /app/src/birdnet_analytics/healthcheck.py`.
It used to be a `python3 -c` one-liner around urllib.request, whose imports
(http.client, email.parser, ...) cost 0.3-0.5 s of CPU per probe. On
ss-auction, probed every 2 s, that was 17% of a core per idle container. A
raw socket costs a third to a half as much. So this file must import
nothing beyond `os`, `sys` and `socket`, and nothing from the app: -I -S
keeps the venv and this directory off sys.path, and
tests/test_healthcheck.py runs it that way.

Unhealthy when /health doesn't answer 200 within the timeout, or when
/tmp/draining exists. Nothing here creates that file yet: it is the drain
switch of the fleet's rolling-deploy runbook
(~/dev/reverse-proxy/docs-site/docs/services/zero-downtime-deploys.md), kept
so this file stays the same as the other services' copies.
"""
import os
import socket
import sys

PORT = 8787
DRAIN_FILE = "/tmp/draining"
# Under compose's own 3 s timeout, so a slow app fails the probe cleanly.
TIMEOUT = 2.5


def healthy(port: int = PORT, drain_file: str = DRAIN_FILE, timeout: float = TIMEOUT) -> bool:
    if os.path.exists(drain_file):
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.sendall(b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n")
            # The status line is all we need: "HTTP/1.x 200 ...".
            head = s.recv(64)
    except OSError:
        return False
    parts = head.split(None, 2)
    return len(parts) >= 2 and parts[0].startswith(b"HTTP/") and parts[1] == b"200"


def main(argv: list[str]) -> int:
    # Arguments are for the tests; compose passes none.
    port = int(argv[1]) if len(argv) > 1 else PORT
    drain_file = argv[2] if len(argv) > 2 else DRAIN_FILE
    return 0 if healthy(port, drain_file) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

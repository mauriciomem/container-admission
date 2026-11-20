"""HTTPS server: TLS, routing, health checks and request-size limits.

Everything Kubernetes-specific lives in reviews.py; this module only moves bytes.
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import signal
import ssl
import sys
from urllib.parse import urlsplit

from .config import Config
from .reviews import MODES, BadReview, process_review

logger = logging.getLogger("webhook.server")
access_logger = logging.getLogger("webhook.access")

HEALTH_PATHS = {"/healthz", "/health"}
MAX_BODY_BYTES = 1024 * 1024  # 1 MiB; real reviews are a few KiB


class ReviewServer(http.server.ThreadingHTTPServer):
    """Threaded so a slow request (or a probe) doesn't block reviews from the API server."""

    def __init__(self, config: Config, handler_class):
        super().__init__((config.host, config.port), handler_class)
        self.config = config
        self.mode = MODES[config.mode]

    def handle_error(self, request, client_address):
        # Typically a failed TLS handshake (untrusted CA, plain HTTP to the TLS port) or a client
        # that hung up. Log one line instead of a traceback on stderr.
        exc = sys.exc_info()[1]
        logger.warning("connection from %s:%s failed: %s: %s", client_address[0], client_address[1],
                       type(exc).__name__, exc)
        logger.debug("connection error details", exc_info=True)


class ReviewHandler(http.server.BaseHTTPRequestHandler):
    # Socket timeout in seconds, so a stalled client can't hold a worker thread forever.
    timeout = 10
    server: ReviewServer

    def do_GET(self):
        if urlsplit(self.path).path in HEALTH_PATHS:
            self._send(200, b"ok\n", "text/plain")
        else:
            self._send(404, b"not found\n", "text/plain")

    def do_POST(self):
        # Reviews are accepted on any path, so a path typo in the kubeconfig or webhook
        # configuration cannot silently break admission. Use /imagepolicy or /validate by convention.
        path = urlsplit(self.path).path
        body = self._read_body(path)
        if body is None:
            return  # an error response has already been sent

        try:
            payload = json.loads(body)
        except (ValueError, RecursionError) as exc:  # bad JSON/UTF-8, or absurdly deep nesting
            self._reject(400, path, f"body is not valid JSON: {exc}")
            return

        try:
            response = process_review(self.server.mode, self.server.config.policy, payload)
        except BadReview as exc:  # includes "wrong review kind for this mode"
            self._reject(400, path, str(exc))
            return
        except Exception:
            logger.exception("internal error while handling POST %s from %s", path, self.client_address[0])
            self._send(500, b"internal error\n", "text/plain")
            return

        self._send(200, json.dumps(response).encode("utf-8"), "application/json")

    def _read_body(self, path: str):
        """Return the request body, or None after sending an error response."""
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.strip().isdigit():
            self._reject(400, path, f"missing or invalid Content-Length header ({raw_length!r})")
            return None
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            self._reject(413, path, f"request body of {length} bytes exceeds the {MAX_BODY_BYTES} byte limit")
            return None
        try:
            body = self.rfile.read(length)
        except OSError as exc:  # includes socket timeouts and TLS errors
            logger.warning("could not read request body from %s: %s: %s", self.client_address[0],
                           type(exc).__name__, exc)
            self.close_connection = True
            return None
        if len(body) < length:
            self._reject(400, path, f"request body truncated: got {len(body)} of {length} bytes")
            return None
        return body

    def _reject(self, status: int, path: str, message: str):
        logger.error("rejecting POST %s from %s with HTTP %d: %s", path, self.client_address[0], status, message)
        self.close_connection = True  # the body may be unread, so don't reuse the connection
        self._send(status, (message + "\n").encode("utf-8"), "text/plain")

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Access log, DEBUG only. Probes arrive every few seconds; keep them out entirely.
        if urlsplit(getattr(self, "path", "")).path not in HEALTH_PATHS:
            access_logger.debug("%s %s", self.client_address[0], format % args)

    def log_error(self, format, *args):
        # Protocol-level problems (bad request line, request timeout) are worth seeing.
        logger.warning("%s %s", self.client_address[0], format % args)


def build_ssl_context(config: Config) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=config.cert_file, keyfile=config.key_file)
    return ctx


def _explain_tls_files_error(config: Config, exc: Exception) -> str:
    """Turn a certificate loading failure into an actionable message."""
    for label, path in (("certificate", config.cert_file), ("private key", config.key_file)):
        if not os.path.isfile(path):
            return f"TLS {label} not found: {path} (mount it, or pass --cert-file/--key-file)"
        if not os.access(path, os.R_OK):
            return (f"TLS {label} {path} is not readable by uid {os.getuid()}. A root-owned 0600 key "
                    f"needs 'chown {os.getuid()} <key>' (see scripts/gen-certs.sh)")
    return f"cannot load TLS certificate/key ({config.cert_file}, {config.key_file}): {exc}"


def run(config: Config) -> int:
    """Start the server and block until SIGTERM/SIGINT. Returns the process exit code."""
    try:
        ssl_context = build_ssl_context(config)
    except (OSError, ssl.SSLError) as exc:
        logger.error(_explain_tls_files_error(config, exc))
        return 1

    try:
        server = ReviewServer(config, ReviewHandler)
    except OSError as exc:
        logger.error("cannot listen on %s:%d: %s", config.host, config.port, exc)
        return 1

    # do_handshake_on_connect=False moves the TLS handshake out of the accept loop and into the
    # worker thread, where the socket timeout applies.
    server.socket = ssl_context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)

    # As PID 1 in a container, SIGTERM would be ignored without a handler (slow 'docker stop').
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))

    logger.info("starting mode=%s listen=%s:%d cert=%s key=%s log_level=%s", config.mode, config.host,
                config.port, config.cert_file, config.key_file, config.log_level)
    logger.info("policy %s", config.policy.describe())
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("shutting down")
    finally:
        server.server_close()
    return 0

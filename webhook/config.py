"""Command-line configuration. Flags are the only way to configure the webhook."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional, Sequence

from .policy import Policy

MODES = ("imagepolicy", "validating")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

DESCRIPTION = """\
Allow or deny Pods based on the registry and tag of their container images.
Runs as an HTTPS backend in one of two modes:

  imagepolicy  kube-apiserver ImagePolicyWebhook admission plugin (ImageReview)
  validating   ValidatingWebhookConfiguration (AdmissionReview, Pods only)
"""

EPILOG = """\
Registries are matched after normalization: lowercase, and Docker Hub aliases
(index.docker.io, registry-1.docker.io, registry.hub.docker.com) become docker.io.
An image without a registry prefix (e.g. "nginx") lives on docker.io.

Examples:
  python3 -m webhook --mode imagepolicy --disallowed-registries docker.io
  python3 -m webhook --mode validating --allowed-registries registry.local:5000 \\
      --disallowed-tags latest,dev
"""


@dataclass(frozen=True)
class Config:
    mode: str
    host: str
    port: int
    cert_file: str
    key_file: str
    policy: Policy
    log_level: str


def _csv(value: str) -> list[str]:
    """Split "a, b,,c" into ["a", "b", "c"]."""
    return [item.strip() for item in value.split(",") if item.strip()]


def _registry_list(value: str) -> list[str]:
    registries = _csv(value)
    for registry in registries:
        if "/" in registry or any(ch.isspace() for ch in registry):
            raise argparse.ArgumentTypeError(
                f"invalid registry {registry!r}: use host[:port] only, e.g. 'docker.io' or 'registry.local:5000'")
    return registries


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"invalid port {value!r}: expected 1-65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m webhook",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mode", choices=MODES, default="imagepolicy",
                        help="Which kind of review to serve (default: %(default)s).")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Address to listen on (default: %(default)s).")
    parser.add_argument("--port", type=_port, default=8443,
                        help="HTTPS port to listen on (default: %(default)s).")
    parser.add_argument("--cert-file", default="/certs/tls.crt",
                        help="TLS server certificate, PEM (default: %(default)s).")
    parser.add_argument("--key-file", default="/certs/tls.key",
                        help="TLS private key, PEM (default: %(default)s).")
    parser.add_argument("--allowed-registries", type=_registry_list, default=[], metavar="LIST",
                        help="Comma-separated registries. If set, images from any other registry "
                             "are rejected. Empty (default) allows every registry.")
    parser.add_argument("--disallowed-registries", type=_registry_list, default=[], metavar="LIST",
                        help="Comma-separated registries that are always rejected. "
                             "Takes precedence over --allowed-registries.")
    parser.add_argument("--disallowed-tags", type=_csv, default=["latest"], metavar="LIST",
                        help="Comma-separated tags that are rejected (default: latest). "
                             "Pass an empty string ('') to disable the tag check. "
                             "Images pinned by digest only (no tag) are never tag-rejected.")
    parser.add_argument("--log-level", type=str.upper, choices=LOG_LEVELS, default="INFO",
                        help="Log verbosity (default: %(default)s). DEBUG adds per-image parse "
                             "results and the HTTP access log.")
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> Config:
    args = build_parser().parse_args(argv)
    return Config(
        mode=args.mode,
        host=args.host,
        port=args.port,
        cert_file=args.cert_file,
        key_file=args.key_file,
        policy=Policy.from_lists(args.allowed_registries, args.disallowed_registries, args.disallowed_tags),
        log_level=args.log_level,
    )

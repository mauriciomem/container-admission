"""Entrypoint: python3 -m webhook --mode imagepolicy|validating [flags]"""
import sys

from .config import parse_args
from .log import setup_logging
from .server import run


def main() -> int:
    config = parse_args()
    setup_logging(config.log_level)
    return run(config)


if __name__ == "__main__":
    sys.exit(main())

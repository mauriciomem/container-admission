"""Logging setup and the one-line-per-review log helper."""
from __future__ import annotations

import logging
import sys
from typing import Mapping, Sequence

from .policy import Decision

logger = logging.getLogger("webhook.review")


def setup_logging(level: str) -> None:
    """Plain-text logs on stdout: '<timestamp> <LEVEL> <message>'."""
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        force=True,
    )


def _value(value: object) -> str:
    """Render a log value; quote it when it is empty or contains whitespace."""
    text = str(value)
    if not text or any(ch.isspace() for ch in text):
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return text


def log_review(mode: str, fields: Mapping[str, object], images: Sequence[str], decision: Decision) -> None:
    """Log one concise INFO line per review: essentials only, never the payload."""
    parts = [f"mode={mode}"]
    parts += [f"{key}={_value(value)}" for key, value in fields.items()]
    parts.append("images=[" + ",".join(images) + "]")
    parts.append("decision=" + ("allowed" if decision.allowed else "denied"))
    parts.append(f"reasons={_value(decision.message)}")
    logger.info("review " + " ".join(parts))

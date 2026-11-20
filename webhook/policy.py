"""Image policy engine: parse an image reference and check it against the policy.

This module knows nothing about Kubernetes review objects or HTTP. The mode adapters in
reviews.py extract image strings from a request and hand them to evaluate_images().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger("webhook.policy")

DOCKER_HUB = "docker.io"
# Other hostnames that also point at Docker Hub. They are mapped to docker.io so that
# "index.docker.io/library/nginx" cannot slip past a rule on docker.io. The same mapping
# is applied to registries given on the command line.
DOCKER_HUB_ALIASES = frozenset({"index.docker.io", "registry-1.docker.io", "registry.hub.docker.com"})

ALLOWED_MESSAGE = "All container images passed policy checks"


def normalize_registry(registry: str) -> str:
    """Lowercase a registry host[:port] and map Docker Hub aliases to docker.io."""
    registry = registry.strip().lower()
    return DOCKER_HUB if registry in DOCKER_HUB_ALIASES else registry


def parse_image(image: str) -> tuple[str, Optional[str]]:
    """Split an image reference into (registry, tag).

    Examples:
      nginx                                  -> ("docker.io", "latest")
      registry.local:5000/team/app:1.2       -> ("registry.local:5000", "1.2")
      nginx@sha256:abc...                    -> ("docker.io", None)      # pinned by digest, no tag
      nginx:latest@sha256:abc...             -> ("docker.io", "latest")  # the explicit tag is kept
    """
    name, _, digest = image.partition("@")
    first, has_slash, rest = name.partition("/")

    # The first path segment is a registry only if it looks like a hostname
    # (contains '.' or ':' or is 'localhost'). Otherwise the image lives on Docker Hub.
    if has_slash and ("." in first or ":" in first or first == "localhost"):
        registry, path = normalize_registry(first), rest
    else:
        registry, path = DOCKER_HUB, name

    # Once the registry (which may contain ':port') is removed, any ':' marks the tag.
    if ":" in path:
        tag = path.rsplit(":", 1)[1]
    elif digest:
        tag = None  # e.g. nginx@sha256:... is pinned to an exact image, not to :latest
    else:
        tag = "latest"  # what the container runtime pulls when no tag is given

    return registry, tag


@dataclass(frozen=True)
class Policy:
    """What is allowed. Registries are stored normalized (see normalize_registry)."""

    allowed_registries: tuple[str, ...] = ()     # empty = any registry is allowed
    disallowed_registries: tuple[str, ...] = ()
    disallowed_tags: tuple[str, ...] = ("latest",)

    @classmethod
    def from_lists(cls, allowed_registries: Iterable[str] = (),
                   disallowed_registries: Iterable[str] = (),
                   disallowed_tags: Iterable[str] = ("latest",)) -> "Policy":
        """Build a Policy from raw user input, normalizing registries."""
        return cls(
            allowed_registries=_unique(normalize_registry(r) for r in allowed_registries),
            disallowed_registries=_unique(normalize_registry(r) for r in disallowed_registries),
            disallowed_tags=_unique(t.strip() for t in disallowed_tags),
        )

    def describe(self) -> str:
        return (f"allowed_registries={_show(self.allowed_registries, 'any')} "
                f"disallowed_registries={_show(self.disallowed_registries, 'none')} "
                f"disallowed_tags={_show(self.disallowed_tags, 'none')}")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    message: str  # human-readable; sent back to the user when denied


def evaluate_image(policy: Policy, image: str) -> list[str]:
    """Return every policy violation for one image (an empty list means the image is fine).

    At most one registry violation is reported: a disallowed registry takes precedence
    over "not in the allowed list". A tag violation is reported in addition to it.
    """
    registry, tag = parse_image(image)
    logger.debug("parsed image=%s registry=%s tag=%s", image, registry, tag)

    violations = []
    if registry in policy.disallowed_registries:
        violations.append(f"registry '{registry}' is in --disallowed-registries "
                          f"({', '.join(policy.disallowed_registries)})")
    elif policy.allowed_registries and registry not in policy.allowed_registries:
        violations.append(f"registry '{registry}' is not in --allowed-registries "
                          f"({', '.join(policy.allowed_registries)})")

    if tag is not None and tag in policy.disallowed_tags:
        violations.append(f"tag '{tag}' is in --disallowed-tags ({', '.join(policy.disallowed_tags)})")

    return violations


def evaluate_images(policy: Policy, images: Iterable[str]) -> Decision:
    """Check all images; deny if any image violates the policy.

    Each rejected image yields "Image '<ref>' rejected: <violation>; <violation>".
    Messages for different images are joined with " | ". Repeated images are reported once.
    """
    rejections = []
    for image in _unique(images):
        violations = evaluate_image(policy, image)
        if violations:
            rejections.append(f"Image '{image}' rejected: {'; '.join(violations)}")

    if rejections:
        return Decision(False, " | ".join(rejections))
    return Decision(True, ALLOWED_MESSAGE)


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    """Drop duplicates and empty strings, keeping the first-seen order."""
    return tuple(dict.fromkeys(item for item in items if item))


def _show(values: tuple[str, ...], when_empty: str) -> str:
    return ",".join(values) if values else f"<{when_empty}>"

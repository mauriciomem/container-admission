"""Mode adapters: translate a Kubernetes review request into images, and a decision into a response.

Each mode is a thin adapter around the shared policy engine in policy.py:

    parse(payload)             -> ParsedReview (images to check + context for logging/response)
    respond(parsed, decision)  -> response body (dict)

Anything the client sent that we cannot understand raises BadReview (HTTP 400).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .log import log_review
from .policy import Decision, Policy, evaluate_images

logger = logging.getLogger("webhook.reviews")

IMAGEPOLICY_API_VERSION = "imagepolicy.k8s.io/v1alpha1"
ADMISSION_API_VERSION = "admission.k8s.io/v1"


class BadReview(ValueError):
    """The request body is not a usable review. Becomes HTTP 400 with this message."""


@dataclass(frozen=True)
class ParsedReview:
    images: tuple[str, ...]
    # Values for the log line and the response, in log order. Mode specific.
    context: dict[str, Any] = field(default_factory=dict)
    # If set, the review is allowed without checking images; the text says why.
    skip_reason: str = ""


# --- small helpers for walking untrusted JSON -------------------------------------------------

def _dig(data: Any, *keys: str) -> Any:
    """data[k1][k2]... or None if any step is missing or not an object. Used for log fields."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _text(data: Any, *keys: str) -> str:
    value = _dig(data, *keys)
    return value if isinstance(value, str) else ""


def _require_object(parent: dict, key: str, path: str) -> dict:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise BadReview(f"{path} must be a JSON object, got {_describe(value)}")
    return value


def _collect_images(container_list: Any, path: str, required: bool) -> list[str]:
    """Return the image of every entry in a containers-style list, validating its shape."""
    if container_list is None and not required:
        return []
    if not isinstance(container_list, list):
        raise BadReview(f"{path} must be a list, got {_describe(container_list)}")
    images = []
    for index, container in enumerate(container_list):
        if not isinstance(container, dict):
            raise BadReview(f"{path}[{index}] must be a JSON object, got {_describe(container)}")
        image = container.get("image")
        if not isinstance(image, str) or not image.strip():
            raise BadReview(f"{path}[{index}].image is missing or not a non-empty string")
        images.append(image.strip())
    return images


def _describe(value: Any) -> str:
    return "null" if value is None else type(value).__name__


def _check_kind(payload: Any, mode: str, expected_kind: str) -> dict:
    """Make sure the body is a JSON object of the kind this mode serves."""
    if not isinstance(payload, dict):
        raise BadReview(f"request body must be a JSON object, got {_describe(payload)}")
    kind = payload.get("kind")
    # A missing kind is tolerated; the structure checks below still apply.
    if isinstance(kind, str) and kind and kind != expected_kind:
        raise BadReview(
            f"wrong review type for --mode {mode}: this instance serves kind={expected_kind} but "
            f"received kind={kind} apiVersion={payload.get('apiVersion')}. "
            f"Is the API server (or ValidatingWebhookConfiguration) pointing at the wrong "
            f"webhook, or is the webhook started with the wrong --mode?")
    return payload


# --- imagepolicy mode ---------------------------------------------------------------------------

class ImagePolicyMode:
    """kube-apiserver ImagePolicyWebhook: ImageReview in, ImageReview with status out.

    An ImageReview carries only spec.namespace, spec.annotations and spec.containers
    (images). It has no pod name and no user, so the log line cannot show them.
    """

    name = "imagepolicy"
    expected_kind = "ImageReview"

    def parse(self, payload: Any) -> ParsedReview:
        review = _check_kind(payload, self.name, self.expected_kind)
        spec = _require_object(review, "spec", "spec")
        images = _collect_images(spec.get("containers"), "spec.containers", required=True)
        api_version = review.get("apiVersion")
        context = {
            "apiVersion": api_version if isinstance(api_version, str) and api_version else IMAGEPOLICY_API_VERSION,
            "namespace": _text(spec, "namespace"),
        }
        return ParsedReview(tuple(images), context)

    def respond(self, parsed: ParsedReview, decision: Decision) -> dict:
        return {
            "apiVersion": parsed.context["apiVersion"],  # echo what the API server sent
            "kind": "ImageReview",
            "status": {"allowed": decision.allowed, "reason": decision.message},
        }


# --- validating mode ----------------------------------------------------------------------------

class ValidatingMode:
    """ValidatingWebhookConfiguration: AdmissionReview v1 in, AdmissionReview v1 out. Pods only."""

    name = "validating"
    expected_kind = "AdmissionReview"
    checked_operations = ("CREATE", "UPDATE")
    # Pod spec lists that hold images. ephemeralContainers arrive via pods/ephemeralcontainers.
    container_lists = ("containers", "initContainers", "ephemeralContainers")

    def parse(self, payload: Any) -> ParsedReview:
        review = _check_kind(payload, self.name, self.expected_kind)
        if review.get("apiVersion") != ADMISSION_API_VERSION:
            raise BadReview(f"unsupported apiVersion {review.get('apiVersion')!r}: only "
                            f"{ADMISSION_API_VERSION} is supported "
                            f"(set admissionReviewVersions: [\"v1\"] in the webhook configuration)")
        request = _require_object(review, "request", "request")
        uid = request.get("uid")
        if not isinstance(uid, str) or not uid:
            raise BadReview("request.uid is missing; it must be echoed back in the response")

        context = self._log_context(request, uid)

        kind = request.get("kind")
        if not isinstance(kind, dict) or kind.get("kind") != "Pod" or kind.get("group", "") != "":
            logger.warning("uid=%s received kind=%s but only Pods are checked; allowing. Fix the "
                           "webhook 'rules' so only pods and pods/ephemeralcontainers are sent",
                           uid, context["kind"])
            return ParsedReview((), context, skip_reason=f"not a Pod (kind={context['kind']}), nothing to check")

        operation = request.get("operation")
        pod = request.get("object")
        if operation not in self.checked_operations or pod is None:
            return ParsedReview((), context, skip_reason=f"no Pod object to check (operation={operation})")

        pod = _require_object(request, "object", "request.object")
        spec = _require_object(pod, "spec", "request.object.spec")
        images: list[str] = []
        for list_name in self.container_lists:
            images += _collect_images(spec.get(list_name), f"request.object.spec.{list_name}",
                                      required=(list_name == "containers"))
        return ParsedReview(tuple(images), context)

    def respond(self, parsed: ParsedReview, decision: Decision) -> dict:
        response: dict[str, Any] = {"uid": parsed.context["uid"], "allowed": decision.allowed}
        if not decision.allowed:
            response["status"] = {"code": 403, "message": decision.message}
        return {"apiVersion": ADMISSION_API_VERSION, "kind": "AdmissionReview", "response": response}

    @staticmethod
    def _log_context(request: dict, uid: str) -> dict[str, Any]:
        """Essentials for the log line. Lenient on purpose: odd values here must not cause a 400."""
        group, version, kind = (_text(request, "kind", k) for k in ("group", "version", "kind"))
        resource = _text(request, "resource", "resource")
        if _text(request, "subResource"):
            resource += "/" + _text(request, "subResource")

        # On CREATE with metadata.generateName the name is still empty; show "<generateName>*".
        name = _text(request, "name") or _text(request, "object", "metadata", "name")
        if not name and _text(request, "object", "metadata", "generateName"):
            name = _text(request, "object", "metadata", "generateName") + "*"

        return {
            "uid": uid,
            "operation": _text(request, "operation"),
            "kind": "/".join(part for part in (group, version, kind) if part),
            "resource": resource,
            "object": f"{_text(request, 'namespace')}/{name}",
            "user": _text(request, "userInfo", "username"),
            "dryRun": "true" if _dig(request, "dryRun") is True else "false",
        }


MODES = {mode.name: mode for mode in (ImagePolicyMode(), ValidatingMode())}


def process_review(mode: Any, policy: Policy, payload: Any) -> dict:
    """Run one review end to end and return the response body. Raises BadReview on bad input."""
    parsed = mode.parse(payload)
    if parsed.skip_reason:
        decision = Decision(True, parsed.skip_reason)
    else:
        decision = evaluate_images(policy, parsed.images)
    log_review(mode.name, parsed.context, parsed.images, decision)
    return mode.respond(parsed, decision)

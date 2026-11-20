# Logging

The webhook logs plain text to stdout in the form `<timestamp> <LEVEL> <message>`. Each review produces one
`INFO` line with the essentials and never the full payload. Health probes are not logged.

At startup it logs the mode, listen address, certificate paths and log level, then the policy:

```text
2026-11-19T22:34:14+0100 INFO    starting mode=validating listen=0.0.0.0:18444 cert=/certs/tls.crt key=/certs/tls.key log_level=INFO
2026-11-19T22:34:14+0100 INFO    policy allowed_registries=registry.local:5000,docker.io,gcr.io disallowed_registries=docker.io disallowed_tags=latest
```

## Validating mode

A `validating` review line shows who asked and for which object: `uid`, `operation`, `kind` as
group/version/kind, `resource` with the subresource if there is one, `object` as namespace/name, `user` and
`dryRun`. A non-Pod object is allowed with a `WARNING`.

```text
2026-11-19T22:34:41+0100 INFO    review mode=validating uid=705ab4f5-6393-11e8-b7cc-42010a800002 operation=CREATE kind=v1/Pod resource=pods object=default/web-5d8f-* user=system:serviceaccount:kube-system:replicaset-controller dryRun=false images=[nginx:latest,busybox:1.36] decision=denied reasons="Image 'nginx:latest' rejected: registry 'docker.io' is in --disallowed-registries (docker.io); tag 'latest' is in --disallowed-tags (latest) | Image 'busybox:1.36' rejected: registry 'docker.io' is in --disallowed-registries (docker.io)"
2026-11-19T22:34:41+0100 INFO    review mode=validating uid=ok-1 operation=CREATE kind=v1/Pod resource=pods object=default/ok user=bob dryRun=false images=[gcr.io/x/y:2.0,gcr.io/x/z@sha256:abc] decision=allowed reasons="All container images passed policy checks"
2026-11-19T22:34:41+0100 WARNING uid=dep-1 received kind=apps/v1/Deployment but only Pods are checked; allowing. Fix the webhook 'rules' so only pods and pods/ephemeralcontainers are sent
```

`object=default/web-5d8f-*` means the Pod has no name yet. The webhook shows its `generateName` prefix followed by
`*`.

## Imagepolicy mode

An `ImageReview` contains only the namespace, annotations and container images. It has no Pod name and no user,
so an `imagepolicy` review line shows only `apiVersion` and `namespace`:

```text
2026-11-19T22:34:25+0100 INFO    review mode=imagepolicy apiVersion=imagepolicy.k8s.io/v1alpha1 namespace=default images=[nginx:latest] decision=denied reasons="Image 'nginx:latest' rejected: registry 'docker.io' is in --disallowed-registries (docker.io); tag 'latest' is in --disallowed-tags (latest)"
```

## Wrong mode

A review of the wrong kind for the running mode is rejected with `400` and an `ERROR` line:

```text
2026-11-19T22:34:26+0100 ERROR   rejecting POST /imagepolicy from 127.0.0.1 with HTTP 400: wrong review type for --mode imagepolicy: this instance serves kind=ImageReview but received kind=AdmissionReview apiVersion=admission.k8s.io/v1. Is the API server (or ValidatingWebhookConfiguration) pointing at the wrong webhook, or is the webhook started with the wrong --mode?
```

## Debug level

`--log-level DEBUG` adds a line per image, such as `parsed image=nginx:latest registry=docker.io tag=latest`, and
an HTTP access log line per request.

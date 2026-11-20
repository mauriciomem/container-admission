# Known limitations

The server uses TLS but does not require a client certificate, so any client that can reach the port can ask
for decisions. Restrict access with a NetworkPolicy or add mutual TLS.

Only Pods are checked. Other workload kinds are covered only indirectly, when their controllers create Pods.

An `ImageReview` has no Pod name or user, so `imagepolicy` logs cannot show them. Use `validating` mode if you
need them in the logs.

The webhook checks image names, not image content. Tags are mutable and digests are not verified, so a tag can be
re-pointed after admission.

Registries are matched by exact `host[:port]` string after
[normalization](../README.md#registry-normalization).

`validating` mode supports only `admission.k8s.io/v1`.

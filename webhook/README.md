# webhook package

The policy engine in `policy.py` knows nothing about Kubernetes objects. Each mode is an adapter in `reviews.py`
that extracts the images from the request and builds the response.

| Module | Purpose |
|--------|---------|
| `__main__.py` | Entrypoint for `python3 -m webhook` |
| `config.py` | Command-line flags, parsed into a `Config` |
| `policy.py` | Image parsing and policy checks, independent of the mode |
| `reviews.py` | `ImageReview` and `AdmissionReview` adapters |
| `server.py` | HTTPS server, routing, health check and body size limit |
| `log.py` | Logging setup and the one-line-per-review log helper |

The certificate helper is [`scripts/gen-certs.sh`](../scripts/gen-certs.sh), described in
[docs/certificates.md](../docs/certificates.md).

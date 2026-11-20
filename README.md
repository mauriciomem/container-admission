# image-registry-control

An image-policy webhook for Kubernetes. It reads the container images of a Pod and allows or denies the Pod
based on each image's registry and tag. 

It serves one of two Kubernetes integration points, selected with `--mode`:

| Mode | Kubernetes feature | Request / response |
|------|--------------------|--------------------|
| `imagepolicy` (default) | `ImagePolicyWebhook` admission plugin in kube-apiserver | `ImageReview` (`imagepolicy.k8s.io/v1alpha1`) |
| `validating` | `ValidatingWebhookConfiguration` | `AdmissionReview` (`admission.k8s.io/v1`) |

## Quick start

This runs the webhook locally without a cluster. Create a throwaway CA and a certificate for `localhost`:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout /tmp/ca.key -out /tmp/ca.crt -subj /CN=test-ca -days 7
scripts/gen-certs.sh imagepolicy --ca-cert /tmp/ca.crt --ca-key /tmp/ca.key --out-dir /tmp/certs
```

Start the webhook:

```bash
python3 -m webhook --mode imagepolicy --port 8443 \
  --cert-file /tmp/certs/tls.crt --key-file /tmp/certs/tls.key \
  --disallowed-registries docker.io
```

Send it a review:

```bash
curl -sk -X POST https://localhost:8443/imagepolicy -d '{
  "apiVersion":"imagepolicy.k8s.io/v1alpha1","kind":"ImageReview",
  "spec":{"namespace":"default","containers":[{"image":"nginx:latest"}]}}'
```

## Flags

The webhook is configured with flags only. It reads no environment variables.

| Flag | Default | Meaning |
|------|---------|---------|
| `--mode` | `imagepolicy` | `imagepolicy` or `validating` |
| `--host` | `0.0.0.0` | Listen address |
| `--port` | `8443` | HTTPS port |
| `--cert-file` | `/certs/tls.crt` | TLS server certificate (PEM) |
| `--key-file` | `/certs/tls.key` | TLS private key (PEM) |
| `--allowed-registries` | empty, any registry | Comma-separated. If set, images from any other registry are rejected |
| `--disallowed-registries` | empty | Comma-separated. Always rejected, and takes precedence over `--allowed-registries` |
| `--disallowed-tags` | `latest` | Comma-separated. Pass `""` to disable the tag check |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

`python3 -m webhook --help` prints the same information.

## Policy rules

The webhook reports all violations for each image.

An image gets at most one registry finding. If its registry is in `--disallowed-registries`, it is rejected.
Otherwise, if `--allowed-registries` is set and the registry is not in that list, it is rejected. Separately, if
the image's tag is in `--disallowed-tags`, it is rejected for the tag as well.

A Pod is denied if any of its images is rejected. Messages for different images are joined with ` | `, and a
repeated image is reported once. For example, with `--allowed-registries quay.io` the image `gcr.io/b:1` is
rejected with `registry 'gcr.io' is not in --allowed-registries (quay.io)`.

### Registry normalization

The webhook lowercases both the image registries and the registries given on the command line. It maps the
Docker Hub aliases `index.docker.io`, `registry-1.docker.io` and `registry.hub.docker.com` to `docker.io`, so
`--disallowed-registries index.docker.io` blocks `nginx`, `docker.io/library/nginx` and
`index.docker.io/library/nginx` alike. Apart from that, matching is an exact string match on `host[:port]`:
`registry.local:5000` and `registry.local` are different registries.

### Image reference parsing

| Image | Registry | Tag |
|-------|----------|-----|
| `nginx` | `docker.io` | `latest`, which is what the runtime pulls |
| `nginx:1.25` | `docker.io` | `1.25` |
| `myorg/app:2` | `docker.io` | `2` |
| `registry.local:5000/team/app:1.2` | `registry.local:5000` | `1.2` |
| `localhost/app` | `localhost` | `latest` |
| `nginx@sha256:abc…` | `docker.io` | none: pinned by digest, so tag rules do not apply |
| `nginx:latest@sha256:abc…` | `docker.io` | `latest`: the explicit tag is kept and still checked |

The first path segment counts as a registry only if it contains `.` or `:`, or is `localhost`. The webhook
neither verifies nor requires digests, so it does not enforce pinning.

## Endpoints

A `POST` to any path is treated as a review. Use `/imagepolicy` or `/validate` by convention; accepting every path
means a typo in a kubeconfig or webhook configuration cannot silently break admission.

`GET /healthz` and `GET /health` return `200 ok` and are not logged. Any other `GET` returns `404`.

Bad input returns `400` with a message and logs an `ERROR` line. So does a missing or invalid `Content-Length`,
and a review of the wrong kind for the running mode. A body larger than 1 MiB returns `413`.

## Build the image

```bash
cd image-registry-control
docker build -t image-registry-control:1.0 .
docker save image-registry-control:1.0 | sudo ctr -n k8s.io images import -
```

The image listens on 8443, expects certificates at `/certs/tls.{crt,key}`, runs as uid 10001, and defaults to
`--mode imagepolicy`.

## Documentation

- [Certificates](docs/certificates.md): generating the TLS certificate for each mode.
- [ImagePolicyWebhook setup](docs/imagepolicy.md): running in `imagepolicy` mode.
- [ValidatingWebhookConfiguration setup](docs/validating.md): running in `validating` mode.
- [Switching modes](docs/switching-modes.md): moving from one mode to the other.
- [Logging](docs/logging.md): log format and examples.
- [Known limitations](docs/limitations.md).
- [Package layout](webhook/README.md): what each Python module does.

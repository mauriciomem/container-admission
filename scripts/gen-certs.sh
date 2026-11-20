#!/usr/bin/env bash
# Generate a TLS server certificate for the webhook, signed by the cluster CA.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  gen-certs.sh imagepolicy [options]
  gen-certs.sh validating  [options]
  gen-certs.sh -h | --help

Modes:
  imagepolicy  Cert for the ImagePolicyWebhook admission plugin. The webhook runs on the
               control-plane host and the API server calls https://localhost:8443.
               SAN: DNS:localhost, IP:127.0.0.1
               Default out dir: /etc/kubernetes/epconfig/certs (needs root)
               The key is chown'ed to --key-uid (default 10001) with mode 0600, because the
               container runs as that non-root uid and the dir is usually a hostPath mount.

  validating   Cert for a ValidatingWebhookConfiguration backed by an in-cluster Service.
               CN: <service>.<namespace>.svc
               SAN: <service>, <service>.<ns>, <service>.<ns>.svc, <service>.<ns>.svc.cluster.local
               Default out dir: ./certs
               Prints the 'kubectl create secret tls' command and the caBundle value.

Options:
  --out-dir DIR      Where to write tls.crt and tls.key
  --service NAME     validating only: Service name            (default: image-bouncer)
  --namespace NAME   validating only: Service namespace       (default: image-bouncer)
  --key-uid UID      imagepolicy only: owner of tls.key       (default: 10001)
  --ca-cert FILE     Cluster CA certificate                   (default: /etc/kubernetes/pki/ca.crt)
  --ca-key FILE      Cluster CA private key                   (default: /etc/kubernetes/pki/ca.key)
  --days N           Certificate lifetime in days             (default: 365)
  --force            Overwrite existing tls.crt/tls.key
  -h, --help         Show this help

Reading the CA key usually requires root, so on a control-plane node run with sudo.
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

mode=""
out_dir=""
service="image-bouncer"
namespace="image-bouncer"
key_uid="10001"
ca_cert="/etc/kubernetes/pki/ca.crt"
ca_key="/etc/kubernetes/pki/ca.key"
days="365"
force="false"

case "${1:-}" in
  -h | --help) usage; exit 0 ;;
  imagepolicy | validating) mode="$1"; shift ;;
  "") usage >&2; exit 2 ;;
  *) echo "error: unknown mode '$1'" >&2; usage >&2; exit 2 ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --force) force="true"; shift ;;
    --out-dir | --service | --namespace | --key-uid | --ca-cert | --ca-key | --days)
      [[ $# -ge 2 ]] || die "option $1 needs a value"
      case "$1" in
        --out-dir) out_dir="$2" ;;
        --service) service="$2" ;;
        --namespace) namespace="$2" ;;
        --key-uid) key_uid="$2" ;;
        --ca-cert) ca_cert="$2" ;;
        --ca-key) ca_key="$2" ;;
        --days) days="$2" ;;
      esac
      shift 2
      ;;
    *) die "unknown option '$1' (see --help)" ;;
  esac
done

command -v openssl >/dev/null || die "openssl is required"
[[ -r "$ca_cert" ]] || die "cannot read CA certificate: $ca_cert"
[[ -r "$ca_key" ]] || die "cannot read CA key: $ca_key (try sudo)"
[[ "$days" =~ ^[0-9]+$ ]] || die "--days must be a number"

if [[ "$mode" == "imagepolicy" ]]; then
  : "${out_dir:=/etc/kubernetes/epconfig/certs}"
  cn="localhost"  # ignored by clients when a SAN is present; the SAN list below is what matters
  san="DNS:localhost,IP:127.0.0.1"
else
  : "${out_dir:=./certs}"
  fqdn="${service}.${namespace}"
  cn="${fqdn}.svc"
  san="DNS:${service},DNS:${fqdn},DNS:${fqdn}.svc,DNS:${fqdn}.svc.cluster.local"
fi

crt="${out_dir}/tls.crt"
key="${out_dir}/tls.key"
if [[ "$force" != "true" && ( -e "$crt" || -e "$key" ) ]]; then
  die "$crt or $key already exists (use --force to overwrite)"
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

mkdir -p "$out_dir"

# The extension file replaces the old process-substitution trick; it also marks the cert
# as a server-only leaf certificate.
cat >"${tmp_dir}/ext.cnf" <<EOF
subjectAltName=${san}
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EOF

# Private key is created 0600; the temp dir holds only the CSR and extfile.
(
  umask 077
  openssl req -newkey rsa:2048 -nodes -keyout "$key" -subj "/CN=${cn}" -out "${tmp_dir}/tls.csr" 2>/dev/null
)
openssl x509 -req -in "${tmp_dir}/tls.csr" \
  -CA "$ca_cert" -CAkey "$ca_key" \
  -CAcreateserial -CAserial "${tmp_dir}/ca.srl" \
  -out "$crt" -days "$days" -extfile "${tmp_dir}/ext.cnf" 2>/dev/null

chmod 0644 "$crt"
chmod 0600 "$key"

echo "Wrote ${crt}"
echo "Wrote ${key}"
echo "CN=${cn}  SAN=${san}"

if [[ "$mode" == "imagepolicy" ]]; then
  if chown "$key_uid" "$key" 2>/dev/null; then
    echo "Key owner set to uid ${key_uid} (mode 0600) so the non-root container can read it."
  else
    echo "WARNING: could not chown the key to uid ${key_uid} (not root?)." >&2
    echo "         The container will fail to read it. Run: sudo chown ${key_uid} ${key}" >&2
  fi
  echo
  echo "Next: point the kubeconfig at https://localhost:8443/imagepolicy and start the webhook"
  echo "      with --cert-file ${crt} --key-file ${key} (see README)."
else
  echo
  echo "Create the Secret (the namespace must exist):"
  echo "  kubectl -n ${namespace} create secret tls ${service}-tls --cert=${crt} --key=${key}"
  echo
  echo "caBundle for the ValidatingWebhookConfiguration:"
  base64 <"$ca_cert" | tr -d '\n'
  echo
fi

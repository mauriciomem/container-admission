# Certificates

`scripts/gen-certs.sh` creates a server certificate signed by the cluster CA. By default it reads the CA from
`/etc/kubernetes/pki/ca.crt` and `/etc/kubernetes/pki/ca.key`; override them with `--ca-cert` and `--ca-key`.
Reading the CA key usually needs root, so run it on a control-plane node with `sudo`.

For `imagepolicy` mode the certificate has the SANs `DNS:localhost` and `IP:127.0.0.1` and is written to
`/etc/kubernetes/epconfig/certs/tls.crt` and `tls.key`:

```bash
sudo scripts/gen-certs.sh imagepolicy
```

For `validating` mode the SANs are `<service>`, `<service>.<namespace>`, `<service>.<namespace>.svc` and
`<service>.<namespace>.svc.cluster.local`, and the files go to `./certs/tls.crt` and `./certs/tls.key`:

```bash
sudo scripts/gen-certs.sh validating --service image-bouncer --namespace image-bouncer
```

The `validating` run also prints the `kubectl create secret tls` command and the base64 `caBundle` value.
`scripts/gen-certs.sh --help` lists the other options.

## Key permissions in imagepolicy mode

The container runs as uid 10001, not as root. In `imagepolicy` mode the key is normally mounted with `hostPath`,
and that uid cannot read a root-owned `0600` key. The webhook then exits with `TLS private key ... is not
readable`. For this reason, in `imagepolicy` mode the script changes the owner of the key to `--key-uid` and
keeps mode `0600`.

Kubernetes Secret mounts are readable by default, so `validating` mode needs nothing extra.

# ImagePolicyWebhook setup

In `imagepolicy` mode the API server calls the webhook synchronously for every Pod it admits. The webhook
usually runs with host networking on the same control-plane node as the API server, which calls it at
`https://localhost:8443`.

## 1. Certificate

```bash
sudo scripts/gen-certs.sh imagepolicy
```

This writes `/etc/kubernetes/epconfig/certs/tls.crt` and `tls.key`. See [certificates.md](certificates.md).

## 2. Admission configuration

Create `/etc/kubernetes/epconfig/admission-config.yaml`:

```yaml
apiVersion: apiserver.config.k8s.io/v1
kind: AdmissionConfiguration
plugins:
  - name: ImagePolicyWebhook
    configuration:
      imagePolicy:
        kubeConfigFile: /etc/kubernetes/epconfig/kubeconfig.yaml
        allowTTL: 50
        denyTTL: 50
        retryBackoff: 500
        defaultAllow: false
```

`allowTTL` and `denyTTL` are the number of seconds the API server caches an allowed or denied answer.
`retryBackoff` is the time between retries in milliseconds. `defaultAllow` decides what happens when the webhook
is unreachable: `false` denies the Pod and `true` allows it.

## 3. Webhook kubeconfig

Create `/etc/kubernetes/epconfig/kubeconfig.yaml`:

```yaml
apiVersion: v1
kind: Config
clusters:
  - name: image-bouncer
    cluster:
      certificate-authority: /etc/kubernetes/pki/ca.crt
      server: https://localhost:8443/imagepolicy
users:
  - name: api-server
    user: {}
current-context: image-bouncer
contexts:
  - name: image-bouncer
    context:
      cluster: image-bouncer
      user: api-server
```

`certificate-authority` is the CA that signed the webhook certificate. The server name, `localhost`, must match a
SAN in the certificate, which is why the certificate has `DNS:localhost` and `IP:127.0.0.1`.

## 4. Run the webhook

As a static Pod, in `/etc/kubernetes/manifests/image-bouncer.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: image-bouncer
  namespace: kube-system
spec:
  hostNetwork: true
  containers:
    - name: image-bouncer
      image: image-registry-control:1.0
      imagePullPolicy: IfNotPresent
      args:
        - --mode=imagepolicy
        - --host=127.0.0.1
        - --disallowed-registries=docker.io
        - --disallowed-tags=latest
      securityContext:
        runAsUser: 10001
        runAsNonRoot: true
        allowPrivilegeEscalation: false
      volumeMounts:
        - name: certs
          mountPath: /certs
          readOnly: true
  volumes:
    - name: certs
      hostPath:
        path: /etc/kubernetes/epconfig/certs
        type: Directory
```

Or directly on the node:

```bash
sudo python3 -m webhook --mode imagepolicy --host 127.0.0.1 \
  --cert-file /etc/kubernetes/epconfig/certs/tls.crt --key-file /etc/kubernetes/epconfig/certs/tls.key \
  --disallowed-registries docker.io
```

The static Pod's own image, `image-registry-control:1.0`, resolves to `docker.io`. Static Pods keep running even
if the API server rejects their mirror Pod, but with `docker.io` disallowed the webhook Pod does not show up in
`kubectl get pods`.

## 5. Enable the plugin

In `/etc/kubernetes/manifests/kube-apiserver.yaml`, add `ImagePolicyWebhook` to the existing
`--enable-admission-plugins` list, set `--admission-control-config-file`, and mount `/etc/kubernetes/epconfig`:

```yaml
spec:
  containers:
    - command:
        - kube-apiserver
        - --enable-admission-plugins=NodeRestriction,ImagePolicyWebhook
        - --admission-control-config-file=/etc/kubernetes/epconfig/admission-config.yaml
        # ...
      volumeMounts:
        - name: epconfig
          mountPath: /etc/kubernetes/epconfig
          readOnly: true
  volumes:
    - name: epconfig
      hostPath:
        path: /etc/kubernetes/epconfig
        type: DirectoryOrCreate
```

The kubelet restarts the API server when the manifest changes.

## 6. Try it

```bash
kubectl run web --image=nginx:latest
```

```text
Error from server (Forbidden): pods "web" is forbidden: image policy webhook backend denied one or more images:
  Image 'nginx:latest' rejected: registry 'docker.io' is in --disallowed-registries (docker.io); tag 'latest' ...
```

The API server puts the images of both regular and init containers into the `spec.containers` list of the
`ImageReview`, so all of them are checked. For the `pods/ephemeralcontainers` subresource it sends the images of
the ephemeral containers.

## Failure behaviour and caching

`ImagePolicyWebhook` has no namespace exemptions. With `defaultAllow: false`, every new Pod is denied while the
webhook is unreachable, system Pods included.

Because of `allowTTL` and `denyTTL`, a policy change can take up to that many seconds to apply to an image the API
server has already evaluated.

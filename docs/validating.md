# ValidatingWebhookConfiguration setup

In `validating` mode the webhook runs in the cluster as a Deployment behind a Service. It checks Pods only: the
images in `containers`, `initContainers` and `ephemeralContainers`. `CREATE` and `UPDATE` are evaluated, and any
request without an object, such as `DELETE`, is allowed. If the webhook rules are too broad and a non-Pod object
arrives, it is allowed and a `WARNING` is logged.

## 1. Namespace and certificate

```bash
kubectl create namespace image-bouncer
sudo scripts/gen-certs.sh validating --service image-bouncer --namespace image-bouncer
kubectl -n image-bouncer create secret tls image-bouncer-tls --cert=certs/tls.crt --key=certs/tls.key
```

`gen-certs.sh` prints the `kubectl create secret tls` command and the base64 `caBundle` value. Keep the
`caBundle` for step 3. See [certificates.md](certificates.md).

## 2. Deployment and Service

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: image-bouncer
  namespace: image-bouncer
spec:
  replicas: 2
  selector:
    matchLabels: {app: image-bouncer}
  template:
    metadata:
      labels: {app: image-bouncer}
    spec:
      containers:
        - name: image-bouncer
          image: image-registry-control:1.0
          imagePullPolicy: IfNotPresent
          args:
            - --mode=validating
            - --disallowed-registries=docker.io
            - --disallowed-tags=latest
          ports:
            - containerPort: 8443
          securityContext:
            runAsUser: 10001
            runAsNonRoot: true
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
          readinessProbe:
            httpGet: {path: /healthz, port: 8443, scheme: HTTPS}
          livenessProbe:
            httpGet: {path: /healthz, port: 8443, scheme: HTTPS}
          volumeMounts:
            - name: tls
              mountPath: /certs
              readOnly: true
      volumes:
        - name: tls
          secret:
            secretName: image-bouncer-tls
---
apiVersion: v1
kind: Service
metadata:
  name: image-bouncer
  namespace: image-bouncer
spec:
  selector: {app: image-bouncer}
  ports:
    - port: 443
      targetPort: 8443
```

## 3. ValidatingWebhookConfiguration

```yaml
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingWebhookConfiguration
metadata:
  name: image-bouncer
webhooks:
  - name: image-bouncer.image-bouncer.example.com
    admissionReviewVersions: ["v1"]
    sideEffects: None
    failurePolicy: Fail
    timeoutSeconds: 5
    clientConfig:
      service:
        name: image-bouncer
        namespace: image-bouncer
        path: /validate
        port: 443
      caBundle: <BASE64 CA FROM gen-certs.sh>
    rules:
      - apiGroups: [""]
        apiVersions: ["v1"]
        operations: ["CREATE", "UPDATE"]
        resources: ["pods", "pods/ephemeralcontainers"]
        scope: Namespaced
    namespaceSelector:
      matchExpressions:
        - key: kubernetes.io/metadata.name
          operator: NotIn
          values: ["kube-system", "image-bouncer"]
```

The webhook only accepts `admission.k8s.io/v1`, so `admissionReviewVersions` must be `["v1"]`.

With `failurePolicy: Fail`, a request is denied when the webhook is unreachable. `Ignore` fails open instead.
The `namespaceSelector` excludes the webhook's own namespace and `kube-system`; see
[Excluded namespaces](#excluded-namespaces).

## 4. Try it

```bash
kubectl run web --image=nginx:latest
```

```text
Error from server (Forbidden): admission webhook "image-bouncer.image-bouncer.example.com" denied the request:
  Image 'nginx:latest' rejected: registry 'docker.io' is in --disallowed-registries (docker.io); tag 'latest' ...
```

A Deployment that uses a rejected image is accepted, but its Pods are not. The check runs when the ReplicaSet
creates the Pod, so the denial shows up as `FailedCreate` events on the ReplicaSet (`kubectl describe rs`) and
not at `kubectl apply`.

`UPDATE` is checked too, so changing the labels of an existing Pod that uses a rejected image is denied.

`kubectl run --dry-run=server` tests a Pod without creating it. The webhook logs it with `dryRun=true`.

## Excluded namespaces

Without the exclusion of its own namespace, and with `failurePolicy: Fail`, the webhook can lock the cluster out.
When the webhook Pods go away after a node reboot, a rollout or a crash, the API server cannot create replacement
Pods because it cannot reach a webhook that is not running. Excluding `kube-system` keeps system components out
of the policy for the same reason.

To recover from a lockout, delete the configuration:

```bash
kubectl delete validatingwebhookconfiguration image-bouncer
```

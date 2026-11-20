# Switching modes

The two modes are independent and can run side by side, in which case the API server asks both. To switch, set
up the new mode and turn off the old one:

| To use | Set up | Turn off the other |
|--------|--------|--------------------|
| `imagepolicy` | [ImagePolicyWebhook setup](imagepolicy.md) | Delete the webhook configuration with `kubectl delete validatingwebhookconfiguration image-bouncer` and scale down the Deployment. |
| `validating` | [ValidatingWebhookConfiguration setup](validating.md) | In `kube-apiserver.yaml`, remove `ImagePolicyWebhook` from `--enable-admission-plugins`, and remove the `--admission-control-config-file` flag if nothing else uses it. Then stop the static Pod or the process on the node. |

Each running instance has exactly one mode, set on its command line. If a `ValidatingWebhookConfiguration` points
at an `imagepolicy` instance, or the reverse, the webhook answers with `400` and logs an error that names the
mismatch.

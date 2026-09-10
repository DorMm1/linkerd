# Linkerd installation and policy GitOps

This repository contains the public Linkerd Helm charts, a separate Helm chart
for destination access policies, and Git-managed platform/project policy values.
It does not install anything merely by being cloned.

## Repository contents

| Path | Purpose |
| --- | --- |
| [linkerd-chart/](linkerd-chart/) | Downloaded upstream charts, original release archives, checksums, and license. |
| [linkerd-chart/linkerd-crds/](linkerd-chart/linkerd-crds/) | Installs Linkerd's Kubernetes custom resource definitions. Install first. |
| [linkerd-chart/linkerd-control-plane/](linkerd-chart/linkerd-control-plane/) | Installs the identity, destination/policy, and proxy-injection control-plane components. |
| [linkerd-policies/](linkerd-policies/) | Local policy chart: deny-by-default `Server` resources with explicit `MeshTLSAuthentication` and `AuthorizationPolicy` grants. |
| [linkerd-policy-gitops/](linkerd-policy-gitops/) | Platform-owned destination inventory and project-owned ServiceAccount grants, supplied as Helm values. |

The upstream charts are pinned to **2026.9.1**, running **edge-26.9.1**: the
latest versions in the [official public edge repository](https://helm.linkerd.io/edge/index.yaml)
when downloaded on **2026-09-10**. Both charts require **Kubernetes 1.31+**.
The upstream project no longer publishes stable release artifacts; those are
provided by vendors. See [Linkerd releases](https://linkerd.io/releases/) and
review the [pinned release notes](https://github.com/linkerd/linkerd2/releases/tag/edge-26.9.1)
before choosing this release for your environment.

The charts are vendored unchanged, including their `partials` dependencies;
no `helm dependency update` or remote chart repository is needed to install
these local copies. Container images and other prerequisites still need to be
available: this is not an air-gapped installation bundle. Viz, multicluster,
and Linkerd CNI extension charts are not included.

## Install Linkerd

Commands below use **PowerShell from the repository root** and describe a
**fresh installation**, not adoption of an existing Linkerd deployment.
Review every target context and values file before running cluster commands.

### 1. Prerequisites

- Kubernetes 1.31 or newer, with `kubectl` configured for the intended cluster.
- Helm 3 (local validation used Helm 3.17.2).
- The [Linkerd CLI for edge-26.9.1](https://github.com/linkerd/linkerd2/releases/tag/edge-26.9.1)
  on your `PATH`, for health and proxy checks.
- The [Smallstep CLI](https://smallstep.com/docs/step-cli/installation/) (`step`)
  for the certificate example, or certificates from your approved PKI.
- Permissions to create CRDs, cluster-scoped RBAC/webhooks, and the control plane.

```powershell
kubectl config current-context
kubectl version
helm version --short
linkerd version --client
```

The examples use the `linkerd` control-plane namespace, `cluster.local` DNS/trust
domain, and the default proxy-init networking mode. Review the upstream
[values](linkerd-chart/linkerd-control-plane/values.yaml) for your cluster's
networks, DNS domain, CNI, and security requirements. Cilium kube-proxy replacement
and other nonstandard networking setups may need
[additional configuration](https://linkerd.io/2-edge/reference/cluster-configuration/).

### 2. Ensure Gateway API is installed

Gateway API CRDs are a separate prerequisite. The downloaded CRD chart defaults
to `installGatewayAPI: false`, so these instructions manage Gateway API separately.
Check the current bundle first:

```powershell
kubectl get crd httproutes.gateway.networking.k8s.io -o 'jsonpath={.metadata.annotations.gateway\.networking\.k8s\.io/bundle-version}'
```

If it is absent, the current upstream guide uses the **1.5.1 standard bundle**:

```powershell
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.1/standard-install.yaml
```

Do not replace an existing Gateway API installation without reviewing its
ownership, other consumers, and
[Linkerd's compatibility guidance](https://linkerd.io/2-edge/features/gateway-api/).
Then run the pre-install checks and resolve any failures:

```powershell
linkerd check --pre
```

### 3. Provide identity certificates

Helm requires an ECDSA P-256 trust anchor and issuer certificate/private key.
For a local evaluation, generate them once using the
[upstream certificate procedure](https://linkerd.io/2-edge/tasks/generate-certificates/):

```powershell
New-Item -ItemType Directory -Force .\.local\certs | Out-Null
step certificate create root.linkerd.cluster.local .\.local\certs\ca.crt .\.local\certs\ca.key --profile root-ca --kty EC --curve P-256 --not-after 87600h --no-password --insecure
step certificate create identity.linkerd.cluster.local .\.local\certs\issuer.crt .\.local\certs\issuer.key --profile intermediate-ca --kty EC --curve P-256 --not-after 8760h --no-password --insecure --ca .\.local\certs\ca.crt --ca-key .\.local\certs\ca.key
```

These example keys are **unencrypted**. The local directory and `*.key` files
are Git-ignored, but still require secure storage and access controls. Never
commit keys, secret values, or rendered control-plane manifests. Helm release
Secrets also contain supplied private keys; restrict access to them.

For production, use your approved PKI/secret-management and
[certificate rotation](https://linkerd.io/2-edge/tasks/automatically-rotating-control-plane-tls-credentials/)
process. The example issuer expires after one year; rotation is not configured
by this repository. Do not regenerate the trust anchor during routine upgrades.
The root private key is not passed to Helm.

### 4. Install CRDs, then the control plane

```powershell
helm install linkerd-crds .\linkerd-chart\linkerd-crds `
  --namespace linkerd --create-namespace --wait --timeout 5m
```

After that command succeeds:

```powershell
helm install linkerd-control-plane .\linkerd-chart\linkerd-control-plane `
  --namespace linkerd `
  --set-file 'identityTrustAnchorsPEM=.\\.local\\certs\\ca.crt' `
  --set-file 'identity.issuer.tls.crtPEM=.\\.local\\certs\\issuer.crt' `
  --set-file 'identity.issuer.tls.keyPEM=.\\.local\\certs\\issuer.key' `
  --wait --timeout 5m
```

The doubled backslashes in `--set-file` values escape Helm's value parser;
ordinary filesystem paths in the other arguments use single backslashes.

For high availability, also pass
`-f .\linkerd-chart\linkerd-control-plane\values-ha.yaml` to the control-plane
command after reviewing the [HA settings](linkerd-chart/linkerd-control-plane/values-ha.yaml)
and cluster capacity. Keep environment-specific overrides outside the vendored
chart defaults.

```powershell
linkerd check
kubectl get pods --namespace linkerd
helm list --namespace linkerd
```

### 5. Mesh the workloads

Installing Linkerd does not inject proxies into existing application pods.
Annotate the intended source **and** destination namespaces in your platform
manifests. For example, if the existing destination namespace is `rabbitmq`:

```powershell
kubectl annotate namespace rabbitmq linkerd.io/inject=enabled --overwrite
```

Recreate/restart the intended application workloads in a controlled rollout;
namespace injection affects newly created pods, not existing ones. Source pods
must use the exact ServiceAccounts permitted by the policy values. Verify:

```powershell
linkerd check --proxy --namespace rabbitmq
```

Repeat for each participating source namespace. This repository does not create
RabbitMQ, application workloads, application namespaces, or ServiceAccounts.

## Install the policy chart

The [policy chart](linkerd-policies/) creates policy **instances**, not CRDs or
workloads. It defaults to rendering no resources until values are provided.

| Values file | Contents |
| --- | --- |
| [platform.yaml](linkerd-policy-gitops/platform.yaml) | RabbitMQ destinations in namespace `rabbitmq`: AMQP port 5672 (`opaque`) and management port 15672 (`HTTP/1`), selecting pods with `app.kubernetes.io/name: rabbitmq`. Defines each project's permitted namespaces/destinations. |
| [project-demo.yaml](linkerd-policy-gitops/project-demo.yaml) | Grants the `rabbitmq/rabbitmq-spa-demo` ServiceAccount access to both destinations. |
| [project-a.yaml](linkerd-policy-gitops/project-a.yaml), [project-b.yaml](linkerd-policy-gitops/project-b.yaml) | Empty grant maps; neither project currently gets access. |

These are environment-specific example values, **not evidence that those
workloads exist**. Adjust and review them before applying. Destination namespaces,
meshed pods with matching labels/declared ports, and authorized source
ServiceAccounts must already exist. Applying the inventory denies traffic on
the selected ports unless a matching authorization permits it.

Render the complete inventory and all project grants as **one trusted release**:

```powershell
$policyValues = @(
  '-f', '.\linkerd-policy-gitops\platform.yaml'
  '-f', '.\linkerd-policy-gitops\project-a.yaml'
  '-f', '.\linkerd-policy-gitops\project-b.yaml'
  '-f', '.\linkerd-policy-gitops\project-demo.yaml'
)
helm lint .\linkerd-policies --strict @policyValues
helm template mesh-policies .\linkerd-policies --namespace policy-system @policyValues
```

The supplied values render **six resources** in `rabbitmq`: two deny-by-default
Servers, two MeshTLSAuthentications, and two AuthorizationPolicies. After review:

```powershell
helm upgrade --install mesh-policies .\linkerd-policies `
  --namespace policy-system --create-namespace --reset-values @policyValues `
  --wait --timeout 5m
kubectl get servers.policy.linkerd.io,meshtlsauthentications.policy.linkerd.io,authorizationpolicies.policy.linkerd.io --namespace rabbitmq
```

`--create-namespace` creates only the Helm release namespace, not destinations.
Do not use this fresh-install example to take over existing standalone policies;
review ownership, overlapping selectors, and a migration plan first.

Important operating rules:

- Keep schema validation enabled. Protect platform inventory, templates, and
  reconciliation credentials with review and RBAC; Helm cannot enforce which
  values file owns a key.
- Remove a grant and reconcile with `--reset-values` plus the **full current
  file set** to revoke it. Do not use `--reuse-values`. Maps merge recursively;
  lists are replaced, and another file can still supply a removed grant.
- Servers are retained on removal/uninstall with Helm's `keep` annotation and
  Argo CD's `Prune=false,Delete=false`. Grants are deleted normally. Retained
  Servers require deliberate decommissioning and are no longer reconciled once
  removed from the desired output. A rollback can restore old grants.
- This is not cluster-wide isolation: only selected ports on selected meshed pods
  are covered, and other authorization policies can independently permit access.
  Mesh authorization does not replace RabbitMQ credentials or application ACLs.
- Check live specs and test both allowed and denied identities on **new
  connections**. Successful Helm rendering/install alone does not prove traffic
  enforcement or repair all out-of-band custom-resource drift.

See the [policy schema](linkerd-policies/values.schema.json) and
[policy resource explanations](linkerd-policies/README-CRS.md) for the data model.

## GitOps workflow

[linkerd-policy-gitops/](linkerd-policy-gitops/) contains declarative values,
not an installed GitOps controller or ready-to-apply Argo CD/Flux application.
No automatic reconciliation is configured in this repository.

1. Platform owners review destination inventory and project boundaries.
2. Project owners propose exact ServiceAccount grants in their own values files.
3. CI renders/lints the complete file set and reviews the policy change.
4. One reconciler applies the approved state; validate live policy and traffic
   in a test environment before promotion.

For Argo CD, use chart path `linkerd-policies` and enumerate the four files as
`../linkerd-policy-gitops/<file>.yaml` in `helm.valueFiles` (relative to the chart).
For a Flux HelmRelease sourced from this GitRepository, use chart path
`./linkerd-policies` and list `linkerd-policy-gitops/<file>.yaml` in
`spec.chart.spec.valuesFiles` (relative to the source root); use
`reconcileStrategy: Revision` for Git changes and do not enable `preserveValues`.
Add newly introduced project files to the controller's list.

Preserve the chart's Server retention annotations, and do not let a Helm CLI
release and a GitOps controller independently own the same resources. Install
Linkerd CRDs and the healthy control plane before reconciling policies.

**Inherited documentation:** the existing
[policy README](linkerd-policies/README.md) and
[GitOps README](linkerd-policy-gitops/README.md) also describe their original
parent repository's migration helpers, Node YAML parser, and RabbitMQ demo.
Those scripts/dependencies are not included here; their old paths, test results,
and cluster verification claims do not apply to this standalone repository.
Use the root-relative Helm workflow above. The existing Node policy tests
likewise depend on that absent parser and are not a standalone test suite here.

## Validation and upgrades

Offline checks for the vendored CRD chart:

```powershell
helm lint .\linkerd-chart\linkerd-crds --strict --kube-version 1.31.0
helm template linkerd-crds .\linkerd-chart\linkerd-crds --namespace linkerd --kube-version 1.31.0 | Out-Null
```

After generating evaluation certificates, validate the control-plane chart
without installing it:

```powershell
helm lint .\linkerd-chart\linkerd-control-plane --strict --kube-version 1.31.0 `
  --set-file 'identityTrustAnchorsPEM=.\\.local\\certs\\ca.crt' `
  --set-file 'identity.issuer.tls.crtPEM=.\\.local\\certs\\issuer.crt' `
  --set-file 'identity.issuer.tls.keyPEM=.\\.local\\certs\\issuer.key'
```

The explicit Kubernetes version is for offline validation, not a way to bypass
the cluster's minimum version. Use the policy lint/render commands above to
validate changes to access grants.

For chart provenance, integrity checks, and re-fetching the pinned release, see
[linkerd-chart/README.md](linkerd-chart/README.md). On upgrades, review upstream
release notes and changed values, update both chart snapshots and checksums,
upgrade CRDs before the control plane, and retain the existing identity material
unless performing an intentional rotation. Preserve your reviewed environment
overrides and follow the [upstream Helm upgrade guide](https://linkerd.io/2-edge/tasks/install-helm/).
Do not uninstall CRDs as an upgrade or policy rollback mechanism.

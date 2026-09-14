# Linkerd installation and access policies

This repository installs [Linkerd](https://linkerd.io) and provides the Helm
chart that turns the small YAML files of a **catalog** into Linkerd access
policies. Application teams edit only their catalog file; they never touch the
chart and do not need to know how the mesh works.

| Path | What it is |
| --- | --- |
| [linkerd-chart/](linkerd-chart/) | The public Linkerd Helm charts (`linkerd-crds`, `linkerd-control-plane`), pinned to **2026.9.1 / edge-26.9.1**, unchanged. |
| [linkerd-policies/](linkerd-policies/) | Helm chart: catalog files in, `Server` / `MeshTLSAuthentication` / `AuthorizationPolicy` out. |
| [linkerd-catalog/](linkerd-catalog/) | **Example catalog**: `platform.yaml` (DevOps: rabbitmq, kafka, redis, linux-mssql, mongodb, imply) and `projects/<project>/<namespace>.yaml`. Start with its [README](linkerd-catalog/README.md). |
| [argocd/](argocd/) | The multi-source Argo CD `Application`: this chart + the catalog as values. |
| [tests/](tests/) | Python tests: chart behaviour, input validation, catalog conventions. |
| [test.yaml](test.yaml) | The example catalog rendered through the chart; a PR diff shows exactly which policies change. |

## How it fits together

```text
catalog                                         linkerd-policies/  (Helm chart)
platform.yaml                  ─┐
projects/demo/demo.yaml        ─┼─ values files ──▶ helm template ──▶ Server, MeshTLSAuthentication,
projects/project-a/project-a.yaml─┘   (Argo CD)                         AuthorizationPolicy per port
                                                                              │
                                                       Linkerd proxy in the target pod
                                                       allows exactly who was declared
```

- **DevOps** installs Linkerd, prepares namespaces (two annotations), owns the
  chart and the catalog's `platform.yaml`.
- **Projects** own `projects/<project>/` in the catalog, one file per namespace.
- **Argo CD** (3.4+) renders the chart with every catalog file as a values file
  and applies the result. A new catalog file is picked up without touching the
  Application.

The catalog is meant to live in its own on-prem repository; until then the
example in [linkerd-catalog/](linkerd-catalog/) is what Argo CD reads.

## 1. Install Linkerd

Commands use **PowerShell from the repository root** on a fresh cluster
(Kubernetes **1.31+**). You need `kubectl`, Helm 3, the
[Linkerd CLI edge-26.9.1](https://github.com/linkerd/linkerd2/releases/tag/edge-26.9.1)
and, for the certificate example, the [step CLI](https://smallstep.com/docs/step-cli/installation/).

### Gateway API

Linkerd needs the Gateway API CRDs; the vendored CRD chart does not install them
(`installGatewayAPI: false`). Check, and install if missing:

```powershell
kubectl get crd httproutes.gateway.networking.k8s.io -o 'jsonpath={.metadata.annotations.gateway\.networking\.k8s\.io/bundle-version}'
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.1/standard-install.yaml
linkerd check --pre
```

### Certificates

Linkerd's identity needs a trust anchor and an issuer (ECDSA P-256). For an
evaluation cluster, generate them once, following the
[upstream procedure](https://linkerd.io/2-edge/tasks/generate-certificates/):

```powershell
New-Item -ItemType Directory -Force .\.local\certs | Out-Null
step certificate create root.linkerd.cluster.local .\.local\certs\ca.crt .\.local\certs\ca.key --profile root-ca --kty EC --curve P-256 --not-after 87600h --no-password --insecure
step certificate create identity.linkerd.cluster.local .\.local\certs\issuer.crt .\.local\certs\issuer.key --profile intermediate-ca --kty EC --curve P-256 --not-after 8760h --no-password --insecure --ca .\.local\certs\ca.crt --ca-key .\.local\certs\ca.key
```

`.local/` and `*.key` are git-ignored. For production use your PKI and set up
[certificate rotation](https://linkerd.io/2-edge/tasks/automatically-rotating-control-plane-tls-credentials/);
the issuer above expires after one year.

### CRDs, then the control plane

```powershell
helm install linkerd-crds .\linkerd-chart\linkerd-crds `
  --namespace linkerd --create-namespace --wait --timeout 5m

helm install linkerd-control-plane .\linkerd-chart\linkerd-control-plane `
  --namespace linkerd `
  --set-file 'identityTrustAnchorsPEM=.\\.local\\certs\\ca.crt' `
  --set-file 'identity.issuer.tls.crtPEM=.\\.local\\certs\\issuer.crt' `
  --set-file 'identity.issuer.tls.keyPEM=.\\.local\\certs\\issuer.key' `
  --wait --timeout 5m

linkerd check
```

The doubled backslashes are for Helm's `--set-file` parser. For high
availability add `-f .\linkerd-chart\linkerd-control-plane\values-ha.yaml`.
Provenance, checksums and upgrade notes for the vendored charts are in
[linkerd-chart/README.md](linkerd-chart/README.md).

## 2. Prepare a namespace

Every namespace that takes part needs two annotations **before its pods are
created** (existing pods are not changed; restart them once):

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: demo
  annotations:
    linkerd.io/inject: enabled                       # a Linkerd proxy in every pod
    config.linkerd.io/default-inbound-policy: deny   # block everything not declared in the catalog
```

The catalog assumes, per workload, a **ServiceAccount named like the workload**
and the pod label **`app.kubernetes.io/name: <workload>`**; both can be
overridden per workload. A workload that needs its own permissions must have its
own ServiceAccount: Linkerd identifies callers by ServiceAccount, not by
Deployment name.

```powershell
linkerd check --proxy --namespace demo
```

## 3. Install the policies with Argo CD

```powershell
kubectl apply -f .\argocd\linkerd-policies.yaml
```

[argocd/linkerd-policies.yaml](argocd/linkerd-policies.yaml) is a multi-source
Application: source 1 is the `linkerd-policies` chart, source 2 is the catalog
referenced as `$catalog`, with `valueFiles: [$catalog/.../platform.yaml,
$catalog/.../projects/*/*.yaml]`. Automated sync, prune and self-heal are on, so
a merged catalog PR is a change in the cluster:

- a new `projects/<project>/<namespace>.yaml` adds policies,
- a removed line removes a permission,
- a deleted file removes the namespace's policies.

Glob patterns in `valueFiles` need **Argo CD 3.4 or newer**. Without Argo CD the
same render is a plain Helm command with one `--values` per catalog file, which
is what [render_catalog.py](render_catalog.py) runs.

When the catalog moves to your on-prem repository, change `repoURL` /
`targetRevision` of source 2 and drop the `linkerd-catalog/` prefix from the two
`valueFiles` entries. Nothing else changes.

## 4. Write a policy

Teams do this in the catalog. The format, for reference:

```yaml
# linkerd-catalog/projects/demo/demo.yaml
namespaces:
  demo:
    team: demo
    allowSameNamespace: true          # my workloads may call each other

    workloads:
      demo-ui:
        ports:
          http: 3000

      demo-api:
        ports:
          http: 8080
          grpc: { port: 9090, protocol: gRPC }
        publish: [http]               # other namespaces may ask for demo/demo-api/http
        connectsTo:
          - rabbitmq/rabbitmq/amqp
          - project-a/orders-api/http
```

Declare your ports, `publish` the ones others may use, `connectsTo` what you
need; access exists only when both sides agree, everything else is denied.
The field reference is the [catalog README](linkerd-catalog/README.md); what
each field turns into is in [linkerd-policies/README.md](linkerd-policies/README.md).

## 5. Test

```powershell
pip install -r requirements.txt
python -m pytest
helm lint .\linkerd-policies --strict
python render_catalog.py | Out-File .\test.yaml -Encoding utf8   # refresh the committed render
```

[CI](.github/workflows/policies.yml) runs the same on every push and pull
request; [azure-pipelines.yml](azure-pipelines.yml) is the Azure DevOps
equivalent — add it as a **build validation** branch policy on `main` so a PR
can only be merged when the tests pass, and it publishes the policies the PR
adds or removes as the `policy-diff` artifact. A broken catalog file fails the
render with the field to fix.

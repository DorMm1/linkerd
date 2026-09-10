# Vendored public Linkerd charts

Downloaded on **2026-09-10** from the
[official public edge Helm repository](https://helm.linkerd.io/edge/index.yaml).
At download time, both latest chart versions were **2026.9.1** (published
2026-09-04); the control plane's application version is **edge-26.9.1**.

| Chart | Archive | Extracted chart |
| --- | --- | --- |
| CRDs | [linkerd-crds-2026.9.1.tgz](linkerd-crds-2026.9.1.tgz) | [linkerd-crds/](linkerd-crds/) |
| Control plane | [linkerd-control-plane-2026.9.1.tgz](linkerd-control-plane-2026.9.1.tgz) | [linkerd-control-plane/](linkerd-control-plane/) |

Original archive URLs:

- <https://helm.linkerd.io/edge/linkerd-crds-2026.9.1.tgz>
- <https://helm.linkerd.io/edge/linkerd-control-plane-2026.9.1.tgz>

Both archives' SHA-256 digests were checked against the official repository index.
[SHA256SUMS](SHA256SUMS) records those digests. This verifies downloaded archive
integrity against the HTTPS index; it is not a cryptographic publisher-signature
verification.

The extracted charts are unchanged upstream contents, with their `partials`
library dependencies already included under each chart's `charts/` directory.
Do not run `helm dependency update`: the upstream `file://../partials` source
layout is not reproduced here and no dependency download is needed.

Linkerd is licensed under Apache-2.0. [LICENSE](LICENSE) is copied from the
[same upstream release](https://github.com/linkerd/linkerd2/blob/edge-26.9.1/LICENSE)
and applies to the vendored upstream content.

## Verify the archives

Run from the repository root in PowerShell:

```powershell
Get-Content .\linkerd-chart\SHA256SUMS | ForEach-Object {
  $expected, $name = $_ -split '\s+', 2
  $actual = (Get-FileHash (Join-Path .\linkerd-chart $name) -Algorithm SHA256).Hash
  if ($actual -ne $expected) { throw "SHA-256 mismatch: $name" }
  Write-Output "Verified $name"
}
```

## Re-fetch or update

To re-fetch the exact pinned archives into a temporary, Git-ignored directory:

```powershell
helm repo add linkerd-edge https://helm.linkerd.io/edge
helm repo update linkerd-edge
helm search repo linkerd-edge/linkerd-control-plane --versions
helm search repo linkerd-edge/linkerd-crds --versions
New-Item -ItemType Directory -Force .\.local\chart-download | Out-Null
helm pull linkerd-edge/linkerd-crds --version 2026.9.1 --destination .\.local\chart-download
helm pull linkerd-edge/linkerd-control-plane --version 2026.9.1 --destination .\.local\chart-download
```

For a newer release, choose explicit versions from the current index rather than
floating `latest`. Verify the new archives against their index digests before
extracting into fresh directories. Replace the corresponding vendored directories
and archives only after review; do not overlay new files on old chart contents.
Update this metadata, checksums, and the root guide together, and validate both
charts plus the local policy values.

See the [repository installation guide](../README.md) for prerequisites,
certificates, installation order, and policy/GitOps usage. These upstream charts
require Kubernetes 1.31+ and do not include container images.

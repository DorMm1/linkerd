# Linkerd policies

Helm-native, deny-by-default destination policy for Linkerd **edge26.9.1**.
Requires its installed policy CRDs: `Server` **v1beta3**,
`MeshTLSAuthentication` and `AuthorizationPolicy` **v1alpha1**.
Tested with Helm **v3.17.2** and Node **v22**. There is no custom controller.
The chart creates only these three policy kinds, never namespaces, injection
configuration, certificates, workloads, or CRDs. Defaults render nothing.

For plain-language explanations and connected YAML examples of the CRs, see
[Understanding the Linkerd policy CRs](README-CRS.md).

## Templates

Each resource is written as ordinary YAML with loops for repeated entries:

- [server.yaml](templates/server.yaml): protected ports and pod selectors.
- [meshtlsauthentication.yaml](templates/meshtlsauthentication.yaml): allowed ServiceAccounts.
- [authorizationpolicy.yaml](templates/authorizationpolicy.yaml): binds those identities to Servers.
- [validation.yaml](templates/validation.yaml): checks project/destination references and permissions; emits no resource.
- [_helpers.tpl](templates/_helpers.tpl): common labels and stable grant names only.

Types, required fields and formats live in [values.schema.json](values.schema.json),
not a second validation framework in Go templates. Keep Helm schema validation
enabled; do not use `--skip-schema-validation`.

## Values contract

```yaml
platform:
  destinations:
    rabbitmq:
      namespace: messaging
      port: 5672
      protocol: opaque
      podSelector:
        matchLabels:
          app.kubernetes.io/name: rabbitmq
  projects:
    payments:
      namespaces: [payments-dev, payments-prod]
      destinations: [rabbitmq]
projects:
  payments:
    grants:
      rabbitmq:
        serviceAccounts:
          - namespace: payments-dev
            name: worker
          - namespace: payments-prod
            name: worker
```

- `platform.destinations`: platform-owned map of destination IDs to target
  namespace, integer port **1–65535**, and a nonempty `podSelector.matchLabels`
  map of valid Kubernetes label keys and nonempty string label values.
  `protocol` is optional, defaults to `unknown`, and must be one of `unknown`,
  `HTTP/1`, `HTTP/2`, `gRPC`, `opaque`, or `TLS`. It becomes `spec.proxyProtocol`.
- `platform.projects`: platform-owned map of project IDs to required
  `namespaces` (allowed **source** namespaces) and `destinations` (registered
  destination IDs). Either list may be empty; duplicates are rejected.
- `projects`: map of registered project IDs to required `grants` maps.
  Each grant key is an allowed destination ID and has a nonempty
  `serviceAccounts` list of exact `{namespace, name}` objects. The source
  namespace must be allowed for that project. Duplicate namespace/name pairs
  are rejected; the same name in different allowed namespaces is valid.
  Empty `grants: {}` is valid. **Remove a grant to revoke it**, rather than
  passing an empty list.
- Project IDs, destination IDs, grant keys, and namespaces are lowercase DNS
  labels, 1–63 characters. ServiceAccount names are DNS subdomains up to 253
  characters. Unknown properties and invalid types/keys are rejected by a
  strict values schema; cross-map boundaries are checked separately with
  Helm `fail`.

Each destination always gets one `Server`, named exactly its destination ID,
in its target namespace, with **`spec.accessPolicy: deny`**. Each grant adds
one `MeshTLSAuthentication` and one `AuthorizationPolicy` in that namespace.
Authentication uses only `spec.identityRefs` with `kind: ServiceAccount`,
`name`, and explicit source `namespace`; no raw identities, wildcards, or
trust-domain assumptions. Authorization targets that exact Server.

Grant names share a helper: a readable project/destination prefix plus a
16-character SHA-256 suffix of the JSON project/destination tuple, always
bounded to 63 characters. This distinguishes ambiguous hyphen joins and
long truncated names. Rendering fails on any resulting same-namespace
grant-name collision. Names do not depend on release name. Labels identify
the Helm release and, for grants, project; the `linkerd-policy.gitops/owner`
annotation identifies `platform` or the project ID.

## Render, reconcile, and revoke

Keep platform inventory and individual project grants in separate values
files. Helm recursively merges maps; later files win for repeated scalar keys
and **replace entire lists**. One trusted release must reconcile the complete
inventory and all projects together:

```powershell
helm template mesh-policies .\chart\linkerd-policies --namespace policy-system -f .\platform.yaml -f .\payments.yaml -f .\analytics.yaml
helm upgrade --install mesh-policies .\chart\linkerd-policies --namespace policy-system --reset-values -f .\platform.yaml -f .\payments.yaml -f .\analytics.yaml
```

The release namespace must already exist. All resource target namespaces
must also exist; namespace creation is deliberately outside this chart.
Use `--reset-values` and the full current file set, not `--reuse-values`, so
deleted grants are not resurrected from a prior release. Removing a key from
one file does not remove the same key from another merged file. Removing all
grants removes authentication/authorization but leaves deny Servers rendered.

Validation is **not a security boundary against untrusted values files**:
Helm does not know which file owns a key. Protect platform files and release
credentials with repository review and RBAC; project contributors must not
override platform inventory or apply arbitrary policies.

Existing Linkerd policies can independently authorize traffic. Platform
operators must prevent conflicting/overlapping Servers and unauthorized
policies. This chart only denies selected ports on selected meshed pods; other
ports, unselected pods, traffic to unmeshed workloads, and mesh injection are outside its
scope. Verify meshing and effective policy separately before production use.

## Kept Servers: deliberate decommission and adoption

**Only Servers have `helm.sh/resource-policy: keep` and
`argocd.argoproj.io/sync-options: Prune=false,Delete=false`.** Removing a
destination from values, uninstalling the release, or pruning/deleting an
Argo CD application must not delete the Server and silently restore a more
permissive default for its port. Argo CD renders Helm charts without Helm
release lifecycle semantics, so it needs its own retention annotation.
Authentication and authorization resources have neither retention annotation
and delete normally.

For the repository's existing RabbitMQ migration, use the
[reviewed apply helper](../../service-mesh/policies.ps1). It also applies the
rendered intent after Helm reconciliation and compares live specs: Helm's
custom-resource merge patch alone can leave adopted or drifted state unchanged.

A kept Server becomes orphaned from Helm reconciliation when removed or
uninstalled. It is not automatically garbage-collected or continuously
repaired; preserve its deny configuration and track it in platform inventory.
Retention is not protection from manual deletion, edits, or independently
managed authorizations. Rollbacks can restore historical grants.

Platform-approved recovery procedure:

An adoption migration may pre-apply only the rendered `MeshTLSAuthentication`
resources, then the rendered `AuthorizationPolicy` resources. The migration
script must add `meta.helm.sh/release-name` and
`meta.helm.sh/release-namespace` annotations for the intended release; the
chart already supplies the `app.kubernetes.io/managed-by: Helm` label.
Do not pre-apply Servers in that step: preserve their existing deny coverage
and reconcile/adopt them through the reviewed platform procedure below.

1. Inspect the retained Server, its namespace, selector, port, protocol,
   `accessPolicy: deny`, and all policies targeting it. Keep the port denied
   throughout recovery; do not delete/recreate a live Server to clear a Helm
   ownership error.
2. Restore its exact destination ID and namespace to the authoritative
   inventory and render/review the complete release. Do not restore revoked
   grants. With the same release name/namespace, retained Helm ownership
   metadata normally permits adoption on the next install/upgrade.
3. If ownership differs, coordinate the old and new release owners first.
   After confirming the old release will no longer reconcile it, explicitly
   update the Server's `app.kubernetes.io/managed-by` label to `Helm` and
   `meta.helm.sh/release-name` / `meta.helm.sh/release-namespace` annotations
   to the intended release. Then install/upgrade that release and verify
   its manifest includes the Server and its live policy still denies.
4. For permanent decommission, first revoke grants and independently close
   the port (for example retire the workload or establish verified replacement
   protection). Remove the inventory entry, reconcile, and **explicitly**
   delete the retained Server only after platform approval. Treat namespace,
   port, or selector changes as migrations: keeping an old Server can conflict
   with a replacement, and changing an in-place Server can uncover old pods.

## Local validation (no cluster access)

```powershell
Push-Location .\service-mesh
npm ci
Pop-Location
helm lint .\chart\linkerd-policies --strict
node --test .\chart\linkerd-policies\tests\policies.test.js .\service-mesh\policy-documents.test.js
```

Tests use native `node:test`, `spawnSync('helm', ...)`, and the pinned `yaml`
parser in [service-mesh](../../service-mesh/package.json). The same parser lets
the local PowerShell workflow read both readable YAML and JSON manifests from
older Helm revisions. Node/npm are local tooling requirements only: installing
or rendering the chart with Helm does not require them.

Test values files are created beneath
the chart's tests directory and removed afterward. Tests validate rendering,
merge behavior, deny retention annotations, cross-namespace references,
protocols, name collision resistance, and invalid input with schema validation
enabled. They do not install CRDs or test live proxy enforcement.

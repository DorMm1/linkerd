# Linkerd policy GitOps

Same values-file model as [Keycloak GitOps](../keycloak-config-gitops/README.md),
but the output is native Kubernetes policy resources, not a realm import.
The [Helm chart](../chart/linkerd-policies) merges maps; Linkerd's policy
controllers enforce them dynamically. The Linkerd CLI checks health and lists
authorizations; it does not generate, apply, or prune application policies.
`linkerd prune` must not be used for this workflow.

## Ownership and scope

| File | Owner | Meaning |
| --- | --- | --- |
| `platform.yaml` | Platform | Protected destination pod selectors, namespaces, ports, protocols, and each project's permitted source namespaces/destinations |
| `project-demo.yaml` | Demo team + platform approval | Grants for the existing demo ServiceAccount |
| `project-a.yaml`, `project-b.yaml` | Corresponding team + platform approval | Empty grant maps, ready for real callers; no fictional access is deployed |

Protect platform configuration, the chart, CI and ownership rules with required
platform review. Project files are requests for access, not self-approved
entitlements. Restrict a team's changes to its own project key. Helm sees merged
values, not file provenance: a filename or chart validation is not a security
boundary. Use your Git host's protected-branch/CODEOWNERS rules and admission/
RBAC controls. This example does not configure repository protection remotely.

The platform bounds which source namespaces and destinations a project may
request. Only explicit, non-wildcard ServiceAccounts can be granted. Possession
of permission to create pods with an authorized ServiceAccount confers its mesh
identity; enforce Kubernetes RBAC accordingly.

The initial release protects only RabbitMQ 5672 and 15672. It does not change
Keycloak, Kafka, certificates, namespace injection, CRDs or workloads. Namespace
injection can remain in your platform namespace manifests independently.
Unmeshed traffic is denied even when it presents a valid JWT. Mesh grants do not
replace RabbitMQ JWT scopes or Kafka ACLs.

## Adding access

After platform approval of `project-a`'s namespaces and destinations:

```yaml
projects:
  project-a:
    grants:
      rabbitmq-amqp:
        serviceAccounts:
          - namespace: project-a
            name: order-worker
```

This creates an authorization in the RabbitMQ namespace referencing an exact
ServiceAccount in `project-a`. It does not create the account or mesh its pod.
Do not grant the management API to ordinary AMQP clients.

Each destination produces a default-deny `Server`. Each project/destination
grant produces a `MeshTLSAuthentication` and `AuthorizationPolicy`. Separate
authorizations are additive (OR); an unrelated broad policy can widen access.
The local apply helper refuses unmanaged authorizations in destination
namespaces so those must be reviewed rather than silently left in effect.
Another reconciler or cluster administrator can still create broader policies.

For Keycloak or Kafka, first inventory ingress, clients, peers/controllers,
probes, metrics, actual declared pod ports and labels. Add destinations and
grants only after that review. No HTTP routes, CIDR exceptions, wildcard
identities or cluster-wide defaults are implemented without a concrete need.
Use `protocol: unknown` for automatic detection; specify `opaque` when explicitly
describing a TCP Server. This does not bypass mTLS.

## Local workflow

The local helper requires Helm 3, kubectl, Windows PowerShell, Node.js 18+
and an installed Linkerd CLI. Its pinned YAML parser is used only on the
operator's machine, not in the cluster. Direct Helm chart usage needs no Node.js.
Validated against Linkerd edge-26.9.1: Server v1beta3, other policies v1alpha1.
This is not a recommendation to deploy an unsupported edge build to production.
Use the CRDs and supported version approved for your environment.

```powershell
# One-time local YAML parser setup (repeat after package-lock.json changes):
Push-Location .\service-mesh
npm ci
Pop-Location

# Offline schema/reference validation and rendering:
.\service-mesh\policies.ps1 -Action Validate
.\service-mesh\policies.ps1 -Action Render

# Explicitly select the target; the helper refuses a context mismatch.
kubectl config use-context docker-desktop
.\service-mesh\policies.ps1 -Action Diff -Context docker-desktop

# ONE-TIME migration of the two existing standalone RabbitMQ Servers:
.\service-mesh\policies.ps1 -Action Apply -Context docker-desktop -AdoptExistingServers

# Subsequent reconciliations; no workload restart needed:
.\service-mesh\policies.ps1 -Action Apply -Context docker-desktop
.\service-mesh\policies.ps1 -Action Verify -Context docker-desktop
.\service-mesh\verify-policies.ps1 -Context docker-desktop
.\rabbitmq-mesh-demo\verify.ps1
```

`-ValuesDirectory` selects another directory containing `platform.yaml` and
`project-*.yaml`. The helper passes all files in sorted order and uses
`--reset-values`, not `--reuse-values`, so removed grants do not persist in
Helm values. Render/diff contain no credentials. Diff does not represent Helm
pruning: use the Helm release manifest/revision history to review removed
resources as well.

Helm owns resource lifecycle and prunes removed grants. The apply helper then
uses `kubectl apply` on the same rendered resources to converge live specs:
Helm's merge patch for custom resources can otherwise leave adopted state or
out-of-band drift unchanged when the recorded and desired manifests match.
Verification compares every live policy spec, not just Helm's success status.

`verify-policies.ps1` creates two temporary meshed Node probe pods: one with the
allowed account and one with a new, unlisted account. It checks RabbitMQ HTTP
401 versus Linkerd 403, and AMQP Connection.Start versus closed connection with
a broker denial-counter increment. It deletes its own pods/account afterward.
It is specific to this repository's local RabbitMQ names. Existing demo
verification additionally checks JWT happy/sad flows and unmeshed denial.
Broker counters are shared supporting evidence, not unique packet attribution.

Offline regression command (Node's built-in runner, after the parser setup):

```powershell
node --test .\chart\linkerd-policies\tests\policies.test.js .\service-mesh\policy-documents.test.js .\rabbitmq-mesh-demo\flows.test.js
```

Local validation on 2026-09-09: 54 tests passed; allowed and unlisted meshed
identities were checked on HTTP and AMQP; all six comparison-demo flows passed.
A temporary project's grants were added, exercised, removed and confirmed
pruned, with the same meshed pod denied on new connections afterward. In an
isolated namespace, removing a destination and uninstalling Helm both retained
its deny Server. Repeated apply converged with an empty diff. RabbitMQ/demo pod
UIDs and the two original Server UIDs did not change.

### First migration and recovery

The helper validates selectors, port, protocol, existing ownership and the old
`cluster-authenticated` policy before adoption. It performs server-side dry-run,
backs up the original Servers under `~/.linkerd2/policy-backups`, creates exact
grants, then adopts the same Server names into Helm and changes their fallback
to `deny`. It does not delete/recreate Servers or restart the broker.

Kubernetes reconciliation is not atomic across multiple objects; a brief denial
is possible while proxies observe changes. Schedule production changes
accordingly. The helper does not silently roll back to broad access on failure.
If interrupted, inspect the Helm release, ownership and Linkerd authorizations;
fix the reported error and rerun. Migration snapshots are for reviewed recovery,
not automatic restoration of broad access.
Automatic adoption is limited to the two original RabbitMQ demo Servers with
their exact selectors/ports. Other installations require an explicit migration.
Pre-created grants missing from both desired values and Helm's recorded manifest
are rejected as orphaned migration grants; review and remove them explicitly
before retrying so an interrupted migration cannot silently retain revoked access.

### Revocation, deletion and rollback

- Remove a ServiceAccount or grant, then reconcile. Helm updates/deletes only
  release-owned grant resources. Test a **new connection** afterward; do not
  assume already-established TCP sessions were terminated.
- Servers carry `helm.sh/resource-policy: keep`. Removing a destination from
  values or uninstalling the release keeps that Server default-deny rather than
  restoring a permissive namespace fallback. Inspect retained Servers explicitly;
  they are no longer reconciled if removed from the chart's rendered output.
- If using Argo CD directly as a Helm renderer, also configure its equivalent
  no-prune/no-delete protection for Server resources (see below). Helm's
  annotation alone is not a universal GitOps-controller deletion policy.
- Decommissioning a Server requires platform review of namespace fallback and
  remaining grants. Remove grants first, then explicitly delete the retained
  Server only when reopening/unprotecting the port is intentional.
- Renaming destinations changes Server identity and can cause overlapping
  selectors; do not rename a live destination as an ordinary edit. Plan its
  migration separately.
- Helm history/rollback can restore an earlier grant set. Review that set first:
  rolling back revocation re-grants access. Initial pre-Helm state is not a Helm
  revision. Do not delete CRDs or uninstall Linkerd to roll back an access grant.

## Continuous GitOps

Like the Keycloak example, the directory/chart are ready for a reconciler;
running a PowerShell command alone is **not continuous GitOps**.

- Flux HelmRelease: point to this chart, enumerate these files in `valuesFiles`,
  and use normal Helm upgrade/uninstall behavior, not `preserveValues`.
- Argo CD: point to this chart and enumerate `helm.valueFiles`. Argo uses Helm
  for rendering, not Helm release reconciliation. Before enabling pruning,
  configure retained Server resources with
  `argocd.argoproj.io/sync-options: Prune=false,Delete=false`. The chart includes
  this resource-level protection. Require review before removing it.
- Use one owner only: do not have Helm CLI and Argo CD independently manage the
  same policy objects. Adopt/migrate ownership before switching reconcilers.
- Adding a new project to a controller's explicit values-file list requires
  updating that list. The local helper discovers `project-*.yaml` automatically.
- Run schema/render tests, server-side validation in a test cluster, then real
  allowed/disallowed traffic tests before promotion. Scope the reconciler's RBAC
  to policy resource types and approved destination namespaces; no cluster-admin
  permission is required for routine policy changes.

No Argo CD/Flux installation, remote repository or automatic sync is created by
this local rollout. Those need your actual controller, repository URL and
promotion/approval configuration.

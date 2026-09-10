# linkerd-policies chart

Values in, Linkerd policies out. The values are the files of the catalog
([linkerd-catalog/](../linkerd-catalog/) here; your on-prem repository later):
`platform.yaml` plus `projects/<project>/<namespace>.yaml`. Argo CD passes each
one as a `--values` file and Helm merges them into a single `namespaces` map.

- Writing the files: [catalog README](../linkerd-catalog/README.md)
- Installing Linkerd and this chart: [../README.md](../README.md)

## Input

```yaml
namespaces:
  <namespace>:
    team: <team>                          # label linkerd-policies/team on every generated resource
    allowSameNamespace: false             # optional
    workloads:
      <workload>:
        ports:                            # every port the pods listen on
          <port name>: <number>
          <port name>: { port: <number>, protocol: unknown|HTTP/1|HTTP/2|gRPC|opaque|TLS }
        publish: [<port name>]            # optional: ports other namespaces may request
        connectsTo: [<namespace>/<workload>/<port name>]   # optional
        serviceAccount: <name>            # optional, default <workload>
        podLabels: { <label>: <value> }   # optional, default app.kubernetes.io/name: <workload>
```

Shapes are enforced by [values.schema.json](values.schema.json) (Helm rejects
unknown fields, bad names, ports outside 1-65535, unknown protocols).
[_helpers.tpl](templates/_helpers.tpl) then checks what a schema cannot and
fails the render with the offending path when:

- a workload declares the same port number twice;
- `<workload>-<port name>` is longer than 63 characters;
- `publish` names a port the workload does not have;
- `connectsTo` names a port that does not exist, or one in **another namespace
  that is not published**.

## Output: what one port becomes

For every port, in the target's namespace, all named `<workload>-<port name>`:

| Resource | Purpose |
| --- | --- |
| `Server` (`policy.linkerd.io/v1beta3`) | Selects the pods and the port; `accessPolicy: deny` blocks anyone not listed below. |
| `MeshTLSAuthentication` (`v1alpha1`) | The allowed identities: the whole namespace when `allowSameNamespace: true`, plus the ServiceAccounts that listed the port in `connectsTo`. Omitted when nobody is allowed. |
| `AuthorizationPolicy` (`v1alpha1`) | "Traffic to this Server must carry one of these identities." Omitted when nobody is allowed. |

```yaml
# linkerd-catalog/platform.yaml  ->  namespace rabbitmq
apiVersion: policy.linkerd.io/v1beta3
kind: Server
metadata: { name: rabbitmq-amqp, namespace: rabbitmq }
spec:
  podSelector: { matchLabels: { app.kubernetes.io/name: rabbitmq } }
  port: 5672
  proxyProtocol: opaque
  accessPolicy: deny
---
apiVersion: policy.linkerd.io/v1alpha1
kind: MeshTLSAuthentication
metadata: { name: rabbitmq-amqp, namespace: rabbitmq }
spec:
  identityRefs:
    - { kind: Namespace, name: rabbitmq }                          # allowSameNamespace: true
    - { kind: ServiceAccount, name: demo-api, namespace: demo }    # projects/demo/demo.yaml connectsTo
---
apiVersion: policy.linkerd.io/v1alpha1
kind: AuthorizationPolicy
metadata: { name: rabbitmq-amqp, namespace: rabbitmq }
spec:
  targetRef: { group: policy.linkerd.io, kind: Server, name: rabbitmq-amqp }
  requiredAuthenticationRefs:
    - { group: policy.linkerd.io, kind: MeshTLSAuthentication, name: rabbitmq-amqp }
```

`kubectl get server,meshtlsauthentication,authorizationpolicy -A -l linkerd-policies/team=demo`
shows one team's policies.

Semantics worth knowing:

- A port with no `publish` and no `connectsTo` pointing at it is still a
  `Server` with `deny`: declaring a port protects it.
- `allowSameNamespace` is a chart feature, not a Linkerd annotation; Linkerd
  itself has no "same namespace" default policy. It works because every declared
  port has a `Server`, so the namespace-identity grant has something to attach to.
- A local `connectsTo` inside an `allowSameNamespace: true` namespace adds
  nothing; inside a closed namespace it grants that one ServiceAccount.
- Two catalog files declaring the same namespace would be merged by Helm;
  [tests/test_catalog.py](../tests/test_catalog.py) forbids that (one project
  file per namespace, named after it; platform namespaces only in `platform.yaml`).

## Limits

The chart cannot see the cluster. That the ServiceAccount and pod labels match
the real workloads, and that each namespace carries
`linkerd.io/inject: enabled` and `config.linkerd.io/default-inbound-policy: deny`,
is the platform's job.

## Layout

```text
linkerd-policies/
  Chart.yaml
  values.yaml              defaults (namespaces: {}) and a commented example
  values.schema.json       validation, also used for editor autocompletion in the catalog
  templates/
    _helpers.tpl           builds one "target" per port, checks references
    policies.yaml          renders Server / MeshTLSAuthentication / AuthorizationPolicy
```

Tests: [../tests](../tests), `python -m pytest`.

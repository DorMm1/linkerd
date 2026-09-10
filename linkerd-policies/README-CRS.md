# Understanding the Linkerd policy CRs

This guide explains the Kubernetes resources behind this chart.
For values-file structure and deployment commands, see the [chart README](README.md).
For team ownership and day-to-day changes, see the [GitOps runbook](../../linkerd-policy-gitops/README.md).

A **CRD** installs a resource type into Kubernetes. A **CR** is an instance of
that type. Linkerd installs the policy CRDs; this chart creates policy CRs.
These resources configure the existing Linkerd proxies. They do not deploy
RabbitMQ, inject proxies, or issue certificates.

## The three resources in one sentence each

| Resource | Question it answers | Example |
| --- | --- | --- |
| `Server` | Which destination pods and port are protected? | RabbitMQ pods on port 5672 |
| `MeshTLSAuthentication` | Which mesh identities can satisfy this authentication requirement? | The `project-a/order-worker` ServiceAccount |
| `AuthorizationPolicy` | Which identities are allowed to reach which destination? | Allow that worker to reach that RabbitMQ port |

```text
Source pod: project-a/order-worker
                  |
             Linkerd mTLS
                  |
                  v
Destination proxy checks:
  Server                  -> selects RabbitMQ :5672
  AuthorizationPolicy     -> requires the identity set below
  MeshTLSAuthentication   -> includes project-a/order-worker
                  |
                  v
RabbitMQ checks its own credentials and permissions
```

The examples below form one connected example. They illustrate a hypothetical
project grant, not an additional grant already deployed. Names are shortened
for readability, and chart ownership/retention annotations are omitted.
**Do not apply these examples alongside the chart.** Edit the values instead.

## 1. Server: define the protected destination

```yaml
apiVersion: policy.linkerd.io/v1beta3
kind: Server
metadata:
  name: rabbitmq-amqp
  namespace: rabbitmq
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: rabbitmq
  port: 5672
  proxyProtocol: opaque
  accessPolicy: deny
```

| Field | Meaning |
| --- | --- |
| `metadata.namespace` | The namespace containing the destination pods. |
| `podSelector.matchLabels` | Labels on the **pods**, not the Kubernetes Service or just the Deployment's metadata. |
| `port` | The protected destination pod port. It must be declared in the pod's container ports. This chart uses numeric ports. |
| `proxyProtocol` | How Linkerd handles the application protocol on that port. |
| `accessPolicy: deny` | Reject traffic that is not allowed by an associated authorization. |

Despite its name, a `Server` does not create a server process, listener, Service,
or load balancer. It selects an existing destination for inbound policy.
It can select multiple replicas. Two Servers must not select the same pod/port
pair, even if they have different resource names.

### What does `opaque` mean?

It means **proxy this as TCP without HTTP protocol detection**. It does not
bypass the proxy or disable mesh encryption.

Our RabbitMQ AMQP Server uses `opaque`; the separate management Server uses
`HTTP/1` on port 15672. The chart also supports `unknown` for automatic protocol
detection. A Server's explicit protocol supersedes the pod's opaque-port
annotation for that selected port.

`proxyProtocol: TLS` describes application TLS traffic. It does not enable
Linkerd mTLS or configure an application certificate.

### Why explicitly set `deny`?

It makes the fallback clear: no matching grant means no access on that port.
Using `cluster-authenticated` instead would allow other authenticated identities
from the same cluster even when they are absent from our project allowlists.

Ports without a matching Server use their applicable default policy. Protecting
5672 does not automatically protect RabbitMQ's other ports.

Source: [server.yaml](templates/server.yaml).

## 2. MeshTLSAuthentication: identify the allowed callers

```yaml
apiVersion: policy.linkerd.io/v1alpha1
kind: MeshTLSAuthentication
metadata:
  name: orders-workers
  namespace: rabbitmq
spec:
  identityRefs:
    - kind: ServiceAccount
      name: order-worker
      namespace: project-a
```

Notice the two namespaces:

- `metadata.namespace: rabbitmq`: where this authentication resource lives.
- `identityRefs[].namespace: project-a`: where the caller's ServiceAccount lives.

The caller must present a Linkerd mesh identity corresponding to that
ServiceAccount. Merely sending a ServiceAccount name in a header, or presenting
a JWT from that account, does not satisfy this requirement.

Several entries in `identityRefs` mean **any one** of those identities can match.
The chart uses ServiceAccount references rather than hand-written certificate
identity strings, so values do not need to construct the Linkerd trust-domain
suffix.

This CR does **not**:

- Create the ServiceAccount or inject its pods.
- Issue or renew certificates.
- Grant access by itself; an AuthorizationPolicy must reference it.
- Validate Keycloak JWTs.

Source: [meshtlsauthentication.yaml](templates/meshtlsauthentication.yaml).

## 3. AuthorizationPolicy: connect the destination and callers

```yaml
apiVersion: policy.linkerd.io/v1alpha1
kind: AuthorizationPolicy
metadata:
  name: orders-to-rabbitmq
  namespace: rabbitmq
spec:
  targetRef:
    group: policy.linkerd.io
    kind: Server
    name: rabbitmq-amqp
  requiredAuthenticationRefs:
    - group: policy.linkerd.io
      kind: MeshTLSAuthentication
      name: orders-workers
```

Read it as:

> Allow traffic to `rabbitmq-amqp` when the client matches `orders-workers`.

The policy, its target Server, and the referenced MeshTLSAuthentication live
in the destination namespace. The source ServiceAccount can be in another
namespace, as shown above.

Two rules matter when reviewing access:

- Multiple requirements **inside one** `requiredAuthenticationRefs` list are
  **AND**: every requirement must match.
- Separate authorization policies granting access to the same target are
  **OR**: another policy can independently allow the connection.

Consequently, adding a narrow policy does not cancel an existing broad grant.
Our apply helper checks for unmanaged authorizations before applying changes.

Linkerd also supports a direct ServiceAccount authentication reference. This
chart consistently uses MeshTLSAuthentication so a grant can contain several
ServiceAccounts as alternatives, rather than treating separate requirements
as an AND.

Source: [authorizationpolicy.yaml](templates/authorizationpolicy.yaml).

## How the values become these resources

| Values entry | Generated resources |
| --- | --- |
| One `platform.destinations` entry | One Server in the destination namespace |
| One `projects.<project>.grants.<destination>` entry | One MeshTLSAuthentication plus one AuthorizationPolicy in that namespace |
| Several ServiceAccounts in a grant | Several identity references in its MeshTLSAuthentication |
| A project with `grants: {}` | No authentication or authorization resources |

The current [platform values](../../linkerd-policy-gitops/platform.yaml) define
two destinations: RabbitMQ AMQP and management. The
[demo project](../../linkerd-policy-gitops/project-demo.yaml) grants the existing
`rabbitmq/rabbitmq-spa-demo` account access to both.
That produces **two Servers, two MeshTLSAuthentications and two AuthorizationPolicies**.

Removing a grant removes its authentication/authorization resources on sync,
but leaves the Server denying unapproved access. The chart retains Servers
when removed from values or on uninstall to avoid silently restoring a more
permissive namespace fallback. See the [decommission procedure](README.md#kept-servers-deliberate-decommission-and-adoption).
Test revocation with new connections; do not assume existing TCP sessions
were terminated.

## What these policies do not replace

| Layer | Responsibility |
| --- | --- |
| Namespace/pod injection annotations | Put the workload into the mesh. |
| Linkerd identity and certificates | Authenticate and encrypt proxy-to-proxy connections. |
| These policy CRs | Decide which callers may reach selected inbound ports. |
| RabbitMQ JWT scopes / Kafka ACLs / application authorization | Decide what an accepted caller may do. |
| Kubernetes RBAC and network controls | Control workload creation, administrative access and broader network boundaries. |

An allowed mesh identity can still receive RabbitMQ **401** because its JWT is
missing or insufficient. A disallowed identity can receive Linkerd **403** on
the management HTTP port before RabbitMQ evaluates the JWT. An AMQP denial is
a transport failure, not an HTTP response.

Both happy and sad JWT flows in our SPA use the same SPA mesh identity.
Linkerd permits that pod to connect; RabbitMQ distinguishes their JWT scopes.

These policies cannot protect a destination that has no Linkerd proxy, and
they are not a complete Kubernetes NetworkPolicy replacement. Namespace
injection alone also does not establish an allowlist.

## Other policy CRs you may encounter

These are not generated by this chart:

| Resource | When it is useful |
| --- | --- |
| `HTTPRoute` | Match HTTP paths/methods and authorize only a portion of an HTTP API instead of an entire Server. It cannot express RabbitMQ queue permissions or Kafka topic ACLs. |
| `NetworkAuthentication` | Match source IP networks/CIDRs, for example a reviewed non-meshed caller. IP matching alone is not mTLS; combine requirements if both identity and network restrictions are needed. |
| `ServerAuthorization` | The older Server-focused authorization resource. Linkerd supports it, but AuthorizationPolicy is the more flexible model used here. Existing ServerAuthorizations can still grant access and must be included in reviews. |

Only introduce these when there is a concrete requirement. Check the installed
Linkerd/Gateway API versions before choosing an HTTPRoute API version.

## Inspect the deployed policy

These commands are read-only:

```powershell
kubectl --context docker-desktop -n rabbitmq get servers,meshtlsauthentications,authorizationpolicies
kubectl --context docker-desktop -n rabbitmq get server rabbitmq-amqp -o yaml

& "$HOME\.linkerd2\bin\linkerd.exe" --context docker-desktop authz -n rabbitmq pod/rabbitmq-server-0
```

`linkerd authz` shows which authorizations apply to a workload; it does not
prove a real connection succeeded. The [policy verifier](../../service-mesh/verify-policies.ps1)
exercises allowed and unlisted meshed identities on HTTP and AMQP.

The API versions above match this chart's tested Linkerd edge-26.9.1 installation,
not a promise of compatibility with every Linkerd release.

## Official references

- [Authorization policy and CR field reference](https://linkerd.io/docs/reference/authorization-policy/)
- [Authorization policy behavior](https://linkerd.io/docs/features/server-policy/)
- [Automatic mTLS](https://linkerd.io/docs/features/automatic-mtls/)
- [Protocol detection and opaque ports](https://linkerd.io/docs/features/protocol-detection/)

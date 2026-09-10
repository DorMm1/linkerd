# linkerd-catalog (example)

**Who may talk to what** in the cluster. This folder is an example of the
catalog repository that lives in your on-prem Git; Argo CD reads it and turns
every file into Linkerd policies. Nothing not written here is allowed.

You do not need to know Linkerd. Edit your project's file, open a PR, and once it
is merged the cluster follows.

```text
linkerd-catalog/
  platform.yaml               DevOps: the 3rd-party apps everyone uses (rabbitmq, kafka, redis,
                              linux-mssql, mongodb, imply)
  projects/
    demo/demo.yaml            project demo, namespace demo
    demo/demo-preview-123.yaml   ...and one of its preview namespaces
    project-a/project-a.yaml  project project-a, namespace project-a
```

Rules of the road:

- **DevOps** owns `platform.yaml`; **each project** owns `projects/<project>/`
  and has one file per namespace, named after the namespace;
- both sides must agree: a port is reachable from another namespace only when its
  owner **publishes** it *and* the caller lists it in **connectsTo**;
- everything else is denied.

## The whole format

```yaml
namespaces:
  <namespace>:                        # same as the file name
    team: <project>                   # same as the folder name
    allowSameNamespace: true          # optional, default false

    workloads:
      <workload>:                     # your Deployment / StatefulSet name
        ports:                        # every port the pods listen on
          <port name>: <number>
          <port name>: { port: <number>, protocol: opaque }
        publish: [<port name>, ...]   # ports other namespaces may ask for
        connectsTo:                   # ports this workload needs to reach
          - <namespace>/<workload>/<port name>
```

| Field | Meaning |
| --- | --- |
| `allowSameNamespace` | `true`: workloads in this namespace can call each other on every declared port, no `connectsTo` needed. `false`: even neighbours must be listed in `connectsTo`. |
| `ports` | Declare **all** ports, also private ones (metrics, health...). Undeclared ports are unreachable for everyone. |
| `protocol` | Only for non-HTTP traffic: `opaque` for databases, AMQP, Kafka, Redis... Otherwise leave it out. Allowed: `unknown` (default), `HTTP/1`, `HTTP/2`, `gRPC`, `opaque`, `TLS`. |
| `publish` | Which of your ports **other namespaces** may request. Not listed = private to your namespace. |
| `connectsTo` | What you need. Works only when the target port is published, or is in your own namespace. |

Two assumptions about your pods, both overridable per workload:

| Default | Override when it does not hold |
| --- | --- |
| Pods run as a **ServiceAccount named like the workload** | `serviceAccount: <name>` |
| Pods carry the label **`app.kubernetes.io/name: <workload>`** | `podLabels: { <label>: <value> }` |

Permissions are granted to the ServiceAccount, so every workload that needs its
own permissions needs its own ServiceAccount.

## Example

Your API calls RabbitMQ and another project's API; your UI only calls your API:

```yaml
# projects/demo/demo.yaml
namespaces:
  demo:
    team: demo
    allowSameNamespace: true

    workloads:
      demo-ui:
        ports:
          http: 3000

      demo-api:
        ports:
          http: 8080
          grpc: { port: 9090, protocol: gRPC }
        publish: [http]
        connectsTo:
          - rabbitmq/rabbitmq/amqp
          - project-a/orders-api/http
```

- Let another namespace call you: add the port to `publish`; they add
  `<your namespace>/<your workload>/<port>` to their `connectsTo`.
- Revoke: remove the line.
- New namespace (also a preview/ephemeral one): add a file. Remove the
  namespace: delete the file. Argo CD picks both up on its own.

## Shared infrastructure

Defined in [platform.yaml](platform.yaml), owned by DevOps. Ask for it like any
other namespace:

| Need | `connectsTo` entry |
| --- | --- |
| RabbitMQ | `rabbitmq/rabbitmq/amqp` |
| Kafka | `kafka/kafka/clients` |
| Redis | `redis/redis/redis` |
| SQL Server | `linux-mssql/mssql/sql` |
| MongoDB | `mongodb/mongodb/mongodb` |
| Imply / Druid broker | `imply/imply-broker/http` |

## Before your first policy works

DevOps prepares the namespace **before the pods start**:

```yaml
metadata:
  annotations:
    linkerd.io/inject: enabled                       # a Linkerd proxy in every pod
    config.linkerd.io/default-inbound-policy: deny   # block everything not declared here
```

## Checking a change

From the repository root:

```powershell
python -m pytest                                                # conventions + full render
python render_catalog.py | Out-File .\test.yaml -Encoding utf8  # the policies for the whole catalog
```

A mistake fails the render and names the field, for example
`namespaces.demo.workloads.demo-api.connectsTo: "project-a/orders-api/metrics" is not published to other namespaces`.

## Moving this to its own repository

When the catalog lives in your on-prem Git, keep the same layout, add a
`CODEOWNERS` (DevOps for `platform.yaml`, each project for its folder) and point
the second source of [argocd/linkerd-policies.yaml](../argocd/linkerd-policies.yaml)
at that repository. Nothing else changes.

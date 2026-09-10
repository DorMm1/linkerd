"""What the chart renders for valid input."""

import textwrap

from conftest import CHART, helm, render

# A shared service, a team namespace that uses it, and a second team that publishes an API.
CATALOG = [
    textwrap.dedent("""
        namespaces:
          rabbitmq:
            team: platform
            allowSameNamespace: true
            workloads:
              rabbitmq:
                ports:
                  amqp: { port: 5672, protocol: opaque }
                  management: { port: 15672, protocol: HTTP/1 }
                publish: [amqp]
    """),
    textwrap.dedent("""
        namespaces:
          redis:
            team: platform
            workloads:
              redis:
                ports:
                  redis: { port: 6379, protocol: opaque }
                publish: [redis]
    """),
    textwrap.dedent("""
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
    """),
    textwrap.dedent("""
        namespaces:
          demo-preview-123:
            team: demo
            allowSameNamespace: true
            workloads:
              demo-api:
                ports:
                  http: 8080
                connectsTo:
                  - rabbitmq/rabbitmq/amqp
    """),
    textwrap.dedent("""
        namespaces:
          project-a:
            team: project-a
            allowSameNamespace: true
            workloads:
              orders-api:
                podLabels: { app: orders }
                ports:
                  http: 8080
                  metrics: 9090
                publish: [http]
    """),
]


def test_chart_lints_cleanly():
    result = helm("lint", str(CHART), "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_values_render_nothing():
    assert helm("template", "linkerd-policies", str(CHART)).docs == []


def test_every_declared_port_becomes_a_deny_server(tmp_path):
    servers = render(tmp_path, *CATALOG).of_kind("Server")
    assert set(servers) == {
        ("rabbitmq", "rabbitmq-amqp"),
        ("rabbitmq", "rabbitmq-management"),
        ("redis", "redis-redis"),
        ("demo", "demo-ui-http"),
        ("demo", "demo-api-http"),
        ("demo", "demo-api-grpc"),
        ("demo-preview-123", "demo-api-http"),
        ("project-a", "orders-api-http"),
        ("project-a", "orders-api-metrics"),
    }
    for server in servers.values():
        assert server["apiVersion"] == "policy.linkerd.io/v1beta3"
        assert server["spec"]["accessPolicy"] == "deny"


def test_server_describes_the_declared_port(tmp_path):
    rendered = render(tmp_path, *CATALOG)
    server = rendered.of_kind("Server")[("rabbitmq", "rabbitmq-amqp")]
    assert server["spec"] == {
        "podSelector": {"matchLabels": {"app.kubernetes.io/name": "rabbitmq"}},
        "port": 5672,
        "proxyProtocol": "opaque",
        "accessPolicy": "deny",
    }
    assert server["metadata"]["labels"] == {
        "app.kubernetes.io/name": "linkerd-policies",
        "app.kubernetes.io/instance": "linkerd-policies",
        "app.kubernetes.io/managed-by": "Helm",
        "linkerd-policies/team": "platform",
    }
    assert rendered.of_kind("Server")[("demo", "demo-ui-http")]["spec"]["proxyProtocol"] == "unknown"
    assert rendered.of_kind("Server")[("demo", "demo-api-grpc")]["spec"]["proxyProtocol"] == "gRPC"


def test_pod_labels_override_replaces_the_default_selector(tmp_path):
    server = render(tmp_path, *CATALOG).of_kind("Server")[("project-a", "orders-api-http")]
    assert server["spec"]["podSelector"] == {"matchLabels": {"app": "orders"}}


def test_published_port_admits_exactly_the_workloads_that_asked(tmp_path):
    rendered = render(tmp_path, *CATALOG)
    assert rendered.identities("rabbitmq", "rabbitmq-amqp") == {
        "Namespace:rabbitmq",
        "ServiceAccount:demo/demo-api",
        "ServiceAccount:demo-preview-123/demo-api",
    }
    assert rendered.identities("project-a", "orders-api-http") == {
        "Namespace:project-a",
        "ServiceAccount:demo/demo-api",
    }


def test_unpublished_ports_stay_inside_their_namespace(tmp_path):
    rendered = render(tmp_path, *CATALOG)
    assert rendered.identities("rabbitmq", "rabbitmq-management") == {"Namespace:rabbitmq"}
    assert rendered.identities("project-a", "orders-api-metrics") == {"Namespace:project-a"}


def test_the_ui_never_inherits_the_api_permissions(tmp_path):
    for authn in render(tmp_path, *CATALOG).of_kind("MeshTLSAuthentication").values():
        assert all(ref.get("name") != "demo-ui" for ref in authn["spec"]["identityRefs"])


def test_port_nobody_may_reach_is_just_a_deny_server(tmp_path):
    rendered = render(tmp_path, *CATALOG)
    assert ("redis", "redis-redis") in rendered.of_kind("Server")
    assert ("redis", "redis-redis") not in rendered.of_kind("MeshTLSAuthentication")
    assert ("redis", "redis-redis") not in rendered.of_kind("AuthorizationPolicy")


def test_same_namespace_access_uses_the_namespace_identity(tmp_path):
    rendered = render(tmp_path, *CATALOG)
    assert rendered.identities("demo", "demo-ui-http") == {"Namespace:demo"}
    assert rendered.identities("demo", "demo-api-grpc") == {"Namespace:demo"}
    # A preview namespace is a different namespace: nothing from "demo" leaks into it.
    assert rendered.identities("demo-preview-123", "demo-api-http") == {"Namespace:demo-preview-123"}


def test_a_closed_namespace_needs_explicit_local_requests(tmp_path):
    closed = textwrap.dedent("""
        namespaces:
          alpha:
            team: alpha
            workloads:
              api:
                ports: { http: 8080, metrics: 9090 }
              worker:
                connectsTo: [alpha/api/metrics]
    """)
    rendered = render(tmp_path, closed)
    assert rendered.identities("alpha", "api-metrics") == {"ServiceAccount:alpha/worker"}
    assert rendered.identities("alpha", "api-http") == set()


def test_local_requests_add_nothing_when_the_namespace_is_open(tmp_path):
    open_ns = textwrap.dedent("""
        namespaces:
          alpha:
            team: alpha
            allowSameNamespace: true
            workloads:
              api:
                ports: { http: 8080 }
              worker:
                connectsTo: [alpha/api/http]
    """)
    assert render(tmp_path, open_ns).identities("alpha", "api-http") == {"Namespace:alpha"}


def test_service_account_override_is_what_gets_authorized(tmp_path):
    caller = textwrap.dedent("""
        namespaces:
          beta:
            team: beta
            workloads:
              worker:
                serviceAccount: batch-runner
                connectsTo: [redis/redis/redis]
              cron:
                serviceAccount: batch-runner
                connectsTo: [redis/redis/redis]
    """)
    rendered = render(tmp_path, CATALOG[1], caller)
    refs = rendered.of_kind("MeshTLSAuthentication")[("redis", "redis-redis")]["spec"]["identityRefs"]
    assert refs == [{"kind": "ServiceAccount", "name": "batch-runner", "namespace": "beta"}]


def test_every_authorization_links_one_server_to_one_authentication(tmp_path):
    rendered = render(tmp_path, *CATALOG)
    servers = rendered.of_kind("Server")
    authns = rendered.of_kind("MeshTLSAuthentication")
    policies = rendered.of_kind("AuthorizationPolicy")
    assert set(authns) == set(policies) and set(policies) <= set(servers)
    for (namespace, name), policy in policies.items():
        assert policy["apiVersion"] == "policy.linkerd.io/v1alpha1"
        assert policy["spec"] == {
            "targetRef": {"group": "policy.linkerd.io", "kind": "Server", "name": name},
            "requiredAuthenticationRefs": [
                {"group": "policy.linkerd.io", "kind": "MeshTLSAuthentication", "name": name}
            ],
        }
        assert authns[(namespace, name)]["spec"]["identityRefs"]


def test_only_linkerd_policy_resources_are_rendered(tmp_path):
    kinds = {doc["kind"] for doc in render(tmp_path, *CATALOG).docs}
    assert kinds == {"Server", "MeshTLSAuthentication", "AuthorizationPolicy"}


def test_a_namespace_without_workloads_renders_nothing(tmp_path):
    assert render(tmp_path, "namespaces:\n  empty:\n    team: t\n    workloads: {}\n").docs == []

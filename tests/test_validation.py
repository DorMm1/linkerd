"""Broken input must fail the render with a message that says what to fix."""

import textwrap

import pytest

from conftest import render

API = textwrap.dedent("""
    namespaces:
      alpha:
        team: team-a
        allowSameNamespace: true
        workloads:
          api:
            ports:
              http: 8080
              metrics: 9090
            publish: [http]
""")


def failing(tmp_path, message, *documents):
    result = render(tmp_path, *documents)
    assert result.returncode != 0, "render should have failed"
    assert message in result.stderr, result.stderr
    return result


def workload(snippet: str) -> str:
    return textwrap.dedent(f"""
        namespaces:
          beta:
            team: team-b
            workloads:
              worker:
                {snippet}
    """)


def test_asking_for_an_unpublished_port_from_another_namespace_fails(tmp_path):
    result = failing(tmp_path, "is not published to other namespaces", API, workload("connectsTo: [alpha/api/metrics]"))
    assert "namespaces.beta.workloads.worker.connectsTo" in result.stderr
    assert 'its owners (team team-a) can add "metrics" to \'publish\'' in result.stderr


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("connectsTo: [alpha/api/nope]", '"alpha/api/nope" is not declared'),
        ("connectsTo: [nowhere/api/http]", '"nowhere/api/http" is not declared'),
        ("connectsTo: [alpha/api]", "connectsTo.0: Does not match pattern"),
        ("connectsTo: alpha/api/http", "connectsTo: Invalid type. Expected: array"),
        ("connectsTo: [alpha/api/http, alpha/api/http]", "connectsTo: array items[0,1] must be unique"),
        ("connectsTo: [42]", "connectsTo.0: Invalid type. Expected: string"),
    ],
)
def test_bad_connections_are_rejected(tmp_path, snippet, message):
    failing(tmp_path, message, API, workload(snippet))


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("ports: {http: 0}", "ports.http: Must be greater than or equal to 1"),
        ("ports: {http: 70000}", "ports.http: Must be less than or equal to 65535"),
        ("ports: {http: 8080.5}", "ports.http: Invalid type. Expected: integer, given: number"),
        ("ports: {http: '8080'}", "ports.http: Invalid type. Expected: integer, given: string"),
        ("ports: {http: {protocol: gRPC}}", "ports.http: port is required"),
        ("ports: {http: {port: 8080, protocol: grpc}}", 'protocol must be one of the following: "unknown", "HTTP/1", "HTTP/2", "gRPC", "opaque", "TLS"'),
        ("ports: {http: {port: 8080, proto: gRPC}}", "ports.http: Additional property proto is not allowed"),
        ("ports: {http: 8080, web: 8080}", 'port 8080 is already declared as "http"'),
        ("ports: {Http: 8080}", 'ports: Property name of "Http" does not match'),
        ("ports: [8080]", "ports: Invalid type. Expected: object, given: array"),
        ("ports: {http: 8080}\n    publish: [https]", 'publish: "https" is not one of this workload\'s ports (http)'),
        ("ports: {http: 8080}\n    publish: http", "publish: Invalid type. Expected: array, given: string"),
        ("ports: {http: 8080}\n    expose: [http]", "api: Additional property expose is not allowed"),
        ("ports: {http: 8080}\n    serviceAccount: Bad_Name", "serviceAccount: Does not match pattern"),
        ("ports: {http: 8080}\n    podLabels: {}", "podLabels: Must have at least 1 properties"),
        ("ports: {http: 8080}\n    podLabels: {app: 1}", "podLabels.app: Invalid type. Expected: string, given: integer"),
    ],
)
def test_bad_workload_fields_are_rejected(tmp_path, snippet, message):
    body = textwrap.dedent(f"""
        namespaces:
          alpha:
            team: team-a
            workloads:
              api:
                {snippet.replace(chr(10), chr(10) + "            ")}
    """)
    failing(tmp_path, message, body)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("namespaces:\n  alpha:\n    team: team-a\n    allowSameNamespace: yes please\n    workloads: {}\n",
         "allowSameNamespace: Invalid type. Expected: boolean, given: string"),
        ("namespaces:\n  alpha:\n    team: team-a\n", "namespaces.alpha: workloads is required"),
        ("namespaces:\n  alpha:\n    workloads: {}\n", "namespaces.alpha: team is required"),
        ("namespaces:\n  alpha:\n    team: Team A\n    workloads: {}\n", "namespaces.alpha.team: Does not match pattern"),
        ("namespaces:\n  Alpha:\n    team: team-a\n    workloads: {}\n", 'namespaces: Property name of "Alpha" does not match'),
        ("namespaces:\n  alpha:\n    team: team-a\n    workloads: []\n", "workloads: Invalid type. Expected: object, given: array"),
        ("namespaces:\n  alpha:\n    team: team-a\n    namespace: alpha\n    workloads: {}\n",
         "namespaces.alpha: Additional property namespace is not allowed"),
        ("namespaces:\n  alpha:\n    team: team-a\n    workloads:\n      My-Api: {}\n",
         'workloads: Property name of "My-Api" does not match'),
        ("teams:\n  alpha: {}\n", "(root): Additional property teams is not allowed"),
    ],
)
def test_bad_files_are_rejected(tmp_path, content, message):
    failing(tmp_path, message, content)


def test_generated_resource_names_must_fit_kubernetes_limits(tmp_path):
    body = f"namespaces:\n  alpha:\n    team: t\n    workloads:\n      {'a' * 40}:\n        ports:\n          {'b' * 30}: 8080\n"
    failing(tmp_path, "must be at most 63 characters", body)


def test_two_files_declaring_the_same_workload_do_not_merge_silently(tmp_path):
    # Helm merges values: a second file for the same namespace would override or add ports.
    # The catalog repository's own tests forbid this; here we document that Helm alone would accept it.
    second = API.replace("http: 8080", "http: 8081").replace("team: team-a", "team: team-b")
    rendered = render(tmp_path, API, second)
    assert rendered.of_kind("Server")[("alpha", "api-http")]["spec"]["port"] == 8081
    assert rendered.of_kind("Server")[("alpha", "api-http")]["metadata"]["labels"]["linkerd-policies/team"] == "team-b"

"""The example catalog in linkerd-catalog/: conventions, and what it renders."""

import sys

import pytest
import yaml

from conftest import ROOT, Rendered, render_values_files

sys.path.insert(0, str(ROOT))
from render_catalog import CATALOG, catalog_files  # noqa: E402

PLATFORM = CATALOG / "platform.yaml"
PROJECT_FILES = [file for file in catalog_files() if file != PLATFORM]
IDS = [file.relative_to(CATALOG).as_posix() for file in PROJECT_FILES]


def load(file):
    return yaml.safe_load(file.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rendered() -> Rendered:
    return render_values_files(catalog_files())


def test_platform_file_holds_only_platform_namespaces():
    for namespace, spec in load(PLATFORM)["namespaces"].items():
        assert spec["team"] == "platform", f"platform.yaml: {namespace} must belong to team platform"


def test_every_shared_service_publishes_at_least_one_port():
    for namespace, spec in load(PLATFORM)["namespaces"].items():
        published = [port for workload in spec["workloads"].values() for port in workload.get("publish", [])]
        assert published, f"platform.yaml: {namespace} publishes nothing, nobody could use it"


@pytest.mark.parametrize("file", PROJECT_FILES, ids=IDS)
def test_project_file_declares_exactly_the_namespace_it_is_named_after(file):
    assert list(load(file)["namespaces"]) == [file.stem]


@pytest.mark.parametrize("file", PROJECT_FILES, ids=IDS)
def test_project_file_belongs_to_its_folder(file):
    assert load(file)["namespaces"][file.stem]["team"] == file.parent.name


@pytest.mark.parametrize("file", PROJECT_FILES, ids=IDS)
def test_project_file_does_not_declare_platform_namespaces(file):
    assert not set(load(file)["namespaces"]) & set(load(PLATFORM)["namespaces"])


def test_no_namespace_is_declared_twice():
    seen = {}
    for file in catalog_files():
        for namespace in load(file)["namespaces"]:
            assert namespace not in seen, f"{namespace}: declared in both {seen[namespace].name} and {file.name}"
            seen[namespace] = file


def test_catalog_renders_a_deny_server_per_declared_port(rendered):
    expected = {
        (namespace, f"{workload}-{port}")
        for file in catalog_files()
        for namespace, spec in load(file)["namespaces"].items()
        for workload, ports in spec["workloads"].items()
        for port in ports.get("ports", {})
    }
    servers = rendered.of_kind("Server")
    assert set(servers) == expected
    assert all(server["spec"]["accessPolicy"] == "deny" for server in servers.values())


def test_demo_api_reaches_rabbitmq_and_project_a_but_the_ui_does_not(rendered):
    assert rendered.identities("rabbitmq", "rabbitmq-amqp") == {
        "Namespace:rabbitmq",
        "ServiceAccount:demo/demo-api",
        "ServiceAccount:demo-preview-123/demo-api",
    }
    assert rendered.identities("project-a", "orders-api-http") == {"Namespace:project-a", "ServiceAccount:demo/demo-api"}
    for authn in rendered.of_kind("MeshTLSAuthentication").values():
        assert all(ref.get("name") != "demo-ui" for ref in authn["spec"]["identityRefs"])


def test_internal_ports_of_shared_services_are_not_reachable_from_outside(rendered):
    for name in ["rabbitmq-management", "rabbitmq-clustering", "rabbitmq-epmd"]:
        assert rendered.identities("rabbitmq", name) == {"Namespace:rabbitmq"}
    assert rendered.identities("kafka", "kafka-controller") == {"Namespace:kafka"}
    assert rendered.identities("project-a", "orders-api-metrics") == {"Namespace:project-a"}


def test_published_but_unused_services_are_only_reachable_from_inside(rendered):
    for namespace, name in [("redis", "redis-redis"), ("linux-mssql", "mssql-sql"), ("mongodb", "mongodb-mongodb"), ("imply", "imply-broker-http")]:
        assert (namespace, name) in rendered.of_kind("Server")
        assert rendered.identities(namespace, name) <= {f"Namespace:{namespace}"}


def test_root_test_yaml_is_the_current_render(rendered):
    committed = [doc for doc in yaml.safe_load_all((ROOT / "test.yaml").read_text(encoding="utf-8-sig")) if doc]
    assert committed == rendered.docs, "regenerate: python render_catalog.py | Out-File .\\test.yaml -Encoding utf8"

"""Shared helpers: render the chart with `helm template` the way Argo CD does (one --values per file)."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "linkerd-policies"


@dataclass
class Rendered:
    returncode: int
    stdout: str
    stderr: str

    @property
    def docs(self) -> list[dict]:
        assert self.returncode == 0, self.stderr
        return [doc for doc in yaml.safe_load_all(self.stdout) if doc]

    def of_kind(self, kind: str) -> dict[tuple[str, str], dict]:
        """Resources of one kind, keyed by (namespace, name)."""
        return {
            (doc["metadata"]["namespace"], doc["metadata"]["name"]): doc
            for doc in self.docs
            if doc["kind"] == kind
        }

    def identities(self, namespace: str, name: str) -> set[str]:
        """Who may reach a port: 'Namespace:<ns>' and 'ServiceAccount:<ns>/<sa>' entries."""
        authn = self.of_kind("MeshTLSAuthentication").get((namespace, name))
        if authn is None:
            return set()
        return {
            f"Namespace:{ref['name']}" if ref["kind"] == "Namespace"
            else f"ServiceAccount:{ref['namespace']}/{ref['name']}"
            for ref in authn["spec"]["identityRefs"]
        }


def helm(*args: str) -> Rendered:
    result = subprocess.run(["helm", *args], capture_output=True, text=True, timeout=120)
    return Rendered(result.returncode, result.stdout, result.stderr)


def render_values_files(files: list[Path], chart: Path = CHART) -> Rendered:
    args = ["template", "linkerd-policies", str(chart)]
    for file in files:
        args += ["--values", str(file)]
    return helm(*args)


def render(tmp_path: Path, *documents: str) -> Rendered:
    """Render inline YAML documents, each written to its own values file."""
    files = []
    for index, document in enumerate(documents):
        file = tmp_path / f"values-{index}.yaml"
        file.write_text(document, encoding="utf-8")
        files.append(file)
    return render_values_files(files)

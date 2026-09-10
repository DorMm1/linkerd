"""Render the example catalog the way Argo CD does: the chart with one --values per catalog file.

    python render_catalog.py > test.yaml
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHART = ROOT / "linkerd-policies"
CATALOG = ROOT / "linkerd-catalog"


def catalog_files() -> list[Path]:
    return [CATALOG / "platform.yaml", *sorted(CATALOG.glob("projects/*/*.yaml"))]


def helm_template(files: list[Path]) -> subprocess.CompletedProcess:
    args = ["helm", "template", "linkerd-policies", str(CHART)]
    for file in files:
        args += ["--values", str(file)]
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


if __name__ == "__main__":
    result = helm_template(catalog_files())
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    sys.exit(result.returncode)

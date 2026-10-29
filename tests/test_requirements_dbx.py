"""The Databricks job's lock must keep every top-level pin as written."""

from pathlib import Path

ROOT = Path(__file__).parent.parent


def _pins(name):
    pins = {}
    for line in (ROOT / name).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pkg, _, version = line.partition("==")
            pins[pkg.lower().replace("_", "-")] = version
    return pins


def test_lock_keeps_top_level_pins():
    top, lock = _pins("requirements-dbx.in"), _pins("requirements-dbx.txt")
    assert top, "no top-level pins"
    for pkg, version in top.items():
        assert lock.get(pkg) == version, pkg
    # numpy 2 breaks shapely 2.0.1 (built against numpy 1.x)
    assert lock["numpy"] == "1.25.1"


def test_job_installs_the_lock():
    yml = (ROOT / "databricks.yml").read_text()
    assert (
        yml.count("requirements: ${workspace.file_path}/requirements-dbx.txt")
        == 1
    )
    assert "pypi:" not in yml

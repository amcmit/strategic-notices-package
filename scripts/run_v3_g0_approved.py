"""One-shot, manifest-bound runner for the proposed v3 analytical G0 gate.

The script is unrun. It requires an owner receipt naming the exact
execution-manifest SHA-256 before it can invoke the analytical tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import subprocess
import sys
from pathlib import Path
from time import monotonic


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "configs/proposed-v3-g0-execution.json"
EXPECTED_TEST = "tests/test_v3_g0_window_proposed.py"
EXPECTED_OUTPUT = "results/v3_g0/acceptance.json"
EXPECTED_MARKER = "results/v3_g0/attempt.started"
EXPECTED_LOG = "results/v3_g0/pytest.txt"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rooted(relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("manifest path must be repository-relative")
    resolved = (ROOT / path).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("manifest path escapes repository")
    return resolved


def peak_rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def main() -> int:
    manifest_bytes = MANIFEST.read_bytes()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    plan = json.loads(manifest_bytes)
    if plan.get("schema") != 1 or plan.get("scope") != "v3-g0-analytical-only":
        raise ValueError("unexpected G0 manifest schema or scope")
    if plan.get("test") != EXPECTED_TEST:
        raise ValueError("unexpected G0 test")
    if plan.get("output") != EXPECTED_OUTPUT or plan.get("marker") != EXPECTED_MARKER:
        raise ValueError("unexpected G0 output or marker")
    if plan.get("log") != EXPECTED_LOG:
        raise ValueError("unexpected G0 log")
    caps = plan["caps"]
    if caps != {"wall_seconds": 900, "peak_rss_mib": 512,
                "grid_cases": 14560, "retries": 0}:
        raise ValueError("unexpected G0 caps")
    expected_files = {
        "docs/PROPOSED_V3_PROTOCOL_RECONCILED.md",
        "docs/V3_WINDOW_REDERIVATION_PROPOSAL.md",
        "docs/V3_BOUNDARY_ALLOCATION_AMENDMENT_PROPOSAL.md",
        "docs/V3_G0_ENUMERATION_SPEC.md",
        "src/sn/analytical_window_v3.py",
        "src/sn_g0_reference_v3.py",
        "tests/test_v3_g0_window_proposed.py",
        "scripts/run_v3_g0_approved.py",
    }
    if set(plan["file_sha256"]) != expected_files:
        raise ValueError("G0 manifest must bind the exact review file set")
    for relative, expected in plan["file_sha256"].items():
        if digest(rooted(relative)) != expected:
            raise ValueError(f"G0 frozen input changed: {relative}")

    receipt_path = rooted(plan["approval_receipt"])
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("approved") is not True
            or receipt.get("scope") != "v3-g0-analytical-only"
            or receipt.get("manifest_sha256") != manifest_sha):
        raise PermissionError("owner receipt does not approve this exact G0 manifest")

    result_path = rooted(EXPECTED_OUTPUT)
    marker_path = rooted(EXPECTED_MARKER)
    log_path = rooted(EXPECTED_LOG)
    if result_path.exists() or marker_path.exists() or log_path.exists():
        raise FileExistsError("G0 already attempted; zero retries")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with marker_path.open("x") as marker:
        marker.write(manifest_sha + "\n")

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    started = monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", EXPECTED_TEST],
            cwd=ROOT, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=caps["wall_seconds"],
            check=False,
        )
        exit_code = completed.returncode
        output = completed.stdout or b""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        output = exc.stdout or b""
    elapsed = monotonic() - started
    peak = peak_rss_mib()
    log_path.write_bytes(output)
    result = {
        "schema": 1,
        "manifest_sha256": manifest_sha,
        "approval_receipt_sha256": digest(receipt_path),
        "test": EXPECTED_TEST,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "elapsed_seconds": elapsed,
        "peak_child_rss_mib": peak,
        "wall_cap_seconds": caps["wall_seconds"],
        "rss_cap_mib": caps["peak_rss_mib"],
        "cap_breached": timed_out or peak > caps["peak_rss_mib"],
        "passed": (exit_code == 0 and not timed_out
                   and peak <= caps["peak_rss_mib"]),
        "log_sha256": digest(log_path),
    }
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

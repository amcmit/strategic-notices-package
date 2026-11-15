"""Candidate design bundle and owner-operated confirmation sealing."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re

from .networks import FAMILIES, SIZES, NetworkCase, _seed, build_case, case_demand_schedule, design_manifest, feature_levels, numeric_features
from .core import Action, Contract, Notice, Production, Realization, Shipment, audit_step, initial_state, step


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode()


def _write(path: Path, value: object) -> str:
    raw = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return sha256(raw).hexdigest()


def _public_row(case: NetworkCase, content_hash: str, split: str) -> dict:
    return {
        "id": case.identifier, "family": case.family, "size": case.size,
        "split": split, "node": case.network.primary_site,
        "features": numeric_features(case), "levels": feature_levels(case),
        "feasible": validate_case(case), "content_sha256": content_hash,
    }


def validate_case(case: NetworkCase) -> bool:
    """Execute a no-disruption path with no spot or backup and no unmet demand."""
    net = case.network
    notice = Notice((case.primary_capacity,) * net.horizon, "baseline")
    state = initial_state(net)
    spine_count = 3 if case.size == "small" else 5
    demand_schedule = case_demand_schedule(case)
    for period in range(net.horizon):
        due_primary = state.outstanding_for(period)
        due_pipeline: dict[tuple[str, str], float] = {}
        for item in state.pipeline:
            if item.due == period:
                key = (item.site, item.product)
                due_pipeline[key] = due_pipeline.get(key, 0) + item.quantity
        shipments = []
        for index in range(spine_count - 1):
            lane = net.lanes[index]
            if period + lane.lead >= net.horizon:
                continue
            available = state.stock(lane.origin, lane.product) + due_pipeline.get((lane.origin, lane.product), 0)
            if lane.origin == net.primary_site:
                available += due_primary
            if available >= case.demand_per_period:
                shipments.append(Shipment(index, case.demand_per_period))
        production = ()
        if case.family == "assembly" and period >= case.demand_start:
            raw = state.stock(case.demand_site, "raw") + due_pipeline.get((case.demand_site, "raw"), 0)
            if net.primary_site == case.demand_site:
                raw += due_primary
            if raw >= case.demand_per_period:
                production = (Production(case.demand_site, "finished", case.demand_per_period),)
        if (case.secondary_demand_site is not None and period >= case.demand_start
                and period + 1 < net.horizon):
            side_lane = next(index for index, lane in enumerate(net.lanes)
                             if lane.origin == case.demand_site
                             and lane.destination == case.secondary_demand_site)
            sink_available = state.stock(case.demand_site, case.demand_product)
            sink_available += due_pipeline.get((case.demand_site, case.demand_product), 0)
            if case.demand_site == net.primary_site:
                sink_available += due_primary
            if sink_available >= case.demand_per_period:
                shipments.append(Shipment(side_lane, 0.25 * case.demand_per_period))
        demand = demand_schedule[period]
        action = Action(primary_order=case.demand_per_period if period + 1 < net.horizon else 0,
                        production=production, shipments=tuple(shipments))
        before = state
        state, ledger = step(net, Contract(), notice, state, action,
                             Realization(case.primary_capacity, demand),
                             revealed_schedule=notice.announced_capacity if period == 2 else None)
        audit_step(net, before, state, ledger)
        if state.backlog:
            return False
    return True


def write_design(root: Path, *, master_seed: int) -> dict:
    """Write only the 24 public design cases and their content hashes."""
    if root.exists():
        raise FileExistsError(f"design destination already exists: {root}")
    cases = design_manifest(master_seed)
    if not all(validate_case(case) for case in cases):
        raise ValueError("at least one design case fails the baseline feasibility path")
    rows = []
    for case in cases:
        digest = _write(root / "networks" / f"{case.identifier}.json", asdict(case))
        rows.append(_public_row(case, digest, "design"))
    manifest = {"schema": 1, "design_master_seed": master_seed, "count": len(rows),
                "networks": rows}
    _write(root / "manifest.json", manifest)
    return manifest


def confirmation_slots() -> tuple[tuple[str, str, int], ...]:
    slots = []
    for stratum, (family, size) in enumerate((f, s) for f in FAMILIES for s in SIZES):
        design_count = 3 if stratum in (0, 3, 6, 9) else 2
        slots.extend((family, size, rep) for rep in range(design_count, 4))
    return tuple(slots)


def verify_confirmation_metadata(path: Path) -> str:
    """Validate only the public manifest; return its SHA-256 digest.

    This cannot certify that a private file matches its declared content hash.
    That check is deferred until confirmation access is authorized.
    """
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "count", "networks"}:
        raise ValueError("unexpected confirmation manifest fields")
    slots = confirmation_slots()
    if (type(manifest["schema"]) is not int or manifest["schema"] != 1
            or type(manifest["count"]) is not int or manifest["count"] != len(slots)
            or not isinstance(manifest["networks"], list)
            or len(manifest["networks"]) != len(slots)):
        raise ValueError("confirmation schema or count mismatch")
    row_keys = {"id", "family", "size", "split", "node", "features", "levels",
                "feasible", "content_sha256"}
    hashes = set()
    for row, (family, size, replicate) in zip(manifest["networks"], slots):
        if not isinstance(row, dict) or set(row) != row_keys:
            raise ValueError("unexpected confirmation row fields")
        expected = build_case(family, size, replicate, seed=0)
        if (row["id"] != expected.identifier or row["family"] != family
                or row["size"] != size or row["split"] != "confirmation"
                or row["node"] != expected.network.primary_site
                or row["levels"] != feature_levels(expected)
                or row["feasible"] is not True):
            raise ValueError(f"confirmation slot mismatch: {expected.identifier}")
        given = row["features"]
        known = numeric_features(expected)
        if (not isinstance(given, dict) or set(given) != set(known)
                or any(type(given[key]) not in (int, float)
                       or not isfinite(given[key]) or given[key] != known[key]
                       for key in known)):
            raise ValueError(f"feature mismatch: {expected.identifier}")
        digest = row["content_sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"malformed content hash: {expected.identifier}")
        hashes.add(digest)
    if len(hashes) != len(slots):
        raise ValueError("duplicate confirmation content hash")
    return sha256(raw).hexdigest()


def seal_confirmation(
    private_root: Path, public_metadata: Path, *, private_master_seed: int,
    repository_root: Path,
) -> dict:
    """Owner-only operation; never call from the agent's development workflow.

    The owner must run this with a private root outside all assistant-readable
    workspaces. Filesystem permissions alone cannot isolate data from a process
    that runs as the same OS user. The function refuses paths inside this repo,
    but the owner is responsible for the stronger access boundary.
    """
    if private_root.resolve().is_relative_to(repository_root.resolve()):
        raise ValueError("private confirmation root must be outside the repository")
    if not public_metadata.resolve().is_relative_to(repository_root.resolve()):
        raise ValueError("public metadata must be inside the repository")
    if public_metadata.resolve().is_relative_to(private_root.resolve()):
        raise ValueError("public metadata cannot be inside private root")
    if public_metadata.exists():
        raise FileExistsError(f"confirmation metadata already exists: {public_metadata}")
    cases = []
    for family, size, replicate in confirmation_slots():
        seed = _seed(private_master_seed, "confirmation", family, size, replicate)
        case = build_case(family, size, replicate, seed=seed)
        if not validate_case(case):
            raise ValueError(f"confirmation candidate is infeasible: {case.identifier}")
        cases.append(case)
    private_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    rows = []
    for case in cases:
        digest = _write(private_root / f"{case.identifier}.json", asdict(case))
        rows.append(_public_row(case, digest, "confirmation"))
    manifest = {"schema": 1, "count": len(rows), "networks": rows}
    _write(public_metadata, manifest)
    return manifest

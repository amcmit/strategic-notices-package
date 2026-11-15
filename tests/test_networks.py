from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from sn.networks import FAMILIES, SIZES, build_case, design_manifest, feature_levels, numeric_features
from sn.suite import canonical, confirmation_slots, validate_case, verify_confirmation_metadata, write_design


def test_all_families_and_sizes_generate_deterministically():
    for family in ("serial", "assembly", "distribution", "layered_dual", "planted"):
        for size in ("small", "large"):
            first = build_case(family, size, 2, seed=517)
            second = build_case(family, size, 2, seed=517)
            assert first == second
            assert first.network.horizon == 10
            assert first.network.primary_site in first.network.sites
            assert first.demand_site in first.network.sites


def test_design_manifest_has_24_disjoint_cases_and_feature_levels():
    manifest = design_manifest(master_seed=20261112)
    assert len(manifest) == 24
    assert len({row.identifier for row in manifest}) == 24
    counts = Counter((row.family, row.size) for row in manifest)
    assert sorted(counts.values()) == [2] * 6 + [3] * 4
    levels = Counter()
    for row in manifest:
        for name, level in feature_levels(row).items():
            levels[name, level] += 1
    for name, expected in {
        "substitutability": ("sole", "dual", "multi"),
        "position": ("upstream", "middle", "downstream"),
        "buffer": ("low", "middle", "high"),
        "slack": ("low", "high"),
        "lead_time": ("short", "long"),
    }.items():
        assert all(levels[name, level] >= 3 for level in expected), (name, levels)


def test_all_40_slots_have_feasible_primary_only_baseline():
    for family in FAMILIES:
        for size in SIZES:
            for replicate in range(4):
                assert validate_case(build_case(family, size, replicate, seed=517))


def test_design_bundle_hashes_and_confirmation_slot_disjointness(tmp_path):
    first = tmp_path / "design-a"
    second = tmp_path / "design-b"
    manifest = write_design(first, master_seed=20261112)
    assert manifest == write_design(second, master_seed=20261112)
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    assert manifest["count"] == 24
    for row in manifest["networks"]:
        raw = (first / "networks" / f"{row['id']}.json").read_bytes()
        assert sha256(raw).hexdigest() == row["content_sha256"]
        assert json.loads(raw)["identifier"] == row["id"]
        assert row["feasible"] is True
    assert not (first / "confirmation").exists()
    with pytest.raises(FileExistsError):
        write_design(first, master_seed=20261112)

    design_slots = {(c.family, c.size, c.replicate)
                    for c in design_manifest(20261112)}
    sealed_slots = set(confirmation_slots())
    assert len(sealed_slots) == 16
    assert not design_slots & sealed_slots
    assert len(design_slots | sealed_slots) == 40
    assert sorted(Counter((f, s) for f, s, _ in sealed_slots).values()) == [1] * 4 + [2] * 6


def test_public_confirmation_metadata_can_be_checked_without_private_files(tmp_path):
    rows = []
    for family, size, replicate in confirmation_slots():
        # Public structural profiles need no private seed or case file.
        case = build_case(family, size, replicate, seed=0)
        rows.append({
            "id": case.identifier, "family": family, "size": size,
            "split": "confirmation", "node": case.network.primary_site,
            "features": numeric_features(case), "levels": feature_levels(case),
            "feasible": True,
            "content_sha256": sha256(case.identifier.encode()).hexdigest(),
        })
    path = tmp_path / "public.json"
    manifest = {"schema": 1, "count": 16, "networks": rows}
    path.write_bytes(canonical(manifest))
    assert verify_confirmation_metadata(path) == sha256(path.read_bytes()).hexdigest()

    bad = deepcopy(manifest)
    bad["networks"][0]["seed"] = 42
    path.write_bytes(canonical(bad))
    with pytest.raises(ValueError, match="fields"):
        verify_confirmation_metadata(path)

    bad = deepcopy(manifest)
    bad["networks"][0]["content_sha256"] = "not-a-hash"
    path.write_bytes(canonical(bad))
    with pytest.raises(ValueError, match="hash"):
        verify_confirmation_metadata(path)

    bad = deepcopy(manifest)
    bad["networks"][0]["features"]["buffer"] = -1
    path.write_bytes(canonical(bad))
    with pytest.raises(ValueError, match="feature"):
        verify_confirmation_metadata(path)

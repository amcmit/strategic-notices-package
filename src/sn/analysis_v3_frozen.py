"""Frozen v3 four-cell network analysis; no solver, files, or private-data access."""

from __future__ import annotations

from collections import Counter, defaultdict
from math import isfinite
from random import Random


BOOTSTRAP_REPEATS = 10_000
BOOTSTRAP_SEED = 20261112
SIGN_FLIP_REPEATS = 100_000
SIGN_FLIP_SEED = 20261113
FAMILIES = ("assembly", "distribution", "layered_dual", "planted", "serial")
CELLS = ("C00", "C10", "C01", "C11")
FIELDS = ("b", "delta_d", "e", "s", "g_3_5", "buyer_harm",
          "contractual", "belief", "interaction")


def _mean(values: list[float]) -> float:
    if not values:
        raise ValueError("empty analysis stratum")
    return sum(values) / len(values)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


def _family_mean(rows: list[dict], field: str) -> float:
    groups = {family: [row[field] for row in rows if row["family"] == family]
              for family in FAMILIES}
    if any(not group for group in groups.values()):
        raise ValueError("an analysis family has no networks")
    return _mean([_mean(groups[family]) for family in FAMILIES])


def _resample_within_family(rows: list[dict], rng: Random) -> list[dict]:
    sampled = []
    for family in FAMILIES:
        group = [row for row in rows if row["family"] == family]
        if not group:
            raise ValueError("empty family bootstrap cell")
        sampled.extend(group[rng.randrange(len(group))] for _ in group)
    return sampled


def _critical_penalty(row: dict) -> dict:
    shortfall, delivery = row["s"], row["delta_d"]
    if shortfall > 1e-8:
        if delivery > 0:
            return {"category": "finite_nonnegative", "value": 3 * delivery / shortfall}
        return {"category": "no_positive_range", "value": None}
    if shortfall < -1e-8:
        return {"category": "gain_increases_with_penalty", "value": None}
    at_zero = 3 * delivery
    category = ("invariant_positive" if at_zero > 1e-8 else
                "invariant_negative" if at_zero < -1e-8 else "invariant_zero")
    return {"category": category, "value": None}


def _validate(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    if len(rows) != 56 or len({row["case_id"] for row in rows}) != 56:
        raise ValueError("analysis requires all 56 distinct selected cases")
    regimes = Counter(row["regime"] for row in rows)
    if regimes != {"R1": 32, "R2": 8, "R3": 8, "R4": 8}:
        raise ValueError("analysis regime allocation differs")
    interior = [row for row in rows if row["regime"] == "R1" and row["category"] == "interior"]
    boundary = [row for row in rows if row["regime"] == "R1" and row["category"] != "interior"]
    if len(interior) != 24 or len(boundary) != 8:
        raise ValueError("H1 population or boundary count differs")
    for row in rows:
        if (row["family"] not in FAMILIES or set(row["cells"]) != set(CELLS)
                or any(not isfinite(row[name]) for name in FIELDS)):
            raise ValueError("analysis row lacks fixed four-cell quantities")
        cells = row["cells"]
        for name in CELLS:
            if (set(cells[name]) != {"buyer_cost", "backup", "primary", "accepted_shortfall",
                                      "pre_horizon_shortfall", "lost_sales", "spot",
                                      "qualification_dates"}
                    or any(not isfinite(value) for key, value in cells[name].items()
                           if key != "qualification_dates")
                    or len(cells[name]["qualification_dates"]) > 1):
                raise ValueError("analysis cell metric shape differs")
        c = {name: cells[name]["buyer_cost"] for name in CELLS}
        if (abs(row["contractual"] - (c["C10"] - c["C00"])) > 1e-7
                or abs(row["belief"] - (c["C01"] - c["C00"])) > 1e-7
                or abs(row["interaction"] - (c["C11"] - c["C10"] - c["C01"] + c["C00"])) > 1e-7
                or abs(row["buyer_harm"] - (c["C11"] - c["C00"])) > 1e-7
                or abs(row["contractual"] + row["belief"] + row["interaction"]
                       - row["buyer_harm"]) > 1e-7):
            raise ValueError("four-cell harm identity differs")
        if (abs(row["b"] - (cells["C00"]["backup"] - cells["C11"]["backup"])) > 1e-7
                or abs(row["delta_d"] - (cells["C11"]["primary"] - cells["C00"]["primary"])) > 1e-7
                or abs(row["s"] - (cells["C11"]["accepted_shortfall"]
                                     - cells["C00"]["accepted_shortfall"])) > 1e-7
                or abs(row["e"] - (row["delta_d"] - row["b"])) > 1e-7
                or abs(row["g_3_5"] - (3 * row["delta_d"] - 5 * row["s"])) > 1e-7):
            raise ValueError("supplier ledger identity differs")
    return interior, boundary


def _analyze(rows: list[dict], *, purpose: str, bootstrap_repeats: int,
             sign_flip_repeats: int) -> dict:
    if purpose not in ("design_validation", "confirmation"):
        raise ValueError("unknown analysis purpose")
    interior, boundary = _validate(rows)
    contrast = [row for row in rows if row["regime"] in ("R2", "R3", "R4")]
    estimates = {
        "b": _family_mean(interior, "b"),
        "delta_d": _family_mean(interior, "delta_d"),
        "h1c": _family_mean(interior, "delta_d") - _family_mean(contrast, "delta_d"),
        "g_3_5": _family_mean(interior, "g_3_5"),
        "buyer_harm": _family_mean(interior, "buyer_harm"),
    }
    samples = {name: [] for name in estimates}
    rng = Random(BOOTSTRAP_SEED)
    for _ in range(bootstrap_repeats):
        a = _resample_within_family(interior, rng)
        c = _resample_within_family(contrast, rng)
        samples["b"].append(_family_mean(a, "b"))
        samples["delta_d"].append(_family_mean(a, "delta_d"))
        samples["h1c"].append(_family_mean(a, "delta_d") - _family_mean(c, "delta_d"))
        samples["g_3_5"].append(_family_mean(a, "g_3_5"))
        samples["buyer_harm"].append(_family_mean(a, "buyer_harm"))
    intervals = {}
    for name, values in samples.items():
        alpha = 0.05 if name in ("b", "delta_d") else 0.0167
        lo, hi = _percentile(values, alpha / 2), _percentile(values, 1 - alpha / 2)
        intervals[name] = {"estimate": estimates[name], "lower": lo, "upper": hi,
                           "width": hi - lo, "level": 1 - alpha}
    signs = Random(SIGN_FLIP_SEED)
    right = {"b": 0, "delta_d": 0}
    for _ in range(sign_flip_repeats):
        flipped = []
        for row in interior:
            direction = -1 if signs.randrange(2) else 1
            flipped.append({"family": row["family"], "b": direction * row["b"],
                            "delta_d": direction * row["delta_d"]})
        for name in right:
            if _family_mean(flipped, name) >= estimates[name]:
                right[name] += 1
    p_values = {name: (1 + count) / (sign_flip_repeats + 1)
                for name, count in right.items()}
    p_values["joint"] = max(p_values.values())
    h1_numeric = intervals["b"]["lower"] > 0 and intervals["delta_d"]["lower"] > 0
    sign_numeric = p_values["joint"] <= 0.05
    by_regime = {}
    for regime in ("R1", "R2", "R3", "R4"):
        group = [row for row in rows if row["regime"] == regime]
        by_regime[regime] = {"count": len(group), "mean": {
            name: _mean([row[name] for row in group])
            for name in ("b", "delta_d", "e", "s", "g_3_5", "buyer_harm",
                         "contractual", "belief", "interaction")},
            "mean_cell_cost": {cell: _mean([row["cells"][cell]["buyer_cost"]
                                             for row in group]) for cell in CELLS},
            "qualification_counts": {cell: sum(bool(row["cells"][cell]["qualification_dates"])
                                              for row in group) for cell in CELLS}}
    by_family_size = {}
    for family in FAMILIES:
        for size in ("small", "large"):
            group = [row for row in rows if row["family"] == family and row["size"] == size]
            if group:
                by_family_size[f"{family}/{size}"] = {
                    "count": len(group),
                    "mean": {name: _mean([row[name] for row in group])
                             for name in ("b", "delta_d", "buyer_harm",
                                          "contractual", "belief", "interaction")},
                }
    r3_comparisons = {
        f"R3_minus_{other}": {name: by_regime["R3"]["mean"][name]
                              - by_regime[other]["mean"][name]
                              for name in ("b", "delta_d", "buyer_harm")}
        for other in ("R2", "R4")
    }
    critical = [_critical_penalty(row) for row in rows]
    finite = sorted(x["value"] for x in critical if x["category"] == "finite_nonnegative")
    critical_report = {"category_counts": dict(sorted(Counter(x["category"] for x in critical).items())),
                       "finite_count": len(finite),
                       "finite_quantiles": ({"min": finite[0], "median": _percentile(finite, 0.5),
                                             "max": finite[-1]} if finite else None)}
    h2_components = {name: _family_mean(interior, name)
                     for name in ("contractual", "belief", "interaction", "buyer_harm")}
    if abs(h2_components["contractual"] + h2_components["belief"]
           + h2_components["interaction"] - h2_components["buyer_harm"]) > 1e-7:
        raise ValueError("equal-family-weight H2 decomposition differs")
    return {
        "schema": 1, "purpose": purpose, "case_count": 56,
        "h1_population_count": 24, "boundary_count": 8,
        "family_counts_h1": dict(sorted(Counter(row["family"] for row in interior).items())),
        "truthful_r1_interior_qualification_count": sum(
            bool(row["cells"]["C00"]["qualification_dates"]) for row in interior),
        "h2_components_r1_interior": h2_components,
        "boundary_controls": [{"case_id": row["case_id"], "category": row["category"],
                               "b": row["b"], "delta_d": row["delta_d"],
                               "buyer_harm": row["buyer_harm"],
                               "qualification_by_cell": {
                                   cell: bool(row["cells"][cell]["qualification_dates"])
                                   for cell in CELLS}}
                              for row in sorted(boundary, key=lambda x: x["case_id"])],
        "by_regime": by_regime, "by_family_size": by_family_size,
        "r3_descriptive_comparisons": r3_comparisons,
        "critical_penalty": critical_report,
        "intervals": intervals,
        "sign_flip": {"seed": SIGN_FLIP_SEED, "draws": sign_flip_repeats,
                      "p_values": p_values,
                      "fragility": h1_numeric != sign_numeric},
        "bootstrap": {"seed": BOOTSTRAP_SEED, "resamples": bootstrap_repeats,
                      "strata": "family within R1 interior and pooled R2-R4",
                      "paired_cells_kept_together": True},
        "h1_decision": ("passed" if h1_numeric else "not_passed")
                       if purpose == "confirmation" else "design_only_no_gate",
        "secondary_decisions": ({name: intervals[name]["lower"] > 0
                                 for name in ("h1c", "g_3_5", "buyer_harm")}
                                if purpose == "confirmation" and h1_numeric else None),
        "h2_status": ("tested" if h1_numeric else "untested_h1_not_passed")
                     if purpose == "confirmation" else "design_only_no_gate",
    }


def analyze(rows: list[dict], *, purpose: str) -> dict:
    """Run the frozen analysis; design mode cannot issue scientific decisions."""
    return _analyze(rows, purpose=purpose,
                    bootstrap_repeats=BOOTSTRAP_REPEATS,
                    sign_flip_repeats=SIGN_FLIP_REPEATS)

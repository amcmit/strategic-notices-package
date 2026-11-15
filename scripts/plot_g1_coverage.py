"""Plot G1 feature coverage using public design and confirmation metadata only."""

from __future__ import annotations

import argparse
from collections import Counter
from itertools import combinations
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize

from sn.suite import verify_confirmation_metadata


FEATURES = ("substitutability", "position", "buffer", "slack", "lead_time")
LABELS = {
    "substitutability": "Sources",
    "position": "Position",
    "buffer": "Buffer",
    "slack": "Slack",
    "lead_time": "Lead",
}
LEVELS = {
    "substitutability": ("sole", "dual", "multi"),
    "position": ("upstream", "middle", "downstream"),
    "buffer": ("low", "middle", "high"),
    "slack": ("low", "high"),
    "lead_time": ("short", "long"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--confirmation", type=Path, required=True)
    parser.add_argument("--output-png", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path, required=True)
    args = parser.parse_args()

    verify_confirmation_metadata(args.confirmation)
    design = json.loads(args.design.read_text())["networks"]
    confirmation = json.loads(args.confirmation.read_text())["networks"]
    if len(design) != 24 or len(confirmation) != 16:
        raise ValueError("expected 24 design and 16 confirmation public rows")
    if any(row.get("split") != "design" for row in design):
        raise ValueError("design manifest contains a non-design row")

    pairs = tuple(combinations(FEATURES, 2))
    counts = []
    for x_feature, y_feature in pairs:
        d = Counter((row["levels"][x_feature], row["levels"][y_feature]) for row in design)
        c = Counter((row["levels"][x_feature], row["levels"][y_feature]) for row in confirmation)
        counts.append((d, c))
    maximum = max(sum((d[key], c[key])) for d, c in counts for key in set(d) | set(c))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(2, 5, figsize=(19.5, 8.8), layout="constrained")
    cmap = plt.get_cmap("Blues").copy()
    cmap.set_under("#eeeeee")
    norm = Normalize(vmin=0.5, vmax=maximum)
    for ax, (x_feature, y_feature), (design_counts, confirmation_counts) in zip(
        axes.flat, pairs, counts
    ):
        x_levels, y_levels = LEVELS[x_feature], LEVELS[y_feature]
        values = [
            [design_counts[x, y] + confirmation_counts[x, y] for x in x_levels]
            for y in y_levels
        ]
        ax.imshow(values, cmap=cmap, norm=norm, aspect="auto")
        ax.set_title(f"{LABELS[x_feature]} × {LABELS[y_feature]}", fontsize=11, pad=9)
        ax.set_xticks(range(len(x_levels)), x_levels, rotation=28, ha="right")
        ax.set_yticks(range(len(y_levels)), y_levels)
        ax.set_xlabel(LABELS[x_feature])
        ax.set_ylabel(LABELS[y_feature])
        ax.set_xticks([index - 0.5 for index in range(1, len(x_levels))], minor=True)
        ax.set_yticks([index - 0.5 for index in range(1, len(y_levels))], minor=True)
        ax.grid(which="minor", color="white", linewidth=1.5)
        ax.tick_params(which="minor", bottom=False, left=False)
        for yi, y in enumerate(y_levels):
            for xi, x in enumerate(x_levels):
                d, c = design_counts[x, y], confirmation_counts[x, y]
                ax.text(xi, yi, f"{d}/{c}", ha="center", va="center",
                        fontsize=12, fontweight="bold",
                        color="white" if d + c >= maximum * 0.55 else "#172536")

    fig.suptitle("G1 public feature coverage: design / confirmation", fontsize=17,
                 fontweight="bold")
    fig.supxlabel("Each cell shows design / confirmation network counts. Gray cells are empty; color shows combined count.",
                  fontsize=11)
    for output in (args.output_png, args.output_pdf):
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()

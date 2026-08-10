#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compute per-round normalization statistics for the dense replay dataset."""
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "mechabellum-matplotlib"))

import matplotlib
import numpy as np

matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D


ROOT = Path(__file__).resolve().parent.parent
MAX_BATTLE_ROUNDS = 18
TRIM_FRACTION = 0.03
VARIANCE_FLOOR = 100.0


def _resolve(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def _portable_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def round_total_samples(investment: np.ndarray, round_mask: np.ndarray) -> list[np.ndarray]:
    """Return one untrimmed side-level sample vector for every round."""
    investment = np.asarray(investment)
    round_mask = np.asarray(round_mask, dtype=bool)
    if investment.ndim != 4:
        raise ValueError("Investment tensor must have shape [match, round, side, unit]")
    if round_mask.shape != investment.shape[:2]:
        raise ValueError("round_mask must have shape [match, round]")
    if investment.shape[1] != MAX_BATTLE_ROUNDS:
        raise ValueError(f"Expected {MAX_BATTLE_ROUNDS} rounds, found {investment.shape[1]}")
    if not np.isfinite(investment).all():
        raise ValueError("Investment tensor contains non-finite values")

    totals = investment.sum(axis=-1, dtype=np.float64)
    return [totals[:, round_index, :][round_mask[:, round_index]].reshape(-1)
            for round_index in range(MAX_BATTLE_ROUNDS)]


def analyze_samples(values: np.ndarray) -> dict[str, Any]:
    """Compute exact rank-trimmed statistics and raw-sample 3-sigma outliers."""
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    if not np.isfinite(values).all():
        raise ValueError("Samples contain non-finite values")

    sample_count = int(values.size)
    if sample_count == 0:
        return {
            "sample_count": 0,
            "trim_each_tail_count": 0,
            "retained_sample_count": 0,
            "robust_mean": None,
            "raw_variance": None,
            "normalization_variance": None,
            "sigma": None,
            "three_sigma_lower": None,
            "three_sigma_upper": None,
            "outside_three_sigma_count": None,
            "outside_three_sigma_percentage": None,
            "below_three_sigma_count": None,
            "above_three_sigma_count": None,
        }

    trim_count = math.floor(sample_count * TRIM_FRACTION)
    sorted_values = np.sort(values)
    retained = sorted_values[trim_count:sample_count - trim_count] if trim_count else sorted_values
    robust_mean = float(np.mean(retained))
    raw_variance = float(np.var(retained, ddof=0))
    normalization_variance = max(raw_variance, VARIANCE_FLOOR)
    sigma = math.sqrt(normalization_variance)
    lower = robust_mean - 3 * sigma
    upper = robust_mean + 3 * sigma
    below_count = int(np.count_nonzero(values < lower))
    above_count = int(np.count_nonzero(values > upper))
    outside_count = below_count + above_count

    return {
        "sample_count": sample_count,
        "trim_each_tail_count": trim_count,
        "retained_sample_count": int(retained.size),
        "robust_mean": robust_mean,
        "raw_variance": raw_variance,
        "normalization_variance": normalization_variance,
        "sigma": sigma,
        "three_sigma_lower": lower,
        "three_sigma_upper": upper,
        "outside_three_sigma_count": outside_count,
        "outside_three_sigma_percentage": outside_count * 100.0 / sample_count,
        "below_three_sigma_count": below_count,
        "above_three_sigma_count": above_count,
    }


def build_statistics(input_npz: str | Path) -> tuple[dict[str, Any], dict[str, list[np.ndarray]]]:
    """Load the dense dataset and return portable statistics plus raw plot samples."""
    input_npz = _resolve(input_npz)
    with np.load(input_npz, allow_pickle=False) as dataset:
        try:
            round_mask = dataset["round_mask"]
            delta = dataset["investment_delta"]
            cumulative = dataset["investment_cumulative"]
        except KeyError as exc:
            raise ValueError(f"Dense dataset is missing required array: {exc.args[0]}") from exc

    samples = {
        "round_investment": round_total_samples(delta, round_mask),
        "board_total_value": round_total_samples(cumulative, round_mask),
    }
    metrics = {
        "round_investment": {
            "name_cn": "当回合投入",
            "source_array": "investment_delta",
            "aggregation": "sum_over_unit_axis",
            "rounds": [{"round": index + 1, **analyze_samples(values)}
                       for index, values in enumerate(samples["round_investment"])],
        },
        "board_total_value": {
            "name_cn": "盘面总价值",
            "source_array": "investment_cumulative",
            "aggregation": "sum_over_unit_axis",
            "rounds": [{"round": index + 1, **analyze_samples(values)}
                       for index, values in enumerate(samples["board_total_value"])],
        },
    }
    return {
        "schema_version": 1,
        "source_dataset": _portable_path(input_npz),
        "max_battle_rounds": MAX_BATTLE_ROUNDS,
        "calculation": {
            "sample_unit": "one valid match-round-side; 2v2 values are already team averages",
            "unit_aggregation": "sum each side's 43 unit-axis values",
            "trim_fraction_each_tail": TRIM_FRACTION,
            "trim_count_rule": "floor(sample_count * trim_fraction_each_tail)",
            "variance_ddof": 0,
            "variance_floor": VARIANCE_FLOOR,
            "outside_three_sigma_rule": "raw sample < lower or raw sample > upper",
            "empty_round_rule": "sample_count=0 and all derived statistics are null",
        },
        "metrics": metrics,
    }, samples


def _format_number(value: Any, digits: int = 2) -> str:
    return "—" if value is None else f"{float(value):,.{digits}f}"


def render_markdown(statistics: dict[str, Any]) -> str:
    lines = [
        "# Mechabellum 按回合归一化常数 v1",
        "",
        "该报告的每个样本是一个有效的 `对局 × 回合 × 阵营`。当回合投入取 "
        "`investment_delta` 在 43 个兵种轴上的和；盘面总价值取 "
        "`investment_cumulative` 在相同轴上的和。padding 不参与统计，2v2 使用数据集内已计算的队内平均值。",
        "",
        "每回合独立排序，首尾各去掉 `floor(N × 3%)` 个样本，再以保留样本计算均值和总体方差（`ddof=0`）。"
        "归一化方差为 `max(原始方差, 100)`；3σ 越界在未裁剪样本上按严格小于下界或严格大于上界统计。",
        "",
    ]
    for metric in statistics["metrics"].values():
        lines.extend([
            f"## {metric['name_cn']}",
            "",
            "| 回合 | 样本数 | 每端裁剪 | 保留数 | Robust 均值 | 原始方差 | 归一化方差 | σ | 3σ 外（下 / 上 / 总） | 占比 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
        for row in metric["rounds"]:
            if row["sample_count"] == 0:
                lines.append(f"| {row['round']} | 0 | 0 | 0 | — | — | — | — | — | — |")
                continue
            outliers = (
                f"{row['below_three_sigma_count']} / {row['above_three_sigma_count']} / "
                f"{row['outside_three_sigma_count']}"
            )
            lines.append(
                f"| {row['round']} | {row['sample_count']} | {row['trim_each_tail_count']} | "
                f"{row['retained_sample_count']} | {_format_number(row['robust_mean'])} | "
                f"{_format_number(row['raw_variance'])} | {_format_number(row['normalization_variance'])} | "
                f"{_format_number(row['sigma'])} | {outliers} | "
                f"{_format_number(row['outside_three_sigma_percentage'])}% |"
            )
        lines.append("")
    lines.extend([
        "第 14–18 回合当前没有有效样本，因此统计值以 `null`（本报告显示为“—”）保存；训练时应继续使用 `round_mask` 跳过这些位置。",
        "",
    ])
    return "\n".join(lines)


def render_boxplot(samples: list[np.ndarray], rows: list[dict[str, Any]], title: str, output_path: Path) -> None:
    """Render one per-round raw-sample boxplot with robust references."""
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "Heiti SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(16, 8), constrained_layout=True)
    valid_positions = [index + 1 for index, values in enumerate(samples) if values.size]
    valid_samples = [values for values in samples if values.size]
    if valid_samples:
        ax.boxplot(
            valid_samples,
            positions=valid_positions,
            widths=0.56,
            patch_artist=True,
            showfliers=True,
            boxprops={"facecolor": "#88bde6", "edgecolor": "#3b6f9c"},
            medianprops={"color": "#173f5f", "linewidth": 1.8},
            whiskerprops={"color": "#3b6f9c"},
            capprops={"color": "#3b6f9c"},
            flierprops={"marker": "o", "markersize": 2.5, "markerfacecolor": "#777777", "markeredgewidth": 0, "alpha": 0.45},
        )
    mean_x = [row["round"] for row in rows if row["robust_mean"] is not None]
    means = [row["robust_mean"] for row in rows if row["robust_mean"] is not None]
    lowers = [row["three_sigma_lower"] for row in rows if row["three_sigma_lower"] is not None]
    uppers = [row["three_sigma_upper"] for row in rows if row["three_sigma_upper"] is not None]
    ax.plot(mean_x, means, color="#d1495b", marker="D", markersize=4, linewidth=1.4, label="Robust 均值")
    ax.plot(mean_x, lowers, color="#e38b29", linestyle="--", linewidth=1.2, label="均值 ± 3σ")
    ax.plot(mean_x, uppers, color="#e38b29", linestyle="--", linewidth=1.2)

    ax.set_title(title)
    ax.set_xlabel("回合")
    ax.set_ylabel("资源 / 盘面价值")
    ax.set_xlim(0.35, MAX_BATTLE_ROUNDS + 0.65)
    ax.set_xticks(range(1, MAX_BATTLE_ROUNDS + 1))
    labels = [str(index) if samples[index - 1].size else f"{index}\n无数据"
              for index in range(1, MAX_BATTLE_ROUNDS + 1)]
    ax.set_xticklabels(labels)
    ax.grid(axis="y", alpha=0.25)
    for row in rows:
        if row["sample_count"] == 0:
            ax.text(row["round"], 0.02, "N=0", transform=ax.get_xaxis_transform(),
                    ha="center", va="bottom", fontsize=8, color="#666666")
    ax.legend(handles=[
        Line2D([0], [0], color="#d1495b", marker="D", markersize=4, linewidth=1.4, label="Robust 均值"),
        Line2D([0], [0], color="#e38b29", linestyle="--", linewidth=1.2, label="均值 ± 3σ"),
    ], loc="upper left")
    fig.savefig(output_path, format="jpg", dpi=180)
    plt.close(fig)


def generate_artifacts(
    input_npz: str | Path,
    output_json: str | Path,
    output_markdown: str | Path,
    delta_plot: str | Path,
    cumulative_plot: str | Path,
) -> dict[str, Any]:
    """Generate the JSON, Markdown report, and both requested JPEG plots."""
    statistics, samples = build_statistics(input_npz)
    output_json = _resolve(output_json)
    output_markdown = _resolve(output_markdown)
    delta_plot = _resolve(delta_plot)
    cumulative_plot = _resolve(cumulative_plot)
    for path in (output_json, output_markdown, delta_plot, cumulative_plot):
        path.parent.mkdir(parents=True, exist_ok=True)

    output_json.write_text(json.dumps(statistics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_markdown.write_text(render_markdown(statistics), encoding="utf-8")
    render_boxplot(
        samples["round_investment"],
        statistics["metrics"]["round_investment"]["rounds"],
        "当回合投入：按回合分布与归一化边界",
        delta_plot,
    )
    render_boxplot(
        samples["board_total_value"],
        statistics["metrics"]["board_total_value"]["rounds"],
        "盘面总价值：按回合分布与归一化边界",
        cumulative_plot,
    )
    return statistics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-npz", default="data/mechabellum_dense_v1.npz")
    parser.add_argument("--output-json", default="data/mechabellum_normalization_v1.json")
    parser.add_argument("--output-markdown", default="information/mechabellum_normalization_v1.md")
    parser.add_argument("--delta-plot", default="data/investment_delta_by_round_boxplot.jpg")
    parser.add_argument("--cumulative-plot", default="data/investment_cumulative_by_round_boxplot.jpg")
    args = parser.parse_args()
    statistics = generate_artifacts(
        args.input_npz,
        args.output_json,
        args.output_markdown,
        args.delta_plot,
        args.cumulative_plot,
    )
    for path in (args.output_json, args.output_markdown, args.delta_plot, args.cumulative_plot):
        print(f"wrote {_portable_path(_resolve(path))}")
    for key, metric in statistics["metrics"].items():
        available = sum(row["sample_count"] > 0 for row in metric["rounds"])
        print(f"{key}: {available}/{MAX_BATTLE_ROUNDS} rounds contain samples")


if __name__ == "__main__":
    main()

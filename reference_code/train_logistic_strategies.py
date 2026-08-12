#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Search and evaluate the three strategy extensions for logistic win rate.

Only the round-winner task is searched.  The strategy dataset is deliberately
kept separate from dense v1 so the original experiment remains reproducible.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import yaml
from scipy import sparse
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score, roc_curve

ROOT = Path(__file__).resolve().parent.parent
COPY_SUFFIX = re.compile(r"\(\d+\)(?=\.grbr$)")
K = 43
M = 3


@dataclass(frozen=True)
class StrategyConfig:
    spatial: str
    buff: str
    common_denominator: bool

    @property
    def name(self) -> str:
        denominator = "harmonic" if self.common_denominator else "side"
        return f"spatial={self.spatial}__buff={self.buff}__denom={denominator}"

    @property
    def short_name(self) -> str:
        return f"{self.spatial}+{self.buff}+{'H' if self.common_denominator else 'side'}"

    @property
    def interaction_dim(self) -> int:
        return K * K

    @property
    def feature_dim(self) -> int:
        return 2 * K + K * K + (4 * K if self.buff == "aligned" else 8 * K if self.buff == "full_cross" else 0)


@dataclass
class Samples:
    match: np.ndarray
    round: np.ndarray
    side: np.ndarray
    target: np.ndarray
    group: np.ndarray

    def __len__(self) -> int:
        return len(self.target)


def _path(value: str | Path) -> Path:
    value = Path(value)
    return value if value.is_absolute() else ROOT / value


def _normal(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def canonical_replay_id(filename: str) -> str:
    return COPY_SUFFIX.sub("", _normal(filename))


def read_config(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(_path(path).read_text(encoding="utf-8"))


def load_bundle(config: dict[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    npz_path = _path(config["strategy_dataset_npz"])
    json_path = _path(config["strategy_dataset_json"])
    with np.load(npz_path, allow_pickle=False) as archive:
        arrays = {key: archive[key].copy() for key in archive.files}
    metadata = json.loads(json_path.read_text(encoding="utf-8"))
    if arrays["investment_cumulative"].shape != (962, 18, 2, K):
        raise ValueError(f"Unexpected strategy dataset shape: {arrays['investment_cumulative'].shape}")
    return arrays, metadata


def all_strategy_configs() -> list[StrategyConfig]:
    spatial = ["off", "h150", "h300", "h600"]
    buffs = ["off", "aligned", "full_cross"]
    return [StrategyConfig(s, b, common) for s in spatial for b in buffs for common in (False, True)]


def make_samples(arrays: dict[str, np.ndarray], metadata: dict[str, Any]) -> Samples:
    valid = arrays["round_valid"].astype(bool)
    match, round_index, side, target, group = [], [], [], [], []
    groups = np.asarray([item["group"] for item in metadata["matches"]], dtype=object)
    winner = arrays["round_winner"]
    for row, round_no in zip(*np.nonzero(valid)):
        for viewpoint in (0, 1):
            match.append(row)
            round_index.append(round_no)
            side.append(viewpoint)
            target.append(int(winner[row, round_no] == viewpoint))
            group.append(groups[row])
    return Samples(np.asarray(match), np.asarray(round_index), np.asarray(side), np.asarray(target, dtype=np.int8), np.asarray(group, dtype=object))


def _groups_to_rows(samples: Samples, groups: Iterable[str]) -> np.ndarray:
    return np.isin(samples.group, np.asarray(list(groups), dtype=object))


def _balanced_group_bins(groups: np.ndarray, target_fractions: list[float], seed: int) -> list[list[str]]:
    unique, counts = np.unique(groups, return_counts=True)
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(unique))
    order = order[np.argsort(counts[order])[::-1]]
    totals = np.zeros(len(target_fractions), dtype=np.float64)
    targets = max(float(len(groups)), 1.0) * np.asarray(target_fractions)
    bins: list[list[str]] = [[] for _ in target_fractions]
    for index in order:
        score = totals / np.maximum(targets, 1.0)
        destination = int(np.argmin(score))
        bins[destination].append(str(unique[index]))
        totals[destination] += counts[index]
    return bins


def make_split(samples: Samples, config: dict[str, Any]) -> dict[str, Any]:
    fractions = [config["test_fraction"]] * 3 + [config["selection_fraction"]]
    bins = _balanced_group_bins(samples.group, fractions, int(config["split_seed"]))
    test_bins, selection_groups = bins[:3], bins[3]
    split = {
        "seed": int(config["split_seed"]),
        "groups": {"selection": selection_groups, "test_a": test_bins[0], "test_b": test_bins[1], "test_c": test_bins[2]},
        "splits": {},
    }
    for name, group_list in [("selection", selection_groups), ("test_a", test_bins[0]), ("test_b", test_bins[1]), ("test_c", test_bins[2])]:
        rows = np.flatnonzero(_groups_to_rows(samples, group_list))
        split["splits"][name] = {"row_indices": rows.tolist(), "group_ids": list(group_list), "sample_count": int(len(rows))}
    selection_folds = _balanced_group_bins(np.asarray(selection_groups, dtype=object), [1.0 / int(config["selection_folds"])] * int(config["selection_folds"]), int(config["split_seed"]) + 1)
    split["selection_folds"] = [{"group_ids": fold, "sample_count": int(_groups_to_rows(samples, fold).sum())} for fold in selection_folds]
    return split


def _add_entries(rows: list[int], cols: list[int], values: list[float], row: int, offset: int, vector: np.ndarray) -> None:
    indices = np.flatnonzero(np.abs(vector) > 1e-12)
    rows.extend([row] * len(indices))
    cols.extend((offset + indices).tolist())
    values.extend(vector[indices].astype(np.float64).tolist())


def feature_matrix(arrays: dict[str, np.ndarray], samples: Samples, strategy: StrategyConfig) -> sparse.csr_matrix:
    cumulative = arrays["investment_cumulative"]
    spatial = arrays["spatial_value"]
    buff = arrays["buff_delta"]
    rows: list[int] = []
    cols: list[int] = []
    values: list[float] = []
    interaction_offset = 2 * K
    buff_offset = interaction_offset + K * K
    spatial_index = {"off": None, "h150": 0, "h300": 1, "h600": 2}[strategy.spatial]
    for output_row, (match, round_no, viewpoint) in enumerate(zip(samples.match, samples.round, samples.side)):
        if spatial_index is None:
            self_raw = cumulative[match, round_no, viewpoint].astype(np.float64)
            other_raw = cumulative[match, round_no, 1 - viewpoint].astype(np.float64)
            self_parts = other_parts = None
        else:
            self_parts = spatial[spatial_index, match, round_no, viewpoint].astype(np.float64)
            other_parts = spatial[spatial_index, match, round_no, 1 - viewpoint].astype(np.float64)
            self_raw = self_parts.sum(axis=0)
            other_raw = other_parts.sum(axis=0)
        x, y = float(self_raw.sum()), float(other_raw.sum())
        if x <= 0.0 or y <= 0.0:
            raise ValueError(f"Non-positive board value at sample {output_row}: {x}, {y}")
        if strategy.common_denominator:
            denominator = 2.0 * x * y / (x + y)
            self_scale = other_scale = 1.0 / denominator
        else:
            self_scale, other_scale = 1.0 / x, 1.0 / y
        self_global = self_raw * self_scale
        other_global = other_raw * other_scale
        _add_entries(rows, cols, values, output_row, 0, self_global)
        _add_entries(rows, cols, values, output_row, K, other_global)
        if self_parts is None:
            interaction = np.outer(self_global, other_global)
        else:
            interaction = sum(np.outer(self_parts[m] * self_scale, other_parts[m] * other_scale) for m in range(M))
        _add_entries(rows, cols, values, output_row, interaction_offset, interaction.reshape(-1))

        if strategy.buff != "off":
            self_attack, self_health = buff[match, round_no, viewpoint]
            other_attack, other_health = buff[match, round_no, 1 - viewpoint]
            blocks = [self_global * self_attack, self_global * self_health, other_global * other_attack, other_global * other_health]
            if strategy.buff == "full_cross":
                blocks = [
                    self_global * self_attack, self_global * self_health,
                    self_global * other_attack, self_global * other_health,
                    other_global * self_attack, other_global * self_health,
                    other_global * other_attack, other_global * other_health,
                ]
            for block_index, block in enumerate(blocks):
                _add_entries(rows, cols, values, output_row, buff_offset + block_index * K, block)
    matrix = sparse.csr_matrix((np.asarray(values), (np.asarray(rows), np.asarray(cols))), shape=(len(samples), strategy.feature_dim), dtype=np.float32)
    matrix.sum_duplicates()
    return matrix


def metrics(target: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    return {
        "roc_auc": float(roc_auc_score(target, probability)),
        "accuracy": float(accuracy_score(target, probability >= 0.5)),
        "log_loss": float(log_loss(target, probability, labels=[0, 1])),
        "brier": float(brier_score_loss(target, probability)),
    }


def fit(x: sparse.csr_matrix, y: np.ndarray, c_value: float, config: dict[str, Any]) -> LogisticRegression:
    model = LogisticRegression(
        C=float(c_value), penalty="l2", solver="lbfgs", fit_intercept=True,
        max_iter=int(config["max_iter"]), tol=float(config["tol"]),
        random_state=int(config["model_seed"]),
    )
    model.fit(x, y)
    return model


def cv_search(arrays: dict[str, np.ndarray], samples: Samples, split: dict[str, Any], config: dict[str, Any], strategies: list[StrategyConfig]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selection_groups = set(split["groups"]["selection"])
    selection_mask = np.isin(samples.group, list(selection_groups))
    selection_samples = Samples(samples.match[selection_mask], samples.round[selection_mask], samples.side[selection_mask], samples.target[selection_mask], samples.group[selection_mask])
    fold_masks = []
    for fold in split["selection_folds"]:
        val = np.isin(selection_samples.group, fold["group_ids"])
        fold_masks.append((~val, val))
    all_records: list[dict[str, Any]] = []
    for index, strategy in enumerate(strategies, 1):
        print(f"search {index}/{len(strategies)} {strategy.short_name}", flush=True)
        x = feature_matrix(arrays, selection_samples, strategy)
        c_records = []
        for c_value in config["regularization_c"]:
            fold_metrics = []
            for train_mask, val_mask in fold_masks:
                model = fit(x[train_mask], selection_samples.target[train_mask], float(c_value), config)
                probability = model.predict_proba(x[val_mask])[:, 1]
                fold_metrics.append(metrics(selection_samples.target[val_mask], probability))
            aucs = np.asarray([item["roc_auc"] for item in fold_metrics])
            record = {"c": float(c_value), "fold_metrics": fold_metrics, "mean_auc": float(aucs.mean()), "std_auc": float(aucs.std(ddof=1))}
            c_records.append(record)
        selected = max(c_records, key=lambda item: (item["mean_auc"], -item["std_auc"], -item["c"]))
        all_records.append({"strategy": asdict(strategy), "name": strategy.name, "short_name": strategy.short_name, "feature_dim": strategy.feature_dim, "candidates": c_records, "selected_c": selected["c"], "selected_mean_auc": selected["mean_auc"], "selected_std_auc": selected["std_auc"]})
        del x
    ranked = sorted(all_records, key=lambda item: (item["selected_mean_auc"], -item["selected_std_auc"], -item["feature_dim"], -item["selected_c"]), reverse=True)
    for rank, item in enumerate(ranked, 1):
        item["selection_rank"] = rank
    baseline = next(item for item in ranked if item["name"] == StrategyConfig("off", "off", False).name)
    return ranked, baseline


def coefficient_tables(models: list[LogisticRegression], strategy: StrategyConfig, unit_axis: list[Any]) -> dict[str, Any]:
    matrix = np.vstack([model.coef_[0] for model in models])
    interaction = matrix[:, 2 * K:2 * K + K * K].reshape(len(models), K, K)
    mean = interaction.mean(axis=0)
    std = interaction.std(axis=0, ddof=1) if len(models) > 1 else np.zeros_like(mean)
    entries = []
    for i in range(K):
        for j in range(K):
            entries.append({"self": unit_axis[i], "opponent": unit_axis[j], "coefficient": float(mean[i, j]), "std": float(std[i, j]), "sign_consistent": bool(np.all(np.sign(interaction[:, i, j]) == np.sign(mean[i, j])))})
    buff_entries = []
    buff_count = 4 if strategy.buff == "aligned" else 8 if strategy.buff == "full_cross" else 0
    if buff_count:
        offset = 2 * K + K * K
        names = ["self×self_attack", "self×self_health", "other×other_attack", "other×other_health"]
        if strategy.buff == "full_cross":
            names = ["self×self_attack", "self×self_health", "self×other_attack", "self×other_health", "other×self_attack", "other×self_health", "other×other_attack", "other×other_health"]
        buff_matrix = matrix[:, offset:offset + buff_count * K].reshape(len(models), buff_count, K)
        for block, name in enumerate(names):
            mean_block = buff_matrix[:, block].mean(axis=0)
            std_block = buff_matrix[:, block].std(axis=0, ddof=1) if len(models) > 1 else np.zeros(K)
            for unit_index in np.argsort(np.abs(mean_block))[::-1][:10]:
                buff_entries.append({"feature": name, "unit": unit_axis[int(unit_index)], "coefficient": float(mean_block[unit_index]), "std": float(std_block[unit_index])})
        buff_entries.sort(key=lambda item: abs(item["coefficient"]), reverse=True)
    return {
        "intercept_mean": float(np.mean([model.intercept_[0] for model in models])),
        "intercept_std": float(np.std([model.intercept_[0] for model in models], ddof=1)) if len(models) > 1 else 0.0,
        "top_positive": sorted(entries, key=lambda item: item["coefficient"], reverse=True)[:40],
        "top_negative": sorted(entries, key=lambda item: item["coefficient"])[:40],
        "buff_top": buff_entries[:40],
        "coefficient_mean": matrix.mean(axis=0).tolist(),
        "coefficient_std": (matrix.std(axis=0, ddof=1) if len(models) > 1 else np.zeros(matrix.shape[1])).tolist(),
    }


def _plot_roc(path: Path, records: list[tuple[np.ndarray, np.ndarray, str]], title: str) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 5), dpi=160)
    for target, probability, label in records:
        fpr, tpr, _ = roc_curve(target, probability)
        ax.plot(fpr, tpr, label=label)
    ax.plot([0, 1], [0, 1], "k--", linewidth=0.8)
    ax.set(xlabel="False positive rate", ylabel="True positive rate", title=title)
    ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(path, format="jpg"); plt.close(fig)


def bootstrap_auc_difference(target: np.ndarray, first: np.ndarray, second: np.ndarray, groups: np.ndarray, replicates: int, seed: int) -> dict[str, float]:
    unique = np.unique(groups)
    group_rows = [np.flatnonzero(groups == group) for group in unique]
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(replicates):
        chosen = rng.integers(0, len(unique), len(unique))
        indices = np.concatenate([group_rows[index] for index in chosen])
        values.append(roc_auc_score(target[indices], first[indices]) - roc_auc_score(target[indices], second[indices]))
    quantiles = np.quantile(values, [0.025, 0.975])
    return {"mean": float(np.mean(values)), "lower": float(quantiles[0]), "upper": float(quantiles[1])}


def _format(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def write_report(path: Path, payload: dict[str, Any], unit_axis: list[Any]) -> None:
    lines = [
        "# Logistic 三策略组合实验报告", "",
        "## 实验口径", "",
        "- 目标：单回合胜负二分类 AUC；策略 4 和 discounted-return 不参与本实验。",
        "- 空间策略固定使用三个探测点共享 `w_ij`，半衰距离测试 150、300、600。",
        "- 三个互不重叠的外层测试集各约 10%；Top 3 与 C 只在独立 70% selection 集的 5 折 group CV 中选择。",
        "- 三次外层指标的方差只有三个观测值，用于稳定性参考。", "",
        "## 搜索排名", "",
        "| 排名 | 策略组合 | 维度 | C | selection AUC 均值 | selection AUC 标准差 |", "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in payload["search"]:
        lines.append(f"| {item['selection_rank']} | `{item['short_name']}` | {item['feature_dim']} | {item['selected_c']:g} | {_format(item['selected_mean_auc'])} | {_format(item['selected_std_auc'])} |")
    lines.extend(["", "## 外层测试结果", "", "| 方案 | C | test-A AUC | test-B AUC | test-C AUC | AUC 均值 | AUC 方差 | accuracy 均值 | log loss 均值 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for item in payload["evaluated"]:
        aucs = [fold["metrics"]["roc_auc"] for fold in item["folds"]]
        accs = [fold["metrics"]["accuracy"] for fold in item["folds"]]
        losses = [fold["metrics"]["log_loss"] for fold in item["folds"]]
        lines.append(f"| `{item['short_name']}` | {item['c']:g} | {_format(aucs[0])} | {_format(aucs[1])} | {_format(aucs[2])} | {_format(np.mean(aucs))} | {_format(np.var(aucs, ddof=1))} | {_format(np.mean(accs))} | {_format(np.mean(losses))} |")
    lines.extend(["", "## 与 baseline 的 AUC 差", ""])
    for item in payload["evaluated"]:
        lines.append(f"- `{item['short_name']}`：bootstrap mean={item['bootstrap']['mean']:.4f}，95% CI=[{item['bootstrap']['lower']:.4f}, {item['bootstrap']['upper']:.4f}]。")
    lines.extend(["", "## 最优方案系数", ""])
    best = payload["evaluated"][0]
    diag = best["coefficient_diagnostics"]
    lines.append(f"最优方案 `{best['short_name']}` 的三个外层训练模型截距均值为 `{diag['intercept_mean']:.6f}`，标准差为 `{diag['intercept_std']:.6f}`。系数表展示三次训练的均值；`sign_consistent` 表示三个模型符号是否一致。")
    for title, key in (("正向 Top 40", "top_positive"), ("负向 Top 40", "top_negative")):
        lines.extend(["", f"### {title}", "", "| 排名 | 兵种配对 | 均值 | 标准差 | 符号一致 |", "| ---: | --- | ---: | ---: | :---: |"])
        for rank, entry in enumerate(diag[key], 1):
            self_name = entry["self"].get("name_cn", entry["self"].get("reserved_slot", "unknown")) if isinstance(entry["self"], dict) else str(entry["self"])
            opp_name = entry["opponent"].get("name_cn", entry["opponent"].get("reserved_slot", "unknown")) if isinstance(entry["opponent"], dict) else str(entry["opponent"])
            lines.append(f"| {rank} | {self_name}A - {opp_name}B | {entry['coefficient']:.6f} | {entry['std']:.6f} | {entry['sign_consistent']} |")
    if diag.get("buff_top"):
        lines.extend(["", "### Buff 交互系数 Top 40", "", "| 排名 | 特征 | 兵种 | 均值 | 标准差 |", "| ---: | --- | --- | ---: | ---: |"])
        for rank, entry in enumerate(diag["buff_top"], 1):
            unit_name = entry["unit"].get("name_cn", entry["unit"].get("reserved_slot", "unknown")) if isinstance(entry["unit"], dict) else str(entry["unit"])
            lines.append(f"| {rank} | {entry['feature']} | {unit_name} | {entry['coefficient']:.6f} | {entry['std']:.6f} |")
    lines.extend(["", "## 数据 QC", "", f"- strategy dataset：{payload['dataset_statistics']}。", f"- 外层分组：{payload['split_summary']}。", "- buff 增量按 Cost Control -11%、Heavy Armor +17%、研究塔最高档 +10%/+24%、先进攻防战术 +30% 解析。", "- 原 dense v1 文件未被修改。", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(config: dict[str, Any], reuse_search: bool = False) -> dict[str, Any]:
    arrays, metadata = load_bundle(config)
    samples = make_samples(arrays, metadata)
    split_path = _path(config["split_path"])
    split_path.parent.mkdir(parents=True, exist_ok=True)
    split = make_split(samples, config)
    split_path.write_text(json.dumps(split, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    strategies = all_strategy_configs()
    output_dir = _path(config["output_dir"]); model_dir = _path(config["model_dir"])
    output_dir.mkdir(parents=True, exist_ok=True); model_dir.mkdir(parents=True, exist_ok=True)
    prediction_dir = output_dir / "predictions"
    prediction_dir.mkdir(parents=True, exist_ok=True)
    search_path = output_dir / "search_metrics.json"
    if reuse_search and search_path.exists():
        search = json.loads(search_path.read_text(encoding="utf-8"))
        baseline = next(item for item in search if item["name"] == StrategyConfig("off", "off", False).name)
    else:
        search, baseline = cv_search(arrays, samples, split, config, strategies)
    top3 = search[:3]
    search_path.write_text(json.dumps(search, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output_dir / "search_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle); writer.writerow(["rank", "name", "feature_dim", "selected_c", "mean_auc", "std_auc"])
        for item in search: writer.writerow([item["selection_rank"], item["name"], item["feature_dim"], item["selected_c"], item["selected_mean_auc"], item["selected_std_auc"]])

    evaluated = []
    split_names = ["test_a", "test_b", "test_c"]
    best_roc_records = []
    for rank_item in top3:
        strategy = StrategyConfig(**rank_item["strategy"])
        x_all = feature_matrix(arrays, samples, strategy)
        fold_records = []
        models = []
        all_targets, all_probabilities, all_groups = [], [], []
        for fold_index, test_name in enumerate(split_names, 1):
            test_groups = set(split["groups"][test_name])
            test_mask = np.isin(samples.group, list(test_groups))
            train_mask = ~test_mask
            model = fit(x_all[train_mask], samples.target[train_mask], rank_item["selected_c"], config)
            probability = model.predict_proba(x_all[test_mask])[:, 1]
            fold_metrics = metrics(samples.target[test_mask], probability)
            fold_records.append({"test": test_name, "sample_count": int(test_mask.sum()), "metrics": fold_metrics})
            sample_rows = np.flatnonzero(test_mask)
            with (prediction_dir / f"rank{rank_item['selection_rank']:02d}_{test_name}.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["sample", "match", "round", "side", "group", "target", "probability"])
                for local_index, sample_index in enumerate(sample_rows):
                    writer.writerow([int(sample_index), int(samples.match[sample_index]), int(samples.round[sample_index]), int(samples.side[sample_index]), samples.group[sample_index], int(samples.target[sample_index]), float(probability[local_index])])
            if rank_item["selection_rank"] == 1:
                best_roc_records.append((samples.target[test_mask], probability, f"{test_name} AUC={fold_metrics['roc_auc']:.3f}"))
            models.append(model)
            all_targets.append(samples.target[test_mask]); all_probabilities.append(probability); all_groups.append(samples.group[test_mask])
            joblib.dump({"model": model, "strategy": asdict(strategy), "selected_c": rank_item["selected_c"], "feature_dim": strategy.feature_dim}, model_dir / f"rank{rank_item['selection_rank']:02d}_fold{fold_index}.joblib")
        evaluated.append({"selection_rank": rank_item["selection_rank"], "short_name": strategy.short_name, "strategy": asdict(strategy), "c": rank_item["selected_c"], "folds": fold_records, "models": models, "targets": all_targets, "probabilities": all_probabilities, "groups": all_groups, "coefficient_diagnostics": coefficient_tables(models, strategy, metadata.get("unit_axis", []))})
        del x_all

    baseline_strategy = StrategyConfig(**baseline["strategy"])
    baseline_x = feature_matrix(arrays, samples, baseline_strategy)
    baseline_targets, baseline_probabilities, baseline_groups = [], [], []
    for test_name in split_names:
        test_mask = np.isin(samples.group, split["groups"][test_name])
        model = fit(baseline_x[~test_mask], samples.target[~test_mask], baseline["selected_c"], config)
        baseline_probability = model.predict_proba(baseline_x[test_mask])[:, 1]
        baseline_targets.append(samples.target[test_mask]); baseline_probabilities.append(baseline_probability); baseline_groups.append(samples.group[test_mask])
        sample_rows = np.flatnonzero(test_mask)
        with (prediction_dir / f"baseline_{test_name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample", "match", "round", "side", "group", "target", "probability"])
            for local_index, sample_index in enumerate(sample_rows):
                writer.writerow([int(sample_index), int(samples.match[sample_index]), int(samples.round[sample_index]), int(samples.side[sample_index]), samples.group[sample_index], int(samples.target[sample_index]), float(baseline_probability[local_index])])
    base_target = np.concatenate(baseline_targets); base_probability = np.concatenate(baseline_probabilities); base_group = np.concatenate(baseline_groups)
    for item in evaluated:
        target = np.concatenate(item["targets"]); probability = np.concatenate(item["probabilities"]); groups = np.concatenate(item["groups"])
        item["bootstrap"] = bootstrap_auc_difference(target, probability, base_probability, groups, int(config["bootstrap_replicates"]), int(config["bootstrap_seed"]))
        if item["selection_rank"] == 1:
            best_roc_records.extend([(target, probability, "Top 1 pooled AUC=" + f"{roc_auc_score(target, probability):.3f}"), (base_target, base_probability, "baseline pooled AUC=" + f"{roc_auc_score(base_target, base_probability):.3f}")])
    _plot_roc(output_dir / "best_roc_test.jpg", best_roc_records, "Best logistic strategy ROC")
    payload = {"config": config, "search": search, "evaluated": [{key: value for key, value in item.items() if key not in {"models", "targets", "probabilities", "groups"}} for item in evaluated], "dataset_statistics": metadata.get("statistics"), "split_summary": {name: split["splits"][name]["sample_count"] for name in ("selection", "test_a", "test_b", "test_c")}}
    report_path = _path(config["report_path"])
    write_report(report_path, payload, metadata.get("unit_axis", []))
    (output_dir / "final_metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/logistic_strategies.yaml")
    parser.add_argument("--reuse-search", action="store_true", help="Reuse a completed search_metrics.json")
    args = parser.parse_args()
    payload = run(read_config(args.config), reuse_search=args.reuse_search)
    print(json.dumps({"report": read_config(args.config)["report_path"], "top3": [item["short_name"] for item in payload["evaluated"]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

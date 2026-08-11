#!/usr/bin/env python3
"""Train interpretable Mechabellum logistic-regression baselines.

The ``run`` command trains six pre-declared experiments: three feature
families (main, interaction, combined) for round-winner classification and
beta-discounted soft-return prediction.  It deliberately reuses the v1 split
so results can be compared with the Transformer report.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import yaml
from scipy import sparse
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score


ROOT = Path(__file__).resolve().parent.parent
FEATURES = ("main", "interaction", "combined")
TASKS = ("round_winner", "discounted_return")
COPY_SUFFIX = re.compile(r"\(\d+\)(?=\.grbr$)")


def repo_path(value: str | Path) -> Path:
    value = Path(value)
    return value if value.is_absolute() else ROOT / value


def read_config(path: str | Path) -> dict[str, Any]:
    with repo_path(path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value)!r}")


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_replay_id(filename: str) -> str:
    return COPY_SUFFIX.sub("", filename)


def load_data(config: dict[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any], dict[str, Any]]:
    npz_path, json_path = repo_path(config["dataset_npz"]), repo_path(config["dataset_json"])
    with np.load(npz_path, allow_pickle=False) as archive:
        arrays = {key: archive[key].copy() for key in archive.files}
    metadata = json.loads(json_path.read_text(encoding="utf-8"))
    split = json.loads(repo_path(config["source_split"]).read_text(encoding="utf-8"))
    if arrays["investment_cumulative"].shape[1:] != (18, 2, 43):
        raise ValueError("Expected dense v1 shape [N,18,2,43]")
    validate_split(split, len(arrays["round_count"]))
    return arrays, metadata, split


def validate_split(split: dict[str, Any], row_count: int) -> None:
    names = ("train", "validation", "test")
    sets = {name: set(split["splits"][name]["row_indices"]) for name in names}
    if any(sets[left] & sets[right] for i, left in enumerate(names) for right in names[i + 1:]):
        raise ValueError("Split row sets overlap")
    if set().union(*sets.values()) != set(range(row_count)):
        raise ValueError("Split does not cover dense rows")
    groups = {name: set(split["splits"][name]["group_ids"]) for name in names}
    if any(groups[left] & groups[right] for i, left in enumerate(names) for right in names[i + 1:]):
        raise ValueError("A replay group crosses split boundaries")


def side_features(arrays: dict[str, np.ndarray], family: str) -> np.ndarray:
    """Return [2N, round, feature] features, with side 0 then side 1 views."""
    if family not in FEATURES:
        raise ValueError(f"Unknown feature family: {family}")
    cumulative = arrays["investment_cumulative"].astype(np.float64)
    totals = cumulative.sum(axis=-1, keepdims=True)
    valid_totals = totals[arrays["round_mask"], :, :]
    if np.any(valid_totals <= 0):
        raise ValueError("Each valid side must have positive total investment")
    # Padding has no board and therefore a zero denominator; it is masked out
    # before fitting, so map it to an all-zero feature vector rather than fail.
    shares = cumulative / np.where(totals == 0, 1.0, totals)
    self0, other0 = shares[:, :, 0], shares[:, :, 1]
    self1, other1 = other0, self0

    def compose(self_share: np.ndarray, other_share: np.ndarray) -> np.ndarray:
        main = np.concatenate((self_share, other_share), axis=-1)
        interaction = np.einsum("nti,ntj->ntij", self_share, other_share, optimize=True).reshape(*self_share.shape[:2], -1)
        if family == "main":
            return main
        if family == "interaction":
            return interaction
        return np.concatenate((main, interaction), axis=-1)

    return np.concatenate((compose(self0, other0), compose(self1, other1)), axis=0).astype(np.float32)


def rewards_and_return_targets(arrays: dict[str, np.ndarray], beta: float, reward: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return raw rewards, discounted returns and per-position maxima [N,T,2]."""
    if not 0 <= beta < 1:
        raise ValueError("beta must be in [0, 1)")
    mask = arrays["round_mask"].astype(bool)
    winner = arrays["round_winner"]
    known = mask & (winner >= 0)
    immediate = np.zeros(mask.shape + (2,), dtype=np.float64)
    for side in (0, 1):
        immediate[..., side] = np.where(known, np.where(winner == side, reward, -reward), 0.0)
    returns = np.zeros_like(immediate)
    running = np.zeros((len(mask), 2), dtype=np.float64)
    for index in range(mask.shape[1] - 1, -1, -1):
        running = immediate[:, index] + beta * running
        running *= mask[:, index, None]
        returns[:, index] = running
    remaining = np.cumsum(mask[:, ::-1], axis=1)[:, ::-1]
    maximum = reward * (1.0 - np.power(beta, remaining)) / (1.0 - beta)
    maximum = np.where(mask, maximum, 1.0)
    return immediate, returns, np.repeat(maximum[..., None], 2, axis=-1)


@dataclass
class TaskData:
    task: str
    family: str
    features: np.ndarray
    probability_target: np.ndarray
    valid_mask: np.ndarray
    raw_target: np.ndarray
    raw_maximum: np.ndarray
    rounds: np.ndarray
    groups: np.ndarray

    def selected(self, rows: list[int] | np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        count = self.features.shape[0] // 2
        rows = np.asarray(rows, dtype=np.int64)
        perspectives = np.concatenate((rows, rows + count))
        valid = self.valid_mask[perspectives]
        sequence, round_index = np.nonzero(valid)
        return (
            self.features[perspectives][sequence, round_index],
            self.probability_target[perspectives][sequence, round_index],
            self.raw_target[perspectives][sequence, round_index],
            self.raw_maximum[perspectives][sequence, round_index],
            np.stack((round_index, perspectives[sequence] % count), axis=1),
        )


def make_task_data(arrays: dict[str, np.ndarray], metadata: dict[str, Any], task: str, family: str, beta: float, reward: float) -> TaskData:
    features = side_features(arrays, family)
    mask = arrays["round_mask"].astype(bool)
    winner = arrays["round_winner"]
    count, rounds = mask.shape
    group_by_row = np.asarray([canonical_replay_id(item["file"]) for item in metadata["matches"]], dtype=object)
    groups = np.concatenate((group_by_row, group_by_row))
    if task == "round_winner":
        valid = mask & (winner >= 0)
        first = (winner == 0).astype(np.float64)
        target = np.concatenate((first, 1.0 - first), axis=0)
        raw = target * 2.0 - 1.0
        maximum = np.ones_like(raw)
    elif task == "discounted_return":
        _, returns, maximum_one = rewards_and_return_targets(arrays, beta, reward)
        normalized = np.clip(returns / maximum_one, -1.0, 1.0)
        first = (normalized[..., 0] + 1.0) / 2.0
        second = (normalized[..., 1] + 1.0) / 2.0
        target = np.concatenate((first, second), axis=0)
        raw = np.concatenate((returns[..., 0], returns[..., 1]), axis=0)
        maximum = np.concatenate((maximum_one[..., 0], maximum_one[..., 1]), axis=0)
        valid = np.concatenate((mask, mask), axis=0)
        return TaskData(task, family, features, target, valid, raw, maximum, np.broadcast_to(np.arange(rounds), (count * 2, rounds)), groups)
    else:
        raise ValueError(f"Unknown task: {task}")
    valid = np.concatenate((valid, valid), axis=0)
    return TaskData(task, family, features, target, valid, raw, maximum, np.broadcast_to(np.arange(rounds), (count * 2, rounds)), groups)


def soft_training_rows(features: np.ndarray, probability: np.ndarray) -> tuple[sparse.csr_matrix, np.ndarray, np.ndarray]:
    """Represent soft Bernoulli labels exactly with weighted hard-label rows."""
    feature_matrix = sparse.csr_matrix(features)
    return (
        sparse.vstack((feature_matrix, feature_matrix), format="csr"),
        np.concatenate((np.ones(len(probability), dtype=np.int8), np.zeros(len(probability), dtype=np.int8))),
        np.concatenate((probability, 1.0 - probability)),
    )


def binary_cross_entropy(target: np.ndarray, probability: np.ndarray) -> float:
    probability = np.clip(probability, 1e-12, 1 - 1e-12)
    return float(-np.mean(target * np.log(probability) + (1 - target) * np.log(1 - probability)))


def fit_model(features: np.ndarray, target: np.ndarray, task: str, c_value: float, max_iter: int, tol: float, seed: int, warm_start: LogisticRegression | None = None) -> LogisticRegression:
    # Matchup vectors are extremely sparse (typically only a few active units).
    # Keeping the soft-label duplication in CSR form prevents dense memory
    # expansion; L-BFGS accepts this representation and converges much faster
    # than stochastic SAGA for this small, deterministic experiment.
    model = warm_start or LogisticRegression(C=c_value, penalty="l2", solver="lbfgs", fit_intercept=True, max_iter=max_iter, tol=tol, random_state=seed, warm_start=True)
    model.set_params(C=c_value, max_iter=max_iter, tol=tol, warm_start=True)
    if task == "discounted_return":
        x, y, weight = soft_training_rows(features, target)
        model.fit(x, y, sample_weight=weight)
    else:
        model.fit(sparse.csr_matrix(features), target.astype(np.int8))
    return model


def evaluate_predictions(task: str, target: np.ndarray, raw_target: np.ndarray, maximum: np.ndarray, probability: np.ndarray, round_index: np.ndarray) -> dict[str, Any]:
    result: dict[str, Any] = {"count": int(len(target)), "cross_entropy": binary_cross_entropy(target, probability)}
    if task == "round_winner":
        result.update({
            "accuracy": float(np.mean((probability >= .5) == target)),
            "roc_auc": float(roc_auc_score(target, probability)),
            "brier": float(np.mean(np.square(probability - target))),
        })
    else:
        predicted_raw = (2.0 * probability - 1.0) * maximum
        error = predicted_raw - raw_target
        result.update({
            "rmse": float(math.sqrt(np.mean(np.square(error)))),
            "mae": float(np.mean(np.abs(error))),
            "r2": _r2(raw_target, predicted_raw),
            "pearson": _pearson(raw_target, predicted_raw),
        })
    per_round: list[dict[str, Any]] = []
    for value in np.unique(round_index):
        selected = round_index == value
        metrics = evaluate_predictions_base(task, target[selected], raw_target[selected], maximum[selected], probability[selected])
        metrics["round"] = int(value + 1)
        per_round.append(metrics)
    result["per_round"] = per_round
    return result


def evaluate_predictions_base(task: str, target: np.ndarray, raw_target: np.ndarray, maximum: np.ndarray, probability: np.ndarray) -> dict[str, Any]:
    result = {"count": int(len(target)), "cross_entropy": binary_cross_entropy(target, probability)}
    if task == "round_winner":
        result.update({"accuracy": float(np.mean((probability >= .5) == target)), "roc_auc": float(roc_auc_score(target, probability)), "brier": float(np.mean(np.square(probability - target)))})
    else:
        predicted = (2.0 * probability - 1.0) * maximum
        result.update({"rmse": float(math.sqrt(np.mean(np.square(predicted - raw_target)))), "mae": float(np.mean(np.abs(predicted - raw_target))), "r2": _r2(raw_target, predicted), "pearson": _pearson(raw_target, predicted)})
    return result


def _r2(actual: np.ndarray, predicted: np.ndarray) -> float | None:
    total = float(np.sum(np.square(actual - actual.mean())))
    return None if total == 0 else float(1 - np.sum(np.square(actual - predicted)) / total)


def _pearson(first: np.ndarray, second: np.ndarray) -> float | None:
    return None if len(first) < 2 or np.std(first) == 0 or np.std(second) == 0 else float(np.corrcoef(first, second)[0, 1])


def predict(model: LogisticRegression, features: np.ndarray) -> np.ndarray:
    return model.predict_proba(sparse.csr_matrix(features))[:, 1]


def choose_c(data: TaskData, split: dict[str, Any], config: dict[str, Any]) -> tuple[LogisticRegression, float, list[dict[str, Any]]]:
    train_x, train_y, _, _, _ = data.selected(split["splits"]["train"]["row_indices"])
    valid_x, valid_y, valid_raw, valid_max, valid_info = data.selected(split["splits"]["validation"]["row_indices"])
    candidates = []
    warm_start: LogisticRegression | None = None
    for c_value in config["regularization_c"]:
        warm_start = fit_model(train_x, train_y, data.task, float(c_value), int(config["max_iter"]), float(config["tol"]), int(config["bootstrap_seed"]), warm_start)
        probability = predict(warm_start, valid_x)
        metrics = evaluate_predictions(data.task, valid_y, valid_raw, valid_max, probability, valid_info[:, 0])
        candidates.append((float(c_value), metrics))
    selected = min(candidates, key=lambda item: (item[1]["cross_entropy"], item[0]))
    model = fit_model(train_x, train_y, data.task, selected[0], int(config["max_iter"]), float(config["tol"]), int(config["bootstrap_seed"]))
    return model, selected[0], [{"c": c, "validation": metric} for c, metric in candidates]


def bootstrap_differences(task: str, target: np.ndarray, raw: np.ndarray, maximum: np.ndarray, probability: np.ndarray, other: np.ndarray | None, groups: np.ndarray, replicates: int, seed: int) -> dict[str, Any]:
    """Cluster bootstrap metric improvements; duplicated replay files stay together."""
    unique = np.unique(groups)
    group_rows = [np.flatnonzero(groups == value) for value in unique]
    rng = np.random.default_rng(seed)
    if task == "round_winner":
        comparisons = {"vs_random_auc": ("roc_auc", .5, 1), "vs_random_cross_entropy": ("cross_entropy", math.log(2), -1)}
        if other is not None:
            comparisons["vs_other_auc"] = ("roc_auc", None, 1)
            comparisons["vs_other_cross_entropy"] = ("cross_entropy", None, -1)
    else:
        comparisons = {"vs_random_cross_entropy": ("cross_entropy", math.log(2), -1), "vs_random_rmse": ("rmse", None, -1)}
        if other is not None:
            comparisons["vs_other_cross_entropy"] = ("cross_entropy", None, -1)
            comparisons["vs_other_rmse"] = ("rmse", None, -1)
    values = {name: [] for name in comparisons}
    for _ in range(replicates):
        selected = np.concatenate([group_rows[index] for index in rng.integers(0, len(group_rows), len(group_rows))])
        try:
            current = evaluate_predictions_base(task, target[selected], raw[selected], maximum[selected], probability[selected])
            reference = evaluate_predictions_base(task, target[selected], raw[selected], maximum[selected], other[selected]) if other is not None else None
            random_reference = None
            if reference is None and task == "discounted_return":
                random_reference = evaluate_predictions_base(
                    task,
                    target[selected],
                    raw[selected],
                    maximum[selected],
                    np.full(np.count_nonzero(selected), 0.5, dtype=np.float64),
                )
        except ValueError:
            continue
        for name, (metric, baseline, direction) in comparisons.items():
            compared = (reference or random_reference)[metric] if baseline is None else baseline
            values[name].append(direction * (current[metric] - compared))
    return {name: {"mean_improvement": float(np.mean(value)), "ci95": [float(np.quantile(value, .025)), float(np.quantile(value, .975))], "significantly_better": bool(np.quantile(value, .025) > 0)} for name, value in values.items() if value}


def write_predictions(path: Path, task: str, family: str, split_name: str, target: np.ndarray, raw: np.ndarray, maximum: np.ndarray, probability: np.ndarray, info: np.ndarray, groups: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["task", "feature_family", "split", "group_id", "row_index", "round", "target_probability", "target_raw", "maximum_absolute_return", "predicted_probability", "predicted_raw"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for index in range(len(target)):
            writer.writerow({"task": task, "feature_family": family, "split": split_name, "group_id": groups[index], "row_index": int(info[index, 1]), "round": int(info[index, 0] + 1), "target_probability": float(target[index]), "target_raw": float(raw[index]), "maximum_absolute_return": float(maximum[index]), "predicted_probability": float(probability[index]), "predicted_raw": float((2 * probability[index] - 1) * maximum[index])})


def _unit_label(unit: Any, index: int | None = None) -> str:
    """Return a report-safe display name for a unit-axis entry.

    Known units carry a Chinese name, while reserved future-unit slots do not.
    The fallback keeps those slots distinguishable instead of failing while
    rendering a coefficient report.
    """
    if isinstance(unit, dict):
        for key in ("name_cn", "name_en", "reserved_slot"):
            value = unit.get(key)
            if value:
                return str(value)
        if unit.get("unit_id") is not None:
            return f"unit_{unit['unit_id']}"
    if unit is not None:
        return str(unit)
    return f"unit_{index}" if index is not None else "unknown_unit"


def coefficient_diagnostics(model: LogisticRegression, family: str, unit_axis: list[Any]) -> dict[str, Any]:
    result = {"intercept": float(model.intercept_[0])}
    if family == "main":
        return result
    offset = 86 if family == "combined" else 0
    matrix = model.coef_[0, offset:offset + 43 * 43].reshape(43, 43)
    anti = matrix + matrix.T
    entries = []
    for i, j in np.ndindex(matrix.shape):
        entries.append({"self": unit_axis[i], "opponent": unit_axis[j], "coefficient": float(matrix[i, j])})
    result.update({"antisymmetry_mae": float(np.mean(np.abs(anti))), "top_positive": sorted(entries, key=lambda item: item["coefficient"], reverse=True)[:15], "top_negative": sorted(entries, key=lambda item: item["coefficient"])[:15]})
    return result


def _append_coefficient_table(lines: list[str], title: str, entries: list[dict[str, Any]]) -> None:
    lines.extend([f"### {title}", "", "| 排名 | 兵种配对 | 系数 |", "| ---: | --- | ---: |"])
    for rank, entry in enumerate(entries, 1):
        self_name = _unit_label(entry.get("self"))
        opponent_name = _unit_label(entry.get("opponent"))
        lines.append(f"| {rank} | {self_name}A - {opponent_name}B | {entry['coefficient']:.6f} |")
    lines.append("")


def _plot_roc(path: Path, target: np.ndarray, probability: np.ndarray, title: str) -> None:
    from sklearn.metrics import roc_curve
    path.parent.mkdir(parents=True, exist_ok=True)
    plt = _plt(); fpr, tpr, _ = roc_curve(target, probability)
    fig, ax = plt.subplots(figsize=(6, 5), dpi=150); ax.plot(fpr, tpr, label=f"AUC={roc_auc_score(target, probability):.3f}"); ax.plot([0, 1], [0, 1], "k--", label="random")
    ax.set(xlabel="False positive rate", ylabel="True positive rate", title=title); ax.legend(); fig.tight_layout(); fig.savefig(path, format="jpg"); plt.close(fig)


def _plot_calibration(path: Path, target: np.ndarray, probability: np.ndarray, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt = _plt(); bins = np.linspace(0, 1, 11); ids = np.digitize(probability, bins[1:-1]); x, y = [], []
    for index in range(10):
        current = ids == index
        if current.any(): x.append(float(probability[current].mean())); y.append(float(target[current].mean()))
    fig, ax = plt.subplots(figsize=(6, 5), dpi=150); ax.plot([0, 1], [0, 1], "k--"); ax.plot(x, y, "o-")
    ax.set(xlabel="Predicted win probability", ylabel="Observed win rate", title=title, xlim=(0, 1), ylim=(0, 1)); fig.tight_layout(); fig.savefig(path, format="jpg"); plt.close(fig)


def _plot_scatter(path: Path, raw: np.ndarray, predicted: np.ndarray, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt = _plt(); extent = max(1.0, float(max(np.abs(raw).max(), np.abs(predicted).max())))
    fig, ax = plt.subplots(figsize=(6, 6), dpi=150); ax.scatter(raw, predicted, s=10, alpha=.45, edgecolors="none"); ax.plot([-extent, extent], [-extent, extent], "k--")
    ax.set(xlabel="Ground truth return", ylabel="Predicted return", xlim=(-extent, extent), ylim=(-extent, extent), title=title); ax.set_aspect("equal", adjustable="box"); fig.tight_layout(); fig.savefig(path, format="jpg"); plt.close(fig)


def _plot_coefficients(path: Path, model: LogisticRegression, family: str, title: str) -> None:
    if family == "main": return
    path.parent.mkdir(parents=True, exist_ok=True)
    plt = _plt(); offset = 86 if family == "combined" else 0; values = model.coef_[0, offset:offset + 1849].reshape(43, 43); extent = max(float(np.abs(values).max()), 1e-8)
    fig, ax = plt.subplots(figsize=(8, 7), dpi=150); image = ax.imshow(values, cmap="coolwarm", vmin=-extent, vmax=extent); fig.colorbar(image, ax=ax, label="Log-odds coefficient")
    ax.set(xlabel="Opponent unit index", ylabel="Self unit index", title=title); fig.tight_layout(); fig.savefig(path, format="jpg"); plt.close(fig)


def _plt():
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _metrics_path(config: dict[str, Any]) -> Path:
    return repo_path(config["output_dir"]) / "final_metrics.json"


def train_all(config: dict[str, Any], tasks: tuple[str, ...] = TASKS, include_bootstrap: bool = True, families: tuple[str, ...] = FEATURES) -> dict[str, Any]:
    arrays, metadata, split = load_data(config)
    output, model_dir = repo_path(config["output_dir"]), repo_path(config["model_dir"])
    output.mkdir(parents=True, exist_ok=True); model_dir.mkdir(parents=True, exist_ok=True)
    unit_axis = metadata["unit_axis"]
    metrics_path = _metrics_path(config)
    existing = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    records: dict[str, dict[str, Any]] = existing.get("tasks", {task: {} for task in TASKS})
    for task in TASKS:
        records.setdefault(task, {})
    prediction_cache: dict[tuple[str, str], dict[str, Any]] = {}
    if include_bootstrap and set(families) != set(FEATURES):
        raise ValueError("Bootstrap requires all three feature families")
    for task in tasks:
        for family in families:
            print(f"training {task}/{family}", flush=True)
            data = make_task_data(arrays, metadata, task, family, float(config["beta"]), float(config["reward"]))
            model, selected_c, validation_grid = choose_c(data, split, config)
            test_x, test_y, test_raw, test_max, test_info = data.selected(split["splits"]["test"]["row_indices"])
            test_probability = predict(model, test_x)
            test_metrics = evaluate_predictions(task, test_y, test_raw, test_max, test_probability, test_info[:, 0])
            validation_best = min(validation_grid, key=lambda item: (item["validation"]["cross_entropy"], item["c"]))
            groups = np.asarray([data.groups[row] for row in test_info[:, 1]], dtype=object)
            package = {"format_version": 1, "task": task, "feature_family": family, "model": model, "selected_c": selected_c, "beta": float(config["beta"]), "reward": float(config["reward"]), "unit_axis": unit_axis, "dataset": {"npz": config["dataset_npz"], "npz_sha256": source_sha256(repo_path(config["dataset_npz"])), "metadata": config["dataset_json"], "metadata_sha256": source_sha256(repo_path(config["dataset_json"]))}, "split_path": config["source_split"], "split_seed": split.get("split_seed")}
            model_path = model_dir / f"{task}_{family}.joblib"; joblib.dump(package, model_path)
            write_predictions(output / "predictions" / f"{task}_{family}_test.csv", task, family, "test", test_y, test_raw, test_max, test_probability, test_info, groups)
            record = {"selected_c": selected_c, "validation_grid": validation_grid, "validation_selected": validation_best["validation"], "test": test_metrics, "model": str(model_path.relative_to(ROOT)), "prediction_csv": str((output / "predictions" / f"{task}_{family}_test.csv").relative_to(ROOT)), "coefficient_diagnostics": coefficient_diagnostics(model, family, unit_axis)}
            records[task][family] = record
            prediction_cache[(task, family)] = {"target": test_y, "raw": test_raw, "maximum": test_max, "probability": test_probability, "groups": groups, "model": model}
            image_root = output / "plots"
            if task == "round_winner":
                _plot_roc(image_root / f"{task}_{family}_roc_test.jpg", test_y, test_probability, f"{family}: round-winner ROC")
                _plot_calibration(image_root / f"{task}_{family}_calibration_test.jpg", test_y, test_probability, f"{family}: round-winner calibration")
            else:
                _plot_scatter(image_root / f"{task}_{family}_gt_vs_pred_test.jpg", test_raw, (2 * test_probability - 1) * test_max, f"{family}: beta=0.3 discounted return")
            _plot_coefficients(image_root / f"{task}_{family}_coefficients.jpg", model, family, f"{task}: {family} interaction coefficients")
            print(f"finished {task}/{family}", flush=True)
        if all(family in records[task] for family in FEATURES):
            best = min(FEATURES, key=lambda family: (records[task][family]["validation_selected"]["cross_entropy"], FEATURES.index(family)))
            records[task]["recommended_feature_family"] = best
        if include_bootstrap:
            for family in FEATURES:
                print(f"bootstrap {task}/{family}", flush=True)
                cache = prediction_cache[(task, family)]
                other = None
                if family == "interaction": other = prediction_cache[(task, "main")]["probability"]
                if family == "combined": other = prediction_cache[(task, "interaction")]["probability"]
                records[task][family]["bootstrap"] = bootstrap_differences(task, cache["target"], cache["raw"], cache["maximum"], cache["probability"], other, cache["groups"], int(config["bootstrap_replicates"]), int(config["bootstrap_seed"]))
    payload = {"format_version": 1, "config": config, "split": {name: split["splits"][name]["row_count"] for name in ("train", "validation", "test")}, "tasks": records}
    write_json(metrics_path, payload)
    if _has_all_bootstrap(payload):
        write_report(repo_path(config["report_path"]), payload)
    return payload


def _has_all_bootstrap(payload: dict[str, Any]) -> bool:
    return all("bootstrap" in payload.get("tasks", {}).get(task, {}).get(family, {}) for task in TASKS for family in FEATURES)


def add_bootstrap(config: dict[str, Any], tasks: tuple[str, ...], families: tuple[str, ...] = FEATURES) -> dict[str, Any]:
    """Add cluster bootstrap summaries to models previously produced by train."""
    metrics_path = _metrics_path(config)
    if not metrics_path.exists():
        raise ValueError("No metrics file exists; run train first")
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    arrays, metadata, split = load_data(config)
    for task in tasks:
        cache: dict[str, dict[str, Any]] = {}
        required = set(families)
        if "interaction" in required: required.add("main")
        if "combined" in required: required.add("interaction")
        for family in required:
            item = payload["tasks"].get(task, {}).get(family)
            if not item:
                raise ValueError(f"No saved model for {task}/{family}; run train --task {task} first")
            package = joblib.load(repo_path(item["model"]))
            data = make_task_data(arrays, metadata, task, family, float(package["beta"]), float(package["reward"]))
            x, target, raw, maximum, info = data.selected(split["splits"]["test"]["row_indices"])
            cache[family] = {"target": target, "raw": raw, "maximum": maximum, "probability": predict(package["model"], x), "groups": np.asarray([data.groups[row] for row in info[:, 1]], dtype=object)}
        for family in families:
            print(f"bootstrap {task}/{family}", flush=True)
            item = cache[family]
            other = cache["main"]["probability"] if family == "interaction" else cache["interaction"]["probability"] if family == "combined" else None
            payload["tasks"][task][family]["bootstrap"] = bootstrap_differences(task, item["target"], item["raw"], item["maximum"], item["probability"], other, item["groups"], int(config["bootstrap_replicates"]), int(config["bootstrap_seed"]))
    write_json(metrics_path, payload)
    if _has_all_bootstrap(payload):
        write_report(repo_path(config["report_path"]), payload)
    return payload


def _format(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def write_report(path: Path, payload: dict[str, Any]) -> None:
    lines = ["# Logistic v1 兵种克制实验报告", "", "## 协议", "", "- 数据：dense v1，固定 train/validation/test = 770/96/96 局切分。", "- 特征：main（86 维）、interaction（43×43=1849 维）、combined（1935 维）；每局同时使用双方视角。", "- reward：beta=0.3；所有已知胜负（含投降与终局）均为 ±100，null 为 0；按剩余有效回合最大绝对值归一化为软标签。", "- C 仅按验证集交叉熵选择；测试集使用 canonical replay group 的 5,000 次 cluster bootstrap。", ""]
    for task in TASKS:
        title = "单回合胜负" if task == "round_winner" else "beta=0.3 累计 reward"
        lines.extend([f"## {title}", "", f"验证集推荐特征：`{payload['tasks'][task]['recommended_feature_family']}`。", ""])
        if task == "round_winner": lines.extend(["| 特征 | C | 测试 AUC | Accuracy | Log loss | Brier |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
        else: lines.extend(["| 特征 | C | 测试软交叉熵 | RMSE | MAE | R² | Pearson |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
        for family in FEATURES:
            item, metric = payload["tasks"][task][family], payload["tasks"][task][family]["test"]
            if task == "round_winner": lines.append(f"| {family} | {item['selected_c']:g} | {_format(metric['roc_auc'])} | {_format(metric['accuracy'])} | {_format(metric['cross_entropy'])} | {_format(metric['brier'])} |")
            else: lines.append(f"| {family} | {item['selected_c']:g} | {_format(metric['cross_entropy'])} | {_format(metric['rmse'])} | {_format(metric['mae'])} | {_format(metric['r2'])} | {_format(metric['pearson'])} |")
        lines.extend(["", "Bootstrap 改善值为“模型优于参照”的正向差；95% CI 下界大于 0 记为显著。", ""])
        for family in FEATURES:
            bootstrap = payload["tasks"][task][family]["bootstrap"]
            text = "；".join(f"{name}: {value['mean_improvement']:.4f} [{value['ci95'][0]:.4f}, {value['ci95'][1]:.4f}]，显著={value['significantly_better']}" for name, value in bootstrap.items())
            lines.append(f"- `{family}`：{text}")
        lines.append("")
        if task == "round_winner":
            recommended_family = payload["tasks"][task]["recommended_feature_family"]
            diagnostics = payload["tasks"][task][recommended_family].get("coefficient_diagnostics", {})
            positive = diagnostics.get("top_positive", [])
            negative = diagnostics.get("top_negative", [])
            lines.extend([
                "## 最佳单回合模型的兵种交互权值",
                "",
                f"验证集交叉熵选出的模型为 `{task} / {recommended_family}`（C={payload['tasks'][task][recommended_family]['selected_c']:g}）。以下只列出该模型的 43×43 兵种交互部分，正负各 Top 15。",
                "",
                "配对格式为“我方兵种A - 对方兵种B”。正系数会提高 A 方的预测胜率，负系数会降低 A 方的预测胜率；系数作用于双方投资占比的乘积，不等同于单独该组合的胜率或因果克制关系。由于训练同时加入双方视角，正负极值通常会近似呈现反向镜像。",
                "",
            ])
            if positive and negative:
                _append_coefficient_table(lines, "正向权值 Top 15", positive)
                _append_coefficient_table(lines, "负向权值 Top 15", negative)
            else:
                lines.extend(["该推荐模型没有可用的兵种交互系数（例如推荐的是 `main` 特征）。", ""])
    lines.extend(["## 产物", "", "- `models/logistic_v1/`：六个可复载模型及其数据与切分元数据。", "- `artifacts/logistic_v1/final_metrics.json`：完整指标、C 搜索、bootstrap 与系数诊断。", "- `artifacts/logistic_v1/predictions/`：测试集逐样本预测；`plots/`：ROC、校准、散点和系数热图。", ""])
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text("\n".join(lines), encoding="utf-8")


def evaluate_saved(config: dict[str, Any], model_path: str, split_name: str) -> dict[str, Any]:
    package = joblib.load(repo_path(model_path)); arrays, metadata, split = load_data(config)
    data = make_task_data(arrays, metadata, package["task"], package["feature_family"], float(package["beta"]), float(package["reward"]))
    x, target, raw, maximum, info = data.selected(split["splits"][split_name]["row_indices"])
    result = evaluate_predictions(package["task"], target, raw, maximum, predict(package["model"], x), info[:, 0])
    print(json.dumps({"model": model_path, "split": split_name, "metrics": result}, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "train", "bootstrap", "evaluate")); parser.add_argument("--config", default="configs/logistic_v1.yaml")
    parser.add_argument("--model"); parser.add_argument("--split", choices=("train", "validation", "test"), default="test")
    parser.add_argument("--task", choices=TASKS, help="Limit train/bootstrap to one target for resumable runs")
    parser.add_argument("--feature", choices=FEATURES, help="Limit train to one feature family for resumable runs")
    args = parser.parse_args(); config = read_config(args.config)
    if args.command == "run":
        payload = train_all(config); print(json.dumps({"report": config["report_path"], "recommended": {task: payload["tasks"][task]["recommended_feature_family"] for task in TASKS}}, ensure_ascii=False, indent=2))
    elif args.command == "train":
        payload = train_all(config, (args.task,) if args.task else TASKS, include_bootstrap=False, families=(args.feature,) if args.feature else FEATURES)
        print(json.dumps({"trained": args.task or "all", "feature": args.feature or "all", "metrics": str(_metrics_path(config).relative_to(ROOT))}, ensure_ascii=False, indent=2))
    elif args.command == "bootstrap":
        payload = add_bootstrap(config, (args.task,) if args.task else TASKS, (args.feature,) if args.feature else FEATURES)
        print(json.dumps({"bootstrapped": args.task or "all", "feature": args.feature or "all", "report_written": _has_all_bootstrap(payload)}, ensure_ascii=False, indent=2))
    else:
        if not args.model: parser.error("evaluate requires --model")
        evaluate_saved(config, args.model, args.split)


if __name__ == "__main__":
    main()

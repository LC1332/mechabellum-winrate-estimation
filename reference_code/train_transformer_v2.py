#!/usr/bin/env python3
"""Damage-terminal v2 experiments without modifying dense v1 or v1 artifacts.

Run the complete 24-run comparison on CUDA:
  python reference_code/train_transformer_v2.py sweep --device cuda
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset
import yaml

from train_transformer import (
    ROOT, MAX_ROUNDS, CausalTransformerRegressor, ModelConfig, RewardConfig,
    TrainingConfig, baseline_predictions, discounted_returns, environment_info,
    evaluate_model, immediate_rewards, load_checkpoint_model, load_dense_dataset,
    masked_mse, metric_summary, round_budget, seed_everything, select_device,
    validate_split,
)


def path(value: str | Path) -> Path:
    value = Path(value)
    return value if value.is_absolute() else ROOT / value


def read_config(value: str | Path) -> dict[str, Any]:
    return yaml.safe_load(path(value).read_text(encoding="utf-8"))


def write_json(destination: Path, payload: Any) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_split(config: dict[str, Any], arrays: dict[str, np.ndarray]) -> dict[str, Any]:
    split = json.loads(path(config["source_split"]).read_text(encoding="utf-8"))
    validate_split(split, arrays)
    return split


def infer_damage_terminals(arrays: dict[str, np.ndarray], metadata: dict[str, Any]) -> dict[str, Any]:
    """Infer only unambiguous 1v1 terminal losses from cumulative core damage."""
    count = arrays["round_count"].astype(np.int64)
    mask = arrays["round_mask"]
    winner = arrays["round_winner"]
    outcome = arrays["round_outcome_type"]
    damage_valid = arrays["damage_valid"]
    taken = np.zeros(mask.shape + (2,), dtype=np.float32)
    for side in (0, 1):
        # winner_damage is defined as damage dealt by the round winner to its loser.
        taken[..., side] = np.where(
            mask & damage_valid & (winner == 1 - side), arrays["winner_damage"], 0.0
        )
    cumulative = np.cumsum(taken, axis=1)
    inferred, ambiguous = [], []
    excluded = {"2v2": 0, "known_terminal": 0, "no_unique_threshold": 0}
    for row in range(len(count)):
        last = int(count[row]) - 1
        if int(arrays["match_mode"][row]) != 1:
            excluded["2v2"] += 1
            continue
        if int(winner[row, last]) >= 0 or int(outcome[row, last]) != 0:
            excluded["known_terminal"] += 1
            continue
        crossings = []
        for loser in (0, 1):
            hits = np.flatnonzero(cumulative[row, :int(count[row]), loser] >= arrays["initial_health"][row, loser])
            if len(hits):
                crossings.append((loser, int(hits[0])))
        if len(crossings) != 1:
            excluded["no_unique_threshold"] += 1
            if len(crossings) == 2:
                ambiguous.append({
                    "row_index": row,
                    "file": metadata["matches"][row]["file"],
                    "reason": "both_sides_reach_initial_health",
                    "crossings": [
                        {"loser_side": loser, "round": index + 1,
                         "cumulative_damage": float(cumulative[row, index, loser]),
                         "initial_health": float(arrays["initial_health"][row, loser])}
                        for loser, index in crossings
                    ],
                })
            continue
        loser, index = crossings[0]
        victor = 1 - loser
        # This makes the inference auditable instead of silently trusting an
        # aggregate: the decisive round must itself identify the victor.
        if int(winner[row, index]) != victor or not bool(damage_valid[row, index]):
            raise ValueError(f"Damage attribution contradiction at row {row}")
        inferred.append({
            "row_index": row,
            "file": metadata["matches"][row]["file"],
            "loser_side": loser,
            "winner_side": victor,
            "crossing_round": index + 1,
            "original_round_count": int(count[row]),
            "masked_after_crossing_rounds": int(count[row]) - index - 1,
            "cumulative_damage": float(cumulative[row, index, loser]),
            "initial_health": float(arrays["initial_health"][row, loser]),
            "round_damage": float(arrays["winner_damage"][row, index]),
        })
    return {
        "schema_version": 1,
        "rule": "1v1, null terminal, exactly one side cumulative winner_damage >= initial_health; terminal at first crossing",
        "inferred": inferred,
        "ambiguous": ambiguous,
        "summary": {
            "inferred_row_count": len(inferred),
            "inferred_unique_group_count": len({item["file"].replace("(1).grbr", ".grbr") for item in inferred}),
            "ambiguous_1v1_count": len(ambiguous),
            "masked_after_crossing_round_count": sum(item["masked_after_crossing_rounds"] for item in inferred),
            "excluded": excluded,
        },
    }


def build_supervision(arrays: dict[str, np.ndarray], reward: RewardConfig, audit: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return effective rewards, returns, and masks after terminal truncation."""
    effective_mask = arrays["round_mask"].copy()
    rewards = immediate_rewards(arrays, reward)
    for item in audit["inferred"]:
        row, index, victor = item["row_index"], item["crossing_round"] - 1, item["winner_side"]
        effective_mask[row, index + 1:] = False
        rewards[row, index] = (-reward.terminal, reward.terminal) if victor == 1 else (reward.terminal, -reward.terminal)
        rewards[row, index + 1:] = 0.0
    rewards *= effective_mask[..., None]
    return rewards, discounted_returns(rewards, effective_mask, reward.beta), effective_mask


def make_task_arrays(arrays: dict[str, np.ndarray], task: str, returns: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Always produce paired side0/side1 views; mode selects them later."""
    cumulative = arrays["investment_cumulative"].astype(np.float32)
    delta = arrays["investment_delta"].astype(np.float32)
    budget = round_budget(cumulative.shape[1])
    scaled_cumulative = cumulative / np.cumsum(budget)[None, :, None, None]
    scaled_delta = delta / budget[None, :, None, None]
    if task == "q":
        previous = np.zeros_like(scaled_cumulative)
        previous[:, 1:] = scaled_cumulative[:, :-1]
        left = np.concatenate((previous[:, :, 0], previous[:, :, 1], scaled_delta[:, :, 0]), axis=-1)
        right = np.concatenate((previous[:, :, 1], previous[:, :, 0], scaled_delta[:, :, 1]), axis=-1)
    elif task == "v":
        left = np.concatenate((scaled_cumulative[:, :, 0], scaled_cumulative[:, :, 1]), axis=-1)
        right = np.concatenate((scaled_cumulative[:, :, 1], scaled_cumulative[:, :, 0]), axis=-1)
    else:
        raise ValueError("task must be q or v")
    features = np.concatenate((left, right), axis=0).astype(np.float32)
    targets = np.concatenate((returns[:, :, 0], returns[:, :, 1]), axis=0).astype(np.float32)
    masks = np.concatenate((mask, mask), axis=0)
    features *= masks[..., None]
    return features, targets, masks


def perspective_indices(rows: list[int], row_count: int, perspective: str) -> np.ndarray:
    base = np.asarray(rows, dtype=np.int64)
    if perspective == "side0":
        return base
    if perspective == "both":
        return np.concatenate((base, base + row_count))
    raise ValueError("perspective must be both or side0")


def checkpoint(model: CausalTransformerRegressor, task: str, depth: int, seed: int, perspective: str, epoch: int, best: float, model_config: ModelConfig, training_config: TrainingConfig, reward: RewardConfig, audit: dict[str, Any], split: dict[str, Any]) -> dict[str, Any]:
    return {
        "format_version": 2,
        "model_state": model.state_dict(), "task": task, "depth": depth, "seed": seed,
        "perspective_mode": perspective, "epoch": epoch, "best_validation_match_balanced_rmse": best,
        "input_dim": model.input_dim, "max_rounds": model.max_rounds,
        "model_config": asdict(model_config), "training_config": asdict(training_config),
        "reward_config": asdict(reward), "terminal_inference": audit["summary"],
        "effective_mask_valid_tokens": int(sum(item["original_round_count"] - item["masked_after_crossing_rounds"] for item in audit["inferred"])),
        "dataset": split["source"], "split_seed": split["split_seed"],
    }


def load_v2_model(checkpoint_path: Path, device: torch.device) -> tuple[CausalTransformerRegressor, dict[str, Any]]:
    payload = torch.load(checkpoint_path, map_location=device, weights_only=False)
    model = CausalTransformerRegressor(payload["input_dim"], payload["depth"], ModelConfig(**payload["model_config"]), payload["max_rounds"]).to(device)
    model.load_state_dict(payload["model_state"])
    return model, payload


def train_one(config: dict[str, Any], arrays: dict[str, np.ndarray], split: dict[str, Any], audit: dict[str, Any], task: str, depth: int, seed: int, perspective: str, device: torch.device) -> dict[str, Any]:
    reward = RewardConfig(beta=float(config["beta"]), **config["reward"])
    model_config, train_config = ModelConfig(**config["model"]), TrainingConfig(**config["training"])
    _, returns, effective_mask = build_supervision(arrays, reward, audit)
    features, targets, masks = make_task_arrays(arrays, task, returns, effective_mask)
    rows = arrays["round_count"].shape[0]
    train_index = perspective_indices(split["splits"]["train"]["row_indices"], rows, perspective)
    validation_index = perspective_indices(split["splits"]["validation"]["row_indices"], rows, perspective)
    seed_everything(seed)
    model = CausalTransformerRegressor(features.shape[-1], depth, model_config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=train_config.learning_rate, weight_decay=train_config.weight_decay)
    run_dir = path(config["output_dir"]) / "runs" / f"{perspective}_{task}_depth{depth}_seed{seed}"
    run_dir.mkdir(parents=True, exist_ok=True)
    best_path = run_dir / "best.pt"
    loader = DataLoader(TensorDataset(torch.from_numpy(features[train_index]), torch.from_numpy(targets[train_index] / train_config.target_scale), torch.from_numpy(masks[train_index])), batch_size=train_config.batch_size, shuffle=True, generator=torch.Generator().manual_seed(seed), pin_memory=device.type == "cuda")
    best, best_epoch, stale, history = float("inf"), 0, 0, []
    for epoch in range(1, train_config.max_epochs + 1):
        model.train(); total_loss = 0.0; tokens = 0
        for feature, target, valid in loader:
            feature, target, valid = feature.to(device), target.to(device), valid.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = masked_mse(model(feature, valid), target, valid)
            loss.backward(); nn.utils.clip_grad_norm_(model.parameters(), train_config.gradient_clip_norm); optimizer.step()
            amount = int(valid.sum()); total_loss += float(loss) * amount; tokens += amount
        validation, _ = evaluate_model(model, features, targets, masks, validation_index, device, train_config.target_scale)
        value = float(validation["match_balanced_rmse"])
        history.append({"epoch": epoch, "train_rmse": math.sqrt(total_loss / max(tokens, 1)) * train_config.target_scale, "validation_match_balanced_rmse": value})
        if value < best - 1e-8:
            best, best_epoch, stale = value, epoch, 0
            torch.save(checkpoint(model, task, depth, seed, perspective, epoch, best, model_config, train_config, reward, audit, split), best_path)
        else:
            stale += 1
        if stale >= train_config.early_stopping_patience:
            break
    best_model, _ = load_v2_model(best_path, device)
    validation, _ = evaluate_model(best_model, features, targets, masks, validation_index, device, train_config.target_scale)
    result = {"task": task, "depth": depth, "seed": seed, "perspective": perspective, "best_epoch": best_epoch, "epochs_completed": history[-1]["epoch"], "best_checkpoint": str(best_path.relative_to(ROOT)), "validation": validation, "history": history, "environment": environment_info(device)}
    write_json(run_dir / "metrics.json", result)
    return result


def choose(results: list[dict[str, Any]], task: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    groups: dict[int, list[dict[str, Any]]] = {}
    for item in results:
        if item["task"] == task:
            groups.setdefault(item["depth"], []).append(item)
    ranking = []
    for depth, runs in groups.items():
        values = np.asarray([run["validation"]["match_balanced_rmse"] for run in runs])
        ranking.append({"depth": depth, "mean": float(values.mean()), "std": float(values.std()), "runs": runs})
    ranking.sort(key=lambda item: (item["mean"], item["std"], item["depth"]))
    selected = min(ranking[0]["runs"], key=lambda item: item["validation"]["match_balanced_rmse"])
    return selected, [{key: value for key, value in item.items() if key != "runs"} for item in ranking]


def paired_metrics(model: CausalTransformerRegressor, features: np.ndarray, targets: np.ndarray, masks: np.ndarray, rows: list[int], device: torch.device, scale: float, inferred_rows: set[int]) -> dict[str, Any]:
    n = features.shape[0] // 2
    side0 = np.asarray(rows, dtype=np.int64); side1 = side0 + n; both = np.concatenate((side0, side1))
    result: dict[str, Any] = {}
    prediction = {}
    for name, index in (("side0", side0), ("side1", side1), ("combined", both)):
        result[name], prediction[name] = evaluate_model(model, features, targets, masks, index, device, scale)
    valid = masks[side0] & masks[side1]
    result["prediction_antisymmetry_mae"] = float(np.mean(np.abs(prediction["side0"][valid] + prediction["side1"][valid])))
    for name, subset in (("inferred_terminal", [row for row in rows if row in inferred_rows]), ("other", [row for row in rows if row not in inferred_rows])):
        if subset:
            index = np.asarray(subset + [row + n for row in subset], dtype=np.int64)
            result[name], _ = evaluate_model(model, features, targets, masks, index, device, scale)
    result["_combined_prediction"] = prediction["combined"]
    return result


def draw_scatter(destination: Path, target: np.ndarray, prediction: np.ndarray, mask: np.ndarray, title: str, metrics: dict[str, Any]) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x, y = target[mask], prediction[mask]; extent = max(1., float(max(np.abs(x).max(), np.abs(y).max())))
    figure, axis = plt.subplots(figsize=(7, 7), dpi=150)
    axis.scatter(x, y, alpha=.55, s=12, edgecolors="none"); axis.plot([-extent, extent], [-extent, extent], "k--", linewidth=1)
    axis.set(xlim=(-extent, extent), ylim=(-extent, extent), xlabel="Ground truth return", ylabel="Predicted return", title=title)
    axis.set_aspect("equal", adjustable="box")
    axis.text(.03, .97, f"RMSE={metrics['rmse']:.2f}\nMAE={metrics['mae']:.2f}\nR²={metrics['r2'] if metrics['r2'] is not None else float('nan'):.3f}", transform=axis.transAxes, va="top", bbox={"facecolor": "white", "alpha": .8, "edgecolor": "none"})
    figure.tight_layout(); destination.parent.mkdir(parents=True, exist_ok=True); figure.savefig(destination, format="jpg"); plt.close(figure)


def draw_curves(destination: Path, runs: list[dict[str, Any]], title: str) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figure, axis = plt.subplots(figsize=(8, 5), dpi=150)
    for run in runs:
        axis.plot([point["epoch"] for point in run["history"]], [point["validation_match_balanced_rmse"] for point in run["history"]], alpha=.65, label=f"depth {run['depth']} seed {run['seed']}")
    axis.set(xlabel="Epoch", ylabel="Validation match-balanced RMSE", title=title)
    axis.legend(fontsize=6, ncol=2); figure.tight_layout(); destination.parent.mkdir(parents=True, exist_ok=True); figure.savefig(destination, format="jpg"); plt.close(figure)


def report(config: dict[str, Any], arrays: dict[str, np.ndarray], split: dict[str, Any], audit: dict[str, Any], both_runs: list[dict[str, Any]], side_runs: list[dict[str, Any]], device: torch.device) -> dict[str, Any]:
    reward = RewardConfig(beta=float(config["beta"]), **config["reward"]); train_config = TrainingConfig(**config["training"])
    _, returns, mask = build_supervision(arrays, reward, audit)
    test_rows = split["splits"]["test"]["row_indices"]; train_rows = split["splits"]["train"]["row_indices"]
    inferred_rows = {item["row_index"] for item in audit["inferred"]}
    output, model_dir = path(config["output_dir"]), path(config["model_dir"]); model_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"environment": environment_info(device), "terminal_inference": audit["summary"], "tasks": {}}
    markdown = ["# Transformer v2：累计扣血终局修正报告", "", "## 终局标签审计", "", f"- 仅 1v1 推断：恢复 {len(audit['inferred'])} 行（109 个回放组）；双方越线的 {len(audit['ambiguous'])} 行和全部 2v2 均未推断。", f"- 首次越线回合替换为 ±1000，并屏蔽后续 {audit['summary']['masked_after_crossing_round_count']} 个回合。测试集有 {len(set(test_rows) & inferred_rows)} 个恢复终局行。", "", "## 实验结果", ""]
    for task in ("q", "v"):
        features, targets, masks = make_task_arrays(arrays, task, returns, mask)
        both_selected, ranking = choose(both_runs, task); side_selected, _ = choose(side_runs, task)
        both_model, _ = load_v2_model(path(both_selected["best_checkpoint"]), device)
        side_model, _ = load_v2_model(path(side_selected["best_checkpoint"]), device)
        v1_model, _ = load_checkpoint_model(ROOT / "models" / "transformer_v1" / f"{task}_best.pt", device)
        both_metrics = paired_metrics(both_model, features, targets, masks, test_rows, device, train_config.target_scale, inferred_rows)
        side_metrics = paired_metrics(side_model, features, targets, masks, test_rows, device, train_config.target_scale, inferred_rows)
        v1_metrics = paired_metrics(v1_model, features, targets, masks, test_rows, device, train_config.target_scale, inferred_rows)
        n = arrays["round_count"].shape[0]; train_index = np.asarray(train_rows + [row + n for row in train_rows]); test_index = np.asarray(test_rows + [row + n for row in test_rows])
        baseline = metric_summary(baseline_predictions(targets[train_index], masks[train_index], masks[test_index]), targets[test_index], masks[test_index])
        shutil.copy2(path(both_selected["best_checkpoint"]), model_dir / f"{task}_both_best.pt")
        shutil.copy2(path(side_selected["best_checkpoint"]), model_dir / f"{task}_side0_best.pt")
        draw_scatter(output / f"{task}_both_gt_vs_pred_test.jpg", targets[test_index], both_metrics.pop("_combined_prediction"), masks[test_index], f"{task.upper()} v2 both-perspective test", both_metrics["combined"])
        draw_curves(output / f"{task}_both_validation_curves.jpg", [run for run in both_runs if run["task"] == task], f"{task.upper()} v2 both-perspective validation")
        draw_curves(output / f"{task}_side0_validation_curves.jpg", [run for run in side_runs if run["task"] == task], f"{task.upper()} v2 side0-only validation")
        side_metrics.pop("_combined_prediction"); v1_metrics.pop("_combined_prediction")
        payload["tasks"][task] = {"depth_ranking_both": ranking, "selected_both": both_selected, "selected_side0": side_selected, "both": both_metrics, "side0_only": side_metrics, "v1_on_v2_labels": v1_metrics, "baseline": baseline}
        markdown.extend([f"### {task.upper()}", "", "| 条件 | 测试 combined RMSE | 恢复终局子集 RMSE | 预测反对称 MAE |", "| --- | ---: | ---: | ---: |", f"| v1 模型，v2 标签 | {v1_metrics['combined']['rmse']:.3f} | {v1_metrics['inferred_terminal']['rmse']:.3f} | {v1_metrics['prediction_antisymmetry_mae']:.3f} |", f"| v2 双视角（depth {both_selected['depth']}） | {both_metrics['combined']['rmse']:.3f} | {both_metrics['inferred_terminal']['rmse']:.3f} | {both_metrics['prediction_antisymmetry_mae']:.3f} |", f"| v2 side0-only（depth {side_selected['depth']}） | {side_metrics['combined']['rmse']:.3f} | {side_metrics['inferred_terminal']['rmse']:.3f} | {side_metrics['prediction_antisymmetry_mae']:.3f} |", f"| 修正标签训练均值基线 | {baseline['rmse']:.3f} | — | — |", "", "双视角深度排名：" + "，".join(f"{item['depth']}层 {item['mean']:.3f}±{item['std']:.3f}" for item in ranking) + "。", ""])
    write_json(output / "terminal_inference_audit.json", audit); write_json(output / "final_metrics.json", payload)
    markdown.extend(["## 产物", "", "- `models/transformer_v2_damage_terminal/*_both_best.pt`：修正终局后的双视角模型。", "- `models/transformer_v2_damage_terminal/*_side0_best.pt`：单边对照模型。", "- `artifacts/transformer_v2_damage_terminal/terminal_inference_audit.json`：全部终局推断依据。", ""])
    path(config["report_path"]).write_text("\n".join(markdown), encoding="utf-8")
    return payload


def sweep(config: dict[str, Any], device: torch.device) -> dict[str, Any]:
    arrays, metadata, _, _ = load_dense_dataset(config); split = load_split(config, arrays); audit = infer_damage_terminals(arrays, metadata)
    output = path(config["output_dir"]); write_json(output / "terminal_inference_audit.json", audit)
    both = []
    for task in ("q", "v"):
        for depth in config["depths"]:
            for seed in config["seeds"]:
                print(f"running both task={task} depth={depth} seed={seed}", flush=True)
                both.append(train_one(config, arrays, split, audit, task, int(depth), int(seed), "both", device))
    side = []
    for task in ("q", "v"):
        selected, _ = choose(both, task)
        for seed in config["seeds"]:
            print(f"running side0 task={task} depth={selected['depth']} seed={seed}", flush=True)
            side.append(train_one(config, arrays, split, audit, task, int(selected["depth"]), int(seed), "side0", device))
    with (output / "sweep_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["perspective", "task", "depth", "seed", "best_epoch", "validation_match_balanced_rmse", "checkpoint"]); writer.writeheader()
        for item in both + side: writer.writerow({"perspective": item["perspective"], "task": item["task"], "depth": item["depth"], "seed": item["seed"], "best_epoch": item["best_epoch"], "validation_match_balanced_rmse": item["validation"]["match_balanced_rmse"], "checkpoint": item["best_checkpoint"]})
    write_json(output / "sweep_metrics.json", {"both": both, "side0": side})
    return report(config, arrays, split, audit, both, side, device)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "sweep")); parser.add_argument("--config", default="configs/transformer_v2_damage_terminal.yaml"); parser.add_argument("--device", default="auto")
    args = parser.parse_args(); config = read_config(args.config); arrays, metadata, _, _ = load_dense_dataset(config); audit = infer_damage_terminals(arrays, metadata)
    if args.command == "audit":
        write_json(path(config["output_dir"]) / "terminal_inference_audit.json", audit); print(json.dumps(audit["summary"], ensure_ascii=False)); return
    print(json.dumps(sweep(config, select_device(args.device)), ensure_ascii=False))


if __name__ == "__main__":
    main()

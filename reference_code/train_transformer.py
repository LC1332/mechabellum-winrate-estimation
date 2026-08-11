#!/usr/bin/env python3
"""Train and evaluate causal Q/V Transformers on the dense v1 replay data.

Examples (run from the repository root):

  /www/wensi/robot/.env/venvs/lerobot-libero/bin/python \
    reference_code/train_transformer.py make-split
  /www/wensi/robot/.env/venvs/lerobot-libero/bin/python \
    reference_code/train_transformer.py sweep --device auto

The split file is deliberately part of the model contract: demo code must reuse
it rather than sampling a new "test" match set.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import random
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

# Configure CuBLAS before importing torch so CUDA runs can honor PyTorch's
# deterministic-algorithm setting when the driver supports it.
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import numpy as np
import torch
from torch import Tensor, nn
from torch.utils.data import DataLoader, TensorDataset
import yaml


ROOT = Path(__file__).resolve().parent.parent
MAX_ROUNDS = 18
COPY_SUFFIX = re.compile(r"\(\d+\)(?=\.grbr$)")


@dataclass(frozen=True)
class RewardConfig:
    beta: float = 0.5
    battle: float = 100.0
    surrender: float = 200.0
    terminal: float = 1000.0


@dataclass(frozen=True)
class ModelConfig:
    d_model: int = 64
    nhead: int = 4
    dim_feedforward: int = 128
    dropout: float = 0.1


@dataclass(frozen=True)
class TrainingConfig:
    batch_size: int = 64
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4
    max_epochs: int = 300
    early_stopping_patience: int = 30
    gradient_clip_norm: float = 1.0
    target_scale: float = 100.0


def repo_path(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def read_config(path: str | Path) -> dict[str, Any]:
    with repo_path(path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value)!r}")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default) + "\n", encoding="utf-8")


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def round_budget(max_rounds: int = MAX_ROUNDS) -> np.ndarray:
    """README-approved total budget for each one-indexed battle round."""
    if max_rounds < 1:
        raise ValueError("max_rounds must be positive")
    values = np.arange(1, max_rounds + 1, dtype=np.float32) * 200.0
    values[0] = 900.0
    return values


def canonical_replay_id(filename: str) -> str:
    """Remove Explorer-style copy suffixes while retaining the real replay id."""
    return COPY_SUFFIX.sub("", filename)


def load_dense_dataset(config: dict[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any], Path, Path]:
    npz_path = repo_path(config["dataset_npz"])
    json_path = repo_path(config["dataset_json"])
    with np.load(npz_path, allow_pickle=False) as archive:
        arrays = {key: archive[key].copy() for key in archive.files}
    metadata = json.loads(json_path.read_text(encoding="utf-8"))
    if arrays["investment_delta"].shape[1:] != (MAX_ROUNDS, 2, 43):
        raise ValueError(f"Unexpected dense dataset shape {arrays['investment_delta'].shape}")
    if len(metadata["matches"]) != arrays["investment_delta"].shape[0]:
        raise ValueError("Metadata matches and NPZ rows disagree")
    return arrays, metadata, npz_path, json_path


def match_stratum(arrays: dict[str, np.ndarray], row: int) -> str:
    count = int(arrays["round_count"][row])
    length_bin = "le6" if count <= 6 else "r7" if count == 7 else "r8" if count == 8 else "r9" if count == 9 else "ge10"
    mode = int(arrays["match_mode"][row])
    surrender = int(np.any(arrays["round_outcome_type"][row] == 2))
    return f"mode{mode}|{length_bin}|surrender{surrender}"


def _allocate_group_splits(
    groups: list[dict[str, Any]], train_fraction: float, validation_fraction: float, seed: int
) -> dict[str, list[dict[str, Any]]]:
    """Deterministic, greedy stratified allocation with group integrity.

    Groups can contain duplicated rows, so target sizes are expressed in rows,
    while assignment is performed once per group.
    """
    if not math.isclose(train_fraction + validation_fraction + (1 - train_fraction - validation_fraction), 1.0):
        raise ValueError("Invalid split fractions")
    names = ("train", "validation", "test")
    fractions = {"train": train_fraction, "validation": validation_fraction, "test": 1 - train_fraction - validation_fraction}
    target = {name: sum(len(group["rows"]) for group in groups) * fractions[name] for name in names}
    assigned: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
    sizes = {name: 0 for name in names}
    by_stratum: dict[str, list[dict[str, Any]]] = {}
    for group in groups:
        by_stratum.setdefault(group["stratum"], []).append(group)
    rng = random.Random(seed)
    for stratum in sorted(by_stratum):
        candidates = by_stratum[stratum]
        rng.shuffle(candidates)
        # Large duplicate groups are assigned first to keep the global row
        # proportions stable while the prior shuffle resolves equal-size ties.
        candidates.sort(key=lambda item: len(item["rows"]), reverse=True)
        stratum_rows = sum(len(item["rows"]) for item in candidates)
        local = {name: stratum_rows * fractions[name] for name in names}
        local_sizes = {name: 0 for name in names}
        for group in candidates:
            group_rows = len(group["rows"])
            # Prefer the split furthest below its local desired size.  Then use
            # global deficit to avoid accumulating rounding errors in sparse strata.
            choice = max(
                names,
                key=lambda name: (
                    local[name] - local_sizes[name],
                    target[name] - sizes[name],
                    -names.index(name),
                ),
            )
            assigned[choice].append(group)
            sizes[choice] += group_rows
            local_sizes[choice] += group_rows
    # A tiny deterministic balancing pass improves the overall ratio without
    # breaking group integrity.  It only accepts a move if total L1 error falls.
    improved = True
    while improved:
        improved = False
        before = sum(abs(sizes[name] - target[name]) for name in names)
        for source in names:
            for group in sorted(assigned[source], key=lambda item: (len(item["rows"]), item["id"])):
                for destination in names:
                    if source == destination:
                        continue
                    amount = len(group["rows"])
                    proposed = dict(sizes)
                    proposed[source] -= amount
                    proposed[destination] += amount
                    after = sum(abs(proposed[name] - target[name]) for name in names)
                    if after + 1e-9 < before:
                        assigned[source].remove(group)
                        assigned[destination].append(group)
                        sizes = proposed
                        improved = True
                        break
                if improved:
                    break
            if improved:
                break
    return assigned


def make_split(config: dict[str, Any], overwrite: bool = False) -> dict[str, Any]:
    arrays, metadata, npz_path, json_path = load_dense_dataset(config)
    output_dir = repo_path(config["output_dir"])
    split_path = output_dir / "split_v1.json"
    if split_path.exists() and not overwrite:
        return json.loads(split_path.read_text(encoding="utf-8"))
    grouped: dict[str, list[int]] = {}
    for record in metadata["matches"]:
        grouped.setdefault(canonical_replay_id(record["file"]), []).append(int(record["row_index"]))
    groups = []
    for group_id, rows in sorted(grouped.items()):
        rows = sorted(rows)
        groups.append({"id": group_id, "rows": rows, "stratum": match_stratum(arrays, rows[0])})
    assignment = _allocate_group_splits(
        groups,
        float(config["train_fraction"]),
        float(config["validation_fraction"]),
        int(config["split_seed"]),
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "split_seed": int(config["split_seed"]),
        "source": {
            "npz": str(config["dataset_npz"]),
            "npz_sha256": source_sha256(npz_path),
            "metadata": str(config["dataset_json"]),
            "metadata_sha256": source_sha256(json_path),
        },
        "fractions": {"train": float(config["train_fraction"]), "validation": float(config["validation_fraction"]), "test": float(config["test_fraction"])},
        "splits": {},
    }
    for name, allocated in assignment.items():
        rows = sorted(row for group in allocated for row in group["rows"])
        strata: dict[str, int] = {}
        for row in rows:
            strata[match_stratum(arrays, row)] = strata.get(match_stratum(arrays, row), 0) + 1
        payload["splits"][name] = {
            "row_indices": rows,
            "files": [metadata["matches"][row]["file"] for row in rows],
            "group_ids": sorted(group["id"] for group in allocated),
            "row_count": len(rows),
            "group_count": len(allocated),
            "strata": dict(sorted(strata.items())),
        }
    write_json(split_path, payload)
    return payload


def ensure_split(config: dict[str, Any]) -> dict[str, Any]:
    return make_split(config, overwrite=False)


def validate_split(split: dict[str, Any], arrays: dict[str, np.ndarray] | None = None) -> None:
    sets = {name: set(item["row_indices"]) for name, item in split["splits"].items()}
    if sets["train"] & sets["validation"] or sets["train"] & sets["test"] or sets["validation"] & sets["test"]:
        raise ValueError("Split row sets overlap")
    if arrays is not None:
        expected = set(range(arrays["round_count"].shape[0]))
        if set().union(*sets.values()) != expected:
            raise ValueError("Split does not cover every dataset row")
    group_sets = {name: set(item["group_ids"]) for name, item in split["splits"].items()}
    if group_sets["train"] & group_sets["validation"] or group_sets["train"] & group_sets["test"] or group_sets["validation"] & group_sets["test"]:
        raise ValueError("A replay group crosses split boundaries")


def immediate_rewards(arrays: dict[str, np.ndarray], reward: RewardConfig) -> np.ndarray:
    """Build signed rewards [match, round, side] from the dense outcome enums."""
    mask = arrays["round_mask"]
    winner = arrays["round_winner"]
    outcome = arrays["round_outcome_type"]
    counts = arrays["round_count"].astype(np.int64)
    result = np.zeros(mask.shape + (2,), dtype=np.float32)
    for side in (0, 1):
        sign = np.where(winner == side, 1.0, np.where(winner >= 0, -1.0, 0.0))
        result[..., side] = np.where(outcome == 1, sign * reward.battle, 0.0)
        result[..., side] = np.where(outcome == 2, sign * reward.surrender, result[..., side])
    # Dense v1 currently has no known normal final battle.  This generic rule
    # retains the contract if a future schema supplies one: final battle reward
    # replaces +/-100 with +/-1000. Surrender remains +/-200.
    final_positions = np.zeros_like(mask)
    final_positions[np.arange(len(counts)), counts - 1] = True
    terminal_battle = final_positions & mask & (outcome == 1) & (winner >= 0)
    for side in (0, 1):
        sign = np.where(winner == side, 1.0, -1.0)
        result[..., side] = np.where(terminal_battle, sign * reward.terminal, result[..., side])
    return result * mask[..., None]


def discounted_returns(rewards: np.ndarray, mask: np.ndarray, beta: float) -> np.ndarray:
    if rewards.shape[:2] != mask.shape:
        raise ValueError("Reward and mask shapes disagree")
    returns = np.zeros_like(rewards, dtype=np.float32)
    running = np.zeros((rewards.shape[0], rewards.shape[2]), dtype=np.float32)
    for round_index in range(rewards.shape[1] - 1, -1, -1):
        running = rewards[:, round_index] + beta * running
        running *= mask[:, round_index, None]
        returns[:, round_index] = running
    return returns


def make_task_arrays(arrays: dict[str, np.ndarray], task: str, reward: RewardConfig) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return two-side-perspective features, returns, and masks.

    The first N samples view side 0 as self, the next N view side 1 as self.
    """
    if task not in {"q", "v"}:
        raise ValueError("task must be q or v")
    cumulative = arrays["investment_cumulative"].astype(np.float32)
    delta = arrays["investment_delta"].astype(np.float32)
    mask = arrays["round_mask"].astype(bool)
    budgets = round_budget(cumulative.shape[1])
    cumulative_budget = np.cumsum(budgets)
    cumulative_scaled = cumulative / cumulative_budget[None, :, None, None]
    delta_scaled = delta / budgets[None, :, None, None]
    if task == "q":
        previous = np.zeros_like(cumulative_scaled)
        previous[:, 1:] = cumulative_scaled[:, :-1]
        side_zero = np.concatenate((previous[:, :, 0], previous[:, :, 1], delta_scaled[:, :, 0]), axis=-1)
        side_one = np.concatenate((previous[:, :, 1], previous[:, :, 0], delta_scaled[:, :, 1]), axis=-1)
    else:
        side_zero = np.concatenate((cumulative_scaled[:, :, 0], cumulative_scaled[:, :, 1]), axis=-1)
        side_one = np.concatenate((cumulative_scaled[:, :, 1], cumulative_scaled[:, :, 0]), axis=-1)
    returns = discounted_returns(immediate_rewards(arrays, reward), mask, reward.beta)
    features = np.concatenate((side_zero, side_one), axis=0)
    targets = np.concatenate((returns[:, :, 0], returns[:, :, 1]), axis=0)
    masks = np.concatenate((mask, mask), axis=0)
    features *= masks[..., None]
    return features.astype(np.float32), targets.astype(np.float32), masks


class CausalTransformerRegressor(nn.Module):
    def __init__(self, input_dim: int, depth: int, config: ModelConfig, max_rounds: int = MAX_ROUNDS) -> None:
        super().__init__()
        self.input_dim = input_dim
        self.depth = depth
        self.max_rounds = max_rounds
        self.input_projection = nn.Linear(input_dim, config.d_model)
        self.position_embedding = nn.Parameter(torch.zeros(1, max_rounds, config.d_model))
        layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.nhead,
            dim_feedforward=config.dim_feedforward,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=depth, enable_nested_tensor=False)
        self.output_norm = nn.LayerNorm(config.d_model)
        self.head = nn.Linear(config.d_model, 1)
        nn.init.normal_(self.position_embedding, std=0.02)

    def forward(self, features: Tensor, valid_mask: Tensor) -> Tensor:
        if features.ndim != 3 or features.shape[-1] != self.input_dim:
            raise ValueError(f"Expected [batch, rounds, {self.input_dim}] features, got {tuple(features.shape)}")
        if features.shape[1] > self.max_rounds:
            raise ValueError("Sequence longer than configured positional embedding")
        rounds = features.shape[1]
        causal = torch.triu(torch.ones((rounds, rounds), device=features.device, dtype=torch.bool), diagonal=1)
        hidden = self.input_projection(features) + self.position_embedding[:, :rounds]
        hidden = self.encoder(hidden, mask=causal, src_key_padding_mask=~valid_mask)
        return self.head(self.output_norm(hidden)).squeeze(-1)


def masked_mse(prediction: Tensor, target: Tensor, valid_mask: Tensor) -> Tensor:
    squared = (prediction - target).square()
    return (squared * valid_mask).sum() / valid_mask.sum().clamp_min(1)


def select_device(requested: str) -> torch.device:
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(requested)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")
    return device


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def expanded_indices(rows: Iterable[int], row_count: int) -> np.ndarray:
    rows = np.asarray(list(rows), dtype=np.int64)
    return np.concatenate((rows, rows + row_count))


def metric_summary(prediction: np.ndarray, target: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    valid_prediction = prediction[mask]
    valid_target = target[mask]
    error = valid_prediction - valid_target
    mse = float(np.mean(np.square(error)))
    result: dict[str, Any] = {
        "token_count": int(mask.sum()),
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(math.sqrt(mse)),
        "r2": None,
        "pearson": None,
    }
    total = float(np.sum(np.square(valid_target - valid_target.mean())))
    if total > 0:
        result["r2"] = float(1.0 - np.sum(np.square(error)) / total)
    if len(valid_target) > 1 and np.std(valid_prediction) > 0 and np.std(valid_target) > 0:
        result["pearson"] = float(np.corrcoef(valid_prediction, valid_target)[0, 1])
    per_sequence_mse = []
    for row in range(mask.shape[0]):
        current = mask[row]
        if current.any():
            per_sequence_mse.append(float(np.mean(np.square(prediction[row, current] - target[row, current]))))
    result["match_balanced_rmse"] = float(math.sqrt(np.mean(per_sequence_mse)))
    per_round = []
    for round_index in range(mask.shape[1]):
        current = mask[:, round_index]
        if current.any():
            round_error = prediction[current, round_index] - target[current, round_index]
            per_round.append({"round": round_index + 1, "count": int(current.sum()), "mae": float(np.mean(np.abs(round_error))), "rmse": float(math.sqrt(np.mean(np.square(round_error))))})
    result["per_round"] = per_round
    return result


@torch.no_grad()
def evaluate_model(model: nn.Module, features: np.ndarray, targets: np.ndarray, masks: np.ndarray, indices: np.ndarray, device: torch.device, target_scale: float) -> tuple[dict[str, Any], np.ndarray]:
    model.eval()
    data = TensorDataset(
        torch.from_numpy(features[indices]),
        torch.from_numpy(targets[indices]),
        torch.from_numpy(masks[indices]),
    )
    loader = DataLoader(data, batch_size=256, shuffle=False)
    predictions = []
    actual = []
    valid = []
    for feature, target, mask in loader:
        feature, target, mask = feature.to(device), target.to(device), mask.to(device)
        predictions.append((model(feature, mask) * target_scale).cpu().numpy())
        actual.append(target.cpu().numpy())
        valid.append(mask.cpu().numpy())
    predicted = np.concatenate(predictions)
    actual_values = np.concatenate(actual)
    mask_values = np.concatenate(valid).astype(bool)
    return metric_summary(predicted, actual_values, mask_values), predicted


def baseline_predictions(train_targets: np.ndarray, train_masks: np.ndarray, requested_masks: np.ndarray) -> np.ndarray:
    by_round = np.zeros(train_targets.shape[1], dtype=np.float32)
    for round_index in range(len(by_round)):
        valid = train_masks[:, round_index]
        by_round[round_index] = float(train_targets[valid, round_index].mean()) if valid.any() else 0.0
    return np.broadcast_to(by_round, requested_masks.shape).copy()


def environment_info(device: torch.device) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "cuda_compiled": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "device": str(device),
    }
    if device.type == "cuda":
        payload["gpu"] = torch.cuda.get_device_name(device)
    return payload


def checkpoint_payload(
    *, model: CausalTransformerRegressor, task: str, depth: int, seed: int, epoch: int,
    best_validation: float, model_config: ModelConfig, training_config: TrainingConfig,
    reward_config: RewardConfig, config: dict[str, Any], split: dict[str, Any],
    optimizer: torch.optim.Optimizer | None = None, best_epoch: int | None = None,
    stale_epochs: int = 0,
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict() if optimizer is not None else None,
        "task": task,
        "depth": depth,
        "input_dim": model.input_dim,
        "max_rounds": model.max_rounds,
        "seed": seed,
        "epoch": epoch,
        "best_epoch": epoch if best_epoch is None else best_epoch,
        "stale_epochs": stale_epochs,
        "best_validation_match_balanced_rmse": best_validation,
        "model_config": asdict(model_config),
        "training_config": asdict(training_config),
        "reward_config": asdict(reward_config),
        "normalization": {"round_budget": round_budget().tolist(), "cumulative_budget": np.cumsum(round_budget()).tolist()},
        "dataset": split["source"],
        "split_seed": split["split_seed"],
        "split_path": "split_v1.json",
        "unit_axis": json.loads(repo_path(config["dataset_json"]).read_text(encoding="utf-8"))["unit_axis"],
    }


def load_checkpoint_model(path: str | Path, device: torch.device) -> tuple[CausalTransformerRegressor, dict[str, Any]]:
    payload = torch.load(path, map_location=device, weights_only=False)
    model_config = ModelConfig(**payload["model_config"])
    model = CausalTransformerRegressor(payload["input_dim"], payload["depth"], model_config, payload["max_rounds"]).to(device)
    model.load_state_dict(payload["model_state"])
    return model, payload


def run_experiment(config: dict[str, Any], task: str, depth: int, seed: int, device: torch.device, resume: str | None = None) -> dict[str, Any]:
    arrays, _, _, _ = load_dense_dataset(config)
    split = ensure_split(config)
    validate_split(split, arrays)
    reward_config = RewardConfig(beta=float(config["beta"]), **config["reward"])
    model_config = ModelConfig(**config["model"])
    training_config = TrainingConfig(**config["training"])
    features, targets, masks = make_task_arrays(arrays, task, reward_config)
    row_count = arrays["round_count"].shape[0]
    train_indices = expanded_indices(split["splits"]["train"]["row_indices"], row_count)
    validation_indices = expanded_indices(split["splits"]["validation"]["row_indices"], row_count)
    seed_everything(seed)
    model = CausalTransformerRegressor(features.shape[-1], depth, model_config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=training_config.learning_rate, weight_decay=training_config.weight_decay)
    start_epoch, best_epoch, best_value, stale_epochs = 1, 0, float("inf"), 0
    output_dir = repo_path(config["output_dir"])
    run_dir = output_dir / "runs" / f"{task}_depth{depth}_seed{seed}"
    run_dir.mkdir(parents=True, exist_ok=True)
    best_path, last_path = run_dir / "best.pt", run_dir / "last.pt"
    if resume:
        loaded_model, payload = load_checkpoint_model(repo_path(resume), device)
        if payload["task"] != task or payload["depth"] != depth or payload["seed"] != seed:
            raise ValueError("Resume checkpoint task/depth/seed do not match requested experiment")
        model = loaded_model
        if payload.get("optimizer_state"):
            optimizer.load_state_dict(payload["optimizer_state"])
        start_epoch = int(payload["epoch"]) + 1
        best_value = float(payload["best_validation_match_balanced_rmse"])
        best_epoch = int(payload.get("best_epoch", payload["epoch"]))
        stale_epochs = int(payload.get("stale_epochs", 0))
    train_dataset = TensorDataset(
        torch.from_numpy(features[train_indices]),
        torch.from_numpy(targets[train_indices] / training_config.target_scale),
        torch.from_numpy(masks[train_indices]),
    )
    generator = torch.Generator().manual_seed(seed)
    loader = DataLoader(train_dataset, batch_size=training_config.batch_size, shuffle=True, generator=generator, pin_memory=device.type == "cuda")
    history: list[dict[str, float]] = []
    for epoch in range(start_epoch, training_config.max_epochs + 1):
        model.train()
        total_loss = 0.0
        total_tokens = 0
        for feature, target, mask in loader:
            feature, target, mask = feature.to(device), target.to(device), mask.to(device)
            optimizer.zero_grad(set_to_none=True)
            prediction = model(feature, mask)
            loss = masked_mse(prediction, target, mask)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), training_config.gradient_clip_norm)
            optimizer.step()
            count = int(mask.sum().item())
            total_loss += float(loss.item()) * count
            total_tokens += count
        validation, _ = evaluate_model(model, features, targets, masks, validation_indices, device, training_config.target_scale)
        train_rmse = math.sqrt(total_loss / max(total_tokens, 1)) * training_config.target_scale
        value = float(validation["match_balanced_rmse"])
        history.append({"epoch": epoch, "train_rmse": train_rmse, "validation_match_balanced_rmse": value, "validation_rmse": float(validation["rmse"])})
        if value < best_value - 1e-8:
            best_value, best_epoch, stale_epochs = value, epoch, 0
            torch.save(checkpoint_payload(model=model, task=task, depth=depth, seed=seed, epoch=epoch, best_validation=value, model_config=model_config, training_config=training_config, reward_config=reward_config, config=config, split=split, optimizer=optimizer, best_epoch=best_epoch, stale_epochs=stale_epochs), best_path)
        else:
            stale_epochs += 1
        torch.save(checkpoint_payload(model=model, task=task, depth=depth, seed=seed, epoch=epoch, best_validation=best_value, model_config=model_config, training_config=training_config, reward_config=reward_config, config=config, split=split, optimizer=optimizer, best_epoch=best_epoch, stale_epochs=stale_epochs), last_path)
        if stale_epochs >= training_config.early_stopping_patience:
            break
    best_model, _ = load_checkpoint_model(best_path, device)
    validation, _ = evaluate_model(best_model, features, targets, masks, validation_indices, device, training_config.target_scale)
    result = {
        "task": task,
        "depth": depth,
        "seed": seed,
        "best_epoch": best_epoch,
        "epochs_completed": history[-1]["epoch"],
        "best_checkpoint": str(best_path.relative_to(ROOT)),
        "last_checkpoint": str(last_path.relative_to(ROOT)),
        "validation": validation,
        "history": history,
        "environment": environment_info(device),
    }
    write_json(run_dir / "metrics.json", result)
    return result


def _draw_scatter(path: Path, target: np.ndarray, prediction: np.ndarray, mask: np.ndarray, title: str, metrics: dict[str, Any]) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x, y = target[mask], prediction[mask]
    extent = max(1.0, float(max(np.abs(x).max(), np.abs(y).max())))
    figure, axis = plt.subplots(figsize=(7, 7), dpi=150)
    axis.scatter(x, y, alpha=0.55, s=12, edgecolors="none")
    axis.plot([-extent, extent], [-extent, extent], "k--", linewidth=1, label="y=x")
    axis.set(xlim=(-extent, extent), ylim=(-extent, extent), xlabel="Ground truth return", ylabel="Predicted return", title=title)
    axis.set_aspect("equal", adjustable="box")
    axis.text(0.03, 0.97, f"RMSE={metrics['rmse']:.2f}\nMAE={metrics['mae']:.2f}\nR²={metrics['r2'] if metrics['r2'] is not None else float('nan'):.3f}", transform=axis.transAxes, va="top", bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none"})
    axis.legend(loc="lower right")
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, format="jpg")
    plt.close(figure)


def _draw_curves(path: Path, results: list[dict[str, Any]], task: str) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(8, 5), dpi=150)
    for item in results:
        label = f"{task.upper()} depth {item['depth']} seed {item['seed']}"
        axis.plot([point["epoch"] for point in item["history"]], [point["validation_match_balanced_rmse"] for point in item["history"]], alpha=0.6, label=label)
    axis.set(xlabel="Epoch", ylabel="Validation match-balanced RMSE", title=f"{task.upper()} validation curves")
    axis.legend(fontsize=6, ncol=2)
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, format="jpg")
    plt.close(figure)


def choose_winner(results: list[dict[str, Any]], task: str) -> dict[str, Any]:
    candidates = [item for item in results if item["task"] == task]
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in candidates:
        grouped.setdefault(int(item["depth"]), []).append(item)
    rankings = []
    for depth, runs in grouped.items():
        values = np.asarray([run["validation"]["match_balanced_rmse"] for run in runs], dtype=np.float64)
        rankings.append({"depth": depth, "mean": float(values.mean()), "std": float(values.std(ddof=0)), "runs": runs})
    rankings.sort(key=lambda item: (item["mean"], item["std"], item["depth"]))
    best_depth = rankings[0]
    selected = min(best_depth["runs"], key=lambda item: item["validation"]["match_balanced_rmse"])
    return {"selected": selected, "rankings": [{key: value for key, value in item.items() if key != "runs"} for item in rankings]}


def write_results_csv(path: Path, results: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["task", "depth", "seed", "best_epoch", "epochs_completed", "validation_match_balanced_rmse", "validation_rmse", "validation_mae", "checkpoint"])
        writer.writeheader()
        for item in results:
            writer.writerow({"task": item["task"], "depth": item["depth"], "seed": item["seed"], "best_epoch": item["best_epoch"], "epochs_completed": item["epochs_completed"], "validation_match_balanced_rmse": item["validation"]["match_balanced_rmse"], "validation_rmse": item["validation"]["rmse"], "validation_mae": item["validation"]["mae"], "checkpoint": item["best_checkpoint"]})


def generate_report(config: dict[str, Any], results: list[dict[str, Any]], device: torch.device) -> dict[str, Any]:
    arrays, _, _, _ = load_dense_dataset(config)
    split = ensure_split(config)
    reward_config = RewardConfig(beta=float(config["beta"]), **config["reward"])
    training_config = TrainingConfig(**config["training"])
    output_dir = repo_path(config["output_dir"])
    model_dir = ROOT / "models" / "transformer_v1"
    model_dir.mkdir(parents=True, exist_ok=True)
    final: dict[str, Any] = {"environment": environment_info(device), "depth_rankings": {}, "tasks": {}}
    row_count = arrays["round_count"].shape[0]
    test_indices = expanded_indices(split["splits"]["test"]["row_indices"], row_count)
    train_indices = expanded_indices(split["splits"]["train"]["row_indices"], row_count)
    for task in ("q", "v"):
        features, targets, masks = make_task_arrays(arrays, task, reward_config)
        choice = choose_winner(results, task)
        selected = choice["selected"]
        model, payload = load_checkpoint_model(repo_path(selected["best_checkpoint"]), device)
        metrics, prediction = evaluate_model(model, features, targets, masks, test_indices, device, training_config.target_scale)
        baseline = baseline_predictions(targets[train_indices], masks[train_indices], masks[test_indices])
        baseline_metrics = metric_summary(baseline, targets[test_indices], masks[test_indices])
        destination = model_dir / f"{task}_best.pt"
        shutil.copy2(repo_path(selected["best_checkpoint"]), destination)
        _draw_scatter(output_dir / f"{task}_gt_vs_pred_test.jpg", targets[test_indices], prediction, masks[test_indices], f"{task.upper()} test: ground truth vs prediction", metrics)
        _draw_curves(output_dir / f"{task}_validation_curves.jpg", [item for item in results if item["task"] == task], task)
        final["depth_rankings"][task] = choice["rankings"]
        final["tasks"][task] = {
            "selected_run": {key: selected[key] for key in ("depth", "seed", "best_epoch", "best_checkpoint")},
            "test": metrics,
            "baseline_test": baseline_metrics,
            "model": str(destination.relative_to(ROOT)),
            "scatter": str((output_dir / f"{task}_gt_vs_pred_test.jpg").relative_to(ROOT)),
        }
    write_json(output_dir / "final_metrics.json", final)
    report = ["# Transformer v1 训练报告", "", "## 数据与协议", "", f"- 数据：{arrays['round_count'].shape[0]} 局；切分 train/validation/test = " + "/".join(str(split['splits'][name]['row_count']) for name in ('train', 'validation', 'test')) + "。", "- 终局未知的 933 局按 0 处理；正常战斗为 ±100，投降为 ±200，折扣因子为 0.5。", f"- 实际设备：`{final['environment']['device']}`；PyTorch `{final['environment']['torch']}`，CUDA 可用：`{final['environment']['cuda_available']}`。", "", "## 深度选择", ""]
    for task in ("q", "v"):
        report.append(f"### {task.upper()}")
        report.append("")
        report.append("| 层数 | 验证集 match-balanced RMSE（3 seeds） | 标准差 |")
        report.append("| ---: | ---: | ---: |")
        for item in final["depth_rankings"][task]:
            report.append(f"| {item['depth']} | {item['mean']:.3f} | {item['std']:.3f} |")
        test = final["tasks"][task]["test"]
        baseline = final["tasks"][task]["baseline_test"]
        selected = final["tasks"][task]["selected_run"]
        report.extend(["", f"选中 depth={selected['depth']}、seed={selected['seed']}、epoch={selected['best_epoch']}。", "", f"测试集：RMSE {test['rmse']:.3f}，MAE {test['mae']:.3f}，R² {test['r2'] if test['r2'] is not None else float('nan'):.3f}，Pearson {test['pearson'] if test['pearson'] is not None else float('nan'):.3f}。", f"按回合均值基线测试 RMSE：{baseline['rmse']:.3f}。", ""])
    report.extend(["## 产物", "", "- `models/transformer_v1/q_best.pt` 与 `models/transformer_v1/v_best.pt`：最终模型。", "- `artifacts/transformer_v1/q_gt_vs_pred_test.jpg` 与 `artifacts/transformer_v1/v_gt_vs_pred_test.jpg`：测试散点图。", "- `artifacts/transformer_v1/split_v1.json`：Demo 必须复用的固定切分。", ""])
    report_path = ROOT / "information" / "transformer_v1_report.md"
    report_path.write_text("\n".join(report), encoding="utf-8")
    return final


def run_sweep(config: dict[str, Any], device: torch.device) -> dict[str, Any]:
    output_dir = repo_path(config["output_dir"])
    ensure_split(config)
    results = []
    for task in ("q", "v"):
        for depth in config["depths"]:
            for seed in config["seeds"]:
                print(f"running task={task} depth={depth} seed={seed}", flush=True)
                results.append(run_experiment(config, task, int(depth), int(seed), device))
    write_json(output_dir / "sweep_metrics.json", {"runs": results})
    write_results_csv(output_dir / "sweep_metrics.csv", results)
    return generate_report(config, results, device)


def evaluate_checkpoint(config: dict[str, Any], checkpoint: str, split_name: str, device: torch.device) -> dict[str, Any]:
    arrays, _, _, _ = load_dense_dataset(config)
    split = ensure_split(config)
    model, payload = load_checkpoint_model(repo_path(checkpoint), device)
    if payload["dataset"]["npz_sha256"] != split["source"]["npz_sha256"]:
        raise ValueError("Checkpoint NPZ checksum differs from current split")
    reward_config = RewardConfig(**payload["reward_config"])
    training_config = TrainingConfig(**payload["training_config"])
    features, targets, masks = make_task_arrays(arrays, payload["task"], reward_config)
    indices = expanded_indices(split["splits"][split_name]["row_indices"], arrays["round_count"].shape[0])
    metrics, _ = evaluate_model(model, features, targets, masks, indices, device, training_config.target_scale)
    return {"checkpoint": checkpoint, "split": split_name, "metrics": metrics}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("make-split", "run", "sweep", "evaluate"))
    parser.add_argument("--config", default="configs/transformer_v1.yaml")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--task", choices=("q", "v"))
    parser.add_argument("--depth", type=int, choices=(2, 3, 4))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--resume", help="Last checkpoint from a matching run")
    parser.add_argument("--checkpoint", help="Checkpoint for evaluate")
    parser.add_argument("--split", choices=("train", "validation", "test"), default="test")
    parser.add_argument("--overwrite", action="store_true", help="Regenerate an existing split")
    args = parser.parse_args()
    config = read_config(args.config)
    if args.command == "make-split":
        split = make_split(config, overwrite=args.overwrite)
        print(json.dumps({name: item["row_count"] for name, item in split["splits"].items()}, ensure_ascii=False))
        return
    device = select_device(args.device)
    if args.command == "run":
        if args.task is None or args.depth is None or args.seed is None:
            parser.error("run requires --task, --depth and --seed")
        result = run_experiment(config, args.task, args.depth, args.seed, device, args.resume)
        print(json.dumps(result, ensure_ascii=False, default=json_default))
    elif args.command == "sweep":
        result = run_sweep(config, device)
        print(json.dumps(result, ensure_ascii=False, default=json_default))
    else:
        if not args.checkpoint:
            parser.error("evaluate requires --checkpoint")
        print(json.dumps(evaluate_checkpoint(config, args.checkpoint, args.split, device), ensure_ascii=False, default=json_default))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Selection and outer evaluation for the battle-skill economic experiment."""
from __future__ import annotations

import argparse
import csv
import json
import os
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
K = 43
SKILLS = 24


@dataclass(frozen=True)
class Samples:
    match: np.ndarray
    round: np.ndarray
    side: np.ndarray
    target: np.ndarray
    group: np.ndarray

    def __len__(self) -> int:
        return len(self.target)


def _path(value: str | Path) -> Path:
    path = Path(value); return path if path.is_absolute() else ROOT / path


def read_config(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(_path(path).read_text(encoding="utf-8"))


def load_bundle(config: dict[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    with np.load(_path(config["strategy_dataset_npz"]), allow_pickle=False) as archive:
        arrays = {key: archive[key].copy() for key in archive.files}
    metadata = json.loads(_path(config["strategy_dataset_json"]).read_text(encoding="utf-8"))
    if arrays["board_value"].shape[0] != 16 or arrays["battle_skill_value"].shape[-1] != SKILLS:
        raise ValueError("unexpected v2 dataset axes")
    return arrays, metadata


def make_samples(arrays: dict[str, np.ndarray], metadata: dict[str, Any]) -> Samples:
    valid = arrays["round_valid"].astype(bool)
    groups = np.asarray([x["group"] for x in metadata["matches"]], dtype=object)
    match, round_no, side, target, group = [], [], [], [], []
    for row, t in zip(*np.nonzero(valid)):
        for viewpoint in (0, 1):
            match.append(row); round_no.append(t); side.append(viewpoint)
            target.append(int(arrays["round_winner"][row, t] == viewpoint)); group.append(groups[row])
    return Samples(np.asarray(match), np.asarray(round_no), np.asarray(side), np.asarray(target, dtype=np.int8), np.asarray(group, dtype=object))


def _global_parts(arrays: dict[str, np.ndarray], sample_index: int, samples: Samples, economic_mask: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    match, t, viewpoint = int(samples.match[sample_index]), int(samples.round[sample_index]), int(samples.side[sample_index])
    self_parts = arrays["spatial_value"][economic_mask, match, t, viewpoint].astype(np.float64)
    other_parts = arrays["spatial_value"][economic_mask, match, t, 1 - viewpoint].astype(np.float64)
    self_raw, other_raw = self_parts.sum(axis=0), other_parts.sum(axis=0)
    x, y = float(self_raw.sum()), float(other_raw.sum())
    if x <= 0 or y <= 0: raise ValueError(f"non-positive board value at row {sample_index}: {x}, {y}")
    denominator = 2.0 * x * y / (x + y)
    return self_parts / denominator, other_parts / denominator, self_raw / denominator, other_raw / denominator, denominator


def _add(rows: list[int], cols: list[int], vals: list[float], row: int, offset: int, vector: np.ndarray) -> None:
    flat = np.asarray(vector).reshape(-1)
    indices = np.flatnonzero(np.abs(flat) > 1e-12)
    rows.extend([row] * len(indices)); cols.extend((offset + indices).tolist()); vals.extend(flat[indices].astype(np.float64).tolist())


def feature_dim(skill_mode: str) -> int:
    if skill_mode not in {"off", "opponent", "both"}: raise ValueError(skill_mode)
    return 2 * K + K * K + 4 * K + (0 if skill_mode == "off" else (2 if skill_mode == "opponent" else 4) * SKILLS * K)


def feature_matrix(arrays: dict[str, np.ndarray], samples: Samples, economic_mask: int = 0, skill_mode: str = "off") -> sparse.csr_matrix:
    """Build CSR in bounded dense chunks to avoid Python-list memory blowups."""
    n = len(samples); parts: list[sparse.csr_matrix] = []
    for start in range(0, n, 256):
        stop = min(start + 256, n)
        match = samples.match[start:stop].astype(np.int64); t = samples.round[start:stop].astype(np.int64); side = samples.side[start:stop].astype(np.int64)
        self_parts = arrays["spatial_value"][economic_mask, match, t, side].astype(np.float32)
        other_parts = arrays["spatial_value"][economic_mask, match, t, 1 - side].astype(np.float32)
        self_raw = self_parts.sum(axis=1); other_raw = other_parts.sum(axis=1)
        denominator = 2.0 * self_raw.sum(axis=1) * other_raw.sum(axis=1) / np.maximum(self_raw.sum(axis=1) + other_raw.sum(axis=1), 1e-12)
        self_parts /= denominator[:, None, None]; other_parts /= denominator[:, None, None]
        self_global = self_parts.sum(axis=1); other_global = other_parts.sum(axis=1)
        interaction = np.einsum("nmk,nml->nkl", self_parts, other_parts).reshape(stop - start, K * K)
        buff = arrays["buff_delta"][match, t]
        sa, sh = buff[np.arange(stop - start), side, 0], buff[np.arange(stop - start), side, 1]
        oa, oh = buff[np.arange(stop - start), 1 - side, 0], buff[np.arange(stop - start), 1 - side, 1]
        dense = np.concatenate((self_global, other_global, interaction, self_global * sa[:, None], self_global * sh[:, None], other_global * oa[:, None], other_global * oh[:, None]), axis=1)
        if skill_mode != "off":
            self_c = arrays["battle_skill_value"][match, t, side].astype(np.float32) / denominator[:, None]
            other_c = arrays["battle_skill_value"][match, t, 1 - side].astype(np.float32) / denominator[:, None]
            skill_blocks = [np.einsum("ns,nk->nsk", self_c, other_global).reshape(stop - start, SKILLS * K), np.einsum("ns,nk->nsk", other_c, self_global).reshape(stop - start, SKILLS * K)]
            if skill_mode == "both": skill_blocks += [np.einsum("ns,nk->nsk", self_c, self_global).reshape(stop - start, SKILLS * K), np.einsum("ns,nk->nsk", other_c, other_global).reshape(stop - start, SKILLS * K)]
            dense = np.concatenate((dense, *skill_blocks), axis=1)
        parts.append(sparse.csr_matrix(dense, dtype=np.float32))
    return sparse.vstack(parts, format="csr")


def _metrics(target: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    return {"roc_auc": float(roc_auc_score(target, probability)), "accuracy": float(accuracy_score(target, probability >= .5)), "log_loss": float(log_loss(target, probability, labels=[0, 1])), "brier": float(brier_score_loss(target, probability))}


def fit(x: sparse.csr_matrix, y: np.ndarray, config: dict[str, Any]) -> LogisticRegression:
    model = LogisticRegression(C=100.0, penalty="l2", solver="lbfgs", fit_intercept=True, max_iter=int(config["max_iter"]), tol=float(config["tol"]), random_state=int(config["model_seed"]))
    model.fit(x, y); return model


def _selection_folds(split: dict[str, Any], samples: Samples) -> list[tuple[np.ndarray, np.ndarray]]:
    selection = set(split["groups"]["selection"]); selected = np.isin(samples.group, list(selection))
    folds = []
    for fold in split["selection_folds"]:
        val = selected & np.isin(samples.group, fold["group_ids"]); folds.append((selected & ~val, val))
    return folds


def cv_score(x: sparse.csr_matrix, samples: Samples, split: dict[str, Any]) -> dict[str, float]:
    scores = []
    for train_mask, val_mask in _selection_folds(split, samples):
        model = fit(x[train_mask], samples.target[train_mask], CONFIG_FOR_SCORE)
        scores.append(roc_auc_score(samples.target[val_mask], model.predict_proba(x[val_mask])[:, 1]))
    return {"mean_auc": float(np.mean(scores)), "std_auc": float(np.std(scores, ddof=1)), "fold_auc": [float(x) for x in scores]}


def _name(mask: int, mode: str) -> str:
    names = ["subsidy", "efficient", "improved", "mass"]
    active = [names[i] for i in range(4) if mask & (1 << i)]
    return "baseline" if not active and mode == "off" else "+".join(active or ["economic-off"]) + f"__skill={mode}"


def _candidate_specs() -> tuple[list[tuple[str, int, str]], dict[str, Any]]:
    full = 15
    initial = [("baseline", 0, "off"), ("full_opponent", full, "opponent"), ("full_both", full, "both")]
    return initial, {"full_mask": full}


def _rank(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda x: (x["mean_auc"], -x["std_auc"], -x["feature_dim"], x["name"]), reverse=True)


def _plot_roc(path: Path, records: list[tuple[np.ndarray, np.ndarray, str]]) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 5), dpi=160)
    for target, prob, label in records:
        fpr, tpr, _ = roc_curve(target, prob); ax.plot(fpr, tpr, label=label)
    ax.plot([0, 1], [0, 1], "k--", linewidth=.8); ax.set(xlabel="False positive rate", ylabel="True positive rate", title="Battle-skill strategy ROC"); ax.legend(fontsize=8); fig.tight_layout(); path.parent.mkdir(parents=True, exist_ok=True); fig.savefig(path, format="jpg"); plt.close(fig)


def _bootstrap(target: np.ndarray, first: np.ndarray, second: np.ndarray, groups: np.ndarray, reps: int, seed: int) -> dict[str, float]:
    unique = np.unique(groups); rows = [np.flatnonzero(groups == group) for group in unique]; rng = np.random.default_rng(seed); values = []
    for _ in range(reps):
        chosen = rng.integers(0, len(unique), len(unique)); idx = np.concatenate([rows[i] for i in chosen]); values.append(roc_auc_score(target[idx], first[idx]) - roc_auc_score(target[idx], second[idx]))
    q = np.quantile(values, [.025, .975]); return {"mean": float(np.mean(values)), "lower": float(q[0]), "upper": float(q[1])}


def _diagnostics(models: list[LogisticRegression], metadata: dict[str, Any], mode: str) -> dict[str, Any]:
    matrix = np.vstack([m.coef_[0] for m in models]); mean = matrix.mean(axis=0); std = matrix.std(axis=0, ddof=1) if len(models) > 1 else np.zeros_like(mean)
    interaction = mean[2*K:2*K+K*K].reshape(K, K); entries = []
    axis = metadata.get("unit_axis", [])
    for i in range(K):
        for j in range(K): entries.append({"self": axis[i] if i < len(axis) else {"index": i}, "opponent": axis[j] if j < len(axis) else {"index": j}, "coefficient": float(interaction[i, j]), "std": float(std[2*K+i*K+j])})
    buff = []
    buff_names = ("self_attack_buff", "self_health_buff", "opponent_attack_buff", "opponent_health_buff")
    buff_offset = 2 * K + K * K
    for block, name in enumerate(buff_names):
        start = buff_offset + block * K
        for j in range(K):
            buff.append({"block": name, "unit": axis[j] if j < len(axis) else {"index": j}, "coefficient": float(mean[start + j]), "std": float(std[start + j])})
    skill = []
    offset = 2*K+K*K+4*K
    if mode != "off":
        count = 2 if mode == "opponent" else 4; block = mean[offset:offset+count*SKILLS*K].reshape(count, SKILLS, K)
        for b in range(count):
            for s in range(SKILLS):
                j = int(np.argmax(np.abs(block[b, s]))); skill.append({"block": b, "skill": s, "unit": axis[j] if j < len(axis) else {"index": j}, "coefficient": float(block[b, s, j])})
    return {
        "intercept_mean": float(np.mean([m.intercept_[0] for m in models])),
        "intercept_std": float(np.std([m.intercept_[0] for m in models], ddof=1)) if len(models) > 1 else 0.0,
        "top_positive": sorted(entries, key=lambda x: x["coefficient"], reverse=True)[:40],
        "top_negative": sorted(entries, key=lambda x: x["coefficient"])[:40],
        "buff_positive": sorted(buff, key=lambda x: x["coefficient"], reverse=True)[:20],
        "buff_negative": sorted(buff, key=lambda x: x["coefficient"])[:20],
        "skill_positive": sorted(skill, key=lambda x: x["coefficient"], reverse=True)[:20],
        "skill_negative": sorted(skill, key=lambda x: x["coefficient"])[:20],
        "skill_top": sorted(skill, key=lambda x: abs(x["coefficient"]), reverse=True)[:40],
    }


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    lines = ["# Logistic 战场技能与经济策略实验报告", "", "## 实验口径", "", "- 基线为修正经济分配后的 `h150+aligned+H`，Logistic `C=100`；原 v1 产物未覆盖。", "- selection 使用固定 split_v1 的 5 折 group CV；测试集只评估最终优胜者与修正基线。", "- 技能价格、卡牌 ID 和经济折算按 `battle_skill.md` 冻结。", "", "## Selection 候选", "", "| 候选 | 经济掩码 | 技能模式 | 维度 | AUC 均值 | AUC 标准差 |", "| --- | ---: | --- | ---: | ---: | ---: |"]
    for item in payload["selection"]["ranked"]: lines.append(f"| `{item['name']}` | {item['economic_mask']} | {item['skill_mode']} | {item['feature_dim']} | {item['mean_auc']:.4f} | {item['std_auc']:.4f} |")
    lines += ["", f"后向消融：`-2` {'已触发' if payload['selection']['leave_two_triggered'] else '未触发'}（{payload['selection']['leave_two_reason']}）。", "", "## 外层测试", "", "| 候选 | test-A AUC | test-B AUC | test-C AUC | AUC 均值 | accuracy 均值 | log loss 均值 | Brier 均值 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for item in payload["evaluated"]:
        auc = [x["metrics"]["roc_auc"] for x in item["folds"]]; acc = [x["metrics"]["accuracy"] for x in item["folds"]]; loss = [x["metrics"]["log_loss"] for x in item["folds"]]; brier = [x["metrics"]["brier"] for x in item["folds"]]
        lines.append(f"| `{item['name']}` | {auc[0]:.4f} | {auc[1]:.4f} | {auc[2]:.4f} | {np.mean(auc):.4f} | {np.mean(acc):.4f} | {np.mean(loss):.4f} | {np.mean(brier):.4f} |")
    lines += ["", "## 与修正基线的 AUC 差", ""]
    for item in payload["evaluated"]:
        if item.get("bootstrap") is not None:
            lines.append(f"- `{item['name']}`：bootstrap mean={item['bootstrap']['mean']:.4f}，95% CI=[{item['bootstrap']['lower']:.4f}, {item['bootstrap']['upper']:.4f}]。")
    best = payload["evaluated"][0]; diag = best["diagnostics"]
    lines += ["", "## 系数诊断", "", f"最终候选 `{best['name']}` 截距均值 `{diag['intercept_mean']:.6f}`。", "", "### 兵种交互正向 Top 40", "", "| self | opponent | coefficient |", "| --- | --- | ---: |"]
    for x in diag.get("top_positive", []): lines.append(f"| {x['self'].get('name_cn', 'unknown')} | {x['opponent'].get('name_cn', 'unknown')} | {x['coefficient']:.6f} |")
    lines += ["", "### 兵种交互负向 Top 40", "", "| self | opponent | coefficient |", "| --- | --- | ---: |"]
    for x in diag.get("top_negative", []): lines.append(f"| {x['self'].get('name_cn', 'unknown')} | {x['opponent'].get('name_cn', 'unknown')} | {x['coefficient']:.6f} |")
    lines += ["", "### Buff 交互正向 Top 20", "", "| block | unit | coefficient |", "| --- | --- | ---: |"]
    for x in diag.get("buff_positive", []): lines.append(f"| {x['block']} | {x['unit'].get('name_cn', 'unknown')} | {x['coefficient']:.6f} |")
    lines += ["", "### Buff 交互负向 Top 20", "", "| block | unit | coefficient |", "| --- | --- | ---: |"]
    for x in diag.get("buff_negative", []): lines.append(f"| {x['block']} | {x['unit'].get('name_cn', 'unknown')} | {x['coefficient']:.6f} |")
    lines += ["", "### 战场技能交互正向 Top 20", "", "| block | skill index | unit | coefficient |", "| ---: | ---: | --- | ---: |"]
    for x in diag.get("skill_positive", []): lines.append(f"| {x['block']} | {x['skill']} | {x['unit'].get('name_cn', 'unknown')} | {x['coefficient']:.6f} |")
    lines += ["", "### 战场技能交互负向 Top 20", "", "| block | skill index | unit | coefficient |", "| ---: | ---: | --- | ---: |"]
    for x in diag.get("skill_negative", []): lines.append(f"| {x['block']} | {x['skill']} | {x['unit'].get('name_cn', 'unknown')} | {x['coefficient']:.6f} |")
    stats = payload["dataset_statistics"] or {}
    split = payload.get("split_summary", {})
    lines += ["", "## 数据覆盖", "", f"- 回放对局：{stats.get('included_match_count', 'n/a')}；有效回合：{stats.get('valid_round_count', 'n/a')}；空间无效回合：{stats.get('spatial_invalid_round_count', 'n/a')}。", f"- 固定 split 样本：selection={split.get('selection', 'n/a')}，test-A={split.get('test_a', 'n/a')}，test-B={split.get('test_b', 'n/a')}，test-C={split.get('test_c', 'n/a')}。", "", "## 数据 QC", "", json.dumps(stats.get("qc", stats), ensure_ascii=False), ""]
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text("\n".join(lines), encoding="utf-8")


CONFIG_FOR_SCORE: dict[str, Any] = {}


def run(config: dict[str, Any]) -> dict[str, Any]:
    global CONFIG_FOR_SCORE; CONFIG_FOR_SCORE = config
    arrays, metadata = load_bundle(config); samples = make_samples(arrays, metadata)
    split = json.loads(_path(config["split_path"]).read_text(encoding="utf-8"))
    # The v2 sample order must remain exactly the v1 order for split reuse.
    if sum(item["sample_count"] for item in split["splits"].values()) != len(samples): raise ValueError("v2 sample count differs from split_v1")
    old_npz = _path("data/logistic_strategy_v1.npz"); old_json = _path("data/logistic_strategy_v1.json")
    if old_npz.exists() and old_json.exists():
        with np.load(old_npz, allow_pickle=False) as old_archive:
            old_arrays = {key: old_archive[key] for key in old_archive.files}
        old_meta = json.loads(old_json.read_text(encoding="utf-8")); old_samples = make_samples(old_arrays, old_meta)
        if not (np.array_equal(samples.match, old_samples.match) and np.array_equal(samples.round, old_samples.round) and np.array_equal(samples.side, old_samples.side) and np.array_equal(samples.target, old_samples.target) and np.array_equal(samples.group, old_samples.group)):
            raise ValueError("v2 sample match/round/side/group order differs from v1 split")
    output = _path(config["output_dir"]); model_dir = _path(config["model_dir"]); pred_dir = output / "predictions"; output.mkdir(parents=True, exist_ok=True); model_dir.mkdir(parents=True, exist_ok=True); pred_dir.mkdir(parents=True, exist_ok=True)
    def get_x(mask: int, mode: str) -> sparse.csr_matrix:
        return feature_matrix(arrays, samples, mask, mode)
    initial, _ = _candidate_specs(); initial_records = []
    for name, mask, mode in initial:
        score = cv_score(get_x(mask, mode), samples, split); initial_records.append({"name": name, "economic_mask": mask, "skill_mode": mode, "feature_dim": feature_dim(mode), **score})
    full_choice = max(initial_records[1:], key=lambda x: (x["mean_auc"], -x["std_auc"], -x["feature_dim"], x["skill_mode"] == "opponent"))
    chosen_mode = full_choice["skill_mode"]
    full_mask = 15
    leave_one = []
    # Strategy 4 is the skill block; strategies 5-8 are the four economic
    # bits in masks 1, 2, 4 and 8 respectively.
    one_specs = [("4", full_mask, "off"), ("5", 14, chosen_mode), ("6", 13, chosen_mode), ("7", 11, chosen_mode), ("8", 7, chosen_mode)]
    for label, mask, mode in one_specs:
        score = cv_score(get_x(mask, mode), samples, split); leave_one.append({"name": f"full-{label}", "economic_mask": mask, "skill_mode": mode, "feature_dim": feature_dim(mode), **score})
    best_one = max(leave_one, key=lambda x: x["mean_auc"]); trigger = best_one["mean_auc"] > full_choice["mean_auc"]
    leave_two = []
    if trigger:
        # Four pairs containing strategy 4 plus one economic strategy.
        for label, mask in (("4-5", 14), ("4-6", 13), ("4-7", 11), ("4-8", 7)):
            score = cv_score(get_x(mask, "off"), samples, split); leave_two.append({"name": f"full-{label}", "economic_mask": mask, "skill_mode": "off", "feature_dim": feature_dim("off"), **score})
        # Six pairs among strategies 5-8.
        for label, mask in (("5-6", 12), ("5-7", 10), ("5-8", 6), ("6-7", 9), ("6-8", 5), ("7-8", 3)):
            score = cv_score(get_x(mask, chosen_mode), samples, split); leave_two.append({"name": f"full-{label}", "economic_mask": mask, "skill_mode": chosen_mode, "feature_dim": feature_dim(chosen_mode), **score})
    ranked = _rank(initial_records + leave_one + leave_two)
    winner = ranked[0]
    selection_payload = {"initial": initial_records, "full_selected_mode": chosen_mode, "leave_one": leave_one, "leave_two": leave_two, "leave_two_triggered": trigger, "leave_two_reason": "best -1 strictly improves full model" if trigger else "best -1 does not strictly improve full model", "ranked": ranked, "winner": winner}
    # Outer evaluation: only winner and corrected baseline.
    evaluated = []; roc_records = []
    baseline = next(x for x in ranked if x["name"] == "baseline")
    for candidate in (winner, baseline):
        x = get_x(candidate["economic_mask"], candidate["skill_mode"]); folds = []; models = []; targets_all = []; probs_all = []; groups_all = []
        for fold_index, test_name in enumerate(("test_a", "test_b", "test_c"), 1):
            test_mask = np.isin(samples.group, split["groups"][test_name]); model = fit(x[~test_mask], samples.target[~test_mask], config); prob = model.predict_proba(x[test_mask])[:, 1]; metric = _metrics(samples.target[test_mask], prob); folds.append({"test": test_name, "sample_count": int(test_mask.sum()), "metrics": metric}); models.append(model); targets_all.append(samples.target[test_mask]); probs_all.append(prob); groups_all.append(samples.group[test_mask])
            with (pred_dir / f"{candidate['name']}_{test_name}.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle); writer.writerow(["sample", "match", "round", "side", "group", "target", "probability"])
                for local, sample_index in enumerate(np.flatnonzero(test_mask)): writer.writerow([int(sample_index), int(samples.match[sample_index]), int(samples.round[sample_index]), int(samples.side[sample_index]), samples.group[sample_index], int(samples.target[sample_index]), float(prob[local])])
            joblib.dump({"model": model, "candidate": candidate, "feature_dim": feature_dim(candidate["skill_mode"])}, model_dir / f"{candidate['name']}_fold{fold_index}.joblib")
        target = np.concatenate(targets_all); probability = np.concatenate(probs_all); groups = np.concatenate(groups_all); evaluated.append({"name": candidate["name"], "economic_mask": candidate["economic_mask"], "skill_mode": candidate["skill_mode"], "folds": folds, "bootstrap": None, "diagnostics": _diagnostics(models, metadata, candidate["skill_mode"]), "_target": target, "_probability": probability, "_groups": groups})
        if candidate is winner: roc_records.append((target, probability, f"winner AUC={roc_auc_score(target, probability):.3f}"))
    winner_eval = next(x for x in evaluated if x["name"] == winner["name"]); base_eval = next(x for x in evaluated if x["name"] == baseline["name"]); winner_eval["bootstrap"] = _bootstrap(winner_eval["_target"], winner_eval["_probability"], base_eval["_probability"], winner_eval["_groups"], int(config["bootstrap_replicates"]), int(config["bootstrap_seed"]))
    roc_records.append((base_eval["_target"], base_eval["_probability"], f"baseline AUC={roc_auc_score(base_eval['_target'], base_eval['_probability']):.3f}")); _plot_roc(output / "best_roc_test.jpg", roc_records)
    for item in evaluated:
        for key in ("_target", "_probability", "_groups"): item.pop(key, None)
    payload = {"config": config, "selection": selection_payload, "evaluated": evaluated, "dataset_statistics": metadata.get("statistics"), "split_summary": {name: split["splits"][name]["sample_count"] for name in ("selection", "test_a", "test_b", "test_c")}}
    _write_report(_path(config["report_path"]), payload); (output / "final_metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); (output / "selection_metrics.json").write_text(json.dumps(selection_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--config", default="configs/logistic_battle_skill.yaml"); args = parser.parse_args(); payload = run(read_config(args.config)); print(json.dumps({"winner": payload["selection"]["winner"]["name"], "evaluated": [x["name"] for x in payload["evaluated"]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()

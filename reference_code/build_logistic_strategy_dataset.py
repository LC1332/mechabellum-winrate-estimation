#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build replay-derived features for the three logistic strategy experiments.

The dense v1 dataset remains the source of truth for labels and cumulative
unit investment.  This companion dataset adds position pooling and the small
set of global attack/health modifiers needed by the strategy sweep.
"""
from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from build_dense_dataset import load_candidates
from parse_match_investment import XSI, parse_root

ROOT = Path(__file__).resolve().parent.parent
MAX_ROUNDS = 18
PROBE_COUNT = 3
HALF_DISTANCES = (150.0, 300.0, 600.0)
ATTACK_SPECIALIST = 20034
HEALTH_SPECIALIST = 20035
ADVANCED_ATTACK = 20002
ADVANCED_HEALTH = 20001
ATTACK_SKILLS = {4: 0.10, 401: 0.24}
HEALTH_SKILLS = {5: 0.10, 501: 0.24}


def _resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _normal_name(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _uid_to_axis(metadata: dict[str, Any]) -> dict[int, int]:
    result = {}
    for item in metadata["unit_axis"]:
        uid = item.get("unit_id")
        if uid is not None:
            result[int(uid)] = int(item["index"])
    return result


def _round_map(player: ET.Element) -> dict[int, ET.Element]:
    return {
        int(record.findtext("round") or 0): record
        for record in player.findall("playerRoundRecords/PlayerRoundRecord")
    }


def _post_record(rounds: dict[int, ET.Element], round_no: int) -> ET.Element:
    """Return the state after this round, falling back to the final snapshot."""
    return rounds.get(round_no + 1, rounds[round_no])


def _effective_actions(record: ET.Element) -> list[ET.Element]:
    """Undo-filter action records in the same way as the investment parser."""
    kept: list[ET.Element] = []
    for action in record.findall("actionRecords/MatchActionData"):
        if action.get(XSI) == "PAD_Undo":
            if kept:
                kept.pop()
        else:
            kept.append(action)
    return kept


def _tower_buffs(record: ET.Element) -> tuple[float, float]:
    attack = 0.0
    health = 0.0
    for action in _effective_actions(record):
        if action.get(XSI) != "PAD_ActiveEnergyTowerSkill":
            continue
        value = action.findtext("SkillID") or action.findtext("ID")
        try:
            skill_id = int(value)
        except (TypeError, ValueError):
            continue
        if skill_id in ATTACK_SKILLS:
            attack = max(attack, ATTACK_SKILLS[skill_id])
        if skill_id in HEALTH_SKILLS:
            health = max(health, HEALTH_SKILLS[skill_id])
    return attack, health


def _buff_delta(current: ET.Element, post: ET.Element) -> tuple[float, float, tuple[str, ...]]:
    officers = set()
    for item in post.findall("playerData/officers/int"):
        try:
            officers.add(int(item.text))
        except (TypeError, ValueError):
            continue
    attack = (-0.11 if ATTACK_SPECIALIST in officers else 0.0)
    health = (-0.11 if ATTACK_SPECIALIST in officers else 0.0)
    if HEALTH_SPECIALIST in officers:
        health += 0.17
    if ADVANCED_ATTACK in officers:
        attack += 0.30
    if ADVANCED_HEALTH in officers:
        health += 0.30
    tower_attack, tower_health = _tower_buffs(current)
    attack += tower_attack
    health += tower_health
    labels = []
    if ATTACK_SPECIALIST in officers:
        labels.append("cost_control")
    if HEALTH_SPECIALIST in officers:
        labels.append("heavy_armor")
    if ADVANCED_ATTACK in officers:
        labels.append("advanced_offensive")
    if ADVANCED_HEALTH in officers:
        labels.append("advanced_defensive")
    if tower_attack:
        labels.append(f"tower_attack_{tower_attack:g}")
    if tower_health:
        labels.append(f"tower_health_{tower_health:g}")
    return attack, health, tuple(labels)


def _position_units(record: ET.Element, uid_to_axis: dict[int, int]) -> tuple[dict[int, list[tuple[float, float]]], int]:
    positions: dict[int, list[tuple[float, float]]] = {}
    missing = 0
    for unit in record.findall("playerData/units/NewUnitData"):
        try:
            uid = int(unit.findtext("id"))
        except (TypeError, ValueError):
            continue
        axis = uid_to_axis.get(uid)
        if axis is None:
            continue
        node = unit.find("Position")
        x = _number(node.findtext("x") if node is not None else None)
        y = _number(node.findtext("y") if node is not None else None)
        if x is None or y is None:
            missing += 1
            continue
        positions.setdefault(axis, []).append((x, y))
    return positions, missing


def _spatial_for_match(
    board_value: np.ndarray,
    player_rounds: list[dict[int, ET.Element]],
    groups: tuple[tuple[int, ...], tuple[int, ...]],
    uid_to_axis: dict[int, int],
    probes: np.ndarray,
    half_distances: tuple[float, ...],
    spatial: np.ndarray,
    buff: np.ndarray,
    valid: np.ndarray,
    qc: Counter,
) -> None:
    for round_no in range(1, MAX_ROUNDS + 1):
        t = round_no - 1
        if round_no not in player_rounds[0]:
            continue
        side_positions = []
        side_missing = []
        for group in groups:
            merged: dict[int, list[tuple[float, float]]] = {}
            missing = 0
            for player_index in group:
                positions, absent = _position_units(_post_record(player_rounds[player_index], round_no), uid_to_axis)
                missing += absent
                for axis, values in positions.items():
                    merged.setdefault(axis, []).extend(values)
            side_positions.append(merged)
            side_missing.append(missing)

        for side, group in enumerate(groups):
            current_records = [player_rounds[index][round_no] for index in group]
            post_records = [_post_record(player_rounds[index], round_no) for index in group]
            attack_values, health_values = [], []
            for current, post in zip(current_records, post_records):
                attack, health, labels = _buff_delta(current, post)
                attack_values.append(attack)
                health_values.append(health)
                qc.update(f"buff_{label}" for label in labels)
            buff[side, t] = (float(np.mean(attack_values)), float(np.mean(health_values)))
            if side_missing[side]:
                qc["missing_position_fields"] += side_missing[side]
                valid[t] = False

            for axis, coords in side_positions[side].items():
                value = float(board_value[t, side, axis])
                if value == 0.0 or not coords:
                    if value != 0.0 and not coords:
                        qc["unallocated_value_without_position"] += 1
                    continue
                per_formation = value / len(coords)
                for x, y in coords:
                    distance = np.sqrt(np.square(probes[:, 0] - x) + np.square(probes[:, 1] - y))
                    for h_index, half_distance in enumerate(half_distances):
                        raw = np.power(2.0, -distance / half_distance)
                        alpha = raw / raw.sum()
                        spatial[h_index, t, side, :, axis] += per_formation * alpha


def _reconstruct_board_values(path: Path, root: ET.Element, player_rounds: list[dict[int, ET.Element]],
                              groups: tuple[tuple[int, ...], tuple[int, ...]], uid_to_axis: dict[int, int], qc: Counter) -> np.ndarray:
    """Reconstruct current formation value plus allocated unlock/technology cost.

    ``SellSupply`` is the replay's current purchase-plus-level value.  Unlock
    and technology actions are tracked by unit type and divided among the
    current formations of that type, matching the strategy definition.
    """
    report = parse_root(root, path, {"slots": {}, "overflow": []})
    per_player: list[np.ndarray] = []
    for player_index, rounds in enumerate(player_rounds):
        costs: dict[int, float] = {}
        values = np.zeros((MAX_ROUNDS, 43), dtype=np.float32)
        for round_no in range(1, MAX_ROUNDS + 1):
            if round_no not in rounds:
                continue
            round_data = next(item for item in report["players"][player_index]["rounds"] if item["round"] == round_no)
            for action in round_data["actions"]:
                if action["type"] not in {"unlock", "tech"} or action.get("cost") is None:
                    continue
                uid = action.get("uid")
                axis = uid_to_axis.get(int(uid)) if uid is not None else None
                if axis is None:
                    continue
                costs[axis] = costs.get(axis, 0.0) + float(action["cost"])
            post = _post_record(rounds, round_no)
            counts: Counter[int] = Counter()
            raw_values: Counter[int] = Counter()
            for unit in post.findall("playerData/units/NewUnitData"):
                try:
                    uid = int(unit.findtext("id")); axis = uid_to_axis.get(uid)
                    sell_supply = float(unit.findtext("SellSupply"))
                except (TypeError, ValueError):
                    qc["missing_sell_supply"] += 1
                    continue
                if axis is None:
                    continue
                counts[axis] += 1
                raw_values[axis] += sell_supply
            for axis, count in counts.items():
                values[round_no - 1, axis] = float(raw_values[axis]) + costs.get(axis, 0.0) / count
            for axis in costs:
                if axis not in counts and costs[axis] != 0:
                    qc["unallocated_unlock_tech_without_unit"] += 1
        per_player.append(values)
    result = np.zeros((MAX_ROUNDS, 2, 43), dtype=np.float32)
    for side, group in enumerate(groups):
        result[:, side] = np.mean([per_player[index] for index in group], axis=0)
    return result


def build_dataset(replay_dir: Path, dense_npz: Path, dense_json: Path, probes: np.ndarray | None = None,
                  half_distances: tuple[float, ...] = HALF_DISTANCES) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    with np.load(dense_npz, allow_pickle=False) as archive:
        dense = {key: archive[key].copy() for key in archive.files}
    metadata = json.loads(dense_json.read_text(encoding="utf-8"))
    candidates, skipped = load_candidates(replay_dir)
    candidate_map = {_normal_name(path.name): (path, root, mode, sequence) for path, root, mode, sequence in candidates}
    matches = metadata["matches"]
    if len(matches) != len(dense["round_count"]):
        raise ValueError("Dense metadata and NPZ row counts differ")
    if any(_normal_name(item["file"]) not in candidate_map for item in matches):
        raise ValueError("Replay directory does not contain every dense v1 source file")

    probes = np.asarray(probes if probes is not None else ((-300.0, 0.0), (0.0, 0.0), (300.0, 0.0)), dtype=np.float64)
    if probes.shape != (3, 2):
        raise ValueError("Exactly three two-dimensional probes are required")
    uid_to_axis = _uid_to_axis(metadata)
    n_matches = len(matches)
    units = dense["investment_cumulative"].shape[-1]
    spatial = np.zeros((len(half_distances), n_matches, MAX_ROUNDS, 2, PROBE_COUNT, units), dtype=np.float32)
    buff = np.zeros((n_matches, MAX_ROUNDS, 2, 2), dtype=np.float32)
    spatial_valid = np.repeat(dense["round_mask"][:, :, None], 2, axis=2)
    qc: Counter = Counter()

    for row_index, item in enumerate(matches):
        _, root, mode, _ = candidate_map[_normal_name(item["file"])]
        players = root.findall("playerRecords/PlayerRecord")
        player_rounds = [_round_map(player) for player in players]
        groups = ((0,), (1,)) if mode == "VS_1_1" else ((0, 1), (2, 3))
        board_value = _reconstruct_board_values(candidate_map[_normal_name(item["file"])][0], root, player_rounds, groups, uid_to_axis, qc)
        _spatial_for_match(
            board_value, player_rounds, groups, uid_to_axis,
            probes, half_distances, spatial[:, row_index], buff[row_index].transpose(1, 0, 2), spatial_valid[row_index], qc,
        )

    # Validity is side-aware internally but a training row requires both sides.
    round_valid = dense["round_mask"] & (dense["round_winner"] >= 0) & spatial_valid.all(axis=2)
    arrays = {
        "investment_cumulative": dense["investment_cumulative"].astype(np.float32),
        "spatial_value": spatial,
        "buff_delta": buff,
        "round_mask": dense["round_mask"].astype(bool),
        "round_winner": dense["round_winner"].astype(np.int8),
        "spatial_valid": spatial_valid.astype(bool),
        "round_valid": round_valid.astype(bool),
        "round_count": dense["round_count"].astype(np.uint8),
        "match_mode": dense["match_mode"].astype(np.uint8),
    }
    if not all(np.isfinite(value).all() for value in arrays.values() if value.dtype.kind in "fc"):
        raise ValueError("Strategy dataset contains non-finite values")
    file_groups = []
    for item in matches:
        filename = item["file"]
        while filename.endswith(tuple(f"({i}).grbr" for i in range(1, 20))):
            filename = filename.rsplit("(", 1)[0] + ".grbr"
        file_groups.append(filename)
    strategy_metadata = {
        "schema_version": 1,
        "source_dense_schema": metadata.get("schema_version"),
        "axes": {"matches": n_matches, "rounds": MAX_ROUNDS, "sides": 2, "units": units, "probes": PROBE_COUNT},
        "probe_points": probes.tolist(),
        "half_distances": list(map(float, half_distances)),
        "unit_axis": metadata.get("unit_axis", []),
        "arrays": {name: {"dtype": str(value.dtype), "shape": list(value.shape)} for name, value in arrays.items()},
        "matches": [{"row_index": i, "file": item["file"], "group": file_groups[i]} for i, item in enumerate(matches)],
        "input_skipped_count": len(skipped),
        "statistics": {
            "included_match_count": n_matches,
            "valid_round_count": int(round_valid.sum()),
            "spatial_invalid_round_count": int((dense["round_mask"] & ~spatial_valid.all(axis=2)).sum()),
            "qc": dict(sorted(qc.items())),
        },
    }
    return arrays, strategy_metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-dir", default="local_data/humen_replay")
    parser.add_argument("--dense-npz", default="data/mechabellum_dense_v1.npz")
    parser.add_argument("--dense-json", default="data/mechabellum_dense_v1.json")
    parser.add_argument("--out-npz", default="data/logistic_strategy_v1.npz")
    parser.add_argument("--out-json", default="data/logistic_strategy_v1.json")
    args = parser.parse_args()
    arrays, metadata = build_dataset(_resolve(args.replay_dir), _resolve(args.dense_npz), _resolve(args.dense_json))
    out_npz, out_json = _resolve(args.out_npz), _resolve(args.out_json)
    out_npz.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_npz, **arrays)
    out_json.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_npz.relative_to(ROOT)} and {out_json.relative_to(ROOT)}")
    print(json.dumps(metadata["statistics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

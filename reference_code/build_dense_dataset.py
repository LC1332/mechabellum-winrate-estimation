#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the portable dense v1 Mechabellum replay dataset.

The NPZ contains numeric training tensors only.  Its JSON sidecar is the
portable schema, unit-axis mapping, source-file manifest, and QC report.
"""
from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import numpy as np

from parse_match_investment import (
    CATALOG,
    ROOT,
    RESERVED_NEW_UNIT_DIMS,
    extract_xml,
    parse_root,
    slot_mapping,
)

SCHEMA_VERSION = 1
MAX_BATTLE_ROUNDS = 18
MODE_CODES = {"VS_1_1": 1, "VS_2_2": 2}
MODE_PLAYER_COUNTS = {"VS_1_1": 2, "VS_2_2": 4}
OUTCOME_NULL = 0
OUTCOME_BATTLE = 1
OUTCOME_SURRENDER = 2
SOURCE_PADDING = 0
SOURCE_SNAPSHOT = 1
SOURCE_FALLBACK = 2


def _round_sequence(player_record):
    return [int(record.findtext("round") or 0)
            for record in player_record.findall("playerRoundRecords/PlayerRoundRecord")]


def qualify_replay(root):
    """Return (mode, rounds) or a deterministic skip reason."""
    mode = root.findtext("BattleInfo/MatchMode") or "VS_1_1"
    if mode not in MODE_CODES:
        return None, "unsupported_match_mode"
    players = root.findall("playerRecords/PlayerRecord")
    if len(players) != MODE_PLAYER_COUNTS[mode]:
        return None, "unexpected_player_count"
    sequences = [_round_sequence(player) for player in players]
    if not sequences[0]:
        return None, "missing_round_records"
    if any(sequence != sequences[0] for sequence in sequences[1:]):
        return None, "unaligned_player_rounds"
    sequence = sequences[0]
    if sequence[0] != 0:
        return None, "rounds_do_not_start_at_zero"
    if sequence != list(range(sequence[-1] + 1)):
        return None, "non_contiguous_rounds"
    if sequence[-1] > MAX_BATTLE_ROUNDS:
        raise ValueError(
            f"Replay has {sequence[-1]} battle rounds, above fixed limit "
            f"{MAX_BATTLE_ROUNDS}. Refuse to truncate it silently."
        )
    return mode, sequence


def load_candidates(replay_dir):
    candidates, skipped = [], []
    for path in sorted(Path(replay_dir).glob("*.grbr")):
        try:
            root = ET.fromstring(extract_xml(path))
        except (ET.ParseError, UnicodeDecodeError, ValueError) as exc:
            # Preserve malformed input in the manifest.  Structural failures
            # from qualify_replay intentionally remain fatal below.
            skipped.append({"file": path.name, "reason": "parse_error", "detail": str(exc)})
            continue
        mode, detail = qualify_replay(root)
        if mode is None:
            skipped.append({"file": path.name, "reason": detail})
            continue
        candidates.append((path, root, mode, detail))
    return candidates, skipped


def _unit_axis(unknown_slots):
    known_ids = sorted(uid for uid, unit in CATALOG.items() if not unit["special_unit"])
    if len(known_ids) != 33:
        raise ValueError(f"Dense v1 requires 33 normal units, found {len(known_ids)}")
    axis = {uid: index for index, uid in enumerate(known_ids)}
    slot_indices = {}
    for uid_text, slot in unknown_slots["slots"].items():
        suffix = int(slot.rsplit("_", 1)[1])
        slot_indices[int(uid_text)] = len(known_ids) + suffix
    return known_ids, axis, slot_indices


def _unit_values(rounds, key, unit_axis, slot_axis):
    values = np.zeros(len(unit_axis) + RESERVED_NEW_UNIT_DIMS, dtype=np.float32)
    for round_data in rounds:
        for uid, value in round_data[key].items():
            if not value:
                continue
            index = unit_axis.get(uid, slot_axis.get(uid))
            if index is None:
                raise ValueError(f"No dense unit-axis slot for replay unit ID {uid!r}")
            values[index] += float(value)
    return values / len(rounds)


def _source_code(rounds):
    sources = {round_data["economy_confidence"] for round_data in rounds}
    if sources == {"snapshot"}:
        return SOURCE_SNAPSHOT
    if sources == {"action_fallback_last_snapshot"}:
        return SOURCE_FALLBACK
    raise ValueError(f"Inconsistent teammate investment sources: {sorted(sources)}")


def _team_outcome(side_rounds, round_no):
    """Return winner, outcome enum, damage, validity, and QC classification."""
    current = [[rounds[side][round_no] for side in range(len(rounds))]
               for rounds in side_rounds]
    surrendered = [side for side, team in enumerate(current)
                   if any(data["gave_up"] for data in team)]
    if len(surrendered) == 1:
        return 1 - surrendered[0], OUTCOME_SURRENDER, 0.0, True, "surrender"
    if len(surrendered) > 1:
        return -1, OUTCOME_NULL, 0.0, True, "conflicting_surrender"

    following = []
    for team in side_rounds:
        next_team = [rounds.get(round_no + 1) for rounds in team]
        if any(item is None for item in next_team):
            return -1, OUTCOME_NULL, 0.0, False, "unavailable"
        following.append(next_team)

    results = [[item["pre_round_fight_result"] for item in team] for team in following]
    if all(value == "Win" for value in results[0]) and all(value == "Lose" for value in results[1]):
        winner = 0
    elif all(value == "Lose" for value in results[0]) and all(value == "Win" for value in results[1]):
        winner = 1
    else:
        label = "deuce" if all(value == "Deuce" for team in results for value in team) else "conflicting_result"
        return -1, OUTCOME_NULL, 0.0, True, label

    loser = 1 - winner
    damage = max(
        0.0,
        sum(item["reactor_core"] for item in current[loser]) / len(current[loser])
        - sum(item["reactor_core"] for item in following[loser]) / len(following[loser]),
    )
    return winner, OUTCOME_BATTLE, damage, True, "battle"


def _initial_health(root, groups):
    values = []
    players = root.findall("playerRecords/PlayerRecord")
    for group in groups:
        health = [player.findtext("data/MaxReactorCore") for player in (players[index] for index in group)]
        if any(value is None for value in health):
            raise ValueError("Missing data/MaxReactorCore")
        values.append(sum(float(value) for value in health) / len(health))
    return np.asarray(values, dtype=np.float32)


def _dense_match(path, root, mode, sequence, unit_axis, slot_axis, qc):
    groups = ((0,), (1,)) if mode == "VS_1_1" else ((0, 1), (2, 3))
    report = parse_root(root, path, {"slots": {}, "overflow": []})
    round_maps = [{item["round"]: item for item in player["rounds"]}
                  for player in report["players"]]
    if any(set(round_map) != set(sequence) for round_map in round_maps):
        raise ValueError(f"Parser changed qualified round sequence for {path.name}")

    delta = np.zeros((MAX_BATTLE_ROUNDS, 2, len(unit_axis) + RESERVED_NEW_UNIT_DIMS), dtype=np.float32)
    cumulative = np.zeros_like(delta)
    source = np.zeros((MAX_BATTLE_ROUNDS, 2), dtype=np.uint8)
    winner = np.full(MAX_BATTLE_ROUNDS, -1, dtype=np.int8)
    outcome = np.zeros(MAX_BATTLE_ROUNDS, dtype=np.uint8)
    damage = np.zeros(MAX_BATTLE_ROUNDS, dtype=np.float32)
    damage_valid = np.zeros(MAX_BATTLE_ROUNDS, dtype=bool)
    mask = np.zeros(MAX_BATTLE_ROUNDS, dtype=bool)
    side_rounds = [[round_maps[player_index] for player_index in group] for group in groups]

    for round_no in sequence:
        if round_no == 0:
            continue
        index = round_no - 1
        mask[index] = True
        for side, team in enumerate(side_rounds):
            rounds = [round_map[round_no] for round_map in team]
            delta[index, side] = _unit_values(rounds, "o_by_unit", unit_axis, slot_axis)
            cumulative[index, side] = _unit_values(rounds, "cumulative_o_by_unit", unit_axis, slot_axis)
            source[index, side] = _source_code(rounds)
            for round_data in rounds:
                for action in round_data["actions"]:
                    if action["cost_basis"] == "assumed_zero_special_unit_tech":
                        qc["assumed_zero_special_unit_tech_actions"] += 1
        found_winner, found_type, found_damage, valid, result_qc = _team_outcome(side_rounds, round_no)
        winner[index] = found_winner
        outcome[index] = found_type
        damage[index] = found_damage
        damage_valid[index] = valid
        qc[f"round_outcome_{result_qc}"] += 1

    round_count = sequence[-1]
    final = cumulative[round_count - 1].copy()
    return {
        "delta": delta,
        "cumulative": cumulative,
        "final": final,
        "source": source,
        "winner": winner,
        "outcome": outcome,
        "damage": damage,
        "damage_valid": damage_valid,
        "mask": mask,
        "initial_health": _initial_health(root, groups),
        "round_count": round_count,
        "mode_code": MODE_CODES[mode],
        "manifest": {
            "file": path.name,
            "version": root.findtext("Version") or root.findtext("version"),
            "match_mode": mode,
            "round_count": round_count,
        },
    }


def _validate(arrays):
    delta = arrays["investment_delta"]
    cumulative = arrays["investment_cumulative"]
    final = arrays["investment_final"]
    mask = arrays["round_mask"]
    counts = arrays["round_count"]
    if not all(np.isfinite(array).all() for array in (delta, cumulative, final, arrays["winner_damage"], arrays["initial_health"])):
        raise ValueError("Dense dataset contains non-finite numeric values")
    for row, count in enumerate(counts):
        if int(mask[row].sum()) != int(count) or not mask[row, :count].all() or mask[row, count:].any():
            raise ValueError(f"Invalid round mask for row {row}")
        if not np.allclose(cumulative[row, :count], np.cumsum(delta[row, :count], axis=0)):
            raise ValueError(f"Cumulative investment mismatch for row {row}")
        if not np.allclose(final[row], cumulative[row, count - 1]):
            raise ValueError(f"Final investment mismatch for row {row}")
        if (delta[row, count:] != 0).any() or (cumulative[row, count:] != 0).any():
            raise ValueError(f"Nonzero padded investment for row {row}")
        if (arrays["investment_source"][row, count:] != SOURCE_PADDING).any():
            raise ValueError(f"Non-padding source after row {row} sequence")
        if (arrays["round_winner"][row, count:] != -1).any() or (arrays["round_outcome_type"][row, count:] != OUTCOME_NULL).any():
            raise ValueError(f"Invalid padded outcome values for row {row}")


def _unit_axis_metadata(known_ids, unknown_slots):
    inverse = {slot: int(uid) for uid, slot in unknown_slots["slots"].items()}
    entries = []
    for index, uid in enumerate(known_ids):
        unit = CATALOG[uid]
        entries.append({"index": index, "unit_id": uid, "name_cn": unit["name_cn"], "name_en": unit["name_en"]})
    for offset in range(RESERVED_NEW_UNIT_DIMS):
        slot = f"unknown_unit_slot_{offset}"
        entries.append({"index": len(known_ids) + offset, "reserved_slot": slot, "unit_id": inverse.get(slot)})
    return entries


def build_dataset(replay_dir):
    candidates, skipped = load_candidates(replay_dir)
    unknown_slots = slot_mapping([root for _, root, _, _ in candidates])
    if unknown_slots["overflow"]:
        raise ValueError(f"Unknown unit IDs exceed {RESERVED_NEW_UNIT_DIMS} reserved slots: {unknown_slots['overflow']}")
    known_ids, unit_axis, slot_axis = _unit_axis(unknown_slots)
    qc = Counter()
    rows = [_dense_match(path, root, mode, sequence, unit_axis, slot_axis, qc)
            for path, root, mode, sequence in candidates]
    if not rows:
        raise ValueError("No eligible replay files found")

    arrays = {
        "investment_delta": np.stack([row["delta"] for row in rows]),
        "investment_cumulative": np.stack([row["cumulative"] for row in rows]),
        "investment_final": np.stack([row["final"] for row in rows]),
        "investment_source": np.stack([row["source"] for row in rows]),
        "round_winner": np.stack([row["winner"] for row in rows]),
        "round_outcome_type": np.stack([row["outcome"] for row in rows]),
        "winner_damage": np.stack([row["damage"] for row in rows]),
        "damage_valid": np.stack([row["damage_valid"] for row in rows]),
        "initial_health": np.stack([row["initial_health"] for row in rows]),
        "round_mask": np.stack([row["mask"] for row in rows]),
        "round_count": np.asarray([row["round_count"] for row in rows], dtype=np.uint8),
        "match_mode": np.asarray([row["mode_code"] for row in rows], dtype=np.uint8),
    }
    _validate(arrays)
    mode_counts = Counter(row["manifest"]["match_mode"] for row in rows)
    skipped_counts = Counter(item["reason"] for item in skipped)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "max_battle_rounds": MAX_BATTLE_ROUNDS,
        "axes": {"matches": len(rows), "rounds": MAX_BATTLE_ROUNDS, "sides": 2, "units": len(known_ids) + RESERVED_NEW_UNIT_DIMS},
        "enums": {
            "match_mode": {"1": "VS_1_1", "2": "VS_2_2"},
            "round_outcome_type": {"0": "null", "1": "battle", "2": "surrender"},
            "investment_source": {"0": "padding", "1": "snapshot", "2": "action_fallback_last_snapshot"},
        },
        "unit_axis": _unit_axis_metadata(known_ids, unknown_slots),
        "unknown_unit_slots": unknown_slots,
        "arrays": {name: {"dtype": str(array.dtype), "shape": list(array.shape)} for name, array in arrays.items()},
        "matches": [{"row_index": index, **row["manifest"]} for index, row in enumerate(rows)],
        "skipped": skipped,
        "statistics": {
            "input_file_count": len(candidates) + len(skipped),
            "included_match_count": len(rows),
            "included_by_match_mode": dict(sorted(mode_counts.items())),
            "skipped_match_count": len(skipped),
            "skipped_by_reason": dict(sorted(skipped_counts.items())),
            "max_observed_battle_round": int(arrays["round_count"].max()),
            "qc": dict(sorted(qc.items())),
        },
    }
    return arrays, metadata


def _resolve(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-dir", default="local_data/humen_replay")
    parser.add_argument("--out-npz", default="data/mechabellum_dense_v1.npz")
    parser.add_argument("--out-json", default="data/mechabellum_dense_v1.json")
    args = parser.parse_args()
    arrays, metadata = build_dataset(_resolve(args.replay_dir))
    out_npz, out_json = _resolve(args.out_npz), _resolve(args.out_json)
    out_npz.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_npz, **arrays)
    out_json.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_npz.relative_to(ROOT)} and {out_json.relative_to(ROOT)}")
    print(json.dumps(metadata["statistics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

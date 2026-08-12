#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the replay-derived v2 dataset used by the battle-skill experiment.

The v1 dense and strategy datasets are intentionally left untouched.  This
builder reconstructs a corrected unit capital value and keeps 16 economic
variants (bit 0 = subsidy, bit 1 = efficient manufacturing, bit 2 = improved
unit, bit 3 = mass production).  The same values are then pooled at the three
fixed probes with half distance 150.
"""
from __future__ import annotations

import argparse
import json
import math
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from build_dense_dataset import load_candidates
from parse_match_investment import CATALOG, XSI, parse_root

ROOT = Path(__file__).resolve().parent.parent
MAX_ROUNDS = 18
K = 43
PROBES = np.asarray(((-300.0, 0.0), (0.0, 0.0), (300.0, 0.0)), dtype=np.float64)
HALF_DISTANCE = 150.0

# The IDs are stable in the replay format.  Prices are frozen from the
# experiment specification, not fetched from the live wiki at training time.
SKILL_CATALOG = {
    "missile": {"kind": "contraption", "id": "10001", "price": 50},
    "shield": {"kind": "contraption", "id": "20001", "price": 100},
    "interceptor": {"kind": "contraption", "id": "30001", "price": 100},
    "missile_strike": {"kind": "skill", "id": "300001", "price": 100},
    "summon_swarm": {"kind": "skill", "id": "1200003", "price": 100},
    "underground_threat": {"kind": "skill", "id": "1200001", "price": 100},
    "rhino_assault": {"kind": "skill", "id": "1200002", "price": 100},
    "electromagnetic_blast": {"kind": "skill", "id": "200001", "price": 100},
    "airdrop_shield": {"kind": "skill", "id": "800001", "price": 100},
    "photon_emission": {"kind": "skill", "id": "200003", "price": 150},
    "smoke_bomb": {"kind": "skill", "id": "600002", "price": 150},
    "incendiary_bomb": {"kind": "skill", "id": "100002", "price": 150},
    "oil_bomb": {"kind": "skill", "id": "1100001", "price": 150},
    "scorpion_assault": {"kind": "skill", "id": "1200009", "price": 150},
    "orbital_bombardment": {"kind": "skill", "id": "300003", "price": 200},
    "acid_blast": {"kind": "skill", "id": "500002", "price": 150},
    "mobilize_battleship": {"kind": "skill", "id": "1200004", "price": 300},
    "vulcan_descent": {"kind": "skill", "id": "1200005", "price": 250},
    "melting_point_descent": {"kind": "skill", "id": "1200013", "price": 250},
    "giant_electromagnetic_blast": {"kind": "skill", "id": "200002", "price": 250},
    "nuke": {"kind": "skill", "id": "300004", "price": 400},
    "ion_blast": {"kind": "skill", "id": "300006", "price": 300},
    "orbital_javelin": {"kind": "skill", "id": "300007", "price": 250},
    "lightning_storm": {"kind": "skill", "id": "300005", "price": 400},
}
SKILL_NAMES = tuple(SKILL_CATALOG)
SKILL_INDEX = {item["id"]: i for i, item in enumerate(SKILL_CATALOG.values())}
CONTRAPTION_INDEX = {item["id"]: i for i, item in enumerate(SKILL_CATALOG.values()) if item["kind"] == "contraption"}

SUBSIDY_CARDS = {"30701": 7, "30801": 8, "30203": 2}  # mustang, steel ball, marksman
EFFICIENT_CARDS = {"20022": "giant", "20023": "small"}
IMPROVED_CARDS = {
    "31603": (16, 100), "32103": (21, 50), "30104": (1, 100),
    "31802": (18, 50), "31702": (17, 200), "32301": (23, 100),
    "30402": (4, 100), "31903": (19, 50), "32401": (24, 50),
    "30803": (8, 50), "31304": (13, 50), "33001": (30, 50),
}
MASS_CARDS = {
    "32601": (26, .40), "30601": (6, .35), "31601": (16, .30),
    "32102": (21, .40), "31801": (18, .20), "32302": (23, .30),
    "30403": (4, .20), "30501": (5, .20), "31902": (19, .20),
    "31301": (13, .30), "31102": (11, .15),
}
GIANT_UNITS = {1, 3, 4, 11, 17, 23, 27, 29, 2001, 2002}


def _normal(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _post_record(rounds: dict[int, ET.Element], round_no: int) -> ET.Element:
    return rounds.get(round_no + 1, rounds[round_no])


def _round_map(player: ET.Element) -> dict[int, ET.Element]:
    return {int(x.findtext("round") or 0): x for x in player.findall("playerRoundRecords/PlayerRoundRecord")}


def _effective_actions(record: ET.Element) -> list[ET.Element]:
    """Apply both ordinary undo and skill-cancel records."""
    kept: list[ET.Element] = []
    for action in record.findall("actionRecords/MatchActionData"):
        kind = action.get(XSI)
        if kind == "PAD_Undo":
            if kept:
                kept.pop()
            continue
        if kind == "PAD_CancelReleaseCommanderSkill":
            sid = action.findtext("ID") or "0"
            index = action.findtext("SkillIndex")
            for i in range(len(kept) - 1, -1, -1):
                candidate = kept[i]
                if candidate.get(XSI) != "PAD_ReleaseCommanderSkill":
                    continue
                if (candidate.findtext("ID") or "0") == sid or (index and candidate.findtext("SkillIndex") == index):
                    kept.pop(i)
                    break
            continue
        kept.append(action)
    return kept


def _commander_skills(record: ET.Element) -> dict[str, str]:
    return {x.findtext("index"): x.findtext("id") for x in record.findall("playerData/commanderSkills/CommanderSkillData") if x.findtext("index") and x.findtext("id")}


def _resolved_skill_id(action: ET.Element, skills: dict[str, str]) -> str:
    sid = action.findtext("ID") or "0"
    if sid == "0":
        sid = skills.get(action.findtext("SkillIndex"), sid)
    return sid


def _buy_indices(record: ET.Element) -> set[str]:
    return {a.findtext("UIDX") for a in _effective_actions(record) if a.get(XSI) == "PAD_BuyUnit" and a.findtext("UIDX") not in {None, "-1"}}


def _officer_ids(record: ET.Element) -> Counter[str]:
    return Counter(x.text for x in record.findall("playerData/officers/int") if x.text)


def _unit_axis(metadata: dict[str, Any]) -> dict[int, int]:
    return {int(item["unit_id"]): int(item["index"]) for item in metadata["unit_axis"] if item.get("unit_id") is not None}


def _base_value(uid: int, level: int, sell_supply: float) -> float:
    unit = CATALOG.get(uid)
    if unit and unit.get("base_buy_cost") is not None and unit.get("upgrade_cost_per_level") is not None:
        return float(unit["base_buy_cost"] + level * unit["upgrade_cost_per_level"])
    return float(sell_supply)


def _variant_value(uid: int, level: int, base: float, cards: Counter[str], purchased: bool, mask: int) -> float:
    value = base
    # Strategies 7/8 apply retroactively to units already on the board.
    if mask & 4:
        for card, (card_uid, increase) in IMPROVED_CARDS.items():
            if card_uid == uid and cards.get(card, 0):
                unit = CATALOG.get(uid)
                base_buy = float(unit["base_buy_cost"]) if unit and unit.get("base_buy_cost") is not None else base
                value = (base_buy + increase) * (1.0 + 0.5 * level)
                break
    if mask & 8:
        for card, (card_uid, reduction) in MASS_CARDS.items():
            if card_uid == uid and cards.get(card, 0):
                unit = CATALOG.get(uid)
                base_buy = float(unit["base_buy_cost"]) if unit and unit.get("base_buy_cost") is not None else base
                value = base_buy * (1.0 - reduction) * (1.0 + 0.5 * level)
                break
    # Strategy 5/6 are positive effective capital, and only apply at purchase.
    # They intentionally stack on top of the improved/mass-produced value.
    if mask & 1 and purchased:
        if any(card in SUBSIDY_CARDS and SUBSIDY_CARDS[card] == uid for card in cards if cards.get(card, 0)):
            value += 50.0
    if mask & 2 and purchased:
        for card, family in EFFICIENT_CARDS.items():
            if cards.get(card, 0) and ((family == "giant") == (uid in GIANT_UNITS)):
                value += 50.0 * cards.get(card, 0)
    return value


def _unit_values_for_round(rounds: dict[int, ET.Element], round_no: int, uid_to_axis: dict[int, int], costs: dict[int, float], qc: Counter) -> tuple[np.ndarray, list[tuple[str, int, float, float]], ET.Element, ET.Element]:
    current = rounds[round_no]
    post = _post_record(rounds, round_no)
    # A round record is the pre-deployment snapshot; its successor is the
    # post-round board used for the feature.  Therefore the current snapshot,
    # not round-1's raw record, is the comparison set for newly bought units.
    old_indices = {u.findtext("Index") for u in current.findall("playerData/units/NewUnitData")}
    cards = _officer_ids(current)
    post_cards = _officer_ids(post)
    for card, count in post_cards.items():
        cards[card] = max(cards.get(card, 0), count)
    improved_units = {uid for card, (uid, _) in IMPROVED_CARDS.items() if cards.get(card, 0)}
    mass_units = {uid for card, (uid, _) in MASS_CARDS.items() if cards.get(card, 0)}
    for uid in sorted(improved_units & mass_units):
        qc["conflicting_improved_mass"] += 1
    values = np.zeros((16, K), dtype=np.float32)
    units: list[tuple[str, int, float, float]] = []
    purchased_indices = _buy_indices(current)
    purchase_budget = Counter()
    for action in _effective_actions(current):
        if action.get(XSI) == "PAD_BuyUnit":
            try:
                purchase_budget[int(action.findtext("UID"))] += 1
            except (TypeError, ValueError):
                pass
    for unit in post.findall("playerData/units/NewUnitData"):
        try:
            uid = int(unit.findtext("id")); axis = uid_to_axis.get(uid)
            level = int(unit.findtext("Level") or 0)
            sell_supply = float(unit.findtext("SellSupply") or 0)
        except (TypeError, ValueError):
            qc["missing_unit_fields"] += 1
            continue
        if axis is None:
            continue
        index = unit.findtext("Index") or ""
        purchased = index in purchased_indices
        if index not in old_indices and not purchased and purchase_budget[uid] > 0:
            purchased = True
            purchase_budget[uid] -= 1
        base = _base_value(uid, level, sell_supply)
        units.append((index, uid, base, 1.0 if purchased else 0.0))
        for mask in range(16):
            values[mask, axis] += _variant_value(uid, level, base, cards, purchased, mask)
    # Allocate the complete accumulated unlock/technology cost to the current
    # formations.  This makes sum(value) equal to units + all allocated costs.
    counts = Counter(uid_to_axis.get(uid) for _, uid, _, _ in units)
    for axis, cost in costs.items():
        if axis not in counts:
            if cost:
                qc["unallocated_unlock_tech_without_unit"] += 1
            continue
        for mask in range(16):
            values[mask, axis] += float(cost)
    return values, units, current, post


def _position_units(record: ET.Element, uid_to_axis: dict[int, int]) -> tuple[dict[int, list[tuple[float, float]]], int]:
    positions: dict[int, list[tuple[float, float]]] = {}
    missing = 0
    for unit in record.findall("playerData/units/NewUnitData"):
        try: uid = int(unit.findtext("id"))
        except (TypeError, ValueError): continue
        axis = uid_to_axis.get(uid)
        if axis is None: continue
        node = unit.find("Position")
        try:
            x = float(node.findtext("x")); y = float(node.findtext("y"))
        except (AttributeError, TypeError, ValueError):
            missing += 1; continue
        positions.setdefault(axis, []).append((x, y))
    return positions, missing


def _skill_counts(current: ET.Element, post: ET.Element, qc: Counter) -> np.ndarray:
    counts = np.zeros(len(SKILL_CATALOG), dtype=np.float32)
    skills = _commander_skills(current)
    actions = _effective_actions(current)
    released: Counter[str] = Counter()
    for action in actions:
        if action.get(XSI) == "PAD_ReleaseCommanderSkill":
            sid = _resolved_skill_id(action, skills)
            if sid in SKILL_INDEX:
                released[sid] += 1
    # Persistent battlefield objects are present in the current snapshot.
    existing = Counter(c.findtext("id") for c in current.findall("playerData/contraptions/ContraptionData") if c.findtext("id"))
    for cid, index in CONTRAPTION_INDEX.items():
        counts[index] += existing[cid]
        if released[cid] > existing[cid]:
            counts[index] += released[cid] - existing[cid]
    for sid, number in released.items():
        if sid in SKILL_INDEX and sid not in CONTRAPTION_INDEX:
            counts[SKILL_INDEX[sid]] += number
    # Air-drop shields are represented by rangeItems rather than contraptions.
    airdrop = next((x for x in current.findall("playerData/commanderSkills/CommanderSkillData") if x.findtext("id") == "800001"), None)
    if airdrop is not None:
        ranges = airdrop.findall("rangeItems/CommanderSkillRangeItemData")
        counts[SKILL_INDEX["800001"]] += len(ranges)
    if released.get("800001") and (airdrop is None or not airdrop.findall("rangeItems/CommanderSkillRangeItemData")):
        counts[SKILL_INDEX["800001"]] += released["800001"]
    if not np.isfinite(counts).all():
        qc["nonfinite_skill_count"] += 1
    return counts


def build_dataset(replay_dir: Path, dense_npz: Path, dense_json: Path) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    with np.load(dense_npz, allow_pickle=False) as archive:
        dense = {key: archive[key].copy() for key in archive.files}
    metadata = json.loads(dense_json.read_text(encoding="utf-8"))
    candidates, skipped = load_candidates(replay_dir)
    candidate_map = {_normal(path.name): (path, root, mode) for path, root, mode, _ in candidates}
    if len(metadata["matches"]) != len(dense["round_count"]):
        raise ValueError("dense metadata and NPZ row counts differ")
    uid_to_axis = _unit_axis(metadata)
    n = len(metadata["matches"])
    board = np.zeros((16, n, MAX_ROUNDS, 2, K), dtype=np.float32)
    spatial = np.zeros((16, n, MAX_ROUNDS, 2, 3, K), dtype=np.float32)
    skills = np.zeros((n, MAX_ROUNDS, 2, len(SKILL_CATALOG)), dtype=np.float32)
    spatial_valid = np.repeat(dense["round_mask"][:, :, None], 2, axis=2).astype(bool)
    qc: Counter = Counter()
    for row, item in enumerate(metadata["matches"]):
        key = _normal(item["file"])
        if key not in candidate_map:
            raise ValueError(f"missing replay for {item['file']}")
        path, root, mode = candidate_map[key]
        players = root.findall("playerRecords/PlayerRecord")
        groups = ((0,), (1,)) if mode == "VS_1_1" else ((0, 1), (2, 3))
        player_rounds = [_round_map(player) for player in players]
        report = parse_root(root, path, {"slots": {}, "overflow": []})
        team_values = []
        team_units = []
        for side, group in enumerate(groups):
            member_values = []
            for player_index in group:
                rounds = player_rounds[player_index]
                costs: dict[int, float] = defaultdict(float)
                values = np.zeros((MAX_ROUNDS, 16, K), dtype=np.float32)
                units_by_round = {}
                for round_no in range(1, MAX_ROUNDS + 1):
                    if round_no not in rounds: continue
                    data = next((x for x in report["players"][player_index]["rounds"] if x["round"] == round_no), None)
                    if data:
                        for action in data["actions"]:
                            if action["type"] in {"unlock", "tech"} and action.get("cost") is not None:
                                uid = action.get("uid"); axis = uid_to_axis.get(int(uid)) if uid is not None else None
                                if axis is not None: costs[axis] += float(action["cost"])
                    current_values, units, current, post = _unit_values_for_round(rounds, round_no, uid_to_axis, costs, qc)
                    values[round_no - 1] = current_values
                    units_by_round[round_no] = (units, current, post)
                    skills[row, round_no - 1, side] += _skill_counts(current, post, qc)
                member_values.append(values)
            team_values.append(np.mean(member_values, axis=0))
            # Combine positions and distribute each unit-type value across its formations.
            team_units.append(units_by_round)
        for side in range(2):
            board[:, row, :, side, :] = np.transpose(team_values[side], (1, 0, 2))
        # Recompute spatial values from the team snapshots and values.  For 2v2
        # the position lists are merged and values are averaged per teammate.
        for round_no in range(1, MAX_ROUNDS + 1):
            t = round_no - 1
            if not dense["round_mask"][row, t]: continue
            for side, group in enumerate(groups):
                merged: dict[int, list[tuple[float, float]]] = {}
                missing = 0
                for player_index in group:
                    positions, absent = _position_units(_post_record(player_rounds[player_index], round_no), uid_to_axis)
                    missing += absent
                    for axis, coords in positions.items(): merged.setdefault(axis, []).extend(coords)
                if missing:
                    spatial_valid[row, t, side] = False; qc["missing_position_fields"] += missing
                for axis, coords in merged.items():
                    if not coords: continue
                    for mask in range(16):
                        value = float(team_values[side][t, mask, axis])
                        per_unit = value / len(coords)
                        for x, y in coords:
                            distance = np.sqrt(np.square(PROBES[:, 0] - x) + np.square(PROBES[:, 1] - y))
                            weights = np.power(2.0, -distance / HALF_DISTANCE)
                            weights /= weights.sum()
                            spatial[mask, row, t, side, :, axis] += per_unit * weights
    # Each member/side was accumulated only once above for 1v1. Normalize 2v2
    # skill sums and preserve the original dense winner labels.
    if np.any(dense["match_mode"] == 2):
        # The builder adds each team member's skill vector; divide those rows by
        # two only for 2v2.  This is equivalent to the existing team averaging.
        skills[dense["match_mode"] == 2] *= 0.5
    round_valid = dense["round_mask"].astype(bool) & (dense["round_winner"] >= 0) & spatial_valid.all(axis=2)
    arrays = {
        "board_value": board,
        "spatial_value": spatial,
        "battle_skill_value": skills,
        "buff_delta": np.zeros((n, MAX_ROUNDS, 2, 2), dtype=np.float32),
        "round_mask": dense["round_mask"].astype(bool),
        "round_winner": dense["round_winner"].astype(np.int8),
        "spatial_valid": spatial_valid,
        "round_valid": round_valid.astype(bool),
        "round_count": dense["round_count"].astype(np.uint8),
        "match_mode": dense["match_mode"].astype(np.uint8),
    }
    # Reuse the corrected board values for the existing aligned buff feature.
    # The training code reads buff_delta from the v1 companion builder when
    # available; populate it below from the already validated v1 array.
    try:
        with np.load(ROOT / "data/logistic_strategy_v1.npz", allow_pickle=False) as old:
            arrays["buff_delta"] = old["buff_delta"].astype(np.float32)
    except FileNotFoundError:
        qc["missing_v1_buff_delta"] += 1
    file_groups = []
    for item in metadata["matches"]:
        filename = item["file"]
        while filename.endswith(tuple(f"({i}).grbr" for i in range(1, 20))):
            filename = filename.rsplit("(", 1)[0] + ".grbr"
        file_groups.append(filename)
    qc["conflicting_improved_mass"] += 0
    qc["skill_zero_coverage"] = [name for i, name in enumerate(SKILL_NAMES) if not np.any(skills[..., i])]
    out_meta = {
        "schema_version": 2,
        "source_dense_schema": metadata.get("schema_version"),
        "axes": {"matches": n, "rounds": MAX_ROUNDS, "sides": 2, "units": K, "probes": 3, "economic_masks": 16, "skills": len(SKILL_CATALOG)},
        "economic_mask_bits": {"1": "subsidy", "2": "efficient_manufacturing", "4": "improved", "8": "mass_production"},
        "probe_points": PROBES.tolist(), "half_distance": HALF_DISTANCE,
        "unit_axis": metadata.get("unit_axis", []),
        "skill_axis": [{"index": i, "name": name, **item} for i, (name, item) in enumerate(SKILL_CATALOG.items())],
        "card_ids": {"subsidy": SUBSIDY_CARDS, "efficient": EFFICIENT_CARDS, "improved": IMPROVED_CARDS, "mass": MASS_CARDS},
        "arrays": {name: {"dtype": str(value.dtype), "shape": list(value.shape)} for name, value in arrays.items()},
        "matches": [{"row_index": i, "file": metadata["matches"][i]["file"], "group": file_groups[i]} for i in range(n)],
        "input_skipped_count": len(skipped),
        "statistics": {"included_match_count": n, "valid_round_count": int(round_valid.sum()), "spatial_invalid_round_count": int((dense["round_mask"] & ~spatial_valid.all(axis=2)).sum()), "qc": dict(sorted(qc.items()))},
    }
    return arrays, out_meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-dir", default="local_data/humen_replay")
    parser.add_argument("--dense-npz", default="data/mechabellum_dense_v1.npz")
    parser.add_argument("--dense-json", default="data/mechabellum_dense_v1.json")
    parser.add_argument("--out-npz", default="data/logistic_battle_skill_v2.npz")
    parser.add_argument("--out-json", default="data/logistic_battle_skill_v2.json")
    args = parser.parse_args()
    arrays, metadata = build_dataset(_resolve(args.replay_dir), _resolve(args.dense_npz), _resolve(args.dense_json))
    out_npz, out_json = _resolve(args.out_npz), _resolve(args.out_json)
    out_npz.parent.mkdir(parents=True, exist_ok=True); out_json.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_npz, **arrays); out_json.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata["statistics"], ensure_ascii=False, indent=2))


def _resolve(value: str | Path) -> Path:
    path = Path(value); return path if path.is_absolute() else ROOT / path


if __name__ == "__main__":
    main()

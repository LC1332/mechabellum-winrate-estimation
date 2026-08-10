#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse per-unit net investment from Mechabellum replay snapshots.

Adjacent player snapshots are the primary source of truth because an action log
can retain undone actions.  The final action record has no following snapshot,
so it is parsed with undo filtering and explicitly marked lower confidence.
"""
from __future__ import annotations

import argparse
import glob
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"
FIELD_RECOVERY_SKILL_ID = 900001
ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = Path(__file__).with_name("unit_cost_source.json")
RESERVED_NEW_UNIT_DIMS = 10


def numeric(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def extract_xml(file_path):
    data = Path(file_path).read_bytes()
    start = data.find(b"<?xml")
    end = data.rfind(b"BattleRecord>") + len(b"BattleRecord>")
    if start < 0 or end == len(b"BattleRecord>") - 1:
        raise ValueError(f"No XML BattleRecord in {file_path}")
    return data[start:end].decode("utf-8")


def load_cost_catalog(path=CATALOG_PATH):
    """Load the sole offline source of cost constants."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    units = {int(k): v for k, v in raw["units"].items()}
    for uid, unit in units.items():
        unit["technologies_by_id"] = {
            int(t["tech_id"]): t for t in unit.get("technologies", [])
        }
        unit["unit_id"] = uid
    return raw, units


CATALOG_RAW, CATALOG = load_cost_catalog()


def unit_label(uid):
    unit = CATALOG.get(numeric(uid))
    return f"{unit['name_cn']}({unit['name_en']})" if unit else f"未知兵种({uid})"


def unit_report_label(uid):
    unit = CATALOG.get(numeric(uid))
    return unit["name_cn"] if unit else f"未知兵种({uid})"


def tech_label(uid, tech_id):
    tech = CATALOG.get(numeric(uid), {}).get("technologies_by_id", {}).get(numeric(tech_id))
    return f"{tech['name_cn']}({tech['name_en']})" if tech else f"未知科技({tech_id})"


def snapshot(record):
    data = record.find("playerData")
    units = {}
    for u in data.findall("units/NewUnitData"):
        index = u.findtext("Index")
        if index is not None:
            units[index] = {
                "uid": numeric(u.findtext("id")),
                "level": int(u.findtext("Level") or 0),
                "sell_supply": int(u.findtext("SellSupply") or 0),
            }
    unlocked = {numeric(x.text) for x in data.findall("shop/unlockedUnits/*") if x.text}
    techs = defaultdict(set)
    for u in data.findall("activeTechnologies/UnitData"):
        uid = numeric(u.findtext("id"))
        techs[uid].update(numeric(x.get("data")) for x in u.findall("techs/tech") if x.get("data"))
    commander_skills = {
        numeric(skill.findtext("index")): numeric(skill.findtext("id"))
        for skill in data.findall("commanderSkills/CommanderSkillData")
        if skill.findtext("index") is not None and skill.findtext("id") is not None
    }
    return {
        "round": int(record.findtext("round") or 0),
        "supply": int(data.findtext("supply") or 0),
        "reactor_core": int(data.findtext("reactorCore") or 0),
        "units": units,
        "unlocked": unlocked,
        "techs": techs,
        "commander_skills": commander_skills,
        "pre_round_fight_result": data.findtext("preRoundFightResult"),
    }


def raw_actions(record, commander_skills=None):
    """Return economic actions and resolved Field Recovery actions in log order."""
    commander_skills = commander_skills or {}
    out = []
    for action in record.findall("actionRecords/MatchActionData"):
        kind = action.get(XSI)
        if kind in {"PAD_BuyUnit", "PAD_UnlockUnit", "PAD_UpgradeUnit", "PAD_UpgradeTechnology"}:
            out.append({"kind": kind, "uid": numeric(action.findtext("UID")),
                        "uidx": action.findtext("UIDX"), "tech_id": numeric(action.findtext("TechID"))})
        elif kind == "PAD_ReleaseCommanderSkill":
            skill_id = numeric(action.findtext("ID"))
            skill_index = numeric(action.findtext("SkillIndex"))
            if skill_id == 0:
                skill_id = commander_skills.get(skill_index, skill_id)
            if skill_id == FIELD_RECOVERY_SKILL_ID:
                out.append({"kind": "PAD_SellUnit", "uidx": action.findtext("UnitIndex"),
                            "skill_id": skill_id, "skill_index": skill_index})
        elif kind == "PAD_GiveUp":
            out.append({"kind": "PAD_GiveUp"})
        elif kind == "PAD_Undo":
            out.append({"kind": "PAD_Undo"})
    return out


def undo_filtered_actions(record, commander_skills=None):
    """Best-effort final-record fallback: undo reverses the latest tracked action."""
    kept = []
    for action in raw_actions(record, commander_skills):
        if action["kind"] == "PAD_Undo":
            if kept:
                kept.pop()
        elif action["kind"] != "PAD_GiveUp":
            kept.append(action)
    return kept


def has_give_up(record):
    return any(action.get(XSI) == "PAD_GiveUp"
               for action in record.findall("actionRecords/MatchActionData"))


def slot_mapping(replay_roots):
    """Stable 10-slot mapping for unknown numeric IDs across a parse run."""
    seen = set()
    for root in replay_roots:
        for u in root.iter("NewUnitData"):
            uid = numeric(u.findtext("id"))
            if isinstance(uid, int) and uid > 0 and uid not in CATALOG:
                seen.add(uid)
        for a in root.findall(".//actionRecords/MatchActionData"):
            if a.get(XSI) not in {"PAD_BuyUnit", "PAD_UnlockUnit", "PAD_UpgradeTechnology"}:
                continue
            uid = numeric(a.findtext("UID"))
            unknown_like = uid not in CATALOG or CATALOG.get(uid, {}).get("special_unit", False)
            if isinstance(uid, int) and uid > 0 and unknown_like:
                seen.add(uid)
    ordered = sorted(seen)
    return {"slots": {str(uid): f"unknown_unit_slot_{i}" for i, uid in enumerate(ordered[:RESERVED_NEW_UNIT_DIMS])},
            "overflow": ordered[RESERVED_NEW_UNIT_DIMS:]}


def action_cost(uid, kind, *, sell_supply=None, tech_id=None, prior_tech_count=0):
    unit = CATALOG.get(uid)
    if kind == "reinforcement":
        # A free reinforcement still contributes the unit's normal capital
        # value.  Do not let a mass-production/other modified SellSupply make
        # a free unit look cheaper than its original catalogue price.
        if unit and unit["base_buy_cost"] is not None:
            return unit["base_buy_cost"], "free_reinforcement_catalog_base_buy_cost"
        if sell_supply and sell_supply > 0:
            return sell_supply, "free_reinforcement_replay_sell_supply_fallback"
        return None, "unknown_reinforcement_without_base_cost"
    if kind in {"buy", "initial"}:
        if sell_supply and sell_supply > 0:
            basis = {
                "buy": "replay_sell_supply",
                "initial": "initial_loadout_sell_supply",
            }[kind]
            return sell_supply, basis
        if unit and unit["base_buy_cost"] is not None:
            basis = "initial_loadout_catalog_default" if kind == "initial" else "catalog_default"
            return unit["base_buy_cost"], basis
        basis = "unknown_initial_unit_without_sellsupply" if kind == "initial" else "unknown_buy_without_sellsupply"
        return None, basis
    if kind == "sell":
        # Zero is a meaningful replay value: the unit can be removed without a
        # refund (for example, a generated unit).  Treat only a missing value
        # as unknown.
        return ((-sell_supply, "replay_sell_supply_refund") if sell_supply is not None
                else (None, "unknown_sell_without_sellsupply"))
    if kind == "level":
        if sell_supply and sell_supply > 0:
            return sell_supply / 2, "replay_sell_supply_half"
        if unit and unit["upgrade_cost_per_level"] is not None:
            return unit["upgrade_cost_per_level"], "catalog_default"
        return None, "unknown_upgrade_without_sellsupply"
    if kind == "unlock":
        return ((unit["unlock_cost"], "catalog_default") if unit and unit["unlock_cost"] is not None
                else (None, "unknown_unlock"))
    if kind == "tech":
        tech = unit and unit["technologies_by_id"].get(tech_id)
        if tech:
            return tech["base_cost"] + 200 * prior_tech_count, "catalog_base_plus_tech_surcharge"
        if unit and unit.get("special_unit"):
            # The dense v1 contract explicitly treats unpriced experimental
            # unit technologies as zero-cost rather than dropping the match.
            return 0, "assumed_zero_special_unit_tech"
        return None, "unknown_tech"
    raise ValueError(kind)


def make_action(kind, uid, **extra):
    cost, basis = action_cost(
        uid, kind,
        sell_supply=extra.get("sell_supply"),
        tech_id=extra.get("tech_id"),
        prior_tech_count=extra.get("prior_tech_count", 0),
    )
    out = {"type": kind, "uid": uid, "unit": unit_label(uid), "cost": cost, "cost_basis": basis}
    out.update(extra)
    if kind == "sell" and cost is not None:
        out["refund"] = -cost
    if kind == "tech":
        out["tech"] = tech_label(uid, extra["tech_id"])
    return out


def initial_loadout_actions(state):
    """Count the five normal starting units as R1 investment, not supply spend."""
    if state["round"] != 1:
        return []
    units = sorted(state["units"].items(), key=lambda item: int(item[0]) if item[0].isdigit() else item[0])
    return [
        make_action("initial", unit["uid"], sell_supply=unit["sell_supply"],
                    instance_index=index, initial_loadout=True)
        for index, unit in units
    ]


def state_actions(before, after, record):
    """Accepted actions derived from a current to next player-state delta."""
    actions = []
    for uid in sorted(after["unlocked"] - before["unlocked"], key=str):
        actions.append(make_action("unlock", uid))
    for uid in sorted(set(before["techs"]) | set(after["techs"]), key=str):
        previous = len(before["techs"].get(uid, set()))
        for tech_id in sorted(after["techs"].get(uid, set()) - before["techs"].get(uid, set()), key=str):
            actions.append(make_action("tech", uid, tech_id=tech_id, prior_tech_count=previous))
            previous += 1

    effective = undo_filtered_actions(record, before.get("commander_skills"))
    new = [(idx, u) for idx, u in after["units"].items() if idx not in before["units"]]
    buy_logs = [a for a in effective if a["kind"] == "PAD_BuyUnit"]
    consumed = set()
    for log in buy_logs:
        for idx, unit in new:
            if idx not in consumed and unit["uid"] == log["uid"]:
                consumed.add(idx)
                actions.append(make_action("buy", unit["uid"], sell_supply=unit["sell_supply"], instance_index=idx))
                break
    reinforcements = [{"uid": u["uid"], "unit": unit_label(u["uid"]), "instance_index": idx}
                      for idx, u in new if idx not in consumed]
    for reinforcement in reinforcements:
        unit = after["units"][reinforcement["instance_index"]]
        actions.append(make_action(
            "reinforcement", unit["uid"], sell_supply=unit["sell_supply"],
            instance_index=reinforcement["instance_index"], free=True,
        ))

    sale_indices = {a["uidx"] for a in effective if a["kind"] == "PAD_SellUnit"}
    for idx in sorted(set(before["units"]) - set(after["units"]), key=str):
        if idx in sale_indices:
            unit = before["units"][idx]
            actions.append(make_action("sell", unit["uid"], sell_supply=unit["sell_supply"], instance_index=idx))

    for idx, after_unit in after["units"].items():
        before_unit = before["units"].get(idx)
        if not before_unit or after_unit["level"] <= before_unit["level"]:
            continue
        for level in range(before_unit["level"] + 1, after_unit["level"] + 1):
            actions.append(make_action("level", after_unit["uid"], sell_supply=after_unit["sell_supply"],
                                       instance_index=idx, from_level=level - 1, level=level))
    return actions, reinforcements


def fallback_actions(before, record):
    """Last snapshot only: undo-filtered actions, marked low confidence."""
    actions = []
    prior_tech_counts = {uid: len(techs) for uid, techs in before["techs"].items()}
    for log in undo_filtered_actions(record, before.get("commander_skills")):
        uid = log.get("uid")
        if log["kind"] == "PAD_BuyUnit":
            actions.append(make_action("buy", uid, sell_supply=None))
        elif log["kind"] == "PAD_UnlockUnit":
            actions.append(make_action("unlock", uid))
        elif log["kind"] == "PAD_UpgradeUnit":
            unit = before["units"].get(log["uidx"], {})
            # A last-round upgrade can target a unit purchased earlier in the
            # same action log, so it is absent from the current snapshot.  The
            # action UID is the deterministic fallback in that case.
            uid = unit.get("uid", uid)
            actions.append(make_action("level", uid, sell_supply=unit.get("sell_supply"),
                                       instance_index=log["uidx"]))
        elif log["kind"] == "PAD_UpgradeTechnology":
            prior = prior_tech_counts.get(uid, 0)
            actions.append(make_action("tech", uid, tech_id=log["tech_id"], prior_tech_count=prior))
            prior_tech_counts[uid] = prior + 1
        elif log["kind"] == "PAD_SellUnit":
            unit = before["units"].get(log["uidx"], {})
            if unit:
                actions.append(make_action("sell", unit["uid"], sell_supply=unit["sell_supply"],
                                           instance_index=log["uidx"]))
    return actions, []


def summarize_round(actions):
    cat, by_unit, unknown = Counter(), Counter(), []
    for action in actions:
        if action["cost"] is None:
            unknown.append(action)
            by_unit.setdefault(action["uid"], 0)
        else:
            cat[action["type"]] += action["cost"]
            by_unit[action["uid"]] += action["cost"]
    return dict(cat), dict(by_unit), unknown, sum(cat.values())


def _round_results(players):
    """Return 1v1 round winners from surrender or the following snapshot."""
    if len(players) != 2:
        return []
    by_player_round = [{rd["round"]: rd for rd in player["rounds"]} for player in players]
    results = []
    for round_no in sorted(set(by_player_round[0]) & set(by_player_round[1])):
        current = [rounds[round_no] for rounds in by_player_round]
        quitters = [i for i, rd in enumerate(current) if rd["gave_up"]]
        winner, source = None, "unavailable"
        if len(quitters) == 1:
            winner, source = 1 - quitters[0], "give_up"
        else:
            following = [rounds.get(round_no + 1) for rounds in by_player_round]
            wins = [i for i, rd in enumerate(following)
                    if rd and rd["pre_round_fight_result"] == "Win"]
            losses = [i for i, rd in enumerate(following)
                      if rd and rd["pre_round_fight_result"] == "Lose"]
            if len(wins) == 1 and len(losses) == 1:
                winner, source = wins[0], "next_snapshot_pre_round_result"
        results.append({"round": round_no, "winner_player_index": winner,
                        "winner_name": players[winner]["name"] if winner is not None else None,
                        "source": source})
    return results


def _round_damage(players):
    """Attribute a core-health drop to the opposing player for that round."""
    if len(players) != 2:
        return []
    by_player_round = [{rd["round"]: rd for rd in player["rounds"]} for player in players]
    results = []
    for round_no in sorted(set(by_player_round[0]) & set(by_player_round[1])):
        following = [rounds.get(round_no + 1) for rounds in by_player_round]
        current = [rounds[round_no] for rounds in by_player_round]
        taken = [
            max(0, current[index]["reactor_core"] - following[index]["reactor_core"])
            if following[index] else 0
            for index in range(2)
        ]
        dealt = [taken[1], taken[0]]
        winners = [index for index, damage in enumerate(dealt) if damage > 0]
        results.append({
            "round": round_no,
            "damage_dealt": dealt,
            "damage_taken": taken,
            "winner_player_index": winners[0] if len(winners) == 1 else None,
            "winner_name": players[winners[0]]["name"] if len(winners) == 1 else None,
            "source": "next_snapshot_reactor_core",
        })
    return results


def _classify_victory(players):
    """Keep core destruction and post-R2 surrender as distinct end states."""
    if len(players) != 2:
        return {"victory_type": "unavailable", "winner_player_index": None, "winner_name": None}
    give_ups = [
        (player_index, rd["round"])
        for player_index, player in enumerate(players)
        for rd in player["rounds"] if rd["gave_up"]
    ]
    max_round = max((rd["round"] for player in players for rd in player["rounds"]), default=0)
    if len(give_ups) == 1:
        loser, surrender_round = give_ups[0]
        winner = 1 - loser
        return {
            "victory_type": "midgame_surrender" if max_round > 2 else "early_surrender",
            "winner_player_index": winner, "winner_name": players[winner]["name"],
            "loser_player_index": loser, "loser_name": players[loser]["name"],
            "decisive_round": surrender_round,
        }
    final_core = [player["rounds"][-1]["reactor_core"] if player["rounds"] else None for player in players]
    defeated = [index for index, core in enumerate(final_core) if core is not None and core <= 0]
    if len(defeated) == 1:
        loser = defeated[0]
        winner = 1 - loser
        return {
            "victory_type": "core_destroyed",
            "winner_player_index": winner, "winner_name": players[winner]["name"],
            "loser_player_index": loser, "loser_name": players[loser]["name"],
            "decisive_round": players[loser]["rounds"][-1]["round"],
        }
    return {"victory_type": "undetermined", "winner_player_index": None, "winner_name": None}


def parse_root(root, file_path, unknown_slots):
    players = []
    for player_index, pr in enumerate(root.findall("playerRecords/PlayerRecord")):
        data = pr.find("data")
        first = int(data.findtext("firstRoundSupply") or 0)
        inc = int(data.findtext("roundSupplyIncreaseValue") or 0)
        records = pr.findall("playerRoundRecords/PlayerRoundRecord")
        states = [snapshot(r) for r in records]
        rounds, cumulative, final_by_unit = [], 0, Counter()
        for i, record in enumerate(records):
            current = states[i]
            if i + 1 < len(states):
                actions, reinforcements = state_actions(current, states[i + 1], record)
                nxt = states[i + 1]
                econ = current["supply"] + first + current["round"] * inc - nxt["supply"]
                confidence = "snapshot"
            else:
                actions, reinforcements = fallback_actions(current, record)
                econ, confidence = None, "action_fallback_last_snapshot"
            # The R0 -> R1 new instances are the normal loadout.  Count them
            # once on R1 as initial investment instead of as free reinforcements.
            if current["round"] == 0:
                actions, reinforcements = [], []
            actions = initial_loadout_actions(current) + actions
            cat, by_unit, unknown, known_total = summarize_round(actions)
            cumulative += known_total
            final_by_unit.update(by_unit)
            rounds.append({
                "round": current["round"], "supply_remaining": current["supply"],
                "reactor_core": current["reactor_core"], "econ_spent": econ,
                "economy_confidence": confidence, "actions": actions, "reinforcements": reinforcements,
                "cat_cost": cat, "o_by_unit": by_unit, "known_total": known_total,
                "known_net_total": known_total, "has_unknown_cost": bool(unknown),
                "unknown_cost_actions": unknown, "cumulative_known_total": cumulative,
                "cumulative_known_net_total": cumulative, "cumulative_o_by_unit": dict(final_by_unit),
                "pre_round_fight_result": current["pre_round_fight_result"], "gave_up": has_give_up(record),
            })
        players.append({"player_index": player_index, "name": pr.findtext("name") or "",
                        "first_round_supply": first, "round_supply_increase": inc, "rounds": rounds,
                        "final_o_by_unit": dict(final_by_unit), "final_known_total": cumulative,
                        "final_known_net_total": cumulative})
    round_damage = _round_damage(players)
    for result in round_damage:
        for player_index, player in enumerate(players):
            round_data = next((rd for rd in player["rounds"] if rd["round"] == result["round"]), None)
            if round_data is not None:
                round_data["damage_dealt_to_opponent"] = result["damage_dealt"][player_index]
                round_data["damage_taken_from_opponent"] = result["damage_taken"][player_index]
    return {"file": str(file_path), "version": root.findtext("Version") or root.findtext("version"),
            "match_mode": root.findtext("BattleInfo/MatchMode") or "VS_1_1", "players": players,
            "round_results": _round_results(players), "round_damage": round_damage,
            "victory": _classify_victory(players), "unknown_unit_slots": unknown_slots}


def parse_replay(file_path, unknown_slots=None):
    return parse_root(ET.fromstring(extract_xml(file_path)), file_path,
                      unknown_slots or {"slots": {}, "overflow": []})


def _eligible_sample(root):
    if root.findtext("BattleInfo/MatchMode") != "VS_1_1":
        return None
    players = root.findall("playerRecords/PlayerRecord")
    if len(players) != 2:
        return None
    player_records = [p.findall("playerRoundRecords/PlayerRoundRecord") for p in players]
    sequences = [[int(r.findtext("round") or 0) for r in records] for records in player_records]
    if not sequences[0] or sequences[0] != sequences[1]:
        return None
    expected = list(range(sequences[0][0], sequences[0][-1] + 1))
    if sequences[0] != expected:
        return None
    has_surrender = any(has_give_up(record) for records in player_records for record in records)
    return len(sequences[0]), has_surrender


def select_samples(replay_dir, n=5, require_surrender=False):
    """Select deterministic complete 1v1 samples, optionally covering surrender."""
    ranked, surrender_ranked = [], []
    for file_path in sorted(glob.glob(str(Path(replay_dir) / "*.grbr"))):
        try:
            eligible = _eligible_sample(ET.fromstring(extract_xml(file_path)))
        except Exception:
            continue
        if not eligible:
            continue
        length, has_surrender = eligible
        (surrender_ranked if has_surrender else ranked).append((length, file_path))
    ranked.sort(reverse=True)
    surrender_ranked.sort(reverse=True)
    if require_surrender:
        if n < 2:
            raise ValueError("Surrender coverage requires at least two samples")
        if not surrender_ranked:
            raise ValueError("No complete 1v1 replay with PAD_GiveUp is available")
        if len(ranked) < n - 1:
            raise ValueError(f"Need {n - 1} complete non-surrender 1v1 replays, found {len(ranked)}")
        return [file_path for _, file_path in ranked[:n - 1]] + [surrender_ranked[0][1]]
    if len(ranked) + len(surrender_ranked) < n:
        raise ValueError(f"Need {n} complete 1v1 replays")
    return [file_path for _, file_path in (ranked + surrender_ranked)[:n]]


def _format_amount(value, signed=False):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = f"{value:g}" if isinstance(value, float) else str(value)
    return f"+{text}" if signed and value > 0 else text


def _format_unit_values(values, *, absolute_order=False, signed=False):
    nonzero = [(uid, value) for uid, value in values.items() if value]
    if absolute_order:
        nonzero.sort(key=lambda item: (-abs(item[1]), unit_report_label(item[0]), str(item[0])))
    else:
        nonzero.sort(key=lambda item: (-item[1], unit_report_label(item[0]), str(item[0])))
    return "、".join(f"{unit_report_label(uid)} {_format_amount(value, signed)}" for uid, value in nonzero) or "无"


def write_markdown(results, path):
    lines = ["# 5 个对局兵种净投资复查报告", "",
             "> 自动生成。兵种投入计解锁、购买、开局配置、免费增援、升级、科技和出售退款；塔、装置等其他消费不计入。",
             "> 回合 1 的初始 3 个 100 费单位和 2 个 200 费单位作为开局配置投资计入（通常为 700），但不计入该回合 supply 实际花费。",
             "> 免费新增单位按成本目录的单位原价记入兵种总投入（特殊/未知单位才退回实例 SellSupply），但不记 supply 消费；随后出售会抵销其单位价值，避免把累计兵种投入误报为负数。",
             "> 新单位优先使用回放 SellSupply；末回合无后继快照时购买费用回退到费用表并标记低置信度。出售退款按出售前 SellSupply 记为负投资。", ""]
    for match_index, report in enumerate(results, 1):
        lines += [f"## 对局 {match_index}：{Path(report['file']).name}", ""]
        victory = report["victory"]
        if victory["winner_player_index"] is None:
            lines.append(f"- 对局结果：{victory['victory_type']}（胜者未定）")
        else:
            lines.append(f"- 对局结果：{victory['victory_type']}；胜者：{victory['winner_name']}；决定回合：{victory.get('decisive_round', '—')}")
        lines.append("")
        result_by_round = {result["round"]: result for result in report["round_results"]}
        player_rounds = [{round_data["round"]: round_data for round_data in player["rounds"]}
                         for player in report["players"]]
        common_rounds = sorted(set(player_rounds[0]) & set(player_rounds[1])) if len(player_rounds) == 2 else []
        for round_no in (round_no for round_no in common_rounds if round_no >= 1):
            lines += [f"### 回合 {round_no}", ""]
            for player, rounds in zip(report["players"], player_rounds):
                round_data = rounds[round_no]
                text = (f"- 玩家 {player['player_index'] + 1} {player['name']}：本回合 "
                        f"{_format_unit_values(round_data['o_by_unit'], absolute_order=True, signed=True)}"
                        f"｜当前总投入 {_format_unit_values(round_data['cumulative_o_by_unit'])}"
                        f"｜对对手扣血 {round_data.get('damage_dealt_to_opponent', 0)}")
                if any(action.get("initial_loadout") for action in round_data["actions"]):
                    text += "｜含开局五单位"
                notes = []
                if round_data["economy_confidence"] != "snapshot":
                    notes.append("低置信度末回合回退")
                if round_data["has_unknown_cost"]:
                    notes.append(f"未知费用动作 {len(round_data['unknown_cost_actions'])} 项")
                if notes:
                    text += "｜" + "；".join(notes)
                lines.append(text)
            result = result_by_round.get(round_no)
            if result and result["winner_player_index"] is not None:
                lines.append(f"- 本回合胜者：{result['winner_name']}（{result['source']}）")
            else:
                lines.append("- 本回合胜者：null（回放未记录可确认结果）")
            damage = next((item for item in report["round_damage"] if item["round"] == round_no), None)
            if damage and damage["winner_player_index"] is not None:
                lines.append(f"- 核心扣血：{damage['winner_name']} 对对手造成 {damage['damage_dealt'][damage['winner_player_index']]} 点（{damage['source']}）")
            lines.append("")
        lines += ["### 最终兵种净投资", ""]
        for player in report["players"]:
            lines.append(f"- 玩家 {player['player_index'] + 1} {player['name']}："
                         f"{_format_unit_values(player['final_o_by_unit'])}")
        lines += ["", f"未知 ID 预留槽：{json.dumps(report['unknown_unit_slots'], ensure_ascii=False)}", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-dir", default="local_data/humen_replay")
    parser.add_argument("--out-json", default="reference_code/parse_5_matches.json")
    parser.add_argument("--out-md", default="information/parse_5_matches_report.md")
    parser.add_argument("--num", type=int, default=5)
    parser.add_argument("--require-surrender", action="store_true", default=True)
    parser.add_argument("--no-require-surrender", action="store_false", dest="require_surrender")
    args = parser.parse_args()
    replay_dir = ROOT / args.replay_dir
    samples = select_samples(replay_dir, args.num, require_surrender=args.require_surrender)
    roots = [ET.fromstring(extract_xml(file_path)) for file_path in samples]
    slots = slot_mapping(roots)
    matches = [parse_root(root, file_path, slots) for root, file_path in zip(roots, samples)]
    output = {"meta": {"catalog": str(CATALOG_PATH.relative_to(ROOT)), "unknown_unit_slots": slots,
                       "sample_strategy": "longest_non_surrender_plus_longest_surrender"
                       if args.require_surrender else "longest_complete_1v1"}, "matches": matches}
    (ROOT / args.out_json).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(matches, ROOT / args.out_md)
    print(f"wrote {args.out_json} and {args.out_md}")


if __name__ == "__main__":
    main()

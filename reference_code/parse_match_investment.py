#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse replay investment from adjacent final-state snapshots.

The action log is not the source of truth: PAD_Undo may leave an earlier
buy/unlock/tech action behind.  Records with a following snapshot are therefore
derived from final state deltas.  The last record is an undo-filtered, lower
confidence fallback.
"""
from __future__ import annotations

import argparse
import glob
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"
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
    return {
        "round": int(record.findtext("round") or 0),
        "supply": int(data.findtext("supply") or 0),
        "units": units, "unlocked": unlocked, "techs": techs,
    }


def raw_actions(record):
    out = []
    for a in record.findall("actionRecords/MatchActionData"):
        kind = a.get(XSI)
        if kind in {"PAD_BuyUnit", "PAD_UnlockUnit", "PAD_UpgradeUnit", "PAD_UpgradeTechnology"}:
            out.append({"kind": kind, "uid": numeric(a.findtext("UID")),
                        "uidx": a.findtext("UIDX"), "tech_id": numeric(a.findtext("TechID"))})
        elif kind == "PAD_Undo":
            out.append({"kind": "PAD_Undo"})
    return out


def undo_filtered_actions(record):
    """Best-effort final-record fallback: undo reverses the latest econ action."""
    kept = []
    for action in raw_actions(record):
        if action["kind"] == "PAD_Undo":
            if kept:
                kept.pop()
        else:
            kept.append(action)
    return kept


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
    if kind == "buy":
        return ((sell_supply, "replay_sell_supply") if sell_supply and sell_supply > 0
                else (None, "unknown_buy_without_sellsupply"))
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
        return ((tech["base_cost"] + 200 * prior_tech_count, "catalog_base_plus_tech_surcharge")
                if tech else (None, "unknown_tech"))
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
    if kind == "tech":
        out["tech"] = tech_label(uid, extra["tech_id"])
    return out


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

    new = [(idx, u) for idx, u in after["units"].items() if idx not in before["units"]]
    buy_logs = [a for a in undo_filtered_actions(record) if a["kind"] == "PAD_BuyUnit"]
    consumed = set()
    for log in buy_logs:
        for idx, unit in new:
            if idx not in consumed and unit["uid"] == log["uid"]:
                consumed.add(idx)
                actions.append(make_action("buy", unit["uid"], sell_supply=unit["sell_supply"], instance_index=idx))
                break
    reinforcements = [{"uid": u["uid"], "unit": unit_label(u["uid"]), "instance_index": idx}
                      for idx, u in new if idx not in consumed]
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
    for log in undo_filtered_actions(record):
        uid = log["uid"]
        if log["kind"] == "PAD_BuyUnit":
            actions.append(make_action("buy", uid, sell_supply=None))
        elif log["kind"] == "PAD_UnlockUnit":
            actions.append(make_action("unlock", uid))
        elif log["kind"] == "PAD_UpgradeUnit":
            unit = before["units"].get(log["uidx"], {})
            actions.append(make_action("level", unit.get("uid"), sell_supply=unit.get("sell_supply"),
                                       instance_index=log["uidx"]))
        elif log["kind"] == "PAD_UpgradeTechnology":
            prior = len(before["techs"].get(uid, set()))
            actions.append(make_action("tech", uid, tech_id=log["tech_id"], prior_tech_count=prior))
    return actions, []


def summarize_round(actions):
    cat, by_unit, unknown = Counter(), Counter(), []
    for action in actions:
        if action["cost"] is None:
            unknown.append(action)
            # Keep the original unit dimension visible even when the action
            # cannot contribute an exact monetary amount.
            by_unit.setdefault(action["uid"], 0)
        else:
            cat[action["type"]] += action["cost"]
            by_unit[action["uid"]] += action["cost"]
    return dict(cat), dict(by_unit), unknown, sum(cat.values())


def parse_root(root, file_path, unknown_slots):
    players = []
    for pr in root.findall("playerRecords/PlayerRecord"):
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
            cat, by_unit, unknown, known_total = summarize_round(actions)
            cumulative += known_total
            final_by_unit.update(by_unit)
            rounds.append({"round": current["round"], "supply_remaining": current["supply"], "econ_spent": econ,
                           "economy_confidence": confidence, "actions": actions, "reinforcements": reinforcements,
                           "cat_cost": cat, "o_by_unit": by_unit, "known_total": known_total,
                           "has_unknown_cost": bool(unknown), "unknown_cost_actions": unknown,
                           "cumulative_known_total": cumulative})
        players.append({"name": pr.findtext("name") or "", "first_round_supply": first,
                        "round_supply_increase": inc, "rounds": rounds,
                        "final_o_by_unit": dict(final_by_unit), "final_known_total": cumulative})
    return {"file": str(file_path), "version": root.findtext("Version") or root.findtext("version"),
            "match_mode": root.findtext("BattleInfo/MatchMode") or "VS_1_1",
            "players": players, "unknown_unit_slots": unknown_slots}


def parse_replay(file_path, unknown_slots=None):
    return parse_root(ET.fromstring(extract_xml(file_path)), file_path,
                      unknown_slots or {"slots": {}, "overflow": []})


def select_samples(replay_dir, n=3):
    ranked = []
    for f in sorted(glob.glob(str(Path(replay_dir) / "*.grbr"))):
        try:
            root = ET.fromstring(extract_xml(f))
            if root.findtext("BattleInfo/MatchMode") != "VS_1_1":
                continue
            records = root.findall("playerRecords/PlayerRecord/playerRoundRecords/PlayerRoundRecord")
            numbers = [int(x.findtext("round") or 0) for x in records]
            if numbers and set(numbers) == set(range(min(numbers), max(numbers) + 1)):
                ranked.append((len(numbers), f))
        except Exception:
            continue
    return [f for _, f in sorted(ranked, reverse=True)[:n]]


def write_markdown(results, path):
    lines = ["# 3 个对局投入资源解析报告", "",
             "> 自动生成。普通单位默认费用与科技基础价均来自 reference_code/unit_cost_source.json。",
             "> 科技局内结算价 = 基础价 + 200 × 同兵种此前已生效科技数；相邻快照差为最终事实，最后快照为撤销感知的低置信度回退。", ""]
    for i, rep in enumerate(results, 1):
        lines += [f"## 对局 {i}：{Path(rep['file']).name}", ""]
        for p in rep["players"]:
            lines += [f"### 玩家：{p['name']}", "",
                      "| 回合 | 剩余 supply | 实际花费 | 已知投入 | 未知费用 | 置信度 | 累计已知投入 |",
                      "|---:|---:|---:|---:|:---:|:---|---:|"]
            for rd in p["rounds"]:
                lines.append(f"| {rd['round']} | {rd['supply_remaining']} | {rd['econ_spent'] if rd['econ_spent'] is not None else '—'} | {rd['known_total']} | {'是' if rd['has_unknown_cost'] else '否'} | {rd['economy_confidence']} | {rd['cumulative_known_total']} |")
            lines += ["", "**动作明细：**", ""]
            for rd in p["rounds"]:
                bits = []
                for a in rd["actions"]:
                    title = a["unit"] + ("/" + a["tech"] if a["type"] == "tech" else "")
                    bits.append(f"{a['type']} {title} ({a['cost'] if a['cost'] is not None else '未知'}；{a['cost_basis']})")
                lines.append(f"- R{rd['round']}：{'；'.join(bits) or '无经济动作'}")
                if rd["reinforcements"]:
                    lines.append("  - 免费增援/非购买新实例：" + "、".join(x["unit"] for x in rd["reinforcements"]))
                if rd["unknown_cost_actions"]:
                    lines.append("  - 未知费用动作已保留在原始兵种统计，未计入 known_total。")
            lines += ["", f"已知累计投入：{p['final_known_total']}。未知 ID 预留槽：{json.dumps(rep['unknown_unit_slots'], ensure_ascii=False)}", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay-dir", default="local_data/humen_replay")
    ap.add_argument("--out-json", default="reference_code/parse_3_matches.json")
    ap.add_argument("--out-md", default="information/parse_3_matches_report.md")
    ap.add_argument("--num", type=int, default=3)
    args = ap.parse_args()
    replay_dir = ROOT / args.replay_dir
    samples = select_samples(replay_dir, args.num)
    roots = [ET.fromstring(extract_xml(f)) for f in samples]
    slots = slot_mapping(roots)
    matches = [parse_root(root, file_path, slots) for root, file_path in zip(roots, samples)]
    output = {"meta": {"catalog": str(CATALOG_PATH.relative_to(ROOT)), "unknown_unit_slots": slots}, "matches": matches}
    (ROOT / args.out_json).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(matches, ROOT / args.out_md)
    print(f"wrote {args.out_json} and {args.out_md}")


if __name__ == "__main__":
    main()

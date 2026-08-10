#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Materialize the checkable unit-cost table from the one static source.

This script never estimates a price from supply residuals.  Replays are used
only to count configuration/research/deployment observations and to report
unmapped IDs.  The parser imports the same static source directly.
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from parse_match_investment import CATALOG_PATH, extract_xml, load_cost_catalog, numeric

ROOT = Path(__file__).resolve().parent.parent


def replay_coverage(replay_dir):
    """Count real board/action/config occurrences without treating them as costs."""
    unit_counts = defaultdict(Counter)
    tech_counts = defaultdict(Counter)
    special_tech = Counter()
    versions = Counter()
    files = sorted(Path(replay_dir).glob("*.grbr"))
    for file_path in files:
        try:
            root = ET.fromstring(extract_xml(file_path))
        except Exception:
            continue
        versions[root.findtext("Version") or root.findtext("version") or "unknown"] += 1
        for u in root.iter("NewUnitData"):
            uid = numeric(u.findtext("id"))
            unit_counts[uid]["deployed"] += 1
        for action in root.findall(".//actionRecords/MatchActionData"):
            xsi = action.get("{http://www.w3.org/2001/XMLSchema-instance}type")
            uid = numeric(action.findtext("UID"))
            if xsi == "PAD_BuyUnit":
                unit_counts[uid]["buy_actions"] += 1
            elif xsi == "PAD_UnlockUnit":
                unit_counts[uid]["unlock_actions"] += 1
            elif xsi == "PAD_UpgradeTechnology":
                tech_counts[uid][numeric(action.findtext("TechID"))] += 1
        # Configuration is the final player data and deliberately counted
        # separately: configuration-only IDs do not become standard units.
        for unit_data in root.findall(".//data/unitDatas/unitData"):
            uid = numeric(unit_data.findtext("id"))
            unit_counts[uid]["configured"] += 1
            for tech in unit_data.findall("techs/tech"):
                special_tech[(uid, numeric(tech.get("data")))] += 1
    return files, versions, unit_counts, tech_counts, special_tech


def build_table(replay_dir):
    raw, catalog = load_cost_catalog()
    files, versions, unit_counts, tech_counts, configured = replay_coverage(replay_dir)
    units = {}
    missing_tech = []
    special_unmapped_tech = []
    unknown_actual_ids = []
    for uid, source in sorted(catalog.items()):
        unit = {key: value for key, value in source.items() if key != "technologies_by_id"}
        observation = dict(unit_counts.get(uid, {}))
        techs = []
        for tech in unit["technologies"]:
            row = dict(tech)
            row["replay_research_actions"] = tech_counts.get(uid, {}).get(tech["tech_id"], 0)
            row["replay_configurations"] = configured.get((uid, tech["tech_id"]), 0)
            techs.append(row)
        unit["technologies"] = techs
        unit["observation"] = observation
        units[str(uid)] = unit

    actual = {uid for uid, count in unit_counts.items()
              if count["deployed"] or count["buy_actions"] or count["unlock_actions"] or tech_counts.get(uid)}
    for uid in sorted(actual):
        if uid not in catalog:
            unknown_actual_ids.append(uid)
        for tid, n in tech_counts.get(uid, {}).items():
            if uid not in catalog or tid not in catalog[uid]["technologies_by_id"]:
                target = (special_unmapped_tech if catalog.get(uid, {}).get("special_unit", False)
                          else missing_tech)
                target.append({"unit_id": uid, "tech_id": tid, "research_actions": n})

    return {
        "meta": {
            **raw["meta"], "generated_from": "unit_cost_source.json",
            "replay_files_checked": len(files), "versions": dict(versions),
            "standard_unit_ids": [int(uid) for uid, unit in units.items() if not unit["special_unit"]],
            "special_unit_ids": [4001],
            "configured_only_ids_excluded": sorted(
                uid for uid, c in unit_counts.items()
                if c["configured"] and not (c["deployed"] or c["buy_actions"] or c["unlock_actions"] or tech_counts.get(uid))
                and uid not in catalog
            ),
            "unknown_actual_ids": unknown_actual_ids,
            "missing_catalog_tech_ids": missing_tech,
            "expected_special_unit_unmapped_tech_ids": special_unmapped_tech,
        },
        "units": units,
    }


def write_markdown(table, path):
    lines = [
        "# 兵种默认成本与科技基础价确认表", "",
        f"> 静态快照日期：{table['meta']['snapshot_date']}。单位默认解锁/一级购买费优先来自人工确认表；科技基础价来自中文 Wiki 快照。",
        "> 局内科技结算价 = 下表基础价 + 200 × 同兵种此前已生效科技数。这个 +200 不是科技基础价推导规则。", "",
        "## 逐兵种总表", "",
        "| ID | 兵种 | 默认解锁 | 一级购买 | 每级升级 | 科技数 | 来源 | 置信度 | 回放部署/研究 |",
        "|---:|---|---:|---:|---:|---:|---|---|---:|",
    ]
    for uid, u in table["units"].items():
        o = u.get("observation", {})
        deployed = o.get("deployed", 0)
        research = sum(x["replay_research_actions"] for x in u["technologies"])
        cost = lambda x: x if x is not None else "未定"
        lines.append(f"| {uid} | {u['name_cn']} ({u['name_en']}) | {cost(u['unlock_cost'])} | {cost(u['base_buy_cost'])} | {cost(u['upgrade_cost_per_level'])} | {len(u['technologies'])} | {u['source']} | {u['confidence']} | {deployed}/{research} |")
    lines += ["", "## 科技明细", ""]
    for uid, u in table["units"].items():
        lines += [f"### {uid}：{u['name_cn']} ({u['name_en']})", "",
                  "| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |",
                  "|---:|---|---:|---:|---:|---|---|"]
        if not u["technologies"]:
            lines.append("| — | 特殊单位：不套用普通丧钟价格模型 | 未定 | — | — | replay_only | unknown |")
        for t in u["technologies"]:
            lines.append(f"| {t['tech_id']} | {t['name_cn']} ({t['name_en']}) | {t['base_cost']} | {t['replay_research_actions']} | {t['replay_configurations']} | {t['source']} | {t['confidence']} |")
        lines.append("")
    meta = table["meta"]
    lines += ["## 覆盖检查", "",
              f"- 检查回放：{meta['replay_files_checked']} 局；版本：{meta['versions']}。",
              f"- 特殊 ID：4001 保留为实验丧钟/特殊单位；未知默认解锁和科技费用保持未定。",
              f"- 配置中出现但未进入棋盘或经济动作、未纳入标准表的 ID：{meta['configured_only_ids_excluded']}。",
              f"- 实际出现但不在目录的 ID：{meta['unknown_actual_ids']}。",
              f"- 普通单位缺少科技目录映射：{meta['missing_catalog_tech_ids']}。",
              f"- 特殊单位预期保留的未定科技：{meta['expected_special_unit_unmapped_tech_ids']}。",
              "",
              "所有正常单位的升级费均为一级购买费用的一半；解析器和本表都只读取同一份静态成本源。",
              ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay-dir", default="local_data/humen_replay")
    ap.add_argument("--out-json", default="reference_code/unit_cost_table.json")
    ap.add_argument("--out-md", default="information/unit_cost_table.md")
    args = ap.parse_args()
    table = build_table(ROOT / args.replay_dir)
    (ROOT / args.out_json).write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(table, ROOT / args.out_md)
    print(f"wrote {args.out_json} and {args.out_md}; source={CATALOG_PATH.name}")


if __name__ == "__main__":
    main()

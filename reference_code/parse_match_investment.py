#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
解析 Mechabellum 回放，打印两个玩家在各个回合的投入资源。

对应 README TODO #1：
  "尝试解析3个对局的数据，打印两个玩家在各个回合投入的资源和owner确认是否解析正确
   （干什么 花了多少钱）"

成本模型（详见 information/parse_3_matches_report.md）：
  - 解锁费用 / 首购基础价：来自回放内 NewUnitData 的 SellSupply 字段（数据驱动，按版本准确）
  - 兵种等级费用：来自 UNIT_LEVEL_COST 表（按 (兵种, 目标等级) 覆盖，默认值待 owner 确认）
  - 总科技费用：来自 TECH_COST 表（默认 50，个别 100，待 owner 确认）
  - 交叉校验：利用每回合剩余 supply 反推「实际花费」，与上面推导成本对比
    （仅当回合序列连续时可靠）

运行：
  python3 reference_code/parse_match_investment.py
  python3 reference_code/parse_match_investment.py --out-json reference_code/parse_3_matches.json
"""
import sys
import json
import glob
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET

# 复用既有解析器的 ID 查找表（ID -> 英文名 / 中文名 / 科技名）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mechabellum_stats import UNIT_LOOKUP, TECH_LOOKUP, UNIT_CN  # noqa: E402

XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"

# ============================================================
# 成本表（全部为「待 owner 确认」的估算值，集中在此便于一处修改）
# ============================================================
# 解锁费用：默认 0。回放内无独立解锁花费字段，且 supply 反推未显示额外开销。
#   若实际上 unlock 需付费（通常等于首购基础价），可在此改为非负值。
UNLOCK_COST = 0

# 兵种每升一级的默认费用。键为 (兵种ID, 升到该等级) -> 费用。
# 例：(1, 2) 表示 fortress 升到 2 级。缺失时回退到 DEFAULT_LEVEL_COST。
# 注意：SellSupply 字段证实「升级不改变单位 SellSupply」，故等级费用无法从数据推导，必须依赖此表。
DEFAULT_LEVEL_COST = 50
UNIT_LEVEL_COST = {
    # 可在确认数值后按兵种/等级补全，例如：
    # 1: {2: 100, 3: 150, 4: 200},   # fortress
}

# 科技费用：默认 50，部分科技为 100。
DEFAULT_TECH_COST = 50
TECH_COST = {
    # 例：180110: 100,  # Replicate
}

# 新兵种预留维度（README 要求）：解析时遇到未知兵种 ID 也照常记录，不报错。
RESERVED_NEW_UNIT_DIMS = 10


def unit_name(uid):
    uid = int(uid) if str(uid).lstrip("-").isdigit() else uid
    en = UNIT_LOOKUP.get(uid, f"未知兵种({uid})")
    cn = UNIT_CN.get(en, "")
    return f"{cn}({en})" if cn else en


def tech_name(tid):
    tid = int(tid) if str(tid).lstrip("-").isdigit() else tid
    en = TECH_LOOKUP.get(tid, f"未知科技({tid})")
    cn = UNIT_CN.get(en, "")  # TECH_CN 未在此模块导出，仅用英文
    return f"{cn}({en})" if cn else en


# ============================================================
# XML 提取
# ============================================================
def extract_xml(file_path):
    with open(file_path, "rb") as f:
        data = f.read()
    start = data.find(b"<?xml")
    end = data.rfind(b"BattleRecord>") + len(b"BattleRecord>")
    if start == -1 or end == 12:
        raise ValueError(f"No XML found in {file_path}")
    return data[start:end].decode("utf-8")


# ============================================================
# 基础价推导：扫描整局，取每个兵种「首次出现」时的 SellSupply 作为基础价
# （首次出现为满血、0 级、刚购买状态，SellSupply 即基础价；
#   后续受损单位的 SellSupply 会偏低，故用首次出现而非最小值）
# ============================================================
def compute_base_costs(root):
    base = {}
    for u in root.iter("NewUnitData"):
        uid = u.findtext("id")
        ss = u.findtext("SellSupply")
        if uid is None or ss is None:
            continue
        ss = int(ss)
        if ss <= 0:
            continue
        if uid not in base:
            base[uid] = ss
    return base


# ============================================================
# 解析单个对局
# ============================================================
def parse_replay(file_path):
    root = ET.fromstring(extract_xml(file_path))
    match_mode = root.findtext("BattleInfo/MatchMode") or "VS_1_1"
    is_2v2 = (match_mode == "VS_2_2")
    base_costs = compute_base_costs(root)

    players = []
    for pr in root.findall("playerRecords/PlayerRecord"):
        name = pr.findtext("name")
        data_el = pr.find("data")
        first = int(data_el.findtext("firstRoundSupply") or 0)
        inc = int(data_el.findtext("roundSupplyIncreaseValue") or 0)

        prev_snap = {}          # Index -> (uid, level, sellsupply)
        prev_rem = None
        rounds = []

        for r in pr.findall("playerRoundRecords/PlayerRoundRecord"):
            rnd = int(r.findtext("round"))
            rem = int(r.findtext("playerData/supply") or 0)

            # 当前回合单位快照
            snap = {}
            for u in r.findall("playerData/units/NewUnitData"):
                idx = u.findtext("Index")
                snap[idx] = (
                    u.findtext("id"),
                    int(u.findtext("Level") or 0),
                    int(u.findtext("SellSupply") or 0),
                )

            # supply 经济反推「实际花费」（仅连续回合可靠）
            econ_spent = (prev_rem + inc - rem) if prev_rem is not None else None
            prev_rem = rem

            # 新出现的单位实例（本轮有、上轮无）
            new_indices = sorted(set(snap) - set(prev_snap),
                                  key=lambda x: int(x) if str(x).lstrip("-").isdigit() else 0)

            actions = []
            # 用于把 BuyUnit 匹配到新建实例（仅用于增援识别，不影响成本）
            remaining_new = list(new_indices)

            for a in r.findall("actionRecords/MatchActionData"):
                t = a.get(XSI)
                if t == "PAD_BuyUnit":
                    uid = a.findtext("UID")
                    uid_int = int(uid) if uid and uid.isdigit() else uid
                    cost = base_costs.get(uid, 0)
                    # 消耗一个同兵种新实例用于增援识别
                    matched = None
                    for ni in remaining_new:
                        if snap[ni][0] == uid:
                            matched = ni
                            break
                    if matched is not None:
                        remaining_new.remove(matched)
                    actions.append({
                        "type": "buy", "uid": uid_int,
                        "unit": unit_name(uid), "cost": cost,
                    })
                elif t == "PAD_UnlockUnit":
                    uid = a.findtext("UID")
                    uid_int = int(uid) if uid and uid.isdigit() else uid
                    actions.append({
                        "type": "unlock", "uid": uid_int,
                        "unit": unit_name(uid), "cost": UNLOCK_COST,
                    })
                elif t == "PAD_UpgradeUnit":
                    uidx = a.findtext("UIDX")
                    uid = snap.get(uidx, (None, 0, 0))[0]
                    from_level = prev_snap.get(uidx, (None, 0, 0))[1]
                    to_level = from_level + 1
                    uid_int = int(uid) if uid and str(uid).lstrip("-").isdigit() else uid
                    cost = (UNIT_LEVEL_COST.get(uid_int, {}).get(to_level, DEFAULT_LEVEL_COST)
                            if uid_int is not None else DEFAULT_LEVEL_COST)
                    actions.append({
                        "type": "level", "uid": uid_int,
                        "unit": unit_name(uid), "from_level": from_level,
                        "level": to_level, "cost": cost,
                    })
                elif t == "PAD_UpgradeTechnology":
                    uid = a.findtext("UID")
                    tid = a.findtext("TechID")
                    uid_int = int(uid) if uid and uid.isdigit() else uid
                    tid_int = int(tid) if tid and tid.isdigit() else tid
                    cost = TECH_COST.get(tid_int, DEFAULT_TECH_COST)
                    actions.append({
                        "type": "tech", "uid": uid_int,
                        "unit": unit_name(uid), "tech": tech_name(tid),
                        "tech_id": tid_int, "cost": cost,
                    })

            # 未被 BuyUnit 匹配的新实例 -> 视为增援（免费，不计入花费）
            reinforcements = []
            for ni in remaining_new:
                reinforcements.append({
                    "uid": int(snap[ni][0]) if snap[ni][0] and snap[ni][0].isdigit() else snap[ni][0],
                    "unit": unit_name(snap[ni][0]),
                })

            # 分类小计 + 累计 O
            cat = {"unlock": 0, "buy": 0, "level": 0, "tech": 0}
            o_by_unit = {}
            round_total = 0
            for act in actions:
                cat[act["type"]] = cat.get(act["type"], 0) + act["cost"]
                round_total += act["cost"]
                u = act.get("uid")
                if u is not None:
                    o_by_unit[u] = o_by_unit.get(u, 0) + act["cost"]

            rounds.append({
                "round": rnd,
                "supply_remaining": rem,
                "econ_spent": econ_spent,
                "actions": actions,
                "reinforcements": reinforcements,
                "cat_cost": cat,
                "round_total": round_total,
                "o_by_unit": o_by_unit,
            })

            prev_snap = snap

        # 累计 O（逐回合累加）与按兵种累计
        cum_o = 0
        cum_by_unit = {}
        for rd in rounds:
            cum_o += rd["round_total"]
            rd["cumulative_o"] = cum_o
            for u, c in rd["o_by_unit"].items():
                cum_by_unit[u] = cum_by_unit.get(u, 0) + c
        # 最终按兵种累计（跨所有回合）
        final_o_by_unit = {int(k) if str(k).lstrip("-").isdigit() else k: v
                           for k, v in sorted(cum_by_unit.items(),
                                              key=lambda kv: -kv[1])}

        players.append({
            "name": name,
            "first_round_supply": first,
            "round_supply_increase": inc,
            "rounds": rounds,
            "final_o_by_unit": final_o_by_unit,
            "final_total_o": cum_o,
        })

    return {
        "file": str(file_path),
        "match_mode": match_mode,
        "is_2v2": is_2v2,
        "players": players,
    }


# ============================================================
# 样本选择：优先选回合序列连续、回合数多、兵种多样的 1v1
# ============================================================
def select_samples(replay_dir, n=3):
    files = sorted(glob.glob(str(Path(replay_dir) / "*.grbr")))
    candidates = []
    for f in files:
        try:
            root = ET.fromstring(extract_xml(f))
        except Exception:
            continue
        if root.findtext("BattleInfo/MatchMode") != "VS_1_1":
            continue
        # 回合列表
        rounds = []
        ok = True
        pr = root.find("playerRecords/PlayerRecord")
        if pr is None:
            continue
        for r in pr.findall("playerRoundRecords/PlayerRoundRecord"):
            try:
                rounds.append(int(r.findtext("round")))
            except Exception:
                ok = False
                break
        if not ok or not rounds:
            continue
        rmin, rmax = min(rounds), max(rounds)
        consecutive = (set(range(rmin, rmax + 1)) == set(rounds))
        if not consecutive:
            continue  # 跳过有缺口的，保证 supply 交叉校验可靠
        # 兵种多样度
        units = set()
        for u in root.iter("NewUnitData"):
            units.add(u.findtext("id"))
        candidates.append((len(rounds), len(units), f))
    # 按回合数、兵种数降序
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [c[2] for c in candidates[:n]]


# ============================================================
# 打印
# ============================================================
def print_replay(rep):
    print("=" * 78)
    print(f"对局: {Path(rep['file']).name}")
    print(f"模式: {rep['match_mode']}  |  玩家数: {len(rep['players'])}")
    print("=" * 78)
    for p in rep["players"]:
        print(f"\n--- 玩家: {p['name']}  (首回合supply={p['first_round_supply']}, 每回合+{p['round_supply_increase']}) ---")
        for rd in p["rounds"]:
            line = f"  [R{rd['round']:>2}] 剩余supply={rd['supply_remaining']:>4}"
            if rd["econ_spent"] is not None:
                line += f" 实际花费≈{rd['econ_spent']:>4}"
            line += f" | 推导花费={rd['round_total']:>4}"
            if rd["econ_spent"] is not None:
                line += f" (差={rd['econ_spent'] - rd['round_total']:>+4})"
            line += f" | 累计O={rd['cumulative_o']:>4}"
            print(line)
            for act in rd["actions"]:
                if act["type"] == "buy":
                    print(f"       购买 {act['unit']:<14} 基础价={act['cost']}")
                elif act["type"] == "unlock":
                    print(f"       解锁 {act['unit']:<14} 费用={act['cost']}")
                elif act["type"] == "level":
                    print(f"       升级 {act['unit']:<14} Lv{act['from_level']}->Lv{act['level']} 费用={act['cost']}")
                elif act["type"] == "tech":
                    print(f"       科技 {act['unit']:<14} {act['tech']:<22} 费用={act['cost']}")
            for rf in rd["reinforcements"]:
                print(f"       增援 {rf['unit']:<14} (免费, 不计入花费)")
        print(f"  最终累计投入 O = {p['final_total_o']}")
        top = list(p["final_o_by_unit"].items())[:12]
        print("  按兵种累计 O: " + ", ".join(f"{unit_name(u)}={c}" for u, c in top))


# ============================================================
# Markdown 报告生成（供 owner 核对，与数据保持同步）
# ============================================================
def write_markdown(results, path):
    L = []
    L.append("# 3 个对局投入资源解析报告\n")
    L.append("> 自动生成，对应 README TODO #1：尝试解析 3 个对局的数据，打印双方各回合投入资源，"
             "供 owner 确认解析是否正确（干什么 / 花了多少钱）。\n")
    L.append("## 一、成本模型与方法\n")
    L.append("- **解锁费用 / 首购基础价**：来自回放内 `NewUnitData.SellSupply` 的「首次出现值」"
             "（数据驱动，按游戏版本准确；SellSupply 随兵种固定、不随等级变化）。")
    L.append(f"- **兵种等级费用**：来自 `UNIT_LEVEL_COST` 表，默认每级 `{DEFAULT_LEVEL_COST}`"
             "（⚠️ 待 owner 确认具体数值；SellSupply 已证实不随升级变化，故等级费用**无法**从数据推导）。")
    L.append(f"- **总科技费用**：来自 `TECH_COST` 表，默认 `{DEFAULT_TECH_COST}`（⚠️ 待 owner 确认）。")
    L.append(f"- **解锁费用（`PAD_UnlockUnit`）**：当前设为 `{UNLOCK_COST}`（⚠️ 待 owner 确认是否应等于首购基础价）。")
    L.append("- **交叉校验**：利用每回合剩余 supply 反推「实际花费」=`上一轮剩余 + 每回合增量 - 本轮剩余`，"
             "与上面推导成本对比（差值列）。**仅当回合序列连续时可靠**（样本均已筛选为连续回合）。")
    L.append("- **新兵种预留**：解析对未知兵种 ID 也照常记录，不报错（README 要求预留 10 维）。\n")

    L.append("## 二、逐回合明细（含交叉校验）\n")
    for ri, rep in enumerate(results, 1):
        L.append(f"### 对局 {ri}：`{Path(rep['file']).name}`\n")
        L.append(f"- 模式：`{rep['match_mode']}`\n")
        for p in rep["players"]:
            L.append(f"#### 玩家：{p['name']}\n")
            L.append("| 回合 | 剩余supply | 实际花费(反推) | 推导花费 | 差值 | 累计O |")
            L.append("|-----:|-----:|-----:|-----:|-----:|-----:|")
            for rd in p["rounds"]:
                econ = rd["econ_spent"]
                diff = (econ - rd["round_total"]) if econ is not None else ""
                L.append(f"| {rd['round']} | {rd['supply_remaining']} | "
                         f"{econ if econ is not None else '—'} | {rd['round_total']} | "
                         f"{diff} | {rd['cumulative_o']} |")
            L.append("")
            L.append("**动作明细：**\n")
            for rd in p["rounds"]:
                L.append(f"- R{rd['round']}（推导 {rd['round_total']}）：")
                parts = []
                for act in rd["actions"]:
                    if act["type"] == "buy":
                        parts.append(f"购买{act['unit']}({act['cost']})")
                    elif act["type"] == "unlock":
                        parts.append(f"解锁{act['unit']}({act['cost']})")
                    elif act["type"] == "level":
                        parts.append(f"升级{act['unit']}Lv{act['from_level']}→Lv{act['level']}({act['cost']})")
                    elif act["type"] == "tech":
                        parts.append(f"科技{act['unit']}/{act['tech']}({act['cost']})")
                L.append("；".join(parts) if parts else "（无经济动作）")
                if rd["reinforcements"]:
                    rf = ", ".join(r["unit"] for r in rd["reinforcements"])
                    L.append(f"  ｜ 增援(免费)：{rf}")
            L.append("")
            top = "，".join(f"{unit_name(u)}={c}" for u, c in list(p["final_o_by_unit"].items())[:10])
            L.append(f"**按兵种累计 O（Top10）**：{top}\n")
            econ_total = sum(rd["econ_spent"] for rd in p["rounds"] if rd["econ_spent"] is not None)
            ratio = (p["final_total_o"] / econ_total) if econ_total else float("nan")
            L.append(f"**小结**：推导累计 O = {p['final_total_o']}；实际花费(反推)累计 = {econ_total}；"
                     f"比值 ≈ {ratio:.2f}（>1 表示推导偏高，疑似增援单位被误计为购买）。\n")

    L.append("## 三、需 owner 确认的开放问题\n")
    L.append("1. **增援单位是否误计为购买**：多局从 R2 起「推导花费」显著高于「实际花费(反推)」"
             "（差值为负且递增），主因是 unit 31（phantom ray 类）等单位既出现在 `PAD_BuyUnit` 又出现在免费增援列表。"
             "请确认增援单位的判定规则（是否免费、是否应排除计费）。")
    L.append("2. **科技费用数值**：`TECH_COST` 默认 50、个别 100，是否准确？")
    L.append("3. **兵种等级费用**：`UNIT_LEVEL_COST` 默认每级 50，是否准确？不同兵种/等级是否不同？")
    L.append("4. **解锁费用**：`PAD_UnlockUnit` 当前计费 0，是否应等于首购基础价？")
    L.append("5. **兵种中文名**：本数据集回放来自较新版本，部分兵种 ID（如 31、2002）超出既有 `UNIT_LOOKUP`（1–29），"
             "显示为「未知兵种(ID)」。成本计算不受影响（基础价来自数据驱动 SellSupply）；中文名需按当前版本补全。\n")

    L.append("## 四、产出文件\n")
    L.append("- 解析脚本：`reference_code/parse_match_investment.py`")
    L.append("- 结构化 JSON：`reference_code/parse_3_matches.json`（后续「批量解析」的种子）")
    L.append("- 本报告：`information/parse_3_matches_report.md`\n")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    return path


# ============================================================
# main
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay-dir", default="local_data/humen_replay")
    ap.add_argument("--out-json", default="reference_code/parse_3_matches.json")
    ap.add_argument("--out-md", default="information/parse_3_matches_report.md")
    ap.add_argument("--num", type=int, default=3)
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent
    replay_dir = base / args.replay_dir
    out_json = base / args.out_json
    out_md = base / args.out_md

    samples = select_samples(replay_dir, args.num)
    if not samples:
        print("未找到符合条件的 1v1 样本（需回合连续）。")
        return

    print(f"已选择 {len(samples)} 个样本对局：")
    for s in samples:
        print(f"  - {Path(s).name}")

    results = []
    for f in samples:
        rep = parse_replay(f)
        results.append(rep)
        print_replay(rep)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"\n结构化 JSON 已写入: {out_json}")

    md_path = write_markdown(results, out_md)
    print(f"核对文档已写入: {md_path}")


if __name__ == "__main__":
    main()

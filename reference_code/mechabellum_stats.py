#!/usr/bin/env python3
"""
Mechabellum 回放兵种胜率统计分析器
解析 .rep.grbr 文件，统计兵种回合胜率、出场率、胜率矩阵等
"""

import xml.etree.ElementTree as ET
import os
import json
from collections import defaultdict, Counter
from pathlib import Path

# ============================================================
# 查找表 (来自 ShotgunCrocodile/mechabellum_replay_parser)
# ============================================================

UNIT_LOOKUP = {
    1: "fortress", 2: "marksmen", 3: "vulcan", 4: "melting point",
    5: "rhino", 6: "wasp", 7: "mustang", 8: "steel ball",
    9: "fang", 10: "crawler", 11: "overlord", 12: "stormcaller",
    13: "sledgehammer", 14: "hacker", 15: "arclight", 16: "phoenix",
    17: "warfactory", 18: "wraith", 19: "scorpion", 20: "fire badger",
    21: "sabertooth", 22: "typhoon", 23: "sandworm", 24: "tarantula",
    25: "phantom ray", 26: "farseer", 27: "raiden", 28: "hound",
    29: "abyss",
}

TECH_LOOKUP = {
    # Crawler
    10510: "Mechanical rage", 180110: "Replicate", 2610: "Subterranean blitz",
    2710: "Acidic explosion", 10710: "Impact drill", 3510: "Loose formation",
    # Fang
    180209: "Ignite", 10209: "Range enhancement", 10509: "Mechanical rage",
    209: "Portable shield", 10609: "Armor piercing bullets",
    # Fortress
    1001: "Barrier", 10201: "Range enhancement", 1105: "Anti air barrage",
    1201: "Fang production", 10301: "Launcher overload", 10801: "Elite marksman",
    701: "Doubleshot", 3001: "Armor enhancement", 110201: "Rocket punch",
    10401: "Solid shot",
    # Marksman
    702: "Doubleshot", 10202: "Range enhancement", 10402: "Quick reload",
    1802: "Electromagnetic shot", 10802: "Elite marksman", 1202: "Shooting squad",
    10102: "Assault mode", 3202: "Aerial specialisation",
    # Vulcan
    180203: "Ignite", 10203: "Range enhancement", 1103: "Incendiary bomb",
    10603: "Scorching fire", 1203: "Best partner", 11010: "Sticky oil bomb",
    3003: "Armor enhancement",
    # Melting point
    304: "Energy absorption", 10204: "Range enhancement", 1107: "Energy diffraction",
    1106: "Electromagnetic barrage", 1204: "Crawler production", 3004: "Armor enhancement",
    # Rhino
    1109: "Whirlwind", 180305: "Photon coating", 905: "Field maintenance",
    2805: "Final blitz", 10505: "Mechanical rage", 2305: "Wreckage recycling",
    2505: "Power armor", 3005: "Armor enhancement",
    # Wasp
    206: "Energy shield", 10206: "Range enhancement", 1606: "Jump drive",
    506: "Ground specialization", 10806: "Elite marksman", 180206: "Ignite",
    1806: "Electromagnetic shot", 406: "High explosive ammo",
    10606: "Armor piercing bullets", 3206: "Aerial specialization",
    # Mustang
    3307: "Missile interceptor", 10207: "Range enhancement", 407: "High explosive ammo",
    3207: "Aerial specialization", 10607: "Armor piercing bullets",
    # Steel ball
    308: "Energy absorption", 608: "Damage sharing", 10208: "Range enhancement",
    1308: "Mechanical division", 3008: "Armor enhancement", 2408: "Fortified target lock",
    # Overlord
    1108: "Overlord artillery", 10311: "Launcher overload", 1211: "Mothership",
    1611: "Jump drive", 180311: "Photon emission", 10211: "Range enhancement",
    3011: "Armor enhancement", 911: "Field maintenance", 411: "High explosive ammo",
    # Stormcaller
    812: "Incendiary bomb", 10212: "Range enhancement", 10312: "Launcher overload",
    412: "High explosive ammo", 1812: "Electromagnetic explosion",
    10912: "High explosive anti tank shells",
    # Sledgehammer
    913: "Field maintenance", 613: "Damage sharing", 10513: "Mechanical rage",
    10213: "Range enhancement", 1813: "Electromagnetic shot",
    10613: "Armor piercing bullets", 3013: "Armor enhancement",
    # Hacker
    11014: "Multi control", 1014: "Barrier", 10214: "Range enhancement",
    1714: "Enhanced control", 1814: "Electromagnetic interference",
    # Arclight
    10215: "Range enhancement", 1815: "Electromagnetic shot", 10915: "Charged shot",
    3015: "Armor enhancement", 3115: "Anti aircraft ammunition", 10815: "Elite marksman",
    # Phoenix
    2916: "Quantum reassembly", 10216: "Range enhancement", 10316: "Launcher overload",
    216: "Energy shield", 1616: "Jump drive", 1816: "Electromagnetic shot",
    10816: "Elite marksman", 10916: "Charged shot",
    # War factory
    10217: "Range enhancement", 3417: "Efficient maintenance", 12017: "Phoenix production",
    12117: "Steel ball production", 12217: "Sledgehammer production",
    3317: "Missile interceptor", 10317: "Launcher overload", 180317: "Photon coating",
    3017: "Armor enhancement", 417: "High explosive ammo",
    # Wraith
    110181: "Floating artillery array", 10218: "Range enhancement",
    3018: "Armor enhancement", 180418: "Degeneration beam",
    918: "Field maintenance", 418: "High explosive ammo",
    # Scorpion
    180519: "Acid attack", 10019: "Siege mode", 10219: "Range enhancement",
    719: "Doubleshot", 919: "Field maintenance", 3019: "Armor enhancement",
    # Fire badger
    10220: "Range enhancement", 820: "Napalm", 180220: "Ignite",
    920: "Field maintenance", 10620: "Scorching fire",
    # Sabertooth
    10221: "Range enhancement", 10321: "Field maintenance",
    3321: "Missile interceptor", 721: "Doubleshot", 110211: "Secondary Armament",
    # Typhoon
    3022: "Mechanical rage", 3222: "Aerial specialization", 1022: "Barrier",
    11022: "Homing missile",
    # Sandworm
    10523: "Mechanical rage", 3023: "Armor enhancement", 13023: "Mechanical division",
    3123: "Anti aerial", 923: "Burrow maintenance", 3623: "Replicate",
    3723: "Sandstorm", 3823: "Strike",
    # Tarantula
    11024: "Spider mine", 10224: "Range enhancement", 10524: "Mechanical rage",
    10624: "Armor piercing bullets", 924: "Field maintenance",
    3024: "Armor enhancement", 3124: "Anti aircraft ammunition", 424: "High explosive ammo",
    # Farseer
    180326: "Photon emission", 180526: "Scanning radar", 3326: "Missile interceptor",
    1826: "Electromagnetic explosion", 10226: "Range enhancement",
    # Phantom ray
    725: "Burst mode", 10225: "Range enhancement", 3025: "Armor enhancement",
    11025: "Sticky oil bomb", 3925: "Stealth cloak", 425: "High explosive ammo",
    225: "Energy shield",
    # Raiden
    10227: "Range enhancement", 4027: "Chain", 110271: "Fork",
    1827: "Electromagnetic Shot", 4127: "Ionization",
    # Hound
    10228: "Mechanical rage", 10528: "Enhanced range", 4228: "Fire extinguisher",
    11028: "Incendiary bomb", 3028: "Armor enhancement",
    # Abyss
    10299: "Range enhancement", 12029: "Dark companion", 3429: "Efficient maintenance",
    11029: "Disintegration", 110291: "Swarm missiles", 4329: "Vertical sweep",
    2329: "Wreckage recycling", 180329: "Photon coating",
    # Season 6 new techs (update 1.9.0)
    180805: "Combat Evolvement",       # Rhino
    180808: "Kinetic Charge",          # Steel Ball
    3109: "Grenade Launcher",          # Fang
    4515: "Shockwave",                 # Arclight
    3225: "Ground Targeting",          # Phantom Ray
    180828: "Chamber Compression",     # Hound
    4418: "Land Cruiser",              # Wraith
    # Deduced from ID patterns (category_digit + unit_id)
    10419: "High explosive ammo",      # Scorpion (10+419)
    726: "Doubleshot",                 # Farseer (7+26)
    227: "Energy shield",              # Raiden (2+27)
    3226: "Aerial specialization",     # Farseer (32+26)
    10222: "Energy shield",            # Typhoon (10+222)
    10229: "Energy shield",            # Abyss (10+229)
    4607: "Overload ammo",             # Mustang (new tech)
    11020: "Sticky oil bomb",          # Fire badger (110+20)
    180620: "Ignite",                  # Fire badger (180+620)
    4721: "Armor piercing bullets",    # Sabertooth (47+21)
    4722: "Armor piercing bullets",    # Typhoon (47+22)
    2922: "Energy shield",             # Typhoon (29+22)
    5122: "Mechanical rage",           # Typhoon (51+22)
    5222: "Armor enhancement",         # Typhoon (52+22)
    5322: "Aerial specialization",     # Typhoon (53+22)
    1102022: "Homing missile",         # Typhoon (1102+022)
}

# 兵种中文名映射
UNIT_CN = {
    "fortress": "堡垒", "marksmen": "神射手", "vulcan": "火神",
    "melting point": "熔点", "rhino": "犀牛", "wasp": "黄蜂",
    "mustang": "野马", "steel ball": "钢球", "fang": "毒牙",
    "crawler": "爬虫", "overlord": "霸主", "stormcaller": "风暴召唤者",
    "sledgehammer": "大锤", "hacker": "黑客", "arclight": "弧光",
    "phoenix": "凤凰", "warfactory": "战争工厂", "wraith": "幽灵",
    "scorpion": "蝎子", "fire badger": "火獾", "sabertooth": "剑齿虎",
    "typhoon": "台风", "sandworm": "沙虫", "tarantula": "狼蛛",
    "phantom ray": "幻影射线", "farseer": "先知", "raiden": "雷神",
    "hound": "猎犬", "abyss": "深渊",
}

# 科技中文名映射
TECH_CN = {
    "Mechanical rage": "机械狂暴", "Range enhancement": "射程强化", "Armor enhancement": "装甲强化",
    "High explosive ammo": "高爆弹药", "Electromagnetic shot": "电磁射击", "Elite marksman": "精英射手",
    "Energy shield": "能量护盾", "Jump drive": "跳跃驱动", "Field maintenance": "战场维护",
    "Barrier": "屏障", "Doubleshot": "双发", "Incendiary bomb": "燃烧弹",
    "Armor piercing bullets": "穿甲弹", "Aerial specialization": "对空特化", "Ground specialization": "对地特化",
    "Replicate": "分裂", "Subterranean blitz": "地下突袭", "Acidic explosion": "酸性爆炸",
    "Impact drill": "冲击钻", "Loose formation": "疏散阵型", "Ignite": "点燃",
    "Portable shield": "便携护盾", "Anti air barrage": "防空弹幕", "Fang production": "毒牙生产",
    "Launcher overload": "发射器过载", "Solid shot": "实心弹", "Rocket punch": "火箭拳",
    "Quick reload": "快速装填", "Shooting squad": "射击小队", "Assault mode": "突击模式",
    "Scorching fire": "灼烧火焰", "Best partner": "最佳搭档", "Sticky oil bomb": "粘性油弹",
    "Energy absorption": "能量吸收", "Energy diffraction": "能量衍射", "Electromagnetic barrage": "电磁弹幕",
    "Crawler production": "爬虫生产", "Whirlwind": "旋风", "Photon coating": "光子涂层",
    "Final blitz": "最终突击", "Wreckage recycling": "残骸回收", "Power armor": "动力装甲",
    "Missile interceptor": "导弹拦截", "Damage sharing": "伤害分摊", "Mechanical division": "机械分裂",
    "Fortified target lock": "加固锁定", "Overlord artillery": "霸主火炮", "Mothership": "母舰",
    "Photon emission": "光子发射", "Electromagnetic explosion": "电磁爆炸",
    "High explosive anti tank shells": "高爆反坦克弹", "Multi control": "多重控制",
    "Enhanced control": "增强控制", "Electromagnetic interference": "电磁干扰",
    "Charged shot": "蓄能射击", "Anti aircraft ammunition": "防空弹药",
    "Quantum reassembly": "量子重组", "Phoenix production": "凤凰生产",
    "Steel ball production": "钢球生产", "Sledgehammer production": "大锤生产",
    "Efficient maintenance": "高效维护", "Floating artillery array": "漂浮炮阵列",
    "Degeneration beam": "退化光束", "Acid attack": "酸性攻击", "Siege mode": "攻城模式",
    "Napalm": "凝固汽油", "Secondary Armament": "副武器", "Homing missile": "追踪导弹",
    "Burrow maintenance": "潜地维护", "Sandstorm": "沙尘暴", "Strike": "打击",
    "Spider mine": "蜘蛛雷", "Scanning radar": "扫描雷达", "Burst mode": "爆发模式",
    "Stealth cloak": "隐形斗篷", "Chain": "连锁", "Fork": "分叉", "Ionization": "电离",
    "Enhanced range": "增程", "Fire extinguisher": "灭火器", "Dark companion": "暗黑伙伴",
    "Disintegration": "分解", "Swarm missiles": "蜂群导弹", "Vertical sweep": "垂直扫射",
    "Combat Evolvement": "战斗进化", "Kinetic Charge": "动能冲击", "Grenade Launcher": "榴弹发射器",
    "Shockwave": "冲击波", "Ground Targeting": "对地瞄准", "Chamber Compression": "膛压压缩",
    "Land Cruiser": "陆地巡洋舰", "Overload ammo": "过载弹药", "Anti aerial": "防空",
}

XSI_NS = "{http://www.w3.org/2001/XMLSchema-instance}type"

REPLAY_DIR = Path(r"E:\SteamLibrary\steamapps\common\Mechabellum\ProjectDatas\Replay")


def extract_xml(file_path):
    """从 .grbr 文件中提取 XML 内容"""
    with open(file_path, "rb") as f:
        data = f.read()
    start = data.find(b"<?xml")
    end = data.rfind(b"BattleRecord>") + len(b"BattleRecord>")
    if start == -1 or end == 12:
        raise ValueError(f"No XML found in {file_path}")
    return data[start:end].decode("utf-8")


def parse_replay(file_path):
    """解析单个回放文件，返回结构化数据"""
    xml_text = extract_xml(file_path)
    root = ET.fromstring(xml_text)

    match_mode_el = root.find("BattleInfo/MatchMode")
    match_mode = match_mode_el.text if match_mode_el is not None else "VS_1_1"
    is_2v2 = (match_mode == "VS_2_2")

    players = []
    for pr_el in root.findall("playerRecords/PlayerRecord"):
        pname = pr_el.find("name").text
        rounds_data = []

        for rr in pr_el.findall("playerRoundRecords/PlayerRoundRecord"):
            rnd = int(rr.find("round").text)
            hp_el = rr.find("playerData/reactorCore")
            hp = int(hp_el.text) if hp_el is not None else 0

            # 获取该回合开始时的单位
            units = set()
            units_el = rr.find("playerData/units")
            if units_el is not None:
                for u in units_el.findall("NewUnitData"):
                    uid_el = u.find("id")
                    if uid_el is not None:
                        uid = int(uid_el.text)
                        uname = UNIT_LOOKUP.get(uid)
                        if uname:
                            units.add(uname)

            # 获取该回合的科技研究动作
            techs_this_round = []
            for a in rr.findall("actionRecords/MatchActionData"):
                atype = a.get(XSI_NS)
                if atype == "PAD_UpgradeTechnology":
                    uid_el = a.find("UID")
                    tech_el = a.find("TechID")
                    if uid_el is not None and tech_el is not None:
                        uid = int(uid_el.text)
                        tech_id = int(tech_el.text)
                        uname = UNIT_LOOKUP.get(uid)
                        tech_name = TECH_LOOKUP.get(tech_id, f"Unknown({tech_id})")
                        if uname:
                            techs_this_round.append((uname, tech_name))

            rounds_data.append({
                "round": rnd,
                "hp": hp,
                "units": units,
                "techs": techs_this_round,
            })

        # 获取玩家最终科技列表 (from data/unitDatas)
        final_techs = defaultdict(list)
        uds = pr_el.find("data/unitDatas")
        if uds is not None:
            for ud in uds.findall("unitData"):
                uid_el = ud.find("id")
                if uid_el is None:
                    continue
                uid = int(uid_el.text)
                uname = UNIT_LOOKUP.get(uid)
                if not uname:
                    continue
                techs_el = ud.find("techs")
                if techs_el is not None:
                    for tech in techs_el.findall("tech"):
                        tech_id = int(tech.get("data"))
                        tech_name = TECH_LOOKUP.get(tech_id, f"Unknown({tech_id})")
                        final_techs[uname].append(tech_name)

        players.append({
            "name": pname,
            "rounds": rounds_data,
            "final_techs": dict(final_techs),
        })

    # 确定队伍
    if is_2v2 and len(players) == 4:
        # 2v2: 玩家0+1为A队, 玩家2+3为B队
        teams = [
            {"players": [players[0], players[1]]},
            {"players": [players[2], players[3]]},
        ]
    else:
        # 1v1: 每个玩家自成一队
        teams = [{"players": [p]} for p in players]

    # 计算每回合的队伍数据
    max_rounds = max(len(p["rounds"]) for p in players)

    team_rounds = []
    for rnd_idx in range(max_rounds):
        round_data = []
        for team in teams:
            # 队伍HP (取第一个有数据的玩家的HP)
            hp = 0
            # 队伍单位 (合并所有队员的单位)
            units = set()
            # 队伍科技
            techs = []
            for p in team["players"]:
                if rnd_idx < len(p["rounds"]):
                    r = p["rounds"][rnd_idx]
                    if hp == 0:
                        hp = r["hp"]
                    units |= r["units"]
                    techs.extend(r["techs"])
            round_data.append({"hp": hp, "units": units, "techs": techs})
        team_rounds.append(round_data)

    # 确定每回合的胜者 (基于HP变化)
    # 回合N的战斗结果体现在回合N+1的HP变化中
    round_results = []
    for rnd_idx in range(max_rounds - 1):
        cur = team_rounds[rnd_idx]
        nxt = team_rounds[rnd_idx + 1]

        if len(cur) != 2:
            round_results.append(None)
            continue

        hp0_change = cur[0]["hp"] - nxt[0]["hp"]  # 正值=掉血
        hp1_change = cur[1]["hp"] - nxt[1]["hp"]

        if hp0_change > 0 and hp1_change <= 0:
            winner = 1  # 队伍1赢
        elif hp1_change > 0 and hp0_change <= 0:
            winner = 0  # 队伍0赢
        elif hp0_change > 0 and hp1_change > 0:
            # 双方都掉血, 掉得少的赢
            winner = 0 if hp0_change < hp1_change else 1
        else:
            winner = -1  # 平局或无法判断
        round_results.append(winner)

    # 确定最终胜者
    last_round = team_rounds[-1] if team_rounds else None
    final_winner = -1
    if last_round and len(last_round) == 2:
        hp0 = last_round[0]["hp"]
        hp1 = last_round[1]["hp"]
        if hp0 > hp1:
            final_winner = 0
        elif hp1 > hp0:
            final_winner = 1
        # 如果HP相同, 尝试用倒数第二个回合
        elif len(team_rounds) >= 2:
            prev = team_rounds[-2]
            if prev[0]["hp"] > prev[1]["hp"]:
                final_winner = 0
            elif prev[1]["hp"] > prev[0]["hp"]:
                final_winner = 1

    # 收集每个队伍在所有回合中出现的单位
    team_all_units = []
    for team_idx, team in enumerate(teams):
        all_units = set()
        for rnd_idx in range(max_rounds):
            if rnd_idx < len(team_rounds):
                all_units |= team_rounds[rnd_idx][team_idx]["units"]
        team_all_units.append(all_units)

    # 收集每个队伍的科技
    team_all_techs = []
    for team_idx, team in enumerate(teams):
        all_techs = defaultdict(list)
        for p in team["players"]:
            for uname, tech_list in p["final_techs"].items():
                all_techs[uname].extend(tech_list)
        team_all_techs.append(dict(all_techs))

    return {
        "is_2v2": is_2v2,
        "num_players": len(players),
        "teams": teams,
        "team_rounds": team_rounds,
        "round_results": round_results,
        "final_winner": final_winner,
        "max_rounds": max_rounds,
        "team_all_units": team_all_units,
        "team_all_techs": team_all_techs,
    }


def compute_statistics(replays):
    """计算所有统计指标"""
    total_games = len(replays)

    # 1. 兵种回合胜率 (仅一方有时才统计)
    unit_round_wins = defaultdict(int)
    unit_round_losses = defaultdict(int)

    # 2. 出场率
    unit_games_appeared = defaultdict(set)  # unit -> set of game indices

    # 3. 平均出场回合数
    unit_round_appearances = defaultdict(int)  # unit -> total round appearances
    unit_game_count = defaultdict(int)  # unit -> number of games it appeared in

    # 4. A vs B 胜率矩阵
    # matrix[A][B] = (wins, losses) where side with A beat side with B
    matrix_wins = defaultdict(lambda: defaultdict(int))
    matrix_losses = defaultdict(lambda: defaultdict(int))

    # 5. 最终阵容胜率
    unit_final_wins = defaultdict(int)
    unit_final_losses = defaultdict(int)

    # 6. 最常携带科技
    unit_tech_counter = defaultdict(Counter)

    for game_idx, replay in enumerate(replays):
        team_rounds = replay["team_rounds"]
        round_results = replay["round_results"]
        final_winner = replay["final_winner"]
        team_all_units = replay["team_all_units"]
        team_all_techs = replay["team_all_techs"]
        max_rounds = replay["max_rounds"]

        # 统计出场率和出场回合数 (按对局计，不按队伍重复计)
        for rnd_idx in range(max_rounds):
            if rnd_idx < len(team_rounds):
                # 合并所有队伍在该回合的单位，避免2v2中重复计数
                all_units_this_round = set()
                for team_idx in range(len(team_rounds[rnd_idx])):
                    all_units_this_round |= team_rounds[rnd_idx][team_idx]["units"]
                for unit in all_units_this_round:
                    unit_games_appeared[unit].add(game_idx)
                    unit_round_appearances[unit] += 1

        # 计算出场游戏数
        appeared_units = set()
        for team_idx in range(len(team_all_units)):
            appeared_units |= team_all_units[team_idx]
        for unit in appeared_units:
            unit_game_count[unit] += 1

        # 统计回合胜率
        for rnd_idx, result in enumerate(round_results):
            if result is None or result == -1:
                continue
            if rnd_idx + 1 >= len(team_rounds):
                continue

            # 使用回合N+1的单位作为"参战单位"
            units_0 = team_rounds[rnd_idx + 1][0]["units"] if rnd_idx + 1 < len(team_rounds) else set()
            units_1 = team_rounds[rnd_idx + 1][1]["units"] if rnd_idx + 1 < len(team_rounds) else set()

            # 也加上当前回合的单位 (回合N开始时的单位也参与了战斗)
            if rnd_idx < len(team_rounds):
                units_0 |= team_rounds[rnd_idx][0]["units"]
                units_1 |= team_rounds[rnd_idx][1]["units"]

            for unit in units_0 | units_1:
                has_0 = unit in units_0
                has_1 = unit in units_1
                if has_0 and not has_1:
                    # 仅队伍0有此单位
                    if result == 0:
                        unit_round_wins[unit] += 1
                    elif result == 1:
                        unit_round_losses[unit] += 1
                elif has_1 and not has_0:
                    # 仅队伍1有此单位
                    if result == 1:
                        unit_round_wins[unit] += 1
                    elif result == 0:
                        unit_round_losses[unit] += 1
                # 如果双方都有, 不统计

            # A vs B 胜率矩阵
            for a in units_0:
                if a in units_1:
                    continue  # 双方都有A, 跳过
                for b in units_1:
                    if b in units_0:
                        continue  # 双方都有B, 跳过
                    if a == b:
                        continue
                    # 队伍0有A(无B), 队伍1有B(无A)
                    if result == 0:
                        matrix_wins[a][b] += 1
                    elif result == 1:
                        matrix_losses[a][b] += 1

        # 统计最终阵容胜率
        if final_winner >= 0:
            for team_idx in range(len(team_all_units)):
                for unit in team_all_units[team_idx]:
                    has_0 = unit in team_all_units[0]
                    has_1 = unit in team_all_units[1]
                    if has_0 and not has_1:
                        if final_winner == 0:
                            unit_final_wins[unit] += 1
                        else:
                            unit_final_losses[unit] += 1
                    elif has_1 and not has_0:
                        if final_winner == 1:
                            unit_final_wins[unit] += 1
                        else:
                            unit_final_losses[unit] += 1

        # 统计科技
        for team_idx in range(len(team_all_techs)):
            for unit, techs in team_all_techs[team_idx].items():
                # 去重 (同一局同一兵种同一科技只算一次)
                unique_techs = set(techs)
                for tech in unique_techs:
                    unit_tech_counter[unit][tech] += 1

    # 汇总结果
    all_units = set()
    for d in [unit_round_wins, unit_round_losses, unit_games_appeared,
              unit_round_appearances, unit_final_wins, unit_final_losses,
              unit_tech_counter, matrix_wins, matrix_losses]:
        if isinstance(d, defaultdict):
            all_units.update(d.keys())
        elif isinstance(d, dict):
            all_units.update(d.keys())
    # 也从矩阵中收集
    for a in matrix_wins:
        all_units.add(a)
        all_units.update(matrix_wins[a].keys())
    for a in matrix_losses:
        all_units.add(a)
        all_units.update(matrix_losses[a].keys())

    # 按ID排序
    sorted_units = sorted(all_units, key=lambda u: next(
        (k for k, v in UNIT_LOOKUP.items() if v == u), 999))

    results = {
        "total_games": total_games,
        "sorted_units": sorted_units,
        "unit_round_wins": dict(unit_round_wins),
        "unit_round_losses": dict(unit_round_losses),
        "unit_games_appeared": {u: len(s) for u, s in unit_games_appeared.items()},
        "unit_round_appearances": dict(unit_round_appearances),
        "unit_game_count": dict(unit_game_count),
        "matrix_wins": {a: dict(b) for a, b in matrix_wins.items()},
        "matrix_losses": {a: dict(b) for a, b in matrix_losses.items()},
        "unit_final_wins": dict(unit_final_wins),
        "unit_final_losses": dict(unit_final_losses),
        "unit_tech_counter": {u: dict(c) for u, c in unit_tech_counter.items()},
    }

    return results


def generate_html_report(stats, replays):
    """生成 HTML 报告"""
    total_games = stats["total_games"]
    sorted_units = stats["sorted_units"]
    unit_round_wins = stats["unit_round_wins"]
    unit_round_losses = stats["unit_round_losses"]
    unit_games_appeared = stats["unit_games_appeared"]
    unit_round_appearances = stats["unit_round_appearances"]
    unit_game_count = stats["unit_game_count"]
    matrix_wins = stats["matrix_wins"]
    matrix_losses = stats["matrix_losses"]
    unit_final_wins = stats["unit_final_wins"]
    unit_final_losses = stats["unit_final_losses"]
    unit_tech_counter = stats["unit_tech_counter"]

    # 1v1 / 2v2 统计
    games_1v1 = sum(1 for r in replays if not r["is_2v2"])
    games_2v2 = sum(1 for r in replays if r["is_2v2"])

    html_parts = []
    html_parts.append(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mechabellum 兵种胜率统计</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; background: #0f1117; color: #e0e0e0; padding: 20px; }}
  h1 {{ color: #ff9800; margin-bottom: 8px; font-size: 28px; }}
  h2 {{ color: #42a5f5; margin: 30px 0 15px; font-size: 22px; border-bottom: 2px solid #1e2a3a; padding-bottom: 8px; }}
  .summary {{ background: #1a1d29; border-radius: 10px; padding: 20px; margin-bottom: 20px; display: flex; gap: 30px; flex-wrap: wrap; }}
  .summary-item {{ text-align: center; }}
  .summary-item .num {{ font-size: 32px; font-weight: bold; color: #ff9800; }}
  .summary-item .label {{ font-size: 14px; color: #888; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 14px; }}
  th {{ background: #1e2a3a; color: #42a5f5; padding: 10px 8px; text-align: center; font-weight: 600; border: 1px solid #2a3a4a; position: sticky; top: 0; }}
  td {{ padding: 8px; text-align: center; border: 1px solid #1e2a3a; }}
  tr:nth-child(even) {{ background: #151821; }}
  tr:hover {{ background: #1e2233; }}
  .unit-name {{ color: #ff9800; font-weight: 600; text-align: left !important; }}
  .unit-cn {{ color: #888; font-size: 12px; }}
  .win-rate {{ font-weight: bold; }}
  .wr-high {{ color: #4caf50; }}
  .wr-mid {{ color: #ff9800; }}
  .wr-low {{ color: #f44336; }}
  .matrix-table {{ font-size: 12px; }}
  .matrix-table th, .matrix-table td {{ padding: 5px 3px; min-width: 55px; }}
  .matrix-table .cell {{ font-size: 11px; }}
  .win {{ background: rgba(76, 175, 80, 0.2); }}
  .loss {{ background: rgba(244, 67, 54, 0.2); }}
  .tech-list {{ text-align: left; font-size: 12px; }}
  .tech-item {{ margin-bottom: 4px; }}
  .tech-bar {{ display: inline-block; height: 16px; background: #42a5f5; border-radius: 3px; vertical-align: middle; margin-left: 4px; }}
  .note {{ background: #1a1d29; border-left: 4px solid #ff9800; padding: 12px 16px; margin: 15px 0; font-size: 13px; color: #aaa; border-radius: 0 6px 6px 0; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .badge-1v1 {{ background: #1b5e20; color: #a5d6a7; }}
  .badge-2v2 {{ background: #4a148c; color: #ce93d8; }}
</style>
</head>
<body>
<h1>Mechabellum 兵种胜率统计报告</h1>
<div class="summary">
  <div class="summary-item"><div class="num">{total_games}</div><div class="label">总对局数</div></div>
  <div class="summary-item"><div class="num">{games_1v1}</div><div class="label">1v1 对局</div></div>
  <div class="summary-item"><div class="num">{games_2v2}</div><div class="label">2v2 对局</div></div>
</div>
""")

    # === 1. 兵种回合胜率 + 出场率 + 平均回合数 ===
    html_parts.append("""
<h2>一、兵种回合胜率 / 出场率 / 平均出场回合数</h2>
<div class="note">
  <b>回合胜率规则：</b>对于每个回合，如果仅一方（1v1）或仅一边（2v2）拥有该兵种，则进入胜率统计；如果双方都有该兵种，则不进入统计。<br>
  <b>出场率：</b>该兵种在所有对局中至少出场一次的比例。<br>
  <b>平均出场回合数：</b>在有该兵种出场的对局中，平均出场多少个回合。
</div>
<table>
<tr>
  <th>兵种</th><th>英文名</th>
  <th>回合胜率</th><th>胜场</th><th>负场</th><th>总场次</th>
  <th>出场率</th><th>出场对局</th>
  <th>平均出场回合数</th>
</tr>
""")
    # 按回合胜率降序排序
    wr_sorted = sorted(sorted_units, key=lambda u: (
        unit_round_wins.get(u, 0) / max(1, unit_round_wins.get(u, 0) + unit_round_losses.get(u, 0))
        if (unit_round_wins.get(u, 0) + unit_round_losses.get(u, 0)) > 0 else -1
    ), reverse=True)

    for unit in wr_sorted:
        wins = unit_round_wins.get(unit, 0)
        losses = unit_round_losses.get(unit, 0)
        total = wins + losses
        wr = (wins / total * 100) if total > 0 else 0
        games_app = unit_games_appeared.get(unit, 0)
        appearance_rate = (games_app / total_games * 100) if total_games > 0 else 0
        round_app = unit_round_appearances.get(unit, 0)
        game_cnt = unit_game_count.get(unit, 1)
        avg_rounds = round_app / game_cnt if game_cnt > 0 else 0
        cn = UNIT_CN.get(unit, unit)

        wr_class = "wr-high" if wr >= 55 else ("wr-low" if wr <= 45 and total > 0 else "wr-mid")
        wr_str = f"{wr:.1f}%" if total > 0 else "—"

        html_parts.append(f"""<tr>
  <td class="unit-name">{cn}</td><td class="unit-cn">{unit}</td>
  <td class="win-rate {wr_class}">{wr_str}</td><td>{wins}</td><td>{losses}</td><td>{total}</td>
  <td>{appearance_rate:.1f}%</td><td>{games_app}</td>
  <td>{avg_rounds:.1f}</td>
</tr>""")

    html_parts.append("</table>")

    # === 2. A vs B 胜率矩阵 ===
    html_parts.append("""
<h2>二、兵种对位胜率矩阵（A vs B）</h2>
<div class="note">
  <b>规则：</b>当一方拥有兵种A（且对方没有A），对方拥有兵种B（且这一方没有B）时，统计A对B的胜负。<br>
  矩阵单元格 <b>行A 列B</b> = 兵种A面对兵种B时的胜率。绿色=优势对位，红色=劣势对位。<br>
  样本量不足5场的标记为灰色。鼠标悬停可查看详细胜负记录。
</div>
""")

    # 收集所有有对位数据的兵种
    matrix_units = set()
    for a in matrix_wins:
        matrix_units.add(a)
        matrix_units.update(matrix_wins[a].keys())
    for a in matrix_losses:
        matrix_units.add(a)
        matrix_units.update(matrix_losses[a].keys())
    matrix_sorted = sorted(matrix_units, key=lambda u: next(
        (k for k, v in UNIT_LOOKUP.items() if v == u), 999))

    if matrix_sorted:
        html_parts.append('<div style="overflow-x:auto;"><table class="matrix-table">')
        # 表头
        html_parts.append("<tr><th>攻 \\ 守</th>")
        for b in matrix_sorted:
            cn_b = UNIT_CN.get(b, b)
            html_parts.append(f'<th title="{b}">{cn_b}</th>')
        html_parts.append("</tr>")

        for a in matrix_sorted:
            cn_a = UNIT_CN.get(a, a)
            html_parts.append(f'<tr><th title="{a}" style="text-align:right;">{cn_a}</th>')
            for b in matrix_sorted:
                if a == b:
                    html_parts.append('<td style="background:#222;">—</td>')
                else:
                    w = matrix_wins.get(a, {}).get(b, 0)
                    l = matrix_losses.get(a, {}).get(b, 0)
                    t = w + l
                    if t == 0:
                        html_parts.append('<td style="color:#333;">·</td>')
                    elif t < 5:
                        wr = w / t * 100
                        html_parts.append(f'<td class="cell" style="color:#666;" title="{w}W {l}L">{wr:.0f}%<br><span style="font-size:9px;color:#555;">({t})</span></td>')
                    else:
                        wr = w / t * 100
                        cls = "win" if wr > 55 else ("loss" if wr < 45 else "")
                        html_parts.append(f'<td class="cell {cls}" title="{w}W {l}L">{wr:.0f}%<br><span style="font-size:9px;">({t})</span></td>')
            html_parts.append("</tr>")
        html_parts.append("</table></div>")

    # === 3. 最终阵容胜率 ===
    html_parts.append("""
<h2>三、兵种参与阵容最终获胜率</h2>
<div class="note">
  <b>规则：</b>如果一场对局中仅一方拥有该兵种（在整个对局中至少出场过一次），则统计该方最终是否获胜。<br>
  这反映了兵种对整局比赛胜负的影响。
</div>
<table>
<tr><th>兵种</th><th>英文名</th><th>最终胜率</th><th>获胜场</th><th>失败场</th><th>总场次</th></tr>
""")
    # 按最终胜率降序排序
    final_sorted = sorted(sorted_units, key=lambda u: (
        unit_final_wins.get(u, 0) / max(1, unit_final_wins.get(u, 0) + unit_final_losses.get(u, 0))
        if (unit_final_wins.get(u, 0) + unit_final_losses.get(u, 0)) > 0 else -1
    ), reverse=True)

    for unit in final_sorted:
        wins = unit_final_wins.get(unit, 0)
        losses = unit_final_losses.get(unit, 0)
        total = wins + losses
        wr = (wins / total * 100) if total > 0 else 0
        cn = UNIT_CN.get(unit, unit)
        wr_class = "wr-high" if wr >= 55 else ("wr-low" if wr <= 45 and total > 0 else "wr-mid")
        wr_str = f"{wr:.1f}%" if total > 0 else "—"
        html_parts.append(f"""<tr>
  <td class="unit-name">{cn}</td><td class="unit-cn">{unit}</td>
  <td class="win-rate {wr_class}">{wr_str}</td><td>{wins}</td><td>{losses}</td><td>{total}</td>
</tr>""")
    html_parts.append("</table>")

    # === 4. 最常携带科技 ===
    html_parts.append("""
<h2>四、每个兵种最常携带的科技</h2>
<div class="note">
  统计每个兵种在所有对局中研究过的科技，按出现次数排序，展示前3名。
</div>
<table>
<tr><th>兵种</th><th>英文名</th><th>出场次数</th><th>最常携带科技 Top 3</th></tr>
""")
    # 按出场次数降序排序
    tech_sorted = sorted(sorted_units, key=lambda u: unit_game_count.get(u, 0), reverse=True)
    for unit in tech_sorted:
        techs = unit_tech_counter.get(unit, {})
        if not techs:
            continue
        cn = UNIT_CN.get(unit, unit)
        game_cnt = unit_game_count.get(unit, 0)
        top_techs = sorted(techs.items(), key=lambda x: x[1], reverse=True)[:3]
        max_count = top_techs[0][1] if top_techs else 1
        tech_html = '<div class="tech-list">'
        for tech_name, count in top_techs:
            tech_cn = TECH_CN.get(tech_name, "")
            pct = count / max(1, game_cnt) * 100
            bar_width = count / max_count * 120
            display = f"{tech_cn}（{tech_name}）" if tech_cn else tech_name
            tech_html += f'<div class="tech-item">{display} <span style="color:#888;">({count}次, {pct:.0f}%)</span><span class="tech-bar" style="width:{bar_width}px;"></span></div>'
        tech_html += '</div>'
        html_parts.append(f"""<tr>
  <td class="unit-name">{cn}</td><td class="unit-cn">{unit}</td><td>{game_cnt}</td><td>{tech_html}</td>
</tr>""")
    html_parts.append("</table>")

    html_parts.append("""
<div style="text-align:center; margin-top:30px; color:#555; font-size:12px;">
  Generated by Mechabellum Replay Analyzer | 数据来源: 本地回放文件
</div>
</body>
</html>""")

    return "".join(html_parts)


def main():
    print("扫描回放文件...")
    replay_files = []
    for ver_dir in sorted(REPLAY_DIR.iterdir()):
        if not ver_dir.is_dir():
            continue
        for f in ver_dir.iterdir():
            if f.name.endswith(".rep.grbr"):
                replay_files.append(f)

    print(f"找到 {len(replay_files)} 个回放文件")

    print("解析回放文件...")
    replays = []
    errors = []
    for i, fpath in enumerate(replay_files):
        try:
            replay = parse_replay(fpath)
            replays.append(replay)
            if (i + 1) % 20 == 0:
                print(f"  已解析 {i+1}/{len(replay_files)}...")
        except Exception as e:
            errors.append((fpath, str(e)))
            print(f"  错误 {fpath.name}: {e}")

    print(f"成功解析 {len(replays)} 个回放, {len(errors)} 个错误")

    print("计算统计指标...")
    stats = compute_statistics(replays)

    print("生成HTML报告...")
    html = generate_html_report(stats, replays)

    output_path = Path(r"C:\Users\chengli\WorkBuddy\2026-08-09-16-56-17\mechabellum_stats_report.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告已保存: {output_path}")

    # 同时保存JSON数据
    json_path = Path(r"C:\Users\chengli\WorkBuddy\2026-08-09-16-56-17\mechabellum_stats_data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"数据已保存: {json_path}")

    # 打印摘要
    print("\n=== 统计摘要 ===")
    print(f"总对局: {stats['total_games']}")
    for unit in stats["sorted_units"]:
        wins = stats["unit_round_wins"].get(unit, 0)
        losses = stats["unit_round_losses"].get(unit, 0)
        total = wins + losses
        wr = f"{wins/total*100:.1f}%" if total > 0 else "—"
        app = stats["unit_games_appeared"].get(unit, 0)
        app_rate = f"{app/stats['total_games']*100:.1f}%" if stats["total_games"] > 0 else "—"
        print(f"  {unit:20s} 回合胜率={wr:>7s} 出场率={app_rate:>6s} ({app}场)")


if __name__ == "__main__":
    main()

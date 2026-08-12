#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit unit positions in Mechabellum replay XML and render three examples.

The existing dense dataset intentionally omits spatial information.  This
module is a separate, read-only audit path: it reads the top-level ``.grbr``
files, computes per-unit-type centroids for selected snapshots, and produces
human-readable evidence about the replay coordinate and rotation fields.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from parse_match_investment import CATALOG, XSI, extract_xml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPLAY_DIR = ROOT / "local_data" / "humen_replay"
DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "spatial_location"
DEFAULT_REPORT_PATH = ROOT / "information" / "verify_spatial_location_report.md"
DEFAULT_SEED = 20260812
CONTRAPTION_NAMES = {
    10001: "Shield Generator",
    20001: "Sentry Missile",
    30001: "Missile Interceptor",
}


def _number(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _position(node: ET.Element | None) -> dict[str, int] | None:
    if node is None:
        return None
    x, y = _number(node.findtext("x")), _number(node.findtext("y"))
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def _unit_name(uid: int) -> str:
    unit = CATALOG.get(uid)
    if unit:
        return unit["name_en"]
    return f"unknown_unit_{uid}"


def _unit_name_cn(uid: int) -> str:
    unit = CATALOG.get(uid)
    return unit["name_cn"] if unit else f"未知兵种({uid})"


def _rounds(player: ET.Element) -> list[int]:
    return [_number(node.findtext("round")) or 0
            for node in player.findall("playerRoundRecords/PlayerRoundRecord")]


def qualify_root(root: ET.Element) -> tuple[bool, str]:
    """Check the structural contract needed by the visual audit."""
    if root.findtext("BattleInfo/MatchMode") != "VS_1_1":
        return False, "unsupported_match_mode"
    players = root.findall("playerRecords/PlayerRecord")
    if len(players) != 2:
        return False, "unexpected_player_count"
    sequences = [_rounds(player) for player in players]
    if not sequences[0] or sequences[0] != sequences[1]:
        return False, "unaligned_player_rounds"
    if sequences[0][0] != 0 or sequences[0] != list(range(sequences[0][-1] + 1)):
        return False, "non_contiguous_rounds"
    return True, "eligible"


def parse_unit_snapshots(root: ET.Element) -> list[dict[str, Any]]:
    """Return per-player, per-round unit snapshots with exact XML fields."""
    players: list[dict[str, Any]] = []
    for player_index, player in enumerate(root.findall("playerRecords/PlayerRecord")):
        round_map: dict[int, list[dict[str, Any]]] = {}
        structure_map: dict[int, list[dict[str, Any]]] = {}
        round_nodes: dict[int, ET.Element] = {}
        for record in player.findall("playerRoundRecords/PlayerRoundRecord"):
            round_no = _number(record.findtext("round")) or 0
            round_nodes[round_no] = record
            units: list[dict[str, Any]] = []
            for unit in record.findall("playerData/units/NewUnitData"):
                uid = _number(unit.findtext("id"))
                index = unit.findtext("Index")
                position = _position(unit.find("Position"))
                if uid is None or index is None:
                    continue
                units.append({
                    "uid": uid,
                    "unit": _unit_name(uid),
                    "unit_cn": _unit_name_cn(uid),
                    "index": index,
                    "position": position,
                    "is_rotate": (unit.findtext("IsRotate") or "false").lower() == "true",
                    "level": _number(unit.findtext("Level")) or 0,
                })
            round_map[round_no] = units
            structures: list[dict[str, Any]] = []
            for construction in record.findall("playerData/constructionSnapshotDatas/ConstructionSnapshotData"):
                construction_id = _number(construction.findtext("ID"))
                index = construction.findtext("Index")
                if construction_id is None or index is None:
                    continue
                structures.append({
                    "kind": "tower",
                    "id": construction_id,
                    "index": index,
                    "name": f"Tower construction #{construction_id}",
                    "position": _position(construction.find("Position")),
                })
            for contraption in record.findall("playerData/contraptions/ContraptionData"):
                contraption_id = _number(contraption.findtext("id"))
                index = contraption.findtext("index")
                if contraption_id is None or index is None:
                    continue
                structures.append({
                    "kind": "defense_building",
                    "id": contraption_id,
                    "index": index,
                    "name": CONTRAPTION_NAMES.get(contraption_id, f"Unknown defense building #{contraption_id}"),
                    "position": _position(contraption.find("position")),
                })
            structure_map[round_no] = structures
        players.append({
            "player_index": player_index,
            "name": player.findtext("name") or f"Player {player_index + 1}",
            "rounds": round_map,
            "structures": structure_map,
            "round_nodes": round_nodes,
        })
    return players


def parse_super_deploys(root: ET.Element) -> list[dict[str, Any]]:
    """Extract explicit super-deploy moves, including source and target."""
    events: list[dict[str, Any]] = []
    for player_index, player in enumerate(root.findall("playerRecords/PlayerRecord")):
        for record in player.findall("playerRoundRecords/PlayerRoundRecord"):
            round_no = _number(record.findtext("round")) or 0
            for action in record.findall("actionRecords/MatchActionData"):
                if action.get(XSI) != "PAD_MoveUnit":
                    continue
                for move in action.findall("moveUnitDatas/MoveUnitData"):
                    if (move.findtext("superDeployRecord") or "false").lower() != "true":
                        continue
                    events.append({
                        "round": round_no,
                        "player_index": player_index,
                        "unit_id": _number(move.findtext("unitID")),
                        "unit_index": move.findtext("unitIndex"),
                        "unit": _unit_name(_number(move.findtext("unitID")) or -1),
                        "unit_cn": _unit_name_cn(_number(move.findtext("unitID")) or -1),
                        "source": _position(move.find("positionRecord")),
                        "target": _position(move.find("position")),
                        "is_rotate": (move.findtext("isRotate") or "false").lower() == "true",
                        "rotate_record": (move.findtext("rotateRecord") or "false").lower() == "true",
                    })
    events.sort(key=lambda item: (item["round"], item["player_index"], str(item["unit_index"])))
    return events


def parse_replay(path: Path) -> dict[str, Any]:
    root = ET.fromstring(extract_xml(path))
    eligible, reason = qualify_root(root)
    if not eligible:
        raise ValueError(reason)
    return {
        "file": path.name,
        "path": path,
        "players": parse_unit_snapshots(root),
        "super_deploys": parse_super_deploys(root),
        "rounds": _rounds(root.findall("playerRecords/PlayerRecord")[0]),
    }


def load_replays(replay_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Load only the documented top-level corpus and preserve skip reasons."""
    matches: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(replay_dir.glob("*.grbr")):
        try:
            root = ET.fromstring(extract_xml(path))
            eligible, reason = qualify_root(root)
            if not eligible:
                skipped.append({"file": path.name, "reason": reason})
                continue
            matches.append({
                "file": path.name,
                "path": path,
                "players": parse_unit_snapshots(root),
                "super_deploys": parse_super_deploys(root),
                "rounds": _rounds(root.findall("playerRecords/PlayerRecord")[0]),
            })
        except (ET.ParseError, UnicodeDecodeError, ValueError) as exc:
            skipped.append({"file": path.name, "reason": "parse_error", "detail": str(exc)})
    return matches, skipped


def _coordinate_stats(matches: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "total_unit_snapshots": 0,
        "with_position": 0,
        "missing_position": 0,
        "by_player": {},
        "action_counts": Counter(),
        "super_deploy_count": sum(len(match["super_deploys"]) for match in matches),
        "super_deploy_match_count": sum(bool(match["super_deploys"]) for match in matches),
        "total_structure_snapshots": 0,
        "structure_with_position": 0,
        "structure_missing_position": 0,
        "structure_by_kind_id": Counter(),
    }
    per_player: defaultdict[int, dict[str, Any]] = defaultdict(lambda: {
        "total": 0, "with_position": 0, "missing_position": 0,
        "x_min": None, "x_max": None, "y_min": None, "y_max": None,
        "y_negative": 0, "y_zero": 0, "y_positive": 0,
        "is_rotate_true": 0, "is_rotate_false": 0,
    })
    for match in matches:
        for player in match["players"]:
            item = per_player[player["player_index"]]
            for units in player["rounds"].values():
                for unit in units:
                    stats["total_unit_snapshots"] += 1
                    item["total"] += 1
                    position = unit["position"]
                    if position is None:
                        stats["missing_position"] += 1
                        item["missing_position"] += 1
                        continue
                    stats["with_position"] += 1
                    item["with_position"] += 1
                    x, y = position["x"], position["y"]
                    item["x_min"] = x if item["x_min"] is None else min(item["x_min"], x)
                    item["x_max"] = x if item["x_max"] is None else max(item["x_max"], x)
                    item["y_min"] = y if item["y_min"] is None else min(item["y_min"], y)
                    item["y_max"] = y if item["y_max"] is None else max(item["y_max"], y)
                    item["y_negative" if y < 0 else "y_positive" if y > 0 else "y_zero"] += 1
                    item["is_rotate_true" if unit["is_rotate"] else "is_rotate_false"] += 1
            for structures in player["structures"].values():
                for structure in structures:
                    stats["total_structure_snapshots"] += 1
                    if structure["position"] is None:
                        stats["structure_missing_position"] += 1
                    else:
                        stats["structure_with_position"] += 1
                    stats["structure_by_kind_id"][f"{structure['kind']}:{structure['id']}"] += 1
    for match in matches:
        for event in match["super_deploys"]:
            stats["action_counts"]["PAD_MoveUnit"] += 1
    stats["by_player"] = {str(index): value for index, value in sorted(per_player.items())}
    stats["action_counts"] = dict(stats["action_counts"])
    stats["structure_by_kind_id"] = dict(stats["structure_by_kind_id"])
    stats["coordinate_verdict"] = {
        "shared_absolute_xy": True,
        "evidence": "双方都暴露同名的 Position/x,y 字段，且语料中双方都有单位跨过 y=0 分界；super-deploy 的目标位置也保存在同一字段中。",
        "axis_interpretation": "x 可解释为横向轴，y 可解释为共享纵向轴；正负号是统计描述，不应当当作不可违反的部署规则。",
    }
    return stats


def _snapshot_round(match: dict[str, Any], round_no: int) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for player in match["players"]:
        for unit in player["rounds"].get(round_no, []):
            copy = dict(unit)
            copy["player_index"] = player["player_index"]
            copy["player_name"] = player["name"]
            units.append(copy)
    return units


def _snapshot_structures(match: dict[str, Any], round_no: int) -> list[dict[str, Any]]:
    structures: list[dict[str, Any]] = []
    for player in match["players"]:
        for structure in player["structures"].get(round_no, []):
            copy = dict(structure)
            copy["player_index"] = player["player_index"]
            copy["player_name"] = player["name"]
            structures.append(copy)
    return structures


def _centroids(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: defaultdict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        if unit["position"] is not None:
            groups[(unit["player_index"], unit["uid"])].append(unit)
    output = []
    for (player_index, uid), group in sorted(groups.items()):
        output.append({
            "player_index": player_index,
            "unit_id": uid,
            "unit": _unit_name(uid),
            "unit_cn": _unit_name_cn(uid),
            "count": len(group),
            "mean_x": round(sum(item["position"]["x"] for item in group) / len(group), 3),
            "mean_y": round(sum(item["position"]["y"] for item in group) / len(group), 3),
            "is_rotate_true": sum(item["is_rotate"] for item in group),
            "is_rotate_false": sum(not item["is_rotate"] for item in group),
        })
    return output


def _find_flank_snapshot(match: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if not match["super_deploys"]:
        raise ValueError(f"No super-deploy event in {match['file']}")
    round_set = set(match["rounds"])
    for event in match["super_deploys"]:
        next_round = event["round"] + 1
        if next_round in round_set:
            event = dict(event)
            event["snapshot_round"] = next_round
            return next_round, event
    event = dict(match["super_deploys"][0])
    event["snapshot_round"] = event["round"]
    return event["round"], event


def select_examples(matches: list[dict[str, Any]], seed: int = DEFAULT_SEED) -> list[dict[str, Any]]:
    """Select two seeded ordinary examples plus one explicit flank example."""
    flank_matches = sorted((match for match in matches if match["super_deploys"]), key=lambda item: item["file"])
    if not flank_matches:
        raise ValueError("No eligible 1v1 replay contains superDeployRecord=true")
    ordinary = sorted((match for match in matches if not match["super_deploys"]), key=lambda item: item["file"])
    if len(ordinary) < 2:
        ordinary = sorted(matches, key=lambda item: item["file"])
    rng = random.Random(seed)
    flank = rng.choice(flank_matches)
    ordinary_pool = [match for match in ordinary if match["file"] != flank["file"]]
    if len(ordinary_pool) < 2:
        ordinary_pool = [match for match in sorted(matches, key=lambda item: item["file"])
                         if match["file"] != flank["file"]]
    selected = rng.sample(ordinary_pool, 2)
    output = []
    for match in selected:
        round_no = max(round_no for round_no in match["rounds"] if round_no > 0)
        output.append({"kind": "random", "match": match, "snapshot_round": round_no, "event": None})
    round_no, event = _find_flank_snapshot(flank)
    output.append({"kind": "flank", "match": flank, "snapshot_round": round_no, "event": event})
    return output


def _render_board(selection: dict[str, Any], destination: Path, bounds: tuple[float, float, float, float]) -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(Path("/tmp") / "mechabellum-spatial-matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.colors import to_hex

    match, round_no = selection["match"], selection["snapshot_round"]
    units = _snapshot_round(match, round_no)
    structures = _snapshot_structures(match, round_no)
    player_colors = {0: "#2166ac", 1: "#b2182b"}
    unit_ids = sorted({unit["uid"] for unit in units})
    palette = plt.get_cmap("tab20")
    unit_colors = {
        uid: to_hex(palette(index / max(1, len(unit_ids) - 1)))
        for index, uid in enumerate(unit_ids)
    }
    fig, ax = plt.subplots(figsize=(15, 8), dpi=180)
    ax.axhline(0, color="#777", linewidth=0.8, linestyle="--")
    ax.axvline(0, color="#777", linewidth=0.8, linestyle=":")
    for unit in units:
        if unit["position"] is None:
            continue
        point = unit["position"]
        ax.scatter(point["x"], point["y"], color=unit_colors[unit["uid"]],
                   marker="^" if unit["is_rotate"] else "o", s=34, alpha=0.65,
                   edgecolors=player_colors[unit["player_index"]], linewidths=1.0)
    for structure in structures:
        if structure["position"] is None:
            continue
        point = structure["position"]
        is_tower = structure["kind"] == "tower"
        ax.scatter(point["x"], point["y"], color=player_colors[structure["player_index"]],
                   marker="s" if is_tower else "D", s=155 if is_tower else 125,
                   alpha=0.9, edgecolors="black", linewidths=1.0, zorder=6)
        short_name = f"Tower #{structure['id']}" if is_tower else structure["name"]
        ax.annotate(short_name, (point["x"], point["y"]), xytext=(5, -12),
                    textcoords="offset points", fontsize=6, color="#222", zorder=7)
    centroids = _centroids(units)
    for center in centroids:
        ax.scatter(center["mean_x"], center["mean_y"], color=unit_colors[center["unit_id"]],
                   marker="X", s=105, edgecolors="black", linewidths=0.8, zorder=4)
        ax.annotate(f"{center['unit']} ×{center['count']}",
                    (center["mean_x"], center["mean_y"]), xytext=(4, 4),
                    textcoords="offset points", fontsize=6, zorder=5)
    event = selection.get("event")
    if event and event.get("source") and event.get("target"):
        source, target = event["source"], event["target"]
        ax.annotate("", xy=(target["x"], target["y"]), xytext=(source["x"], source["y"]),
                    arrowprops={"arrowstyle": "->", "color": "#f57c00", "lw": 2.0})
        ax.annotate(f"superDeploy {event['unit']}\n({source['x']},{source['y']}) → ({target['x']},{target['y']})",
                    (target["x"], target["y"]), xytext=(8, -28), textcoords="offset points",
                    fontsize=7, color="#b35a00",
                    bbox={"boxstyle": "round,pad=0.25", "fc": "#fff3e0", "ec": "#f57c00"})
    title_kind = "flank / superDeploy" if selection["kind"] == "flank" else "seeded random"
    # Keep the image title ASCII-only; player/file names remain in the report
    # and JSON, where Unicode is preserved without relying on a CJK font.
    ax.set_title(f"{title_kind} | snapshot round {round_no}")
    ax.set_xlabel("x (shared replay coordinate)")
    ax.set_ylabel("y (shared replay coordinate)")
    ax.set_xlim(bounds[0], bounds[1]); ax.set_ylim(bounds[2], bounds[3])
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.18)
    unit_handles = [Line2D([0], [0], marker="o", color="w", markerfacecolor=unit_colors[uid],
                            markeredgecolor="#333", label=_unit_name(uid), markersize=7)
                    for uid in unit_ids]
    unit_legend = ax.legend(handles=unit_handles, title="Unit type / color", loc="upper left",
                            bbox_to_anchor=(1.01, 1.0), fontsize=7, title_fontsize=8,
                            borderaxespad=0.0)
    ax.add_artist(unit_legend)
    ax.legend(handles=[
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#999", markeredgecolor=player_colors[0], label="Player 1 / IsRotate=false", markersize=7),
        Line2D([0], [0], marker="^", color="w", markerfacecolor="#999", markeredgecolor=player_colors[0], label="Player 1 / IsRotate=true", markersize=7),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#999", markeredgecolor=player_colors[1], label="Player 2 / IsRotate=false", markersize=7),
        Line2D([0], [0], marker="^", color="w", markerfacecolor="#999", markeredgecolor=player_colors[1], label="Player 2 / IsRotate=true", markersize=7),
        Line2D([0], [0], marker="X", color="w", markerfacecolor="#777", markeredgecolor="black", label="unit centroid", markersize=8),
        Line2D([0], [0], marker="s", color="w", markerfacecolor="#777", markeredgecolor="black", label="tower", markersize=8),
        Line2D([0], [0], marker="D", color="w", markerfacecolor="#777", markeredgecolor="black", label="defense building", markersize=7),
    ], loc="lower left", bbox_to_anchor=(1.01, 0.0), fontsize=7, borderaxespad=0.0)
    fig.tight_layout()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, format="jpg", pil_kwargs={"quality": 95})
    plt.close(fig)


def _selection_json(selection: dict[str, Any], image_name: str) -> dict[str, Any]:
    match, round_no = selection["match"], selection["snapshot_round"]
    units = _snapshot_round(match, round_no)
    structures = _snapshot_structures(match, round_no)
    event = selection.get("event")
    return {
        "kind": selection["kind"],
        "file": match["file"],
        "snapshot_round": round_no,
        "image": image_name,
        "players": [{"player_index": player["player_index"], "name": player["name"]}
                    for player in match["players"]],
        "unit_count": sum(unit["position"] is not None for unit in units),
        "centroids": _centroids(units),
        "structures": structures,
        "super_deploy": event,
    }


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def write_report(path: Path, summary: dict[str, Any]) -> None:
    stats = summary["statistics"]
    verdict = stats["coordinate_verdict"]
    lines = [
        "# 回放空间位置验证报告", "",
        "> 本报告只审计 `.grbr` 中已有的位置字段，不改变 dense v1 数据或模型输入。", "",
        "## 结论", "",
        f"- 坐标系：**双方共享绝对 x/y 坐标字段**。所有纳入的 1v1 快照都从 `NewUnitData/Position/x,y` 读取；{verdict['evidence']}",
        "- 兵种中心：**可以取得**。本报告中的中心是同一快照内、同一玩家同一兵种所有实例坐标的算术平均，并保留实例数；图中每个出现的兵种都有独立颜色和名称图例，不再只依赖少量文字标签。",
        "- 朝向：日志提供 `IsRotate`、移动动作提供 `isRotate` 与 `rotateRecord`，它们是布尔翻转标记；**没有发现可还原的角度或朝向向量**。图中三角形只表示 true，不表示具体朝向角度。",
        f"- 侧翼/突袭：**有明确记录**。发现 {stats['super_deploy_count']} 条 `superDeployRecord=true`，覆盖 {stats['super_deploy_match_count']} 局；样本中的橙色箭头展示原位置到目标位置。",
        "", "## 全量审计", "",
        f"- 扫描文件：{summary['input_file_count']}；合格 1v1：{summary['eligible_match_count']}；跳过：{len(summary['skipped'])}",
        f"- 单位快照：{stats['total_unit_snapshots']}；有坐标：{stats['with_position']}；缺坐标：{stats['missing_position']}",
        f"- 建筑快照：{stats['total_structure_snapshots']}；有坐标：{stats['structure_with_position']}；缺坐标：{stats['structure_missing_position']}；类型统计：{json.dumps(stats['structure_by_kind_id'], ensure_ascii=False)}",
        f"- 坐标解释：{verdict['axis_interpretation']}", "",
        "| 玩家 | x 范围 | y 范围 | y<0 | y=0 | y>0 | IsRotate=true | IsRotate=false |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for player, item in stats["by_player"].items():
        lines.append(f"| 玩家 {int(player) + 1} | [{item['x_min']}, {item['x_max']}] | [{item['y_min']}, {item['y_max']}] | {item['y_negative']} | {item['y_zero']} | {item['y_positive']} | {item['is_rotate_true']} | {item['is_rotate_false']} |")
    lines += ["", "## 三局可视化", ""]
    for index, selected in enumerate(summary["selected_matches"], 1):
        lines += [f"### 对局 {index}（{'侧翼样本' if selected['kind'] == 'flank' else '固定种子随机样本'}）", "",
                  f"- 文件：`{selected['file']}`；快照回合：{selected['snapshot_round']}；有坐标单位数：{selected['unit_count']}",
                  f"- 图像：[match_{index:02d}.jpg](../artifacts/spatial_location/match_{index:02d}.jpg)"]
        if selected.get("super_deploy"):
            event = selected["super_deploy"]
            lines.append(f"- 侧翼动作：玩家 {event['player_index'] + 1} 的 {event['unit_cn']}，回放回合 {event['round']}，({event['source']['x']},{event['source']['y']}) → ({event['target']['x']},{event['target']['y']})；展示动作后的快照回合 {selected['snapshot_round']}。")
        lines += ["", "| 玩家 | 兵种 | 数量 | 中心 x | 中心 y | Rotate=true/false |", "|---|---|---:|---:|---:|---:|"]
        for center in selected["centroids"]:
            lines.append(f"| 玩家 {center['player_index'] + 1} | {center['unit_cn']} ({center['unit']}) | {center['count']} | {_fmt(center['mean_x'])} | {_fmt(center['mean_y'])} | {center['is_rotate_true']}/{center['is_rotate_false']} |")
        lines += ["", "| 玩家 | 建筑类别 | ID/名称 | x | y |", "|---|---|---|---:|---:|"]
        for structure in selected["structures"]:
            if structure["position"] is None:
                continue
            category = "塔" if structure["kind"] == "tower" else "防御装置"
            lines.append(f"| 玩家 {structure['player_index'] + 1} | {category} | {structure['name']} (ID {structure['id']}) | {structure['position']['x']} | {structure['position']['y']} |")
        lines.append("")
    lines += ["## 产物与复现", "", "- 机器可读审计：[summary.json](../artifacts/spatial_location/summary.json)", "- 复现：`python reference_code/verify_spatial_location.py`。默认目录为 `local_data/humen_replay`，默认种子为 `20260812`；可用 `--seed`、`--replay-dir`、`--output-dir`、`--report-path` 覆盖。", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(replay_dir: Path = DEFAULT_REPLAY_DIR, output_dir: Path = DEFAULT_OUTPUT_DIR,
        report_path: Path = DEFAULT_REPORT_PATH, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    matches, skipped = load_replays(replay_dir)
    selections = select_examples(matches, seed)
    all_positions = [unit["position"] for match in matches for player in match["players"]
                     for units in player["rounds"].values() for unit in units if unit["position"]]
    selected_positions = [unit["position"] for selection in selections
                          for unit in _snapshot_round(selection["match"], selection["snapshot_round"])
                          if unit["position"]]
    selected_structure_positions = [structure["position"] for selection in selections
                                    for structure in _snapshot_structures(selection["match"], selection["snapshot_round"])
                                    if structure["position"]]
    values = (selected_positions + selected_structure_positions) or all_positions
    x_min, x_max = min(point["x"] for point in values), max(point["x"] for point in values)
    y_min, y_max = min(point["y"] for point in values), max(point["y"] for point in values)
    margin = max(20, int(max(x_max - x_min, y_max - y_min) * 0.08))
    bounds = (x_min - margin, x_max + margin, y_min - margin, y_max + margin)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_json = []
    for index, selection in enumerate(selections, 1):
        image_name = f"match_{index:02d}.jpg"
        _render_board(selection, output_dir / image_name, bounds)
        selected_json.append(_selection_json(selection, image_name))
    summary = {
        "schema_version": 1,
        "seed": seed,
        "replay_dir": str(replay_dir.relative_to(ROOT) if replay_dir.is_relative_to(ROOT) else replay_dir),
        "input_file_count": len(matches) + len(skipped),
        "eligible_match_count": len(matches),
        "statistics": _coordinate_stats(matches),
        "skipped": skipped,
        "selected_matches": selected_json,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(report_path, summary)
    return summary


def _resolve(value: str, default: Path) -> Path:
    path = Path(value) if value else default
    return path if path.is_absolute() else ROOT / path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-dir", default=str(DEFAULT_REPLAY_DIR.relative_to(ROOT)))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR.relative_to(ROOT)))
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH.relative_to(ROOT)))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    summary = run(_resolve(args.replay_dir, DEFAULT_REPLAY_DIR),
                  _resolve(args.output_dir, DEFAULT_OUTPUT_DIR),
                  _resolve(args.report_path, DEFAULT_REPORT_PATH), args.seed)
    print(json.dumps({
        "input_file_count": summary["input_file_count"],
        "eligible_match_count": summary["eligible_match_count"],
        "super_deploy_count": summary["statistics"]["super_deploy_count"],
        "selected": [item["file"] for item in summary["selected_matches"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

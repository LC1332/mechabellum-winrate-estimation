#!/usr/bin/env python3
"""Contract tests for the independent spatial-location audit path."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_spatial_location import (  # noqa: E402
    _centroids,
    parse_super_deploys,
    parse_unit_snapshots,
    qualify_root,
    run,
    select_examples,
)


def _unit(index: int, uid: int, x: int, y: int, rotate: bool = False) -> str:
    return f"""<NewUnitData><id>{uid}</id><Index>{index}</Index><Position><x>{x}</x><y>{y}</y></Position><IsRotate>{str(rotate).lower()}</IsRotate><Level>0</Level></NewUnitData>"""


def _replay_xml(name_a: str = "Alpha", name_b: str = "Beta", super_deploy: bool = False) -> str:
    action = ""
    if super_deploy:
        action = """<actionRecords><MatchActionData xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="PAD_MoveUnit"><moveUnitDatas><MoveUnitData><unitID>10</unitID><unitIndex>0</unitIndex><position><x>300</x><y>200</y></position><isRotate>true</isRotate><positionRecord><x>-10</x><y>-20</y></positionRecord><rotateRecord>true</rotateRecord><superDeployRecord>true</superDeployRecord></MoveUnitData></moveUnitDatas></MatchActionData></actionRecords>"""
    else:
        action = "<actionRecords />"
    structures_a = """<constructionSnapshotDatas><ConstructionSnapshotData><Index>0</Index><ID>1</ID><Position><x>100</x><y>-50</y></Position></ConstructionSnapshotData><ConstructionSnapshotData><Index>1</Index><ID>2</ID><Position><x>-100</x><y>-50</y></Position></ConstructionSnapshotData></constructionSnapshotDatas><contraptions><ContraptionData><index>0</index><id>10001</id><position><x>0</x><y>-100</y></position></ContraptionData></contraptions>"""
    structures_b = """<constructionSnapshotDatas><ConstructionSnapshotData><Index>0</Index><ID>1</ID><Position><x>100</x><y>50</y></Position></ConstructionSnapshotData><ConstructionSnapshotData><Index>1</Index><ID>2</ID><Position><x>-100</x><y>50</y></Position></ConstructionSnapshotData></constructionSnapshotDatas><contraptions><ContraptionData><index>0</index><id>20001</id><position><x>0</x><y>100</y></position></ContraptionData></contraptions>"""
    player_a = f"""<PlayerRecord><name>{name_a}</name><playerRoundRecords>
      <PlayerRoundRecord><round>0</round><playerData><units>{_unit(0, 10, -10, -20)}</units>{structures_a}</playerData>{action}</PlayerRoundRecord>
      <PlayerRoundRecord><round>1</round><playerData><units>{_unit(0, 10, 300 if super_deploy else -10, 200 if super_deploy else -20, super_deploy)}</units>{structures_a}</playerData><actionRecords /></PlayerRoundRecord>
    </playerRoundRecords></PlayerRecord>"""
    player_b = f"""<PlayerRecord><name>{name_b}</name><playerRoundRecords>
      <PlayerRoundRecord><round>0</round><playerData><units>{_unit(0, 10, 10, 20)}</units>{structures_b}</playerData><actionRecords /></PlayerRoundRecord>
      <PlayerRoundRecord><round>1</round><playerData><units>{_unit(0, 10, 10, 20)}</units>{structures_b}</playerData><actionRecords /></PlayerRoundRecord>
    </playerRoundRecords></PlayerRecord>"""
    return f"""<?xml version="1.0" encoding="utf-8"?><BattleRecord><BattleInfo><MatchMode>VS_1_1</MatchMode></BattleInfo><playerRecords>{player_a}{player_b}</playerRecords></BattleRecord>"""


class SpatialLocationTests(unittest.TestCase):
    def test_position_rotation_centroid_and_super_deploy_fields(self):
        root = ET.fromstring(_replay_xml(super_deploy=True))
        eligible, reason = qualify_root(root)
        self.assertTrue(eligible, reason)
        players = parse_unit_snapshots(root)
        self.assertEqual({"x": 300, "y": 200}, players[0]["rounds"][1][0]["position"])
        self.assertTrue(players[0]["rounds"][1][0]["is_rotate"])
        self.assertEqual([], [unit for unit in players[0]["rounds"][1] if unit["position"] is None])
        self.assertEqual({1, 2}, {item["id"] for item in players[0]["structures"][1] if item["kind"] == "tower"})
        self.assertEqual("Shield Generator", players[0]["structures"][1][2]["name"])
        events = parse_super_deploys(root)
        self.assertEqual(1, len(events))
        self.assertEqual({"x": -10, "y": -20}, events[0]["source"])
        self.assertEqual({"x": 300, "y": 200}, events[0]["target"])
        centers = _centroids([
            {"player_index": 0, "uid": 10, "position": {"x": 0, "y": 0}, "is_rotate": False},
            {"player_index": 0, "uid": 10, "position": {"x": 10, "y": 20}, "is_rotate": True},
        ])
        self.assertEqual((5.0, 10.0), (centers[0]["mean_x"], centers[0]["mean_y"]))
        self.assertEqual((1, 1), (centers[0]["is_rotate_true"], centers[0]["is_rotate_false"]))

    def test_seeded_selection_has_two_random_and_one_flank(self):
        def match(file_name, is_flank):
            root = ET.fromstring(_replay_xml(super_deploy=is_flank))
            return {
                "file": file_name,
                "players": parse_unit_snapshots(root),
                "super_deploys": parse_super_deploys(root),
                "rounds": [0, 1],
            }

        matches = [match("a.grbr", False), match("b.grbr", False), match("c.grbr", True)]
        first = select_examples(matches, 20260812)
        second = select_examples(matches, 20260812)
        self.assertEqual([item["match"]["file"] for item in first], [item["match"]["file"] for item in second])
        self.assertEqual(3, len({item["match"]["file"] for item in first}))
        self.assertEqual(["random", "random", "flank"], [item["kind"] for item in first])
        self.assertEqual(1, first[-1]["snapshot_round"])
        self.assertEqual(1, first[-1]["event"]["snapshot_round"])

    def test_run_writes_report_summary_and_three_jpgs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            replay_dir, output_dir = root / "replays", root / "artifacts"
            replay_dir.mkdir()
            for index, flank in enumerate((False, False, True), 1):
                (replay_dir / f"sample_{index}.grbr").write_text(_replay_xml(super_deploy=flank), encoding="utf-8")
            report = root / "report.md"
            summary = run(replay_dir, output_dir, report, seed=20260812)
            self.assertEqual(3, summary["eligible_match_count"])
            self.assertEqual(1, summary["statistics"]["super_deploy_count"])
            self.assertTrue(report.exists())
            self.assertIn("共享绝对 x/y", report.read_text(encoding="utf-8"))
            with (output_dir / "summary.json").open(encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(summary["selected_matches"], saved["selected_matches"])
            for index in range(1, 4):
                image = output_dir / f"match_{index:02d}.jpg"
                self.assertTrue(image.exists())
                self.assertGreater(image.stat().st_size, 1000)


if __name__ == "__main__":
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "mechabellum-spatial-matplotlib"))
    unittest.main()

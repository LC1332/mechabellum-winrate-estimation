#!/usr/bin/env python3
"""Offline invariants for the checked cost catalog and state-delta parser."""
import sys
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parse_match_investment import (
    CATALOG,
    _format_unit_values,
    _classify_victory,
    _round_damage,
    _round_results,
    action_cost,
    extract_xml,
    fallback_actions,
    initial_loadout_actions,
    parse_root,
    select_samples,
    slot_mapping,
    state_actions,
    unit_label,
)
from build_unit_cost_table import build_table


class UnitCostCatalogTests(unittest.TestCase):
    def test_normal_units_are_complete_and_half_price_upgrade(self):
        expected = set(range(1, 32)) | {2001, 2002}
        self.assertEqual(expected, {uid for uid, u in CATALOG.items() if not u["special_unit"]})
        for uid in expected:
            unit = CATALOG[uid]
            self.assertTrue(unit["name_cn"])
            self.assertIsNotNone(unit["unlock_cost"])
            self.assertIsNotNone(unit["base_buy_cost"])
            self.assertEqual(unit["upgrade_cost_per_level"], unit["base_buy_cost"] // 2)

    def test_tech_charge_uses_each_tech_base_not_fixed_50(self):
        self.assertEqual((250, "catalog_base_plus_tech_surcharge"),
                         action_cost(30, "tech", tech_id=230, prior_tech_count=0))
        self.assertEqual((300, "catalog_base_plus_tech_surcharge"),
                         action_cost(30, "tech", tech_id=10930, prior_tech_count=1))
        self.assertEqual((900, "catalog_base_plus_tech_surcharge"),
                         action_cost(2002, "tech", tech_id=1022002, prior_tech_count=2))

    def test_current_ids_and_special_unit_are_not_misnamed_or_priced(self):
        self.assertIn("魔眼", unit_label(30))
        self.assertIn("磁暴", unit_label(31))
        self.assertIn("泰山", unit_label(2002))
        self.assertTrue(CATALOG[4001]["special_unit"])
        self.assertEqual((0, "assumed_zero_special_unit_tech"),
                         action_cost(4001, "tech", tech_id=11400101, prior_tech_count=0))

    def test_undoed_buy_is_not_accepted_when_next_state_has_no_new_unit(self):
        before = {"unlocked": set(), "techs": {}, "units": {}}
        after = {"unlocked": set(), "techs": {}, "units": {}}
        record = ET.fromstring(
            '<PlayerRoundRecord xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<actionRecords><MatchActionData xsi:type="PAD_BuyUnit"><UID>30</UID></MatchActionData>'
            '<MatchActionData xsi:type="PAD_Undo"/></actionRecords></PlayerRoundRecord>'
        )
        actions, free = state_actions(before, after, record)
        self.assertEqual([], actions)
        self.assertEqual([], free)

    def test_state_delta_tracks_actual_buy_upgrade_and_field_recovery_refund(self):
        before = {
            "unlocked": set(), "techs": {}, "commander_skills": {0: 900001},
            "units": {
                "1": {"uid": 10, "level": 0, "sell_supply": 100},
                "2": {"uid": 9, "level": 0, "sell_supply": 100},
            },
        }
        after = {
            "unlocked": set(), "techs": {}, "units": {
                "2": {"uid": 9, "level": 1, "sell_supply": 100},
                "3": {"uid": 10, "level": 0, "sell_supply": 60},
                "4": {"uid": 9, "level": 0, "sell_supply": 100},
            },
        }
        record = ET.fromstring(
            '<PlayerRoundRecord xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<actionRecords>'
            '<MatchActionData xsi:type="PAD_BuyUnit"><UID>10</UID></MatchActionData>'
            '<MatchActionData xsi:type="PAD_ReleaseCommanderSkill"><ID>0</ID><SkillIndex>0</SkillIndex><UnitIndex>1</UnitIndex></MatchActionData>'
            '</actionRecords></PlayerRoundRecord>'
        )
        actions, reinforcements = state_actions(before, after, record)
        by_type = {action["type"]: action for action in actions}
        self.assertEqual(60, by_type["buy"]["cost"])
        self.assertEqual(-100, by_type["sell"]["cost"])
        self.assertEqual(100, by_type["sell"]["refund"])
        self.assertEqual(50, by_type["level"]["cost"])
        self.assertEqual(100, by_type["reinforcement"]["cost"])
        self.assertEqual("free_reinforcement_catalog_base_buy_cost",
                         by_type["reinforcement"]["cost_basis"])
        self.assertEqual(["4"], [item["instance_index"] for item in reinforcements])

    def test_initial_loadout_is_r1_investment(self):
        state = {
            "round": 1,
            "units": {
                "1": {"uid": 9, "sell_supply": 100},
                "2": {"uid": 9, "sell_supply": 100},
                "3": {"uid": 9, "sell_supply": 100},
                "4": {"uid": 13, "sell_supply": 200},
                "5": {"uid": 13, "sell_supply": 200},
            },
        }
        actions = initial_loadout_actions(state)
        self.assertEqual(5, len(actions))
        self.assertTrue(all(action["initial_loadout"] for action in actions))
        self.assertEqual(700, sum(action["cost"] for action in actions))

    def test_damage_and_midgame_surrender_are_distinct(self):
        players = [
            {"name": "A", "rounds": [
                {"round": 1, "reactor_core": 4600, "gave_up": False},
                {"round": 2, "reactor_core": 4600, "gave_up": False},
                {"round": 3, "reactor_core": 4600, "gave_up": False},
            ]},
            {"name": "B", "rounds": [
                {"round": 1, "reactor_core": 4600, "gave_up": False},
                {"round": 2, "reactor_core": 4050, "gave_up": False},
                {"round": 3, "reactor_core": 4050, "gave_up": True},
            ]},
        ]
        damage = _round_damage(players)
        self.assertEqual((0, 550), (damage[0]["winner_player_index"], damage[0]["damage_dealt"][0]))
        victory = _classify_victory(players)
        self.assertEqual(("midgame_surrender", 0), (victory["victory_type"], victory["winner_player_index"]))

    def test_last_snapshot_uses_catalog_buy_and_resolved_sale(self):
        before = {
            "techs": {}, "commander_skills": {1: 900001},
            "units": {"4": {"uid": 10, "level": 0, "sell_supply": 80}},
        }
        record = ET.fromstring(
            '<PlayerRoundRecord xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<actionRecords>'
            '<MatchActionData xsi:type="PAD_BuyUnit"><UID>10</UID></MatchActionData>'
            '<MatchActionData xsi:type="PAD_ReleaseCommanderSkill"><ID>0</ID><SkillIndex>1</SkillIndex><UnitIndex>4</UnitIndex></MatchActionData>'
            '</actionRecords></PlayerRoundRecord>'
        )
        actions, _ = fallback_actions(before, record)
        self.assertEqual(["buy", "sell"], [action["type"] for action in actions])
        self.assertEqual((100, "catalog_default"), (actions[0]["cost"], actions[0]["cost_basis"]))
        self.assertEqual((-80, 80), (actions[1]["cost"], actions[1]["refund"]))

    def test_last_snapshot_upgrade_uses_action_uid_and_zero_refund_is_valid(self):
        before = {"techs": {}, "commander_skills": {}, "units": {
            "1": {"uid": 10, "level": 0, "sell_supply": 0},
        }}
        record = ET.fromstring(
            '<PlayerRoundRecord xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<actionRecords>'
            '<MatchActionData xsi:type="PAD_UpgradeUnit"><UID>4</UID><UIDX>99</UIDX></MatchActionData>'
            '<MatchActionData xsi:type="PAD_ReleaseCommanderSkill"><ID>900001</ID><UnitIndex>1</UnitIndex></MatchActionData>'
            '</actionRecords></PlayerRoundRecord>'
        )
        actions, _ = fallback_actions(before, record)
        self.assertEqual(["level", "sell"], [action["type"] for action in actions])
        self.assertEqual((200, "catalog_default"), (actions[0]["cost"], actions[0]["cost_basis"]))
        self.assertEqual((0, 0), (actions[1]["cost"], actions[1]["refund"]))

    def test_round_winner_prefers_surrender_then_next_snapshot(self):
        players = [
            {"name": "A", "rounds": [
                {"round": 1, "gave_up": False, "pre_round_fight_result": "Win"},
                {"round": 2, "gave_up": True, "pre_round_fight_result": "Win"},
            ]},
            {"name": "B", "rounds": [
                {"round": 1, "gave_up": False, "pre_round_fight_result": "Win"},
                {"round": 2, "gave_up": False, "pre_round_fight_result": "Lose"},
            ]},
        ]
        results = _round_results(players)
        self.assertEqual((0, "next_snapshot_pre_round_result"),
                         (results[0]["winner_player_index"], results[0]["source"]))
        self.assertEqual((1, "give_up"),
                         (results[1]["winner_player_index"], results[1]["source"]))

    def test_investment_formatting_is_stable_and_sorts_round_delta_by_magnitude(self):
        text = _format_unit_values({7: -300, 9: 200, 10: 100}, absolute_order=True, signed=True)
        self.assertEqual("野马 -300、尖牙 +200、爬虫 +100", text)

    def test_special_id_gets_a_deterministic_reserved_slot(self):
        root = ET.fromstring(
            '<BattleRecord xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<playerRecords><PlayerRecord><playerRoundRecords><PlayerRoundRecord>'
            '<playerData><units><NewUnitData><id>4001</id></NewUnitData></units></playerData>'
            '<actionRecords><MatchActionData xsi:type="PAD_UpgradeTechnology"><UID>4001</UID><TechID>1</TechID></MatchActionData></actionRecords>'
            '</PlayerRoundRecord></playerRoundRecords></PlayerRecord></playerRecords></BattleRecord>'
        )
        self.assertEqual({"4001": "unknown_unit_slot_0"}, slot_mapping([root])["slots"])

    def test_replay_coverage_has_no_missing_normal_technology(self):
        table = build_table(Path(__file__).resolve().parent.parent / "local_data/humen_replay")
        self.assertEqual([], table["meta"]["missing_catalog_tech_ids"])
        self.assertTrue(table["meta"]["expected_special_unit_unmapped_tech_ids"])

    def test_review_sample_selection_covers_five_matches_sales_and_surrender(self):
        replay_dir = Path(__file__).resolve().parent.parent / "local_data/humen_replay"
        samples = select_samples(replay_dir, 5, require_surrender=True)
        roots = [ET.fromstring(extract_xml(path)) for path in samples]
        slots = slot_mapping(roots)
        matches = [parse_root(root, path, slots) for root, path in zip(roots, samples)]
        self.assertEqual(5, len(matches))
        self.assertEqual(10, sum(len(match["players"]) for match in matches))
        self.assertEqual(1, sum(any(result["source"] == "give_up" for result in match["round_results"])
                                for match in matches))
        self.assertTrue(any(action["type"] == "sell"
                            for match in matches for player in match["players"]
                            for round_data in player["rounds"] for action in round_data["actions"]))
        for match in matches:
            for player in match["players"]:
                cumulative = Counter()
                for round_data in player["rounds"]:
                    self.assertEqual(sum(round_data["o_by_unit"].values()), round_data["known_net_total"])
                    cumulative.update(round_data["o_by_unit"])
                    self.assertEqual(dict(cumulative), round_data["cumulative_o_by_unit"])
                self.assertEqual(dict(cumulative), player["final_o_by_unit"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Offline invariants for the checked cost catalog and state-delta parser."""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parse_match_investment import (
    CATALOG,
    action_cost,
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
        self.assertEqual((None, "unknown_tech"),
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


if __name__ == "__main__":
    unittest.main()

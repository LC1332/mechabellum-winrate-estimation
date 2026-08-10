#!/usr/bin/env python3
"""Contract tests for the portable dense replay dataset."""
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_dense_dataset import (
    MAX_BATTLE_ROUNDS,
    OUTCOME_BATTLE,
    OUTCOME_NULL,
    OUTCOME_SURRENDER,
    _team_outcome,
    _unit_values,
    build_dataset,
    qualify_replay,
)


def round_data(round_no, core, result=None, gave_up=False):
    return {
        "round": round_no,
        "reactor_core": core,
        "pre_round_fight_result": result,
        "gave_up": gave_up,
        "o_by_unit": {},
        "cumulative_o_by_unit": {},
        "economy_confidence": "snapshot",
        "actions": [],
    }


class DenseDatasetTests(unittest.TestCase):
    def test_round_outcomes_cover_battle_deuce_surrender_and_last_round(self):
        battle = [
            [{1: round_data(1, 4500), 2: round_data(2, 4500, "Win")}],
            [{1: round_data(1, 4500), 2: round_data(2, 4100, "Lose")}],
        ]
        self.assertEqual((0, OUTCOME_BATTLE, 400.0, True, "battle"), _team_outcome(battle, 1))

        deuce = [
            [{1: round_data(1, 4500), 2: round_data(2, 4500, "Deuce")}],
            [{1: round_data(1, 4500), 2: round_data(2, 4500, "Deuce")}],
        ]
        self.assertEqual((-1, OUTCOME_NULL, 0.0, True, "deuce"), _team_outcome(deuce, 1))

        surrender = [
            [{1: round_data(1, 4500, gave_up=True)}],
            [{1: round_data(1, 4500)}],
        ]
        self.assertEqual((1, OUTCOME_SURRENDER, 0.0, True, "surrender"), _team_outcome(surrender, 1))

        last_round = [
            [{1: round_data(1, 4500)}],
            [{1: round_data(1, 4500)}],
        ]
        self.assertEqual((-1, OUTCOME_NULL, 0.0, False, "unavailable"), _team_outcome(last_round, 1))

    def test_2v2_unit_aggregation_is_team_average(self):
        team = [
            {"o_by_unit": {1: 100, 2: 50}},
            {"o_by_unit": {1: 300, 2: -50}},
        ]
        values = _unit_values(team, "o_by_unit", {1: 0, 2: 1}, {})
        self.assertEqual((200.0, 0.0), tuple(values[:2]))

    def test_overlength_replay_fails_instead_of_truncating(self):
        records = "".join(f"<PlayerRoundRecord><round>{round_no}</round></PlayerRoundRecord>"
                          for round_no in range(MAX_BATTLE_ROUNDS + 2))
        root = ET.fromstring(
            "<BattleRecord><BattleInfo><MatchMode>VS_1_1</MatchMode></BattleInfo><playerRecords>"
            f"<PlayerRecord><playerRoundRecords>{records}</playerRoundRecords></PlayerRecord>"
            f"<PlayerRecord><playerRoundRecords>{records}</playerRoundRecords></PlayerRecord>"
            "</playerRecords></BattleRecord>"
        )
        with self.assertRaisesRegex(ValueError, "Refuse to truncate"):
            qualify_replay(root)

    def test_full_corpus_matches_dense_v1_contract(self):
        root = Path(__file__).resolve().parent.parent
        arrays, metadata = build_dataset(root / "local_data/humen_replay")
        self.assertEqual((962, MAX_BATTLE_ROUNDS, 2, 43), arrays["investment_delta"].shape)
        self.assertEqual((962, 2, 43), arrays["investment_final"].shape)
        self.assertEqual(np.dtype("float32"), arrays["investment_delta"].dtype)
        self.assertEqual(np.dtype("int8"), arrays["round_winner"].dtype)
        self.assertEqual(962, metadata["statistics"]["included_match_count"])
        self.assertEqual({"VS_1_1": 878, "VS_2_2": 84}, metadata["statistics"]["included_by_match_mode"])
        self.assertEqual(144, metadata["statistics"]["skipped_match_count"])
        self.assertEqual({"non_contiguous_rounds": 144}, metadata["statistics"]["skipped_by_reason"])
        self.assertEqual(19, metadata["statistics"]["qc"]["assumed_zero_special_unit_tech_actions"])
        self.assertEqual({"4001": "unknown_unit_slot_0"}, metadata["unknown_unit_slots"]["slots"])
        self.assertEqual(962, len(metadata["matches"]))
        self.assertFalse(any(Path(item["file"]).is_absolute() for item in metadata["matches"]))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            np.savez_compressed(temp_path / "dense.npz", **arrays)
            (temp_path / "dense.json").write_text(json.dumps(metadata), encoding="utf-8")
            with np.load(temp_path / "dense.npz", allow_pickle=False) as loaded:
                self.assertEqual(arrays["round_mask"].shape, loaded["round_mask"].shape)
                np.testing.assert_array_equal(arrays["investment_final"], loaded["investment_final"])


if __name__ == "__main__":
    unittest.main()

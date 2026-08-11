#!/usr/bin/env python3
"""Contracts for damage-derived terminal labels and paired perspectives."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_transformer import ROOT, RewardConfig, load_dense_dataset
from train_transformer_v2 import build_supervision, infer_damage_terminals, make_task_arrays, perspective_indices


def arrays() -> tuple[dict[str, np.ndarray], dict]:
    n, t, u = 4, 18, 43
    delta = np.zeros((n, t, 2, u), dtype=np.float32)
    delta[:, 0, :, 0] = 900
    mask = np.zeros((n, t), dtype=bool); mask[:, :3] = True
    winner = np.full((n, t), -1, dtype=np.int8)
    outcome = np.zeros((n, t), dtype=np.uint8)
    damage = np.zeros((n, t), dtype=np.float32)
    valid = np.zeros((n, t), dtype=bool)
    # Row 0: side 1 wins round 1, side 0 reaches exactly 100 HP and is inferred.
    winner[0, 0] = 1; outcome[0, 0] = 1; damage[0, 0] = 100; valid[0, 0] = True
    # Row 1: both sides cross, so it is deliberately ambiguous.
    winner[1, 0] = 0; outcome[1, 0] = 1; damage[1, 0] = 100; valid[1, 0] = True
    winner[1, 1] = 1; outcome[1, 1] = 1; damage[1, 1] = 100; valid[1, 1] = True
    # Row 2: 2v2 crosses but must be excluded.
    winner[2, 0] = 0; outcome[2, 0] = 1; damage[2, 0] = 100; valid[2, 0] = True
    # Row 3: known surrender has precedence over damage inference.
    winner[3, 0] = 0; outcome[3, 0] = 2; valid[3, 0] = True
    data = {
        "investment_delta": delta, "investment_cumulative": np.cumsum(delta, axis=1),
        "round_mask": mask, "round_winner": winner, "round_outcome_type": outcome,
        "winner_damage": damage, "damage_valid": valid,
        "initial_health": np.full((n, 2), 100, dtype=np.float32),
        "round_count": np.full(n, 3, dtype=np.uint8),
        "match_mode": np.asarray([1, 1, 2, 1], dtype=np.uint8),
    }
    metadata = {"matches": [{"row_index": i, "file": f"row_{i}.grbr"} for i in range(n)]}
    return data, metadata


class DamageTerminalTests(unittest.TestCase):
    def test_equal_threshold_terminal_and_truncation(self):
        data, metadata = arrays(); audit = infer_damage_terminals(data, metadata)
        self.assertEqual(1, audit["summary"]["inferred_row_count"])
        self.assertEqual(1, audit["summary"]["ambiguous_1v1_count"])
        reward, returns, mask = build_supervision(data, RewardConfig(), audit)
        self.assertEqual(( -1000.0, 1000.0), tuple(reward[0, 0]))
        self.assertFalse(mask[0, 1:].any())
        self.assertEqual((-1000.0, 1000.0), tuple(returns[0, 0]))

    def test_perspectives_are_exactly_opposite_after_terminal_relabel(self):
        data, metadata = arrays(); audit = infer_damage_terminals(data, metadata)
        _, returns, mask = build_supervision(data, RewardConfig(), audit)
        for task in ("q", "v"):
            features, targets, masks = make_task_arrays(data, task, returns, mask)
            n = len(data["round_count"])
            np.testing.assert_array_equal(masks[:n], masks[n:])
            np.testing.assert_array_equal(targets[:n], -targets[n:])
            np.testing.assert_array_equal(features[:n, :, :43], features[n:, :, 43:86])

    def test_side0_indices_never_append_the_reversed_view(self):
        np.testing.assert_array_equal(perspective_indices([1, 3], 5, "side0"), np.asarray([1, 3]))
        np.testing.assert_array_equal(perspective_indices([1, 3], 5, "both"), np.asarray([1, 3, 6, 8]))

    def test_dense_v1_damage_inference_contract(self):
        config = {"dataset_npz": "data/mechabellum_dense_v1.npz", "dataset_json": "data/mechabellum_dense_v1.json"}
        data, metadata, _, _ = load_dense_dataset(config)
        audit = infer_damage_terminals(data, metadata)
        self.assertEqual(112, audit["summary"]["inferred_row_count"])
        self.assertEqual(109, audit["summary"]["inferred_unique_group_count"])
        self.assertEqual(2, audit["summary"]["ambiguous_1v1_count"])
        self.assertEqual(152, audit["summary"]["masked_after_crossing_round_count"])


if __name__ == "__main__":
    unittest.main()

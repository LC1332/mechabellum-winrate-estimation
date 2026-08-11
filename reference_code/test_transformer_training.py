#!/usr/bin/env python3
"""Focused contract tests for the Q/V training pipeline."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_transformer import (
    CausalTransformerRegressor,
    ModelConfig,
    RewardConfig,
    _allocate_group_splits,
    discounted_returns,
    immediate_rewards,
    make_task_arrays,
    masked_mse,
    round_budget,
)


def fake_arrays() -> dict[str, np.ndarray]:
    delta = np.zeros((2, 18, 2, 43), dtype=np.float32)
    delta[0, 0, 0, 0] = 900
    delta[0, 0, 1, 1] = 900
    delta[0, 1, 0, 2] = 400
    delta[0, 1, 1, 3] = 400
    delta[1, 0, 0, 0] = 900
    cumulative = np.cumsum(delta, axis=1)
    mask = np.zeros((2, 18), dtype=bool)
    mask[0, :2] = True
    mask[1, :1] = True
    winner = np.full((2, 18), -1, dtype=np.int8)
    winner[0, 0] = 0
    winner[0, 1] = 1
    outcome = np.zeros((2, 18), dtype=np.uint8)
    outcome[0, 0] = 1
    outcome[0, 1] = 2
    return {"investment_delta": delta, "investment_cumulative": cumulative, "round_mask": mask, "round_winner": winner, "round_outcome_type": outcome, "round_count": np.asarray([2, 1], dtype=np.uint8), "match_mode": np.asarray([1, 2], dtype=np.uint8)}


class TransformerTrainingTests(unittest.TestCase):
    def test_budget_schedule(self):
        np.testing.assert_array_equal(round_budget(5), np.asarray([900, 400, 600, 800, 1000], dtype=np.float32))

    def test_rewards_discount_and_side_symmetry(self):
        arrays = fake_arrays()
        rewards = immediate_rewards(arrays, RewardConfig())
        self.assertEqual((100.0, -100.0), tuple(rewards[0, 0]))
        self.assertEqual((-200.0, 200.0), tuple(rewards[0, 1]))
        returns = discounted_returns(rewards, arrays["round_mask"], 0.5)
        self.assertEqual((0.0, 0.0), tuple(returns[0, 0]))
        self.assertEqual((-200.0, 200.0), tuple(returns[0, 1]))

    def test_known_normal_final_round_uses_terminal_reward(self):
        arrays = fake_arrays()
        arrays["round_mask"][0, 1] = False
        arrays["round_count"][0] = 1
        arrays["round_outcome_type"][0, 0] = 1
        arrays["round_winner"][0, 0] = 0
        rewards = immediate_rewards(arrays, RewardConfig())
        self.assertEqual((1000.0, -1000.0), tuple(rewards[0, 0]))

    def test_q_omits_current_opponent_and_v_includes_it(self):
        arrays = fake_arrays()
        q_features, _, _ = make_task_arrays(arrays, "q", RewardConfig())
        v_features, _, _ = make_task_arrays(arrays, "v", RewardConfig())
        changed = copy.deepcopy(arrays)
        changed["investment_delta"][0, 1, 1, 10] += 1234
        changed["investment_cumulative"] = np.cumsum(changed["investment_delta"], axis=1)
        q_changed, _, _ = make_task_arrays(changed, "q", RewardConfig())
        v_changed, _, _ = make_task_arrays(changed, "v", RewardConfig())
        np.testing.assert_array_equal(q_features[0, 1], q_changed[0, 1])
        self.assertFalse(np.array_equal(v_features[0, 1], v_changed[0, 1]))

    def test_padding_has_no_loss(self):
        prediction = torch.tensor([[3.0, 999.0]])
        target = torch.tensor([[1.0, -999.0]])
        mask = torch.tensor([[True, False]])
        self.assertEqual(4.0, float(masked_mse(prediction, target, mask)))

    def test_causal_model_ignores_future(self):
        torch.manual_seed(7)
        model = CausalTransformerRegressor(3, 2, ModelConfig(d_model=8, nhead=2, dim_feedforward=16, dropout=0.0)).eval()
        feature = torch.randn(1, 4, 3)
        mask = torch.tensor([[True, True, True, True]])
        changed = feature.clone()
        changed[:, 3] += 1000
        with torch.no_grad():
            first = model(feature, mask)
            second = model(changed, mask)
        torch.testing.assert_close(first[:, :3], second[:, :3])

    def test_group_split_is_disjoint_and_reproducible(self):
        groups = [
            {"id": "a", "rows": [0, 1], "stratum": "x"},
            {"id": "b", "rows": [2], "stratum": "x"},
            {"id": "c", "rows": [3], "stratum": "y"},
            {"id": "d", "rows": [4], "stratum": "y"},
            {"id": "e", "rows": [5], "stratum": "y"},
        ]
        first = _allocate_group_splits(copy.deepcopy(groups), .8, .1, 5)
        second = _allocate_group_splits(copy.deepcopy(groups), .8, .1, 5)
        self.assertEqual(first, second)
        sets = {name: {item["id"] for item in values} for name, values in first.items()}
        self.assertFalse(sets["train"] & sets["validation"])
        self.assertFalse(sets["train"] & sets["test"])


if __name__ == "__main__":
    unittest.main()

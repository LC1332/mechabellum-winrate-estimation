#!/usr/bin/env python3
"""Contract tests for the Logistic matchup experiments."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_logistic import (
    binary_cross_entropy, bootstrap_differences, coefficient_diagnostics, make_task_data,
    rewards_and_return_targets, side_features, soft_training_rows,
    write_report,
)


def arrays() -> dict[str, np.ndarray]:
    cumulative = np.zeros((1, 18, 2, 43), dtype=np.float32)
    cumulative[0, 0, 0, :2] = (2, 2); cumulative[0, 0, 1, :2] = (1, 3)
    cumulative[0, 1] = cumulative[0, 0]; cumulative[0, 1, 0, 2] = -1
    mask = np.zeros((1, 18), dtype=bool); mask[0, :2] = True
    winner = np.full((1, 18), -1, dtype=np.int8); winner[0, 0] = 0
    return {"investment_cumulative": cumulative, "round_mask": mask, "round_winner": winner}


class LogisticTests(unittest.TestCase):
    def test_combined_coefficient_offset_order_and_rankings(self):
        unit_axis = [{"name_cn": f"兵种{i}"} for i in range(43)]
        coefficients = np.zeros((1, 1935), dtype=np.float64)
        for rank in range(15):
            coefficients[0, 86 + rank * 43 + (rank + 1)] = 15 - rank
            coefficients[0, 86 + (rank + 1) * 43 + rank] = -(15 - rank)
        model = SimpleNamespace(coef_=coefficients, intercept_=np.asarray([0.0]))

        diagnostics = coefficient_diagnostics(model, "combined", unit_axis)

        self.assertEqual(15, len(diagnostics["top_positive"]))
        self.assertEqual(15, len(diagnostics["top_negative"]))
        self.assertEqual(
            list(range(15, 0, -1)),
            [round(item["coefficient"]) for item in diagnostics["top_positive"]],
        )
        self.assertEqual(
            list(range(-15, 0)),
            [round(item["coefficient"]) for item in diagnostics["top_negative"]],
        )
        self.assertEqual("兵种0", diagnostics["top_positive"][0]["self"]["name_cn"])
        self.assertEqual("兵种1", diagnostics["top_positive"][0]["opponent"]["name_cn"])

    def test_report_renders_reserved_unit_fallback_and_best_model_tables(self):
        unit_axis = [{"name_cn": "已知兵种"}] + [{"reserved_slot": f"unknown_unit_slot_{i}"} for i in range(42)]
        positive = [{"self": unit_axis[0], "opponent": unit_axis[1], "coefficient": 2.0}]
        negative = [{"self": unit_axis[1], "opponent": unit_axis[0], "coefficient": -2.0}]
        item = {
            "selected_c": 10.0,
            "validation_selected": {"cross_entropy": 0.1},
            "test": {"roc_auc": 0.6, "accuracy": 0.5, "cross_entropy": 0.69, "brier": 0.25},
            "coefficient_diagnostics": {"top_positive": positive, "top_negative": negative},
            "bootstrap": {},
        }
        discounted_item = {
            "selected_c": 1.0,
            "validation_selected": {"cross_entropy": 0.2},
            "test": {"cross_entropy": 0.69, "rmse": 1.0, "mae": 0.5, "r2": 0.1, "pearson": 0.2},
            "coefficient_diagnostics": {},
            "bootstrap": {},
        }
        payload = {
            "tasks": {
                "round_winner": {"recommended_feature_family": "combined", "combined": item, "main": item, "interaction": item},
                "discounted_return": {"recommended_feature_family": "main", "combined": discounted_item, "main": discounted_item, "interaction": discounted_item},
            }
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            write_report(path, payload)
            report = path.read_text(encoding="utf-8")
        self.assertIn("已知兵种A - unknown_unit_slot_0B", report)
        self.assertIn("unknown_unit_slot_0A - 已知兵种B", report)
        self.assertIn("round_winner / combined", report)

    def test_discounted_bootstrap_has_zero_prediction_random_rmse_baseline(self):
        result = bootstrap_differences(
            "discounted_return",
            np.asarray([0.5, 0.5]),
            np.asarray([1.0, -1.0]),
            np.asarray([1.0, 1.0]),
            np.asarray([0.5, 0.5]),
            None,
            np.asarray(["a", "b"]),
            replicates=10,
            seed=1,
        )
        self.assertIn("vs_random_rmse", result)
        self.assertAlmostEqual(0.0, result["vs_random_rmse"]["mean_improvement"])

    def test_bootstrap_is_reproducible_at_replay_group_level(self):
        args = (
            "round_winner",
            np.asarray([1.0, 0.0, 1.0, 0.0]),
            np.asarray([1.0, -1.0, 1.0, -1.0]),
            np.ones(4),
            np.asarray([.8, .2, .7, .3]),
            None,
            np.asarray(["replay_a", "replay_a", "replay_b", "replay_b"]),
        )
        self.assertEqual(
            bootstrap_differences(*args, replicates=25, seed=7),
            bootstrap_differences(*args, replicates=25, seed=7),
        )

    def test_feature_dimensions_and_transposed_perspective(self):
        data = arrays()
        self.assertEqual((2, 18, 86), side_features(data, "main").shape)
        interaction = side_features(data, "interaction")
        self.assertEqual((2, 18, 1849), interaction.shape)
        np.testing.assert_allclose(interaction[1, 0].reshape(43, 43), interaction[0, 0].reshape(43, 43).T)
        self.assertEqual((2, 18, 1935), side_features(data, "combined").shape)

    def test_return_and_remaining_horizon_normalization(self):
        data = arrays(); _, returns, maximum = rewards_and_return_targets(data, .3, 100)
        self.assertAlmostEqual(100.0, returns[0, 0, 0])
        self.assertAlmostEqual(0.0, returns[0, 1, 0])
        self.assertAlmostEqual(130.0, maximum[0, 0, 0])
        self.assertAlmostEqual(100.0, maximum[0, 1, 0])

    def test_unknown_round_excluded_only_from_winner_target(self):
        metadata = {"matches": [{"file": "one.grbr"}], "unit_axis": [str(i) for i in range(43)]}
        winner = make_task_data(arrays(), metadata, "round_winner", "main", .3, 100)
        reward = make_task_data(arrays(), metadata, "discounted_return", "main", .3, 100)
        self.assertEqual(2, int(winner.valid_mask.sum()))
        self.assertEqual(4, int(reward.valid_mask.sum()))
        self.assertEqual(1.0, winner.probability_target[0, 0])
        self.assertEqual(0.0, winner.probability_target[1, 0])
        np.testing.assert_allclose(reward.probability_target[0], 1.0 - reward.probability_target[1])

    def test_soft_row_weights_match_direct_bce(self):
        feature = np.asarray([[1.], [2.]], dtype=np.float32); target = np.asarray([.2, .8]); x, y, w = soft_training_rows(feature, target)
        probability = np.asarray([.3, .7]); duplicated = np.asarray([.3, .7, .3, .7])
        direct = binary_cross_entropy(target, probability)
        weighted = -np.average(y * np.log(duplicated) + (1-y) * np.log(1-duplicated), weights=w)
        self.assertAlmostEqual(direct, weighted)
        self.assertEqual((4, 1), x.shape)


if __name__ == "__main__":
    unittest.main()

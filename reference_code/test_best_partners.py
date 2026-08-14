from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reference_code.analyze_best_partners import (  # noqa: E402
    FEATURE_DIM,
    K,
    SKILLS,
    analyze_best_partners,
    build_feature,
    ensemble_probability,
    write_report,
)


class LogisticStub:
    def __init__(self, interaction: np.ndarray, intercept: float = 0.0, main_self: np.ndarray | None = None) -> None:
        self.interaction = interaction
        self.intercept = intercept
        self.main_self = np.zeros(K) if main_self is None else main_self
        self.coef_ = np.zeros((1, FEATURE_DIM))

    def predict_proba(self, feature: np.ndarray) -> np.ndarray:
        row = feature[0]
        self_global = row[:K]
        opponent_global = row[K : 2 * K]
        interaction = row[2 * K : 2 * K + K * K].reshape(K, K)
        logit = self.intercept + float(self.main_self @ self_global) + float((self.interaction * interaction).sum())
        probability = 1.0 / (1.0 + math.exp(-logit))
        return np.asarray([[1.0 - probability, probability]])


def unit(unit_id: int, axis: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(unit_id=unit_id, axis=axis, name_cn=name)


class BestPartnerTests(unittest.TestCase):
    def test_feature_layout(self) -> None:
        feature = build_feature(2, 5, 7)
        self.assertEqual(feature.shape, (FEATURE_DIM,))
        self.assertAlmostEqual(float(feature[2]), 0.5)
        self.assertAlmostEqual(float(feature[5]), 0.5)
        self.assertAlmostEqual(float(feature[K + 7]), 1.0)
        interaction = feature[2 * K : 2 * K + K * K].reshape(K, K)
        self.assertAlmostEqual(float(interaction[2, 7]), 0.5 / 3.0)
        self.assertAlmostEqual(float(interaction[5, 7]), 0.5 / 3.0)
        self.assertTrue(np.all(feature[2 * K + K * K :] == 0.0))
        self.assertEqual(2 * K + K * K + 4 * K + 4 * SKILLS * K, FEATURE_DIM)

    def test_fold_probabilities_are_averaged(self) -> None:
        feature = build_feature(0, 1, 2)
        models = [
            LogisticStub(np.zeros((K, K)), intercept=-math.log(4)),
            LogisticStub(np.zeros((K, K)), intercept=0.0),
            LogisticStub(np.zeros((K, K)), intercept=math.log(4)),
        ]
        self.assertAlmostEqual(ensemble_probability(models, feature), 0.5, places=12)

    def test_worst_counter_and_partner_sort_are_deterministic(self) -> None:
        units = [unit(1, 0, "甲"), unit(2, 1, "乙"), unit(3, 2, "丙")]
        matrix = np.zeros((K, K))
        matrix[0, :3] = [-1.0, -2.0, -3.0]
        matrix[1, :3] = [1.0, 0.0, -1.0]
        matrix[2, :3] = [0.0, 2.0, 1.0]
        selected, ranked = analyze_best_partners([LogisticStub(matrix)], units)
        self.assertEqual(ranked[1][0].partner.unit_id, 3)
        self.assertEqual(ranked[1][0].counter.unit_id, 3)
        self.assertEqual(selected[0].fixed.unit_id, 1)
        self.assertEqual(selected[0].partner.unit_id, 3)
        self.assertEqual(len({item.partner.unit_id for item in ranked[1]}), 2)

    def test_report_has_expected_columns_and_is_repeatable(self) -> None:
        units = [unit(1, 0, "甲"), unit(2, 1, "乙"), unit(3, 2, "丙")]
        models = [LogisticStub(np.zeros((K, K)))]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            write_report(path, models=models, units=units)
            first = path.read_text(encoding="utf-8")
            write_report(path, models=models, units=units)
            second = path.read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertIn("| 兵种 | 最佳搭档 | 最大最小胜率 | 对方最克制的兵种 |", first)
        self.assertIn("当前商店的 3 个兵种", first)
        self.assertLessEqual(sum(line.startswith("| 甲 |") for line in first.splitlines()), 3)


if __name__ == "__main__":
    unittest.main()

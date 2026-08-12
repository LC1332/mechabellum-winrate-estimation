#!/usr/bin/env python3
"""Focused invariants for the three-strategy logistic experiment."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_logistic_strategies import (  # noqa: E402
    StrategyConfig,
    all_strategy_configs,
    feature_matrix,
    load_bundle,
    make_samples,
    make_split,
    read_config,
)


class StrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = read_config("configs/logistic_strategies.yaml")
        cls.arrays, cls.metadata = load_bundle(cls.config)
        cls.samples = make_samples(cls.arrays, cls.metadata)

    def test_search_space_uses_shared_spatial_w(self):
        configs = all_strategy_configs()
        self.assertEqual(24, len(configs))
        self.assertTrue(all(config.spatial in {"off", "h150", "h300", "h600"} for config in configs))
        self.assertNotIn("per_probe", {config.spatial for config in configs})

    def test_spatial_pooling_preserves_value_when_positions_exist(self):
        spatial = self.arrays["spatial_value"][1]
        valid = self.arrays["round_mask"]
        checked = 0
        for match, round_no in zip(*np.nonzero(valid)):
            for side in (0, 1):
                pooled = spatial[match, round_no, side].sum(axis=0)
                self.assertTrue(np.all(pooled >= 0))
                checked += int(np.any(pooled > 0))
                for other_half in (0, 2):
                    other = self.arrays["spatial_value"][other_half, match, round_no, side].sum(axis=0)
                    np.testing.assert_allclose(pooled, other, rtol=1e-5, atol=1e-3)
        self.assertGreater(checked, 1000)

    def test_half_distance_kernel_is_exact(self):
        distance = np.asarray([0.0, 150.0, 300.0])
        weight = np.power(2.0, -distance / 150.0)
        self.assertAlmostEqual(0.5, weight[1])
        self.assertAlmostEqual(0.25, weight[2])

    def test_harmonic_denominator_equals_side_denominator_when_totals_equal(self):
        side = StrategyConfig("off", "off", False)
        harmonic = StrategyConfig("off", "off", True)
        found = False
        for match, round_no in zip(*np.nonzero(self.arrays["round_mask"])):
            x = self.arrays["investment_cumulative"][match, round_no]
            if np.isclose(x[0].sum(), x[1].sum(), rtol=1e-6, atol=1e-6):
                indices = np.flatnonzero((self.samples.match == match) & (self.samples.round == round_no) & (self.samples.side == 0))
                if len(indices):
                    first = feature_matrix(self.arrays, self.samples, side)[indices]
                    second = feature_matrix(self.arrays, self.samples, harmonic)[indices]
                    np.testing.assert_allclose(first.toarray(), second.toarray(), rtol=1e-5, atol=1e-5)
                    found = True
                    break
        self.assertTrue(found)

    def test_split_groups_are_disjoint_and_cover_samples(self):
        split = make_split(self.samples, self.config)
        names = ("selection", "test_a", "test_b", "test_c")
        groups = [set(split["groups"][name]) for name in names]
        self.assertEqual(sum(map(len, groups)), len(set(self.samples.group)))
        for i, first in enumerate(groups):
            for second in groups[i + 1:]:
                self.assertFalse(first & second)
        row_counts = [split["splits"][name]["sample_count"] for name in names]
        self.assertEqual(sum(row_counts), len(self.samples))

    def test_buff_values_contain_confirmed_specialist_effects(self):
        buff = self.arrays["buff_delta"]
        self.assertTrue(np.any(np.isclose(buff[..., 0], -0.11)))
        self.assertTrue(np.any(np.isclose(buff[..., 1], 0.17)))
        self.assertTrue(np.all(np.isfinite(buff)))


if __name__ == "__main__":
    unittest.main()

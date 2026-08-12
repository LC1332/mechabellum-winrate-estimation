#!/usr/bin/env python3
import json
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_battle_skill_dataset import (  # noqa: E402
    GIANT_UNITS, IMPROVED_CARDS, MASS_CARDS, SKILL_CATALOG, _variant_value,
)
from train_battle_skill_strategies import feature_dim  # noqa: E402


class BattleSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.npz = Path("data/logistic_battle_skill_v2.npz")

    def test_economic_masks_and_feature_dimensions(self):
        self.assertEqual(16, 1 << 4)
        self.assertEqual(0, feature_dim("off") - (2 * 43 + 43 * 43 + 4 * 43))
        self.assertEqual(2 * 24 * 43, feature_dim("opponent") - feature_dim("off"))
        self.assertEqual(4 * 24 * 43, feature_dim("both") - feature_dim("off"))

    def test_improved_phoenix_and_mass_sledgehammer(self):
        improved = {"31603": 1}
        self.assertAlmostEqual(300.0, _variant_value(16, 0, 200.0, improved, False, 4))
        self.assertAlmostEqual(450.0, _variant_value(16, 1, 300.0, improved, False, 4))
        mass = {"31301": 1}
        self.assertAlmostEqual(210.0, _variant_value(13, 1, 300.0, mass, False, 8))

    def test_efficient_manufacturing_stacks(self):
        cards = {"20022": 2}
        self.assertAlmostEqual(500.0, _variant_value(1, 0, 400.0, cards, True, 2))
        self.assertAlmostEqual(200.0, _variant_value(2, 0, 100.0, {"20023": 2}, True, 2))

    def test_purchase_bonus_stacks_on_improved_value(self):
        self.assertAlmostEqual(350.0, _variant_value(16, 0, 200.0, {"31603": 1, "20023": 1}, True, 1 | 2 | 4))

    def test_skill_catalog_has_24_unique_dimensions(self):
        self.assertEqual(24, len(SKILL_CATALOG))
        self.assertEqual(24, len({item["id"] for item in SKILL_CATALOG.values()}))

    def test_known_giant_set(self):
        self.assertIn(1, GIANT_UNITS)
        self.assertIn(2002, GIANT_UNITS)
        self.assertNotIn(2, GIANT_UNITS)

    def test_built_dataset_preserves_spatial_value(self):
        if not self.npz.exists():
            self.skipTest("v2 dataset has not been built")
        with np.load(self.npz, allow_pickle=False) as archive:
            board = archive["board_value"]
            pooled = archive["spatial_value"].sum(axis=4)
            valid = archive["spatial_valid"].all(axis=2)
            rows = np.flatnonzero(valid.reshape(-1))
            self.assertGreater(len(rows), 1000)
            # A few deterministic slices are enough to catch axis transposition
            # without turning this test into a full 16x962x18 scan.
            for mask in (0, 1, 4, 8, 15):
                np.testing.assert_allclose(pooled[mask, :20], board[mask, :20], rtol=2e-5, atol=2e-2)

    def test_built_skill_tensor_is_finite_and_nonempty(self):
        if not self.npz.exists():
            self.skipTest("v2 dataset has not been built")
        with np.load(self.npz, allow_pickle=False) as archive:
            value = archive["battle_skill_value"]
            self.assertTrue(np.isfinite(value).all())
            self.assertGreater(int(np.count_nonzero(value)), 0)


if __name__ == "__main__":
    unittest.main()

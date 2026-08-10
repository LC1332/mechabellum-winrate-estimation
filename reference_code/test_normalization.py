#!/usr/bin/env python3
"""Contract tests for per-round normalization statistics and artifacts."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_normalization import MAX_BATTLE_ROUNDS, analyze_samples, generate_artifacts, round_total_samples


class NormalizationTests(unittest.TestCase):
    def test_round_total_samples_sum_units_and_exclude_padding(self):
        investment = np.zeros((2, MAX_BATTLE_ROUNDS, 2, 3), dtype=np.float32)
        investment[0, 0, 0] = (1, 2, 3)
        investment[0, 0, 1] = (10, 20, 30)
        investment[1, 0, 0] = (100, 200, 300)
        investment[1, 0, 1] = (1000, 2000, 3000)
        investment[1, 1, 0] = (4, 5, 6)
        investment[1, 1, 1] = (40, 50, 60)
        round_mask = np.zeros((2, MAX_BATTLE_ROUNDS), dtype=bool)
        round_mask[:, 0] = True
        round_mask[1, 1] = True

        samples = round_total_samples(investment, round_mask)
        np.testing.assert_array_equal((6.0, 60.0, 600.0, 6000.0), samples[0])
        np.testing.assert_array_equal((15.0, 150.0), samples[1])
        self.assertEqual(0, samples[2].size)

    def test_rank_trim_uses_floor_and_population_variance(self):
        values = np.arange(100, dtype=np.float64)
        result = analyze_samples(values)
        retained = np.arange(3, 97, dtype=np.float64)
        self.assertEqual(3, result["trim_each_tail_count"])
        self.assertEqual(94, result["retained_sample_count"])
        self.assertEqual(float(np.mean(retained)), result["robust_mean"])
        self.assertEqual(float(np.var(retained, ddof=0)), result["raw_variance"])
        self.assertEqual(result["raw_variance"], result["normalization_variance"])

    def test_variance_floor_and_strict_three_sigma_boundaries(self):
        values = np.asarray([10.0] * 98 + [40.0, 41.0])
        result = analyze_samples(values)
        self.assertEqual(100.0, result["normalization_variance"])
        self.assertEqual(-20.0, result["three_sigma_lower"])
        self.assertEqual(40.0, result["three_sigma_upper"])
        self.assertEqual(0, result["below_three_sigma_count"])
        self.assertEqual(1, result["above_three_sigma_count"])
        self.assertEqual(1, result["outside_three_sigma_count"])

    def test_empty_round_serializes_null_statistics(self):
        result = analyze_samples(np.asarray([], dtype=np.float64))
        self.assertEqual(0, result["sample_count"])
        self.assertEqual(0, result["retained_sample_count"])
        for key in ("robust_mean", "raw_variance", "normalization_variance", "sigma",
                    "three_sigma_lower", "three_sigma_upper", "outside_three_sigma_count",
                    "outside_three_sigma_percentage", "below_three_sigma_count", "above_three_sigma_count"):
            self.assertIsNone(result[key])

    def test_artifacts_have_full_round_axis_and_valid_jpegs(self):
        delta = np.zeros((1, MAX_BATTLE_ROUNDS, 2, 2), dtype=np.float32)
        cumulative = np.zeros_like(delta)
        delta[0, 0] = ((2, 3), (4, 5))
        cumulative[0, 0] = delta[0, 0]
        mask = np.zeros((1, MAX_BATTLE_ROUNDS), dtype=bool)
        mask[0, 0] = True

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_npz = temp_path / "dense.npz"
            output_json = temp_path / "stats.json"
            output_md = temp_path / "stats.md"
            delta_jpg = temp_path / "delta.jpg"
            cumulative_jpg = temp_path / "cumulative.jpg"
            np.savez_compressed(input_npz, investment_delta=delta, investment_cumulative=cumulative, round_mask=mask)
            generate_artifacts(input_npz, output_json, output_md, delta_jpg, cumulative_jpg)

            statistics = json.loads(output_json.read_text(encoding="utf-8"))
            for metric in statistics["metrics"].values():
                self.assertEqual(MAX_BATTLE_ROUNDS, len(metric["rounds"]))
                self.assertEqual(2, metric["rounds"][0]["sample_count"])
                self.assertEqual(0, metric["rounds"][1]["sample_count"])
                self.assertIsNone(metric["rounds"][1]["robust_mean"])
            self.assertEqual(
                statistics["metrics"]["round_investment"]["rounds"][0]["robust_mean"],
                statistics["metrics"]["board_total_value"]["rounds"][0]["robust_mean"],
            )
            self.assertTrue(output_md.stat().st_size > 0)
            for image in (delta_jpg, cumulative_jpg):
                self.assertTrue(image.stat().st_size > 0)
                self.assertEqual(b"\xff\xd8\xff", image.read_bytes()[:3])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalog import UNITS  # noqa: E402
from app.inference import Formation, SideState, Simulator  # noqa: E402
from app.main import app  # noqa: E402


class SimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sim = Simulator()

    def side(self, *formations: tuple[str, int, int]) -> SideState:
        return SideState([Formation(*item) for item in formations], {item[1] for item in formations}, {})

    def test_catalog_has_confirmed_32_units(self) -> None:
        self.assertEqual(len(UNITS), 32)
        self.assertEqual(len({unit.axis for unit in UNITS}), 32)
        self.assertNotIn(2001, {unit.unit_id for unit in UNITS})

    def test_feature_shape_and_spatial_weight_conservation(self) -> None:
        a, b = self.side(("a", 9, 0)), self.side(("b", 25, -2))
        feature = self.sim.feature(a, b)
        self.assertEqual(feature.shape, (6235,))
        spatial = self.sim._spatial(a)
        self.assertAlmostEqual(float(spatial.sum()), 100.0, places=6)
        self.assertTrue((spatial >= 0).all())

    def test_unit_upgrade_level_changes_capital(self) -> None:
        base = self.side(("a", 9, 0))
        upgraded = SideState([Formation("a", 9, 0, 2)], {9}, {})
        self.assertAlmostEqual(float(self.sim._spatial(upgraded).sum()), 150.0, places=6)
        self.assertAlmostEqual(float(self.sim._spatial(base).sum()), 100.0, places=6)

    def test_recommendation_golden_examples(self) -> None:
        a = self.side(("a", 9, 0))
        b = self.side(("b", 25, -2))
        result = self.sim.evaluate(a, b)
        self.assertEqual(result.recommendations["side_a"]["left"][0]["name_cn"], "先知")
        self.assertEqual(len(result.recommendations["side_a"]["left"]), 5)
        b = self.side(("b", 10, 2))
        result = self.sim.evaluate(a, b)
        self.assertEqual(result.recommendations["side_a"]["right"][0]["name_cn"], "弧光")
        self.assertEqual(result.recommendations["side_a"]["right"][1]["name_cn"], "火獾")

    def test_extra_capital_changes_only_requested_side(self) -> None:
        a, b = self.side(("a", 9, 0)), self.side(("b", 25, -2))
        base = self.sim.feature(a, b)
        changed = self.sim.feature(a, b, ("a", 15, 0, 100.0))
        self.assertFalse((base == changed).all())
        self.assertAlmostEqual(float(self.sim._spatial(a).sum()), float(self.sim._spatial(a).sum()), places=6)

    def test_temperature_one_matches_raw_model_probability(self) -> None:
        a, b = self.side(("a", 9, 0)), self.side(("b", 25, -2))
        feature = self.sim.feature(a, b)
        raw = self.sim.ensemble.probabilities(feature, temperature=1.0)
        expected = self.sim.ensemble.models[0].predict_proba(feature.reshape(1, -1))[0, 1]
        self.assertAlmostEqual(float(raw[0]), float(expected), places=10)

    def test_higher_temperature_softens_probability(self) -> None:
        a, b = self.side(("a", 9, 0)), self.side(("b", 25, -2))
        cold = self.sim.evaluate(a, b, temperature=1.0).side_a
        warm = self.sim.evaluate(a, b, temperature=20.0).side_a
        self.assertLess(abs(warm - 0.5), abs(cold - 0.5))


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @staticmethod
    def side(formation_id: str, unit_id: int, lane: int) -> dict:
        return {"formations": [{"id": formation_id, "unit_id": unit_id, "lane": lane}], "unlocked_unit_ids": [unit_id], "tech_investment": {}}

    def test_health_catalog_and_evaluate(self) -> None:
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        catalog = self.client.get("/api/catalog").json()["units"]
        self.assertEqual(len(catalog), 32)
        self.assertTrue(all("upgrade_cost_per_level" in unit for unit in catalog))
        response = self.client.post("/api/evaluate", json={"side_a": self.side("a", 9, 0), "side_b": self.side("b", 25, -2)})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertAlmostEqual(payload["probability"]["side_a"] + payload["probability"]["side_b"], 1.0, places=8)
        self.assertEqual(payload["model"]["feature_dim"], 6235)
        self.assertEqual(len(payload["recommendations"]["side_a"]["left"]), 5)

    def test_formation_level_defaults_and_validation(self) -> None:
        response = self.client.post("/api/evaluate", json={"side_a": self.side("a", 9, 0), "side_b": self.side("b", 25, -2)})
        self.assertEqual(response.status_code, 200)
        for level in (0, 10):
            invalid = self.side("a", 9, 0)
            invalid["formations"][0]["level"] = level
            response = self.client.post("/api/evaluate", json={"side_a": invalid, "side_b": self.side("b", 25, -2)})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["code"], "invalid_request")

    def test_stable_validation_errors(self) -> None:
        empty = {"formations": [], "unlocked_unit_ids": [], "tech_investment": {}}
        response = self.client.post("/api/evaluate", json={"side_a": empty, "side_b": self.side("b", 25, -2)})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "empty_board_side")
        duplicate = {"formations": [{"id": "same", "unit_id": 9, "lane": 0}, {"id": "same", "unit_id": 10, "lane": 1}], "unlocked_unit_ids": [9, 10], "tech_investment": {}}
        response = self.client.post("/api/evaluate", json={"side_a": duplicate, "side_b": self.side("b", 25, -2)})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "duplicate_formation_id")
        invalid_tech = {"formations": [{"id": "a", "unit_id": 9, "lane": 0}], "unlocked_unit_ids": [9], "tech_investment": {"9": -100}}
        response = self.client.post("/api/evaluate", json={"side_a": invalid_tech, "side_b": self.side("b", 25, -2)})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_tech_investment")

    def test_temperature_defaults_and_validation(self) -> None:
        response = self.client.post("/api/evaluate", json={"side_a": self.side("a", 9, 0), "side_b": self.side("b", 25, -2)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model"]["temperature"], 5.0)
        for value in (0, 21):
            response = self.client.post("/api/evaluate", json={"temperature": value, "side_a": self.side("a", 9, 0), "side_b": self.side("b", 25, -2)})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["code"], "invalid_temperature")


if __name__ == "__main__":
    unittest.main()

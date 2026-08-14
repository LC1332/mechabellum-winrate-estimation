from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import warnings

import joblib
import numpy as np

from .catalog import UNIT_BY_ID, Unit

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "models/logistic_battle_skill_v2"
MODEL_NAME = "full-6-7"
EXPECTED_FEATURE_DIM = 6235
EXPECTED_SKLEARN = "1.7.2"
PROBES = np.asarray(((-300.0, 0.0), (0.0, 0.0), (300.0, 0.0)), dtype=np.float64)
LANE_X = {-2: -300.0, -1: -150.0, 0: 0.0, 1: 150.0, 2: 300.0}
LANE_NAMES = { -2: "left", 0: "middle", 2: "right" }
K = 43
SKILLS = 24


class ModelLoadError(RuntimeError):
    pass


@dataclass
class Formation:
    formation_id: str
    unit_id: int
    lane: int
    level: int = 1


@dataclass
class SideState:
    formations: list[Formation]
    unlocked_unit_ids: set[int]
    tech_investment: dict[int, int]


@dataclass
class Evaluation:
    side_a: float
    side_b: float
    fold_std: float
    recommendations: dict[str, dict[str, list[dict[str, object]]]]


class Ensemble:
    def __init__(self) -> None:
        self.models = []
        self.version_warning: str | None = None
        try:
            import sklearn
            if sklearn.__version__ != EXPECTED_SKLEARN:
                self.version_warning = f"scikit-learn {sklearn.__version__} loaded; expected {EXPECTED_SKLEARN}"
        except Exception as exc:
            raise ModelLoadError(f"cannot import scikit-learn: {exc}") from exc
        for path in sorted(MODEL_DIR.glob(f"{MODEL_NAME}_fold*.joblib")):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    bundle = joblib.load(path)
            except Exception as exc:
                raise ModelLoadError(f"cannot load {path.name}: {exc}") from exc
            model = bundle.get("model")
            if model is None or getattr(model, "coef_", np.empty((0, 0))).shape != (1, EXPECTED_FEATURE_DIM):
                raise ModelLoadError(f"{path.name} does not contain a {EXPECTED_FEATURE_DIM}-feature logistic model")
            self.models.append(model)
        if len(self.models) != 3:
            raise ModelLoadError(f"expected 3 {MODEL_NAME} folds, found {len(self.models)}")

    def probabilities(self, feature: np.ndarray, temperature: float = 1.0) -> np.ndarray:
        if temperature <= 0:
            raise ValueError("temperature must be positive")
        values = np.asarray([
            self._sigmoid(float(model.decision_function(feature.reshape(1, -1))[0]) / temperature)
            for model in self.models
        ], dtype=np.float64)
        return values

    @staticmethod
    def _sigmoid(value: float) -> float:
        if value >= 0:
            z = np.exp(-value)
            return float(1.0 / (1.0 + z))
        z = np.exp(value)
        return float(z / (1.0 + z))


class Simulator:
    def __init__(self, ensemble: Ensemble | None = None) -> None:
        self.ensemble = ensemble or Ensemble()

    @staticmethod
    def _spatial(side: SideState, extra: tuple[int, int, float] | None = None) -> np.ndarray:
        """Return [probe, model-unit-axis] capital for one side."""
        value = np.zeros((3, K), dtype=np.float64)
        counts: dict[int, int] = {}
        for formation in side.formations:
            counts[formation.unit_id] = counts.get(formation.unit_id, 0) + 1
        for formation in side.formations:
            unit = UNIT_BY_ID[formation.unit_id]
            n = counts[formation.unit_id]
            upgrade_cost = unit.upgrade_cost_per_level or 0
            capital = (
                float(unit.base_buy_cost)
                + float(max(0, formation.level - 1) * upgrade_cost)
                + float(side.tech_investment.get(formation.unit_id, 0) + unit.unlock_cost) / n
            )
            x = LANE_X[formation.lane]
            distances = np.sqrt((PROBES[:, 0] - x) ** 2 + (PROBES[:, 1] - 200.0) ** 2)
            weights = np.power(2.0, -distances / 150.0)
            weights /= weights.sum()
            value[:, unit.axis] += capital * weights
        if extra is not None:
            unit_id, lane, amount = extra
            unit = UNIT_BY_ID[unit_id]
            x = LANE_X[lane]
            distances = np.sqrt((PROBES[:, 0] - x) ** 2 + 200.0**2)
            weights = np.power(2.0, -distances / 150.0)
            weights /= weights.sum()
            value[:, unit.axis] += amount * weights
        return value

    @classmethod
    def feature(cls, side_a: SideState, side_b: SideState, extra: tuple[str, int, int, float] | None = None) -> np.ndarray:
        a_extra = None
        b_extra = None
        if extra:
            side, unit_id, lane, amount = extra
            if side == "a":
                a_extra = (unit_id, lane, amount)
            else:
                b_extra = (unit_id, lane, amount)
        a = cls._spatial(side_a, a_extra)
        b = cls._spatial(side_b, b_extra)
        total_a, total_b = float(a.sum()), float(b.sum())
        if total_a <= 0 or total_b <= 0:
            raise ValueError("empty_board_side")
        denominator = 2.0 * total_a * total_b / (total_a + total_b)
        an, bn = a / denominator, b / denominator
        ag, bg = an.sum(axis=0), bn.sum(axis=0)
        interaction = np.einsum("mk,ml->kl", an, bn).reshape(-1)
        # Buff and battle-skill blocks are intentionally zero in this MVP.
        feature = np.concatenate((ag, bg, interaction, np.zeros(4 * K), np.zeros(4 * SKILLS * K)))
        if feature.shape != (EXPECTED_FEATURE_DIM,):
            raise AssertionError(f"feature shape {feature.shape} != {(EXPECTED_FEATURE_DIM,)}")
        return feature.astype(np.float32)

    def evaluate(self, side_a: SideState, side_b: SideState, temperature: float = 1.0) -> Evaluation:
        baseline = self.ensemble.probabilities(self.feature(side_a, side_b), temperature)
        recommendations: dict[str, dict[str, list[dict[str, object]]]] = {"side_a": {}, "side_b": {}}
        for side_name, side in (("side_a", side_a), ("side_b", side_b)):
            recommendations[side_name] = {}
            for lane, lane_name in LANE_NAMES.items():
                candidates = []
                for unit in UNIT_BY_ID.values():
                    extra = ("a" if side_name == "side_a" else "b", unit.unit_id, lane, 100.0)
                    candidate = self.ensemble.probabilities(self.feature(side_a, side_b, extra), temperature)
                    score = float((candidate - baseline).mean() if side_name == "side_a" else (baseline - candidate).mean())
                    candidates.append({
                        "unit_id": unit.unit_id,
                        "name_cn": unit.name_cn,
                        "score": score,
                        "score_percent": score * 100.0,
                        "unlocked": unit.unit_id in side.unlocked_unit_ids,
                        "icon_path": unit.icon_path,
                    })
                candidates.sort(key=lambda item: (-float(item["score"]), int(item["unit_id"])))
                recommendations[side_name][lane_name] = candidates[:5]
        mean = float(baseline.mean())
        return Evaluation(mean, 1.0 - mean, float(baseline.std()), recommendations)

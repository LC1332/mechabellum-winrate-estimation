from __future__ import annotations

"""Generate the offline best-partner report for the current logistic ensemble."""

from dataclasses import dataclass
from pathlib import Path
import sys
import warnings
from typing import Any, Iterable, Sequence

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.catalog import UNITS, Unit  # noqa: E402


MODEL_DIR = ROOT / "models" / "logistic_battle_skill_v2"
MODEL_NAME = "full-6-7"
MODEL_GLOB = f"{MODEL_NAME}_fold*.joblib"
REPORT_PATH = ROOT / "information" / "best_partner_report.md"
K = 43
SKILLS = 24
FEATURE_DIM = 2 * K + K * K + 4 * K + 4 * SKILLS * K
TIE_THRESHOLD = 0.005
SPATIAL_PROBE_COUNT = 3


@dataclass(frozen=True)
class PartnerResult:
    fixed: Unit
    partner: Unit
    maximin_probability: float
    counter: Unit


def build_feature(fixed_axis: int, partner_axis: int, opponent_axis: int) -> np.ndarray:
    """Build the 6235-feature abstract equal-economy scenario.

    The first two blocks are global normalized investments.  The interaction
    block is the three-probe sum under a uniform spatial split, i.e. their outer
    product divided by three.  Buff and skill dimensions are zero in this
    offline scenario.
    """
    self_global = np.zeros(K, dtype=np.float32)
    opponent_global = np.zeros(K, dtype=np.float32)
    self_global[fixed_axis] += 0.5
    self_global[partner_axis] += 0.5
    opponent_global[opponent_axis] = 1.0
    # The trained interaction block is a sum over three spatial probes.  For
    # this report's uniform three-lane assumption, each global capital share is
    # split evenly across the probes, so the interaction is outer/3.
    interaction = (np.outer(self_global, opponent_global) / SPATIAL_PROBE_COUNT).reshape(-1)
    feature = np.concatenate(
        (
            self_global,
            opponent_global,
            interaction,
            np.zeros(4 * K, dtype=np.float32),
            np.zeros(4 * SKILLS * K, dtype=np.float32),
        )
    ).astype(np.float32, copy=False)
    if feature.shape != (FEATURE_DIM,):
        raise AssertionError(f"feature shape {feature.shape} != {(FEATURE_DIM,)}")
    return feature


def _positive_probability(model: Any, feature: np.ndarray) -> float:
    probability = np.asarray(model.predict_proba(feature.reshape(1, -1)), dtype=np.float64)
    if probability.shape != (1, 2):
        raise ValueError(f"model returned probability shape {probability.shape}, expected (1, 2)")
    return float(probability[0, 1])


def ensemble_probability(models: Sequence[Any], feature: np.ndarray) -> float:
    if not models:
        raise ValueError("at least one model is required")
    values = np.asarray([_positive_probability(model, feature) for model in models], dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("model returned a non-finite probability")
    return float(values.mean())


def analyze_best_partners(
    models: Sequence[Any], units: Sequence[Unit] = UNITS
) -> tuple[list[PartnerResult], dict[int, list[PartnerResult]]]:
    """Return selected rows and all ranked candidates grouped by fixed unit."""
    if len(units) < 2:
        raise ValueError("at least two units are required")
    if len({unit.unit_id for unit in units}) != len(units):
        raise ValueError("unit IDs must be unique")
    if len({unit.axis for unit in units}) != len(units):
        raise ValueError("unit axes must be unique")

    ranked_by_fixed: dict[int, list[PartnerResult]] = {}
    selected: list[PartnerResult] = []
    for fixed in units:
        candidates: list[PartnerResult] = []
        for partner in units:
            if partner.unit_id == fixed.unit_id:
                continue
            opponent_scores = []
            for opponent in units:
                feature = build_feature(fixed.axis, partner.axis, opponent.axis)
                probability = ensemble_probability(models, feature)
                opponent_scores.append((probability, opponent.unit_id, opponent))
            worst_probability, _opponent_id, counter = min(opponent_scores, key=lambda item: (item[0], item[1]))
            candidates.append(PartnerResult(fixed, partner, worst_probability, counter))
        candidates.sort(key=lambda item: (-item.maximin_probability, item.partner.unit_id))
        ranked_by_fixed[fixed.unit_id] = candidates
        best_probability = candidates[0].maximin_probability
        selected.extend(
            item
            for item in candidates[:3]
            if best_probability - item.maximin_probability <= TIE_THRESHOLD + 1e-12
        )
    return selected, ranked_by_fixed


def load_models(model_dir: Path = MODEL_DIR) -> list[Any]:
    paths = sorted(model_dir.glob(MODEL_GLOB))
    if len(paths) != 3:
        raise RuntimeError(f"expected 3 {MODEL_NAME} folds in {model_dir}, found {len(paths)}")
    models: list[Any] = []
    for path in paths:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bundle = joblib.load(path)
        model = bundle.get("model") if isinstance(bundle, dict) else None
        if model is None or getattr(model, "coef_", np.empty((0, 0))).shape != (1, FEATURE_DIM):
            raise RuntimeError(f"{path.name} does not contain a {FEATURE_DIM}-feature logistic model")
        models.append(model)
    return models


def _report_lines(
    selected: Iterable[PartnerResult],
    units: Sequence[Unit],
    model_count: int,
) -> list[str]:
    rows_by_fixed: dict[int, list[PartnerResult]] = {unit.unit_id: [] for unit in units}
    for result in selected:
        rows_by_fixed[result.fixed.unit_id].append(result)

    lines = [
        "# 最佳搭档分析报告",
        "",
        "## 模型与计算口径",
        "",
        f"- 模型：`{MODEL_NAME}` Logistic 三折集成；实际加载 {model_count} 个 fold。",
        f"- 特征维度：{FEATURE_DIM}；使用完整模型预测，包括截距、双方兵种主效应和兵种交互项。",
        "- 双方总经济相同；我方固定兵种和搭档各占 50%，对方将全部经济投入到一种兵种。",
        "- 假设兵种经济投入均匀分布在 3 路；交互特征按三路探针求和，因此使用全局外积除以 3；Buff 与战场技能特征置零。",
        "- 使用原始模型概率（温度 T=1），三折概率先取均值，再对对手兵种取最小值。",
        f"- 每个固定兵种最多展示排名前三的搭档；与第一名相差不超过 {TIE_THRESHOLD * 100:.1f} 个百分点（含边界）时一并展示。",
        f"- 兵种范围：当前商店的 {len(units)} 个兵种；排除丧钟、预留槽和未知单位。",
        "",
        "## 结果",
        "",
        "| 兵种 | 最佳搭档 | 最大最小胜率 | 对方最克制的兵种 |",
        "| --- | --- | ---: | --- |",
    ]
    for fixed in units:
        for result in rows_by_fixed[fixed.unit_id]:
            lines.append(
                f"| {result.fixed.name_cn} | {result.partner.name_cn} | "
                f"{result.maximin_probability * 100:.2f}% | {result.counter.name_cn} |"
            )
    lines.extend(
        [
            "",
            "## 解释限制",
            "",
            "上述配置是对模型输入空间的极端合成场景，可能明显偏离训练数据分布。原始概率可能非常低，结果只适合作为相对搭档排序依据，不应解释为经过校准的真实对局胜率。",
            "",
        ]
    )
    return lines


def write_report(
    path: Path = REPORT_PATH,
    models: Sequence[Any] | None = None,
    units: Sequence[Unit] = UNITS,
) -> Path:
    loaded_models = list(models) if models is not None else load_models()
    selected, _ranked = analyze_best_partners(loaded_models, units)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_report_lines(selected, units, len(loaded_models))), encoding="utf-8")
    return path


def main() -> None:
    path = write_report()
    print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()

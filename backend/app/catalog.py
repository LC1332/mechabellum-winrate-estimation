from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Unit:
    unit_id: int
    name_cn: str
    name_en: str
    axis: int
    unlock_cost: int
    base_buy_cost: int
    upgrade_cost_per_level: int | None
    unlock_tier: int

    @property
    def icon_path(self) -> str:
        return f"/assets/units/{self.unit_id}.png"


def _load_units() -> tuple[Unit, ...]:
    metadata = json.loads((ROOT / "data/logistic_battle_skill_v2.json").read_text(encoding="utf-8"))
    axes = {int(item["unit_id"]): item for item in metadata["unit_axis"] if item.get("unit_id") is not None}
    costs = json.loads((ROOT / "reference_code/unit_cost_table.json").read_text(encoding="utf-8"))["units"]
    # This is the first-front shop: the 32 units with a confirmed unlock tier.
    tiers = {
        0: (10, 9, 28, 30, 15, 2, 31, 7, 8, 13, 20, 21, 24),
        50: (12, 5, 6, 25, 16, 14, 26, 19, 22, 18),
        200: (3, 4, 1, 23, 27, 11),
        350: (17, 2002, 29),
    }
    result: list[Unit] = []
    for tier, ids in tiers.items():
        for uid in ids:
            axis = axes.get(uid)
            cost = costs.get(str(uid))
            if not axis or not cost:
                raise RuntimeError(f"catalog unit {uid} is missing from model/cost metadata")
            result.append(Unit(
                unit_id=uid,
                name_cn=axis["name_cn"],
                name_en=axis["name_en"],
                axis=int(axis["index"]),
                unlock_cost=int(cost["unlock_cost"]),
                base_buy_cost=int(cost["base_buy_cost"]),
                upgrade_cost_per_level=(
                    int(cost["upgrade_cost_per_level"])
                    if cost.get("upgrade_cost_per_level") is not None else None
                ),
                unlock_tier=tier,
            ))
    return tuple(result)


UNITS = _load_units()
UNIT_BY_ID = {unit.unit_id: unit for unit in UNITS}


def public_catalog() -> list[dict[str, object]]:
    return [asdict(unit) | {"icon_path": unit.icon_path} for unit in UNITS]

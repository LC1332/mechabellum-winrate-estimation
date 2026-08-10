# Mechabellum Dense Dataset v1

配套文件：

- `mechabellum_dense_v1.npz`：训练使用的压缩数值数组。
- `mechabellum_dense_v1.json`：数组 schema、兵种维度、回放来源清单、跳过原因和质检统计。

当前数据集含 962 局连续回放：878 份 1v1、84 份 2v2。固定张量维度为 `[match, round, side, unit] = [962, 18, 2, 43]`；不足 18 回合的部分由 `round_mask` 标记为 padding。round 0 是准备态，不在数据集中；round 1 已包含开局编队投资。

## NPZ arrays

| 字段 | dtype / shape | 说明 |
| --- | --- | --- |
| `investment_delta` | `float32 [N,18,2,43]` | 每回合各兵种净投资；出售为负数。 |
| `investment_cumulative` | `float32 [N,18,2,43]` | 截至该回合的累计净投资。 |
| `investment_final` | `float32 [N,2,43]` | 每局最后有效回合的累计投资。 |
| `investment_source` | `uint8 [N,18,2]` | `0=padding`、`1=相邻快照确认`、`2=末回合动作日志回退`。 |
| `round_winner` | `int8 [N,18]` | `-1=null`、`0=左方`、`1=右方`。padding 同为 `-1`，以 `round_mask` 区分。 |
| `round_outcome_type` | `uint8 [N,18]` | `0=null`、`1=正常战斗`、`2=投降`。 |
| `winner_damage` | `float32 [N,18]` | 胜方对败方造成的扣血；2v2 为败方两名队员掉血的平均。 |
| `damage_valid` | `bool [N,18]` | 是否可确认本回合扣血；末回合无后继快照时为 `false`。 |
| `initial_health` | `float32 [N,2]` | 开局 `MaxReactorCore`；2v2 为队内平均。 |
| `round_mask` | `bool [N,18]` | 有效战斗回合。 |
| `round_count` | `uint8 [N]` | 每局有效战斗回合数。 |
| `match_mode` | `uint8 [N]` | `1=VS_1_1`、`2=VS_2_2`。 |

兵种轴的前 33 维是当前普通兵种，后 10 维为预留槽。完整 index-to-unit 映射在 JSON 的 `unit_axis`；当前特殊单位 ID `4001` 使用 `unknown_unit_slot_0`。

## 标签与 2v2 规则

正常战斗胜负由下一快照的 `Win/Lose` 确认。`Deuce`、冲突记录和无后继快照均为 `null`。投降优先于普通战斗结果，且扣血为 0。2v2 的左/右方固定为玩家 `[0,1]` 与 `[2,3]`，投资、扣血和开局血量均取队内平均。

无法定价的实验特殊单位科技按 0 成本计入；本版数据中共有 19 个该类动作。非连续回放不导出，完整清单见 JSON 的 `skipped`。

## Python loading

```python
import json
import numpy as np

with np.load("data/mechabellum_dense_v1.npz", allow_pickle=False) as dataset:
    delta = dataset["investment_delta"]
    mask = dataset["round_mask"]

meta = json.loads(open("data/mechabellum_dense_v1.json", encoding="utf-8").read())
```

请将 NPZ 与 JSON 视为一个整体使用：NPZ 只含数值，JSON 定义了兵种维度与每一行对应的回放文件。

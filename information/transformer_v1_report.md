# Transformer v1 训练报告

## 数据与协议

- 数据：962 局；切分 train/validation/test = 770/96/96。
- 终局未知的 933 局按 0 处理；正常战斗为 ±100，投降为 ±200，折扣因子为 0.5。
- 实际设备：`cuda`；PyTorch `2.6.0+cu124`，CUDA 可用：`True`。

## 深度选择

### Q

| 层数 | 验证集 match-balanced RMSE（3 seeds） | 标准差 |
| ---: | ---: | ---: |
| 3 | 114.732 | 0.325 |
| 2 | 114.772 | 0.233 |
| 4 | 114.910 | 0.387 |

选中 depth=3、seed=20260810、epoch=14。

测试集：RMSE 107.121，MAE 90.015，R² 0.018，Pearson 0.138。
按回合均值基线测试 RMSE：108.106。

### V

| 层数 | 验证集 match-balanced RMSE（3 seeds） | 标准差 |
| ---: | ---: | ---: |
| 2 | 114.721 | 0.590 |
| 3 | 114.778 | 0.573 |
| 4 | 115.111 | 0.599 |

选中 depth=2、seed=20260810、epoch=19。

测试集：RMSE 106.034，MAE 89.176，R² 0.038，Pearson 0.199。
按回合均值基线测试 RMSE：108.106。

## 产物

- `models/transformer_v1/q_best.pt` 与 `models/transformer_v1/v_best.pt`：最终模型。
- `artifacts/transformer_v1/q_gt_vs_pred_test.jpg` 与 `artifacts/transformer_v1/v_gt_vs_pred_test.jpg`：测试散点图。
- `artifacts/transformer_v1/split_v1.json`：Demo 必须复用的固定切分。

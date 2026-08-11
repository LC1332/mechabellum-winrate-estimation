整体使用transformer的拟合看起来非常random
我们来尝试一个logistic拟合的方案

我们假设兵种之间存在一定的克制

这个时候每回合

f_ij = 我方兵种 i 的总投资/我方部队总价值 * 对方在兵种 j 的总投资/对方兵种总价值

这个时候用所有的F（也就是兵种总数平方维）
进行一个logistic拟合。

拟合当回合是否胜利。

看看这个的效果如何

如果显著好于乱猜

也可以试试以 beta = 0.3时候的 累积reward（终局以100计算 不以100计算）

以累积reward/最大可能绝对值（在正负一之间）作为logistic回归的目标

## 实验结果（Logistic v1）

已完成三种特征消融：双方阵容主效应（86 维）、全部兵种两两交互 F（1849 维）以及二者组合（1935 维）；每种分别用于单回合胜负和 beta=0.3 的软标签累计 reward，均复用 Transformer v1 固定 770/96/96 对局切分。

- 单回合胜负：interaction 的测试 AUC 为 0.5898，cluster bootstrap 相对随机 AUC 的 95% CI 为 `[+0.0414, +0.1372]`，说明排序能力显著高于乱猜；但 log-loss 改善 CI 略跨 0，interaction 相对 main 的改善也不显著。
- 累计 reward：combined 的测试软交叉熵为 0.6860，虽在该指标上相对随机显著改善，但原始 reward RMSE 的改善不显著；验证集选择的正式模型为 interaction。
- 因此，当前数据支持“阵容/克制信息有弱的单回合排序信号”，但不足以证明两两克制项本身带来显著、稳定的额外收益。

完整的指标、系数表、散点/ROC/校准图见 `information/logistic_v1_report.md`；模型位于 `models/logistic_v1/`，运行命令：

```bash
python3 reference_code/train_logistic.py run --config configs/logistic_v1.yaml
```

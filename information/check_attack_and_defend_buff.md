这个游戏存在全兵种的攻击和血量的buff

基地可以花费100购买
攻击加10%
或者
血量+10%

解锁之后可以花费300继续购买后续升级

另外全局也可以花费400购买一个大约在30%的全局buff

这些全局buff是线性叠加的

另外当开局是 一个特定的专家时

也会获得一个攻击 & 血量的减益buff（大约是-11%）

帮我看看游戏log里面会不会记录这个
甚至是记录所有buff计算之后 当回合所有兵种最终的一个攻击/血量的buff
（还是说都是分开记录的）

调查后以md的形式写回这个报告

## 回放字段审计结果（2026-08-12）

全量扫描 `local_data/humen_replay` 中与 dense v1 对应的 962 局后，确认回放可以分别恢复全局 buff 的来源；没有发现一个已经合并好的“最终攻击/生命倍率”字段。

- 起始专家和已获得的全局卡牌记录在每回合 `playerData/officers/int`。本实验使用 `20034=Cost Control Specialist`、`20035=Heavy Armor Specialist`、`20002=Advanced Offensive Tactics`、`20001=Advanced Defensive Tactics`。
- 研究塔当回合的激活记录在 `actionRecords/MatchActionData`，类型为 `PAD_ActiveEnergyTowerSkill`。`SkillID`（旧格式也兼容 `ID`）为 `4/401` 时表示攻击 I/II，`5/501` 时表示生命 I/II；动作先执行 `PAD_Undo` 过滤。
- `towerStrengthenLevels` 是塔的等级状态，可用于交叉检查，但不能单独证明本回合已经激活 buff，因此不把它直接当成当回合增益。
- 单位最终攻击/生命数值没有在 `NewUnitData` 中以合并倍率保存；空间策略仍从 `NewUnitData/Position/x,y` 读取位置。

本次建模按需求把增益作为线性增量：Cost Control 攻击/生命 `-0.11`，Heavy Armor 生命 `+0.17`，研究塔最高档攻击/生命 `+0.10/+0.24`，先进攻防战术卡分别 `+0.30`。全量语料中的记录计数已写入 `data/logistic_strategy_v1.json` 的 `statistics.qc`。

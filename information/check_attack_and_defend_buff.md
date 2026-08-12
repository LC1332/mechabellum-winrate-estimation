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


# 攻击/生命全局强化在回放日志中的记录调查

## 结论

回放确实记录了这类强化，但记录的是**购买动作和两条强化线的等级**，不是“所有 buff 计算完成后，单位最终攻击/生命加成是多少”。因此：

- 可以从回放还原某位玩家在某回合开始时购买了几级攻击强化、几级防御/生命强化。
- 可以从 `officers` 还原玩家选择了哪个开局专家，包括 Cost Control Specialist。
- 不能直接从现有 `.grbr` 日志读取单位的最终攻击、最终最大生命，或一个合并后的最终 buff 百分比。
- 如果要得到最终数值，必须使用与该回放版本匹配的基础兵种数值、科技/装备效果和专家效果，再自行计算。

## 调查范围与文件格式

本地 `local_data/humen_replay` 下共有 3,459 个 `.grbr` 回放文件；其中当前格式的主目录样本为 1,106 场。`.grbr` 是 BinaryFormatter 外壳包裹 XML，XML 主体可用以下边界提取：

```python
start = data.find(b"<?xml")
end = data.rfind(b"BattleRecord>") + len(b"BattleRecord>")
xml_text = data[start:end].decode("utf-8")
```

本次用 XML 中的 `PlayerRecord/playerRoundRecords/PlayerRoundRecord`、`playerData` 和 `actionRecords/MatchActionData` 进行检查。

## 研究中心强化：日志可以记录购买和等级

购买强化的动作类型是 `PAD_StrengthenTower`。它的字段只有时间和一个索引，例如：

```xml
<MatchActionData xsi:type="PAD_StrengthenTower">
  <Time>0</Time>
  <LocalTime>25.05</LocalTime>
  <Index>1</Index>
</MatchActionData>
```

本地回放中共找到 5,892 个 `PAD_StrengthenTower` 动作，`Index` 为 0 和 1 两类。结合研究中心的升级顺序，索引可按下表解释：

| 日志索引 | 强化线 | 作用 |
| ---: | --- | --- |
| `0` | Attack Enhancement | 全部单位攻击力强化 |
| `1` | Defense Enhancement | 全部单位生命值强化 |

每个回合的 `playerData` 还保存：

```xml
<towerStrengthenLevels>
  <int>2</int>
  <int>1</int>
</towerStrengthenLevels>
```

两个 `<int>` 与 `PAD_StrengthenTower/Index` 一一对应，因此这个字段比单纯扫描动作更适合作为每回合状态：

```text
towerStrengthenLevels[0] = 攻击强化等级
towerStrengthenLevels[1] = 生命强化等级
```

回放时序也能由样本验证：在 `2119_20260710--201334120_[宇宙霸主ultra Pro]VS[Mark二].grbr` 中，同一玩家在第 3 回合出现两次 `PAD_StrengthenTower` 且 `Index=0`；第 4 回合快照变为 `[2, 0]`。也就是说，动作记录发生在购买回合，等级快照体现为后续回合的状态。

## 费用和百分比不是日志字段

`PAD_StrengthenTower` 只有 `Index`，没有 `Cost`、`SkillID`、`Modifier`、`AttackPercent` 或 `HealthPercent`。`towerStrengthenLevels` 也只有整数等级，没有该等级对应的百分比。

当前资料中的研究中心数值为：Attack Enhancement I/II = 100/300 费用、攻击力 +12%/+24%；Defense Enhancement I/II = 100/300 费用、生命值 +15%/+30%。这些数值会随游戏版本调整，不能把它们硬编码为所有回放版本的事实。可参考 [Research Center](https://wiki.mbxmas.com/buildings/research-center/)。

因此，报告需求中“+10%”和“约 30%”更准确的表达应是：**日志给等级，效果百分比需要由版本配置/外部资料补充**。如果要做计算器，应按回放的 `<Version>`/游戏版本选择常数，而不是仅凭日志字段猜测。

## 开局专家：能记录身份，但不记录专家修正值

专家以 ID 列表出现在每回合快照的：

```xml
<officers>
  <int>20034</int>
</officers>
```

现有查找表中 `20034` 是 Cost Control Specialist。全量样本中 `<int>20034</int>` 出现 3,719 次，涉及 502 个回放文件，说明这个专家身份可以可靠识别。

Cost Control Specialist 的“所有单位攻击力和生命值降低约 11%”是专家效果说明，不是回放 XML 中的数值字段；可参考 [Cost Control Specialist](https://wiki.mbxmas.com/specialists/cost-control-specialist/)。因此日志能回答“是否选择了这个专家”，不能单独回答“该专家最终对每个单位施加了多少修正”。

## 是否记录了每个单位的最终攻击/生命

没有发现这种记录。回合单位快照 `units/NewUnitData` 的字段主要是：

```xml
<NewUnitData>
  <id>10</id>
  <Index>0</Index>
  <RoundCount>0</RoundCount>
  <Durability>0</Durability>
  <Exp>0</Exp>
  <Level>0</Level>
  <Position>...</Position>
  <EquipmentID>0</EquipmentID>
  <IsRotate>false</IsRotate>
  <SellSupply>100</SellSupply>
</NewUnitData>
```

这里有单位 ID、等级、装备、位置和耐久相关状态，但没有 `BaseAttack`、`Attack`、`MaxHealth`、`HealthModifier` 或最终计算结果。玩家级别的 `data/MaxReactorCore` 是基地核心生命，不是单位生命；`data/unitDatas/unitData` 主要保存单位科技 ID，也不是运行时属性快照。

## 其他容易混淆的记录

回放中还有大量 `PAD_ActiveEnergyTowerSkill`，其字段是 `SkillID`，主要对应每回合使用的塔/指挥技能。它不等于研究中心两条永久攻击/生命强化线，不能用它替代 `PAD_StrengthenTower` 和 `towerStrengthenLevels`。

## 推荐的重建方法

对每个玩家、每个回合：

1. 读取该回合开始时的 `towerStrengthenLevels`，得到攻击等级 `a` 和生命等级 `d`。
2. 从 `officers/int` 读取专家 ID；若为 `20034`，按对应版本加入 Cost Control Specialist 的攻击和生命惩罚。
3. 从 `activeTechnologies/UnitData`、`data/unitDatas` 和 `NewUnitData/EquipmentID` 收集单位科技、等级和装备。
4. 从与回放版本一致的静态数据表取得基础攻击/生命及各科技、装备、专家的修正。
5. 按游戏实际规则合并修正，输出每个单位的最终攻击和最大生命。

第 1、2 步可以仅依靠回放完成；第 3 步大部分可以依靠回放完成；第 4、5 步不能仅靠当前日志完成。特别是“增益线性叠加、减益如何与增益组合”的具体运算顺序，也没有存储在回放中，需要用版本匹配的游戏资料或控制变量实验验证。

## 最终判断

```text
研究中心购买动作       有：PAD_StrengthenTower(Index)
研究中心当前等级         有：towerStrengthenLevels[2]
开局专家身份             有：officers/int
专家攻击/生命百分比       无：需按专家 ID 和版本表补充
单位最终攻击/最大生命     无：需自行重建
所有 buff 合并后的总百分比 无：需自行计算，不能直接从 XML 读取
```


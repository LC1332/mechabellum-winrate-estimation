# Mechabellum 回放文件格式完整说明

> **分析结合：**
> - 本地文件样本：`ProjectDatas/Replay/1.11.1.2.2227/` 目录下的 `.bak` + `.grbr` 文件
> - 开源项目：[ShotgunCrocodile/mechabellum_replay_parser](https://github.com/ShotgunCrocodile/mechabellum_replay_parser)（Python 解析器）
> - 游戏版本：`1.11.1.2.2227`

---

## 一、概述

Mechabellum 为每场对局生成**一对同名文件**（如 `7671737641439594724.rep`），后缀分别为：

| 文件 | 格式 | 典型大小 | 说明 |
|------|------|----------|------|
| `.rep.grbr` | BinaryFormatter + 内嵌 XML | ~315 KB | **主回放文件**，游戏加载回放时读取。解析该项目的主要目标。 |
| `.rep.bak` | Protocol Buffers (protobuf) | ~31 KB | **备份/紧凑格式**，体积约为 grbr 的 1/10。内容等价。 |

---

## 二、`.rep.grbr` 文件格式

### 2.1 整体结构

```
┌─────────────────────────────────────────────────┐
│  .NET BinaryFormatter 序列化头 (390 bytes)        │  offset 0x000 ~ 0x185
│  ────────────────────────────────────────        │
│  类型元数据：GameRiver.Replay, Version=0.0.0.0   │
│  成员列表：7 个字段                                │
│  程序集信息：GRCore, Version=0.0.0.0              │
├─────────────────────────────────────────────────┤
│  UTF-8 BOM: EF BB BF                             │  offset 0x183
├─────────────────────────────────────────────────┤
│  XML 文档主体 (~314 KB)                           │  offset 0x186 ~ 0x4CD0F
│  <?xml ...><BattleRecord>                        │
│    <playerRecords>                               │
│      <PlayerRecord> ... </PlayerRecord>   (×2)   │
│    </playerRecords>                              │
│    <matchDatas>                                  │
│      <MatchSnapshotData> ... </>          (×N)   │
│    </matchDatas>                                 │
│  </BattleRecord>                                 │
├─────────────────────────────────────────────────┤
│  .NET BinaryFormatter 尾部 (~380 bytes)           │  offset 0x4CD10 ~ EOF
│  List<PlayerRecord> 序列化收尾                     │
└─────────────────────────────────────────────────┘
```

### 2.2 提取 XML 的方法

```python
def extract_xml(file_path):
    with open(file_path, "rb") as f:
        content = f.read()
    start = content.find(b"<?xml")
    # 用 "BattleRecord>" 作为结束标记，避免玩家名中的 ">" 误判
    end = content.rfind(b"BattleRecord>") + len(b"BattleRecord>")
    return content[start:end].decode("utf-8")
```

关键点：
- XML 以 `<?xml` 开头
- 以 `BattleRecord>` 结束（搜索最后一个，用 `rfind`）
- 跳过前后的 BinaryFormatter 元数据即可获得纯净 XML

### 2.3 BinaryFormatter 头部字节布局

| 偏移 | 十六进制 | 含义 |
|------|----------|------|
| `0x00` | `00 01 00 00 00` | SerializedStreamHeader (version 1.0) |
| `0x05` | `FF FF FF FF` | TopId = -1 (根对象) |
| `0x09` | `01 00 00 00 00 00 00 00` | HeaderId |
| `0x11` | `0C` | BinaryLibrary 记录 |
| `0x12` | `02 00 00 00` | LibraryId = 2 |
| `0x16` | `3D` | 字符串长度 = 61 |
| `0x17` | `GRCore, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null` | 程序集全名 |
| `0x54` | `05` | ClassWithMembers 记录 |
| `0x55` | `01 00 00 00` | ObjectId = 1 |
| `0x5A` | `GameRiver.Replay` | 类型名 |

### 2.4 XML 文档结构

根元素：`<BattleRecord xmlns:xsi="..." xmlns:xsd="...">`

```xml
<BattleRecord>
  <version>11571</version>
  <playerRecords>
    <PlayerRecord xsi:type="PlayerRecord">
      <m_version>1571</m_version>
      <m_id>1或2</m_id>
      <m_name>玩家名</m_name>
      <data> ... </data>                          <!-- 玩家基础数据 -->
      <playerRoundRecords>                        <!-- 每回合记录 -->
        <PlayerRoundRecord>
          <index>回合号</index>
          <playerData> ... </playerData>          <!-- 回合初状态 -->
          <actionRecords>
            <MatchActionData xsi:type="xxx">     <!-- 该回合所有操作 -->
              <ID>动作类型ID</ID>
              ...
            </MatchActionData>
          </actionRecords>
        </PlayerRoundRecord>
        ...
      </playerRoundRecords>
    </PlayerRecord>
  </playerRecords>
  <matchDatas>
    <MatchSnapshotData>                          <!-- 每回合快照 -->
      <round>回合号</round>
      <poolOPs> ... </poolOPs>                   <!-- 池中可选操作 -->
      <teamRanks> ... </teamRanks>               <!-- 排位信息 -->
      ...
    </MatchSnapshotData>
  </matchDatas>
  <unitReinforceRounds>                          <!-- 增援轮次列表 -->
    <int>3</int><int>5</int><int>7</int>...
  </unitReinforceRounds>
</BattleRecord>
```

### 2.5 MatchActionData 动作类型

根据 `xsi:type` 属性区分，开源项目定义了以下动作类型：

| xsi:type | 含义 | 关键字段 |
|-----------|------|----------|
| `BuyUnitData` | 购买单位 | unitID, sourceBattalion, gridX, gridY |
| `UnlockUnitData` | 解锁单位 | unitID |
| `UpgradeUnitData` | 单位升级 | localeID, level |
| `NewSkillData` | 研究科技 | unitID, skillID |
| `ResearchCenterTowerData` | 研究塔技能 | skillID |
| `CommandCenterTowerData` | 指挥塔技能 | skillID |
| `DeviceShortData` | 使用装置 | contraptionID |
| `MoveUnitData` | 移动单位 | localeID, sourceGridX/Y, targetGridX/Y |
| `SellUnitData` | 出售单位 | localeID |
| `NewUnitData` | 通过增援获得单位 | unitID, level, gridX, gridY |
| `CardData` | 选择卡牌 | cardID |
| `DeploySkillShortData` | 使用技能 | skillID, gridX, gridY |
| `UnitDeleteData` | 单位被移除 | localeID |

---

## 三、`.rep.bak` 文件格式

### 3.1 格式

纯 Protocol Buffers 二进制流，无封装头。

### 3.2 解码结构

| 字段号 | 类型 | 含义 |
|--------|------|------|
| field 1 | varint | battleID? |
| field 2 | string | 版本号 (如 `1.11.1.2.2227`) |
| field 3 | varint | seat? |
| field 4 | varint | mapID? |
| field 5 | message[] | `playerDatas` — 两段嵌套 message (每段约 3KB) |
| field 6 | message | `realBattleRecordData` — 大型嵌套 message (~24KB) |

核心数据在 `field 6` (realBattleRecordData) 内，结构含多层嵌套：
- 外层含回合数据、时间戳、战斗标识
- 中层含玩家信息、每回合的单位/补给/血量快照
- 内层含具体操作记录（购买/升级/移动等）

### 3.3 .bak vs .grbr 的关系

两者包含**完全相同的对局数据**，只是序列化方式不同：
- `.grbr`：被 .NET BinaryFormatter 包裹的 XML，给游戏主程序读取
- `.bak`：protobuf 紧凑格式，作为备份/存档用途

解析回放时，选择 `.grbr` 的 XML 部分最为简便。

---

## 四、完整 ID 查找表

以下来自 [ShotgunCrocodile/mechabellum_replay_parser](https://github.com/ShotgunCrocodile/mechabellum_replay_parser)。

### 4.1 单位 (UNIT_LOOKUP)

| ID | 名称 | ID | 名称 |
|----|------|----|------|
| 1 | fortress | 16 | phoenix |
| 2 | marksmen | 17 | warfactory |
| 3 | vulcan | 18 | wraith |
| 4 | melting point | 19 | scorpion |
| 5 | rhino | 20 | fire badger |
| 6 | wasp | 21 | sabertooth |
| 7 | mustang | 22 | typhoon |
| 8 | steel ball | 23 | sandworm |
| 9 | fang | 24 | tarantula |
| 10 | crawler | 25 | phantom ray |
| 11 | overlord | 26 | farseer |
| 12 | stormcaller | 27 | raiden |
| 13 | sledgehammer | 28 | hound |
| 14 | hacker | 29 | abyss |
| 15 | arclight | | |

### 4.2 起始专家 (OFFICER_LOOKUP 摘选)

| ID | 名称 |
|----|------|
| 10002 | Supply Specialist |
| 10010 | Quick Supply Specialist |
| 10011 | Missile Specialist |
| 10013 | Amplify Specialist |
| 10014 | Training Specialist |
| 20005 | Giant Specialist |
| 20021 | Aerial Specialist |
| 20024 | Speed Specialist |
| 20029 | Marksman Specialist |
| 20032 | Elite Specialist |
| 20033 | Rhino Specialist |
| 20034 | Cost Control Specialist |
| 20035 | Heavy Armor Specialist |
| 20036 | Sabertooth Specialist |
| 20037 | Farseer Specialist |
| 20038 | Fire Badger Specialist |
| 20039 | Typhoon Specialist |

### 4.3 技能 (SKILL_LOOKUP)

| ID | 名称 |
|----|------|
| 100002 | Incendiary Bomb |
| 200001 | Electromagnetic Impact |
| 200002 | Electromagnetic Blast |
| 200003 | Photon Emission |
| 300001 | Missile Strike |
| 300003 | Orbital Bombardment |
| 300004 | Nuke |
| 300005 | Lightning Storm |
| 300006 | Ion Blast |
| 300007 | Orbital Javelin |
| 400002 | Sticky Oil Bomb Tower |
| 400003 | Sticky Oil Bomb Spell |
| 500002 | Acid Blast |
| 600002 | Smoke Bomb |
| 800001 | Shield Airdrop |
| 900001 | Field Recovery |
| 1000001 | Redeployment |
| 1100001 | Intensive Training |
| 1200001 | Underground Threat |
| 1200002 | Rhino Assault |
| 1200003 | Wasp Swarm |
| 1200004 | Mobilize Battleship |
| 1200005 | Vulcan's Descent |
| 1500001 | Mobile Beacon Tower |
| 1500002 | Mobile Beacon Spell |

### 4.4 科技 (TECH_LOOKUP) — 按单位分类

完整科技表有 120+ 条，以下为代表性条目：

**Crawler**: 10510=Mechanical rage, 180110=Replicate, 2610=Subterranean blitz, 2710=Acidic explosion, 10710=Impact drill, 3510=Loose formation

**Marksman**: 702=Doubleshot, 10202=Range enhancement, 10402=Quick reload, 1802=Electromagnetic shot, 10802=Elite marksman, 1202=Shooting squad

**Fortress**: 1001=Barrier, 10201=Range enhancement, 1105=Anti air barrage, 1201=Fang production, 10301=Launcher overload, 701=Doubleshot

**Melting Point**: 304=Energy absorption, 10204=Range enhancement, 1107=Energy diffraction, 1106=Electromagnetic barrage, 1204=Crawler production

**Phoenix**: 2916=Quantum reassembly, 10216=Range enhancement, 10316=Launcher overload, 216=Energy shield, 1616=Jump drive

**Overlord**: 1108=Overlord artillery, 10311=Launcher overload, 1211=Mothership, 1611=Jump drive, 180311=Photon emission

**Sandworm**: 10523=Mechanical rage, 3023=Armor enhancement, 13023=Mechanical division, 3123=Anti aerial, 3723=Sandstorm

**Wraith**: 110181=Floating artillery array, 10218=Range enhancement, 180418=Degeneration beam

**Abyss**: 10299=Range enhancement, 12029=Dark companion, 3429=Efficient maintenance, 11029=Disintegration, 2329=Wreckage recycling

> 完整科技表请参考 [parser `__init__.py`](https://github.com/ShotgunCrocodile/mechabellum_replay_parser/blob/main/src/mechabellum_replay_parser/__init__.py) 中的 `TECH_LOOKUP` 字典。

### 4.5 装置 (CONTRAPTION_LOOKUP)

| ID | 名称 |
|----|------|
| 10001 | Shield Generator |
| 20001 | Sentry Missile |
| 30001 | Missile Interceptor |

### 4.6 指挥塔技能 (COMMAND_TOWER_SKILLS)

| ID | 名称 |
|----|------|
| 1 | Loan |
| 3 | Mass Recruit |
| 4 | Elite Recruit |
| 5 | Enhanced Range |
| 6 | High Mobility |

### 4.7 研究塔技能 (RESEARCH_TOWER_SKILLS)

| ID | 名称 |
|----|------|
| 1 | Oil Bomb |
| 2 | Field Recovery |
| 3 | Mobile Beacon |
| 4 | Attack Enhancement |
| 5 | Defense Enhancement |
| 401 | Attack Enhancement II |
| 501 | Defense Enhancement II |

### 4.8 物品 (ITEM_LOOKUP)

| ID | 名称 |
|----|------|
| 1305003 | Photon Coating |
| 1306001 | Tank Production Line |
| 1306002 | Mustang Production Line |
| 1306003 | Steel Ball Production Line |
| 1307001 | Barrier |
| 1308001 | Anti Interference Module |
| 1309001 | Absorption Module |
| 13010001 | Portable Shield |
| 13020001 | Nano Repair Kit |
| 13030001 | Laser Sights |
| 13030002 | Heavy Armor |
| 13030003 | Improved Firepower Control System |
| 13030004 | Enhancement Module |
| 13030005 | Haste Module |
| 13030006 | Super Heavy Armor |
| 13030007 | Amplifying Core |
| 13040001 | Deployment Module |

### 4.9 卡牌 (CARD_LOOKUP)

`CARD_LOOKUP = {0: "Skip"} ∪ ITEM_LOOKUP ∪ SKILL_LOOKUP ∪ OFFICER_LOOKUP`

---

## 五、解析建议

### 5.1 推荐方式：解析 .grbr 的 XML

```python
from pathlib import Path
import xml.etree.ElementTree as ET

def parse_replay(grbr_path: Path):
    with open(grbr_path, "rb") as f:
        data = f.read()
    xml_start = data.find(b"<?xml")
    xml_end = data.rfind(b"BattleRecord>") + len(b"BattleRecord>")
    xml_text = data[xml_start:xml_end].decode("utf-8")
    return ET.fromstring(xml_text)
```

然后用标准 XML API 遍历 `PlayerRecord`、`playerRoundRecords`、`MatchSnapshotData` 等节点。

### 5.2 关键解析点

1. **获取玩家信息**：`//playerRecords/PlayerRecord/m_name` + `m_id`
2. **获取回合**：`playerRoundRecords/PlayerRoundRecord`，带 `index` 子元素
3. **获取操作**：`actionRecords/MatchActionData`，`xsi:type` 区分类型
4. **获取快照**：`matchDatas/MatchSnapshotData`，含 `poolOPs`（可选操作）
5. **增援轮次**：`unitReinforceRounds/int[]`

### 5.3 版本演进注意事项

- 从 v1571 开始，`unitReinforceRounds` 只包含已发生轮次，需从最后 snapshot 反查
- 专家单位生成规则随版本变化（`_get_special_case_unit_spawning`）

---

## 六、参考资料

- [ShotgunCrocodile/mechabellum_replay_parser](https://github.com/ShotgunCrocodile/mechabellum_replay_parser) — Python 解析器，含完整 ID 表
- [IcyIcyD/MechabellumReplayParser](https://github.com/IcyIcyD/MechabellumReplayParser) — 早期解析器，部分 ID 来源
- `.grbr` 文件本质 = .NET `BinaryFormatter.Serialize` 包裹的 `GameRiver.Replay` → XML
- 程序集：`GRCore, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null`

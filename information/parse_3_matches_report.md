# 3 个对局投入资源解析报告

> 自动生成，对应 README TODO #1：尝试解析 3 个对局的数据，打印双方各回合投入资源，供 owner 确认解析是否正确（干什么 / 花了多少钱）。

## 一、成本模型与方法

- **解锁费用 / 首购基础价**：来自回放内 `NewUnitData.SellSupply` 的「首次出现值」（数据驱动，按游戏版本准确；SellSupply 随兵种固定、不随等级变化）。
- **兵种等级费用**：来自 `UNIT_LEVEL_COST` 表，默认每级 `50`（⚠️ 待 owner 确认具体数值；SellSupply 已证实不随升级变化，故等级费用**无法**从数据推导）。
- **总科技费用**：来自 `TECH_COST` 表，默认 `50`（⚠️ 待 owner 确认）。
- **解锁费用（`PAD_UnlockUnit`）**：当前设为 `0`（⚠️ 待 owner 确认是否应等于首购基础价）。
- **交叉校验**：利用每回合剩余 supply 反推「实际花费」=`上一轮剩余 + 每回合增量 - 本轮剩余`，与上面推导成本对比（差值列）。**仅当回合序列连续时可靠**（样本均已筛选为连续回合）。
- **新兵种预留**：解析对未知兵种 ID 也照常记录，不报错（README 要求预留 10 维）。

## 二、逐回合明细（含交叉校验）

### 对局 1：`2207_20260725--134225731_[你是蓬莱花仙]VS[哈宝哈宝蛤不思饱Habsburg].grbr`

- 模式：`VS_1_1`

#### 玩家：你是蓬莱花仙

| 回合 | 剩余supply | 实际花费(反推) | 推导花费 | 差值 | 累计O |
|-----:|-----:|-----:|-----:|-----:|-----:|
| 0 | 0 | — | 0 |  | 0 |
| 1 | 0 | 200 | 200 | 0 | 200 |
| 2 | 50 | 150 | 400 | -250 | 600 |
| 3 | 100 | 150 | 400 | -250 | 1000 |
| 4 | 0 | 300 | 500 | -200 | 1500 |
| 5 | 0 | 200 | 750 | -550 | 2250 |
| 6 | 0 | 200 | 800 | -600 | 3050 |
| 7 | 0 | 200 | 850 | -650 | 3900 |
| 8 | 50 | 150 | 600 | -450 | 4500 |
| 9 | 0 | 250 | 650 | -400 | 5150 |
| 10 | 0 | 200 | 950 | -750 | 6100 |
| 11 | 0 | 200 | 1300 | -1100 | 7400 |
| 12 | 0 | 200 | 950 | -750 | 8350 |
| 13 | 0 | 200 | 1000 | -800 | 9350 |

**动作明细：**

- R0（推导 0）：
（无经济动作）
- R1（推导 200）：
解锁爬虫(crawler)(0)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：未知兵种(31), 未知兵种(31), 未知兵种(31), 大锤(sledgehammer), 大锤(sledgehammer)
- R2（推导 400）：
解锁野马(mustang)(0)；购买野马(mustang)(200)；购买未知兵种(31)(100)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：爬虫(crawler)
- R3（推导 400）：
解锁毒牙(fang)(0)；购买毒牙(fang)(100)；科技未知兵种(31)/未知科技(10231)(50)；购买未知兵种(31)(100)；购买未知兵种(31)(100)；升级大锤(sledgehammer)Lv0→Lv1(50)
  ｜ 增援(免费)：野马(mustang), 爬虫(crawler)
- R4（推导 500）：
购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；科技未知兵种(31)/未知科技(631)(50)；升级未知兵种(31)Lv0→Lv1(50)；升级未知兵种(31)Lv0→Lv1(50)；升级未知兵种(31)Lv0→Lv1(50)；解锁钢球(steel ball)(0)；购买未知兵种(31)(100)
  ｜ 增援(免费)：火獾(fire badger), 毒牙(fang), 未知兵种(31)
- R5（推导 750）：
升级未知兵种(31)Lv0→Lv1(50)；升级未知兵种(31)Lv0→Lv1(50)；解锁剑齿虎(sabertooth)(0)；购买未知兵种(31)(100)；购买火獾(fire badger)(200)；购买未知兵种(31)(100)；购买未知兵种(31)(100)；科技未知兵种(31)/未知科技(123101)(50)；购买未知兵种(31)(100)
  ｜ 增援(免费)：爬虫(crawler), 爬虫(crawler)
- R6（推导 800）：
购买爬虫(crawler)(100)；购买野马(mustang)(200)；升级未知兵种(31)Lv0→Lv1(50)；升级未知兵种(31)Lv1→Lv2(50)；升级未知兵种(31)Lv1→Lv2(50)；升级未知兵种(31)Lv0→Lv1(50)；科技野马(mustang)/Range enhancement(50)；购买野马(mustang)(200)；解锁狼蛛(tarantula)(0)；升级野马(mustang)Lv0→Lv1(50)
  ｜ 增援(免费)：未知兵种(31), 未知兵种(31), 未知兵种(31)
- R7（推导 850）：
升级野马(mustang)Lv0→Lv1(50)；升级野马(mustang)Lv0→Lv1(50)；升级未知兵种(31)Lv0→Lv1(50)；升级未知兵种(31)Lv1→Lv2(50)；升级未知兵种(31)Lv0→Lv1(50)；购买野马(mustang)(200)；购买野马(mustang)(200)；购买野马(mustang)(200)；解锁猎犬(hound)(0)
  ｜ 增援(免费)：爬虫(crawler)
- R8（推导 600）：
升级野马(mustang)Lv0→Lv1(50)；升级野马(mustang)Lv0→Lv1(50)；科技野马(mustang)/Aerial specialization(50)；购买野马(mustang)(200)；购买爬虫(crawler)(100)；升级未知兵种(31)Lv1→Lv2(50)；购买未知兵种(31)(100)
  ｜ 增援(免费)：黄蜂(wasp), 黄蜂(wasp), 野马(mustang), 野马(mustang)
- R9（推导 650）：
升级野马(mustang)Lv0→Lv1(50)；升级野马(mustang)Lv1→Lv2(50)；升级野马(mustang)Lv1→Lv2(50)；升级野马(mustang)Lv0→Lv1(50)；升级未知兵种(31)Lv0→Lv1(50)；升级未知兵种(31)Lv1→Lv2(50)；购买未知兵种(31)(100)；购买未知兵种(31)(100)；购买未知兵种(31)(100)；科技爬虫(crawler)/Loose formation(50)
  ｜ 增援(免费)：野马(mustang), 爬虫(crawler)
- R10（推导 950）：
购买爬虫(crawler)(100)；升级未知兵种(31)Lv2→Lv3(50)；升级未知兵种(31)Lv2→Lv3(50)；升级未知兵种(31)Lv1→Lv2(50)；升级未知兵种(31)Lv0→Lv1(50)；购买爬虫(crawler)(100)；科技堡垒(fortress)/Anti air barrage(50)；购买堡垒(fortress)(400)；升级野马(mustang)Lv1→Lv2(50)；升级野马(mustang)Lv0→Lv1(50)
  ｜ 增援(免费)：未知兵种(31), 未知兵种(31), 未知兵种(31)
- R11（推导 1300）：
升级未知兵种(31)Lv2→Lv3(50)；升级未知兵种(31)Lv2→Lv3(50)；升级未知兵种(31)Lv0→Lv1(50)；升级未知兵种(31)Lv2→Lv3(50)；升级未知兵种(31)Lv1→Lv2(50)；升级未知兵种(31)Lv0→Lv1(50)；升级堡垒(fortress)Lv0→Lv1(50)；购买猎犬(hound)(50)；科技猎犬(hound)/Fire extinguisher(50)；购买堡垒(fortress)(400)；购买堡垒(fortress)(400)；升级野马(mustang)Lv1→Lv2(50)
  ｜ 增援(免费)：爬虫(crawler), 爬虫(crawler), 堡垒(fortress)
- R12（推导 950）：
科技幽灵(wraith)/Degeneration beam(50)；升级堡垒(fortress)Lv0→Lv1(50)；升级未知兵种(31)Lv3→Lv4(50)；升级未知兵种(31)Lv2→Lv3(50)；购买爬虫(crawler)(100)；升级野马(mustang)Lv2→Lv3(50)；升级野马(mustang)Lv2→Lv3(50)；升级野马(mustang)Lv2→Lv3(50)；购买黄蜂(wasp)(200)；科技黄蜂(wasp)/Energy shield(50)；购买黄蜂(wasp)(200)；升级黄蜂(wasp)Lv1→Lv2(50)
  ｜ 增援(免费)：猎犬(hound), 堡垒(fortress), 堡垒(fortress)
- R13（推导 1000）：
升级未知兵种(31)Lv1→Lv2(50)；升级未知兵种(31)Lv3→Lv4(50)；升级未知兵种(31)Lv2→Lv3(50)；升级野马(mustang)Lv1→Lv2(50)；升级野马(mustang)Lv1→Lv2(50)；购买火獾(fire badger)(200)；科技堡垒(fortress)/Range enhancement(50)；购买堡垒(fortress)(400)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：幽灵(wraith), 幽灵(wraith), 黄蜂(wasp), 黄蜂(wasp)

**按兵种累计 O（Top10）**：未知兵种(31)=2850，野马(mustang)=2350，堡垒(fortress)=1800，爬虫(crawler)=1150，黄蜂(wasp)=500，火獾(fire badger)=400，毒牙(fang)=100，猎犬(hound)=100，大锤(sledgehammer)=50，幽灵(wraith)=50

**小结**：推导累计 O = 9350；实际花费(反推)累计 = 2600；比值 ≈ 3.60（>1 表示推导偏高，疑似增援单位被误计为购买）。

#### 玩家：哈宝哈宝蛤不思饱Habsburg

| 回合 | 剩余supply | 实际花费(反推) | 推导花费 | 差值 | 累计O |
|-----:|-----:|-----:|-----:|-----:|-----:|
| 0 | 0 | — | 0 |  | 0 |
| 1 | 0 | 200 | 200 | 0 | 200 |
| 2 | 0 | 200 | 300 | -100 | 500 |
| 3 | 50 | 150 | 350 | -200 | 850 |
| 4 | 0 | 250 | 800 | -550 | 1650 |
| 5 | 0 | 200 | 500 | -300 | 2150 |
| 6 | 50 | 150 | 550 | -400 | 2700 |
| 7 | 0 | 250 | 750 | -500 | 3450 |
| 8 | 0 | 200 | 850 | -650 | 4300 |
| 9 | 0 | 200 | 850 | -650 | 5150 |
| 10 | 0 | 200 | 450 | -250 | 5600 |
| 11 | 0 | 200 | 1450 | -1250 | 7050 |
| 12 | 0 | 200 | 1250 | -1050 | 8300 |
| 13 | 0 | 200 | 850 | -650 | 9150 |

**动作明细：**

- R0（推导 0）：
（无经济动作）
- R1（推导 200）：
解锁爬虫(crawler)(0)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：未知兵种(31), 未知兵种(31), 未知兵种(31), 狼蛛(tarantula), 狼蛛(tarantula)
- R2（推导 300）：
解锁毒牙(fang)(0)；购买毒牙(fang)(100)；购买爬虫(crawler)(100)；购买毒牙(fang)(100)
  ｜ 增援(免费)：爬虫(crawler)
- R3（推导 350）：
科技狼蛛(tarantula)/Range enhancement(50)；购买狼蛛(tarantula)(200)；购买爬虫(crawler)(100)；解锁野马(mustang)(0)
  ｜ 增援(免费)：神射手(marksmen), 毒牙(fang), 毒牙(fang)
- R4（推导 800）：
升级狼蛛(tarantula)Lv0→Lv1(50)；升级未知兵种(31)Lv0→Lv1(50)；升级未知兵种(31)Lv0→Lv1(50)；购买毒牙(fang)(100)；购买毒牙(fang)(100)；购买狼蛛(tarantula)(200)；购买狼蛛(tarantula)(200)；解锁剑齿虎(sabertooth)(0)；升级未知兵种(31)Lv0→Lv1(50)
  ｜ 增援(免费)：火獾(fire badger), 爬虫(crawler)
- R5（推导 500）：
解锁钢球(steel ball)(0)；解锁凤凰(phoenix)(0)；升级狼蛛(tarantula)Lv0→Lv1(50)；购买凤凰(phoenix)(150)；购买凤凰(phoenix)(150)；购买凤凰(phoenix)(150)
  ｜ 增援(免费)：毒牙(fang), 狼蛛(tarantula), 狼蛛(tarantula)
- R6（推导 550）：
升级凤凰(phoenix)Lv0→Lv1(50)；科技凤凰(phoenix)/Range enhancement(50)；购买凤凰(phoenix)(150)；购买凤凰(phoenix)(150)；购买凤凰(phoenix)(150)；解锁大锤(sledgehammer)(0)
- R7（推导 750）：
购买爬虫(crawler)(100)；升级凤凰(phoenix)Lv0→Lv1(50)；升级凤凰(phoenix)Lv0→Lv1(50)；升级凤凰(phoenix)Lv0→Lv1(50)；升级凤凰(phoenix)Lv0→Lv1(50)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；升级狼蛛(tarantula)Lv0→Lv1(50)；购买爬虫(crawler)(100)；升级狼蛛(tarantula)Lv0→Lv1(50)；科技狼蛛(tarantula)/High explosive ammo(50)；解锁钢球(steel ball)(0)
  ｜ 增援(免费)：凤凰(phoenix), 凤凰(phoenix), 凤凰(phoenix)
- R8（推导 850）：
升级狼蛛(tarantula)Lv0→Lv1(50)；升级狼蛛(tarantula)Lv1→Lv2(50)；升级狼蛛(tarantula)Lv0→Lv1(50)；购买狼蛛(tarantula)(200)；购买野马(mustang)(200)；购买野马(mustang)(200)；升级凤凰(phoenix)Lv0→Lv1(50)；科技野马(mustang)/Range enhancement(50)；解锁猎犬(hound)(0)
  ｜ 增援(免费)：蝎子(scorpion), 爬虫(crawler), 爬虫(crawler), 爬虫(crawler)
- R9（推导 850）：
升级狼蛛(tarantula)Lv0→Lv1(50)；科技爬虫(crawler)/Subterranean blitz(50)；购买爬虫(crawler)(100)；购买狼蛛(tarantula)(200)；购买狼蛛(tarantula)(200)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；升级凤凰(phoenix)Lv1→Lv2(50)；解锁弧光(arclight)(0)
  ｜ 增援(免费)：野马(mustang), 野马(mustang)
- R10（推导 450）：
升级狼蛛(tarantula)Lv0→Lv1(50)；科技未知兵种(2002)/未知科技(1022002)(50)；科技狼蛛(tarantula)/Armor enhancement(50)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；解锁未知兵种(30)(0)
  ｜ 增援(免费)：狼蛛(tarantula)
- R11（推导 1450）：
购买爬虫(crawler)(100)；升级未知兵种(2002)Lv0→Lv1(50)；升级狼蛛(tarantula)Lv0→Lv1(50)；升级狼蛛(tarantula)Lv1→Lv2(50)；购买未知兵种(2002)(800)；科技凤凰(phoenix)/Electromagnetic shot(50)；升级野马(mustang)Lv0→Lv1(50)；升级野马(mustang)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)；升级凤凰(phoenix)Lv0→Lv1(50)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：爬虫(crawler)
- R12（推导 1250）：
科技大锤(sledgehammer)/Armor enhancement(50)；升级未知兵种(2002)Lv0→Lv1(50)；升级狼蛛(tarantula)Lv1→Lv2(50)；购买未知兵种(2002)(800)；升级狼蛛(tarantula)Lv2→Lv3(50)；购买爬虫(crawler)(100)；升级未知兵种(2002)Lv0→Lv1(50)；购买爬虫(crawler)(100)
- R13（推导 850）：
升级野马(mustang)Lv1→Lv2(50)；升级野马(mustang)Lv1→Lv2(50)；升级未知兵种(2002)Lv0→Lv1(50)；购买狼蛛(tarantula)(200)；购买狼蛛(tarantula)(200)；升级狼蛛(tarantula)Lv2→Lv3(50)；购买野马(mustang)(200)；升级凤凰(phoenix)Lv1→Lv2(50)
  ｜ 增援(免费)：大锤(sledgehammer), 大锤(sledgehammer), 大锤(sledgehammer), 未知兵种(2002), 爬虫(crawler), 爬虫(crawler)

**按兵种累计 O（Top10）**：狼蛛(tarantula)=2450，爬虫(crawler)=1950，未知兵种(2002)=1850，凤凰(phoenix)=1450，野马(mustang)=850，毒牙(fang)=400，未知兵种(31)=150，大锤(sledgehammer)=50，剑齿虎(sabertooth)=0，钢球(steel ball)=0

**小结**：推导累计 O = 9150；实际花费(反推)累计 = 2600；比值 ≈ 3.52（>1 表示推导偏高，疑似增援单位被误计为购买）。

### 对局 2：`2207_20260729--134245856_[juliensam23]VS[my98765431].grbr`

- 模式：`VS_1_1`

#### 玩家：juliensam23

| 回合 | 剩余supply | 实际花费(反推) | 推导花费 | 差值 | 累计O |
|-----:|-----:|-----:|-----:|-----:|-----:|
| 0 | 0 | — | 0 |  | 0 |
| 1 | 0 | 200 | 200 | 0 | 200 |
| 2 | 0 | 200 | 400 | -200 | 600 |
| 3 | 0 | 200 | 600 | -400 | 1200 |
| 4 | 0 | 200 | 600 | -400 | 1800 |
| 5 | 0 | 200 | 450 | -250 | 2250 |
| 6 | 350 | -150 | 1050 | -1200 | 3300 |
| 7 | 0 | 550 | 950 | -400 | 4250 |
| 8 | 0 | 200 | 1250 | -1050 | 5500 |
| 9 | 0 | 200 | 950 | -750 | 6450 |
| 10 | 0 | 200 | 700 | -500 | 7150 |
| 11 | 0 | 200 | 2050 | -1850 | 9200 |
| 12 | 0 | 200 | 900 | -700 | 10100 |
| 13 | 0 | 200 | 700 | -500 | 10800 |

**动作明细：**

- R0（推导 0）：
（无经济动作）
- R1（推导 200）：
解锁爬虫(crawler)(0)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：未知兵种(30), 未知兵种(30), 未知兵种(30), 火獾(fire badger), 火獾(fire badger)
- R2（推导 400）：
解锁大锤(sledgehammer)(0)；购买大锤(sledgehammer)(200)；购买大锤(sledgehammer)(200)
  ｜ 增援(免费)：爬虫(crawler), 爬虫(crawler)
- R3（推导 600）：
解锁野马(mustang)(0)；购买野马(mustang)(200)；解锁野马(mustang)(0)；购买野马(mustang)(200)；购买野马(mustang)(200)
  ｜ 增援(免费)：大锤(sledgehammer), 大锤(sledgehammer)
- R4（推导 600）：
购买野马(mustang)(200)；购买大锤(sledgehammer)(200)；购买大锤(sledgehammer)(200)
  ｜ 增援(免费)：幻影射线(phantom ray), 野马(mustang)
- R5（推导 450）：
升级大锤(sledgehammer)Lv0→Lv1(50)；购买幻影射线(phantom ray)(200)；购买幻影射线(phantom ray)(200)
  ｜ 增援(免费)：野马(mustang), 大锤(sledgehammer), 大锤(sledgehammer)
- R6（推导 1050）：
解锁战争工厂(warfactory)(0)；购买战争工厂(warfactory)(800)；科技战争工厂(warfactory)/Range enhancement(50)；购买大锤(sledgehammer)(200)
  ｜ 增援(免费)：幻影射线(phantom ray), 幻影射线(phantom ray)
- R7（推导 950）：
购买战争工厂(warfactory)(800)；科技战争工厂(warfactory)/Launcher overload(50)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：大锤(sledgehammer)
- R8（推导 1250）：
科技战争工厂(warfactory)/High explosive ammo(50)；购买战争工厂(warfactory)(800)；科技大锤(sledgehammer)/Field maintenance(50)；购买大锤(sledgehammer)(200)；购买爬虫(crawler)(100)；升级野马(mustang)Lv1→Lv2(50)
- R9（推导 950）：
购买战争工厂(warfactory)(800)；科技战争工厂(warfactory)/Sledgehammer production(50)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：大锤(sledgehammer)
- R10（推导 700）：
科技野马(mustang)/Aerial specialization(50)；升级野马(mustang)Lv1→Lv2(50)；购买野马(mustang)(200)；购买野马(mustang)(200)；购买野马(mustang)(200)
  ｜ 增援(免费)：战争工厂(warfactory), 爬虫(crawler)
- R11（推导 2050）：
升级野马(mustang)Lv1→Lv2(50)；购买战争工厂(warfactory)(800)；购买战争工厂(warfactory)(800)；购买大锤(sledgehammer)(200)；购买大锤(sledgehammer)(200)
  ｜ 增援(免费)：野马(mustang), 野马(mustang), 野马(mustang)
- R12（推导 900）：
升级野马(mustang)Lv1→Lv2(50)；升级野马(mustang)Lv2→Lv3(50)；科技野马(mustang)/Armor piercing bullets(50)；科技野马(mustang)/Missile interceptor(50)；购买野马(mustang)(200)；购买野马(mustang)(200)；购买野马(mustang)(200)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：战争工厂(warfactory), 战争工厂(warfactory), 大锤(sledgehammer), 大锤(sledgehammer)
- R13（推导 700）：
升级野马(mustang)Lv2→Lv3(50)；科技野马(mustang)/Range enhancement(50)；购买野马(mustang)(200)；购买野马(mustang)(200)；购买野马(mustang)(200)
  ｜ 增援(免费)：爬虫(crawler)

**按兵种累计 O（Top10）**：战争工厂(warfactory)=5000，野马(mustang)=3100，大锤(sledgehammer)=1700，爬虫(crawler)=600，幻影射线(phantom ray)=400

**小结**：推导累计 O = 10800；实际花费(反推)累计 = 2600；比值 ≈ 4.15（>1 表示推导偏高，疑似增援单位被误计为购买）。

#### 玩家：my98765431

| 回合 | 剩余supply | 实际花费(反推) | 推导花费 | 差值 | 累计O |
|-----:|-----:|-----:|-----:|-----:|-----:|
| 0 | 0 | — | 0 |  | 0 |
| 1 | 0 | 200 | 200 | 0 | 200 |
| 2 | 0 | 200 | 400 | -200 | 600 |
| 3 | 0 | 200 | 550 | -350 | 1150 |
| 4 | 50 | 150 | 400 | -250 | 1550 |
| 5 | 0 | 250 | 1000 | -750 | 2550 |
| 6 | 0 | 200 | 400 | -200 | 2950 |
| 7 | 0 | 200 | 350 | -150 | 3300 |
| 8 | 0 | 200 | 750 | -550 | 4050 |
| 9 | 0 | 200 | 500 | -300 | 4550 |
| 10 | 0 | 200 | 850 | -650 | 5400 |
| 11 | 0 | 200 | 1800 | -1600 | 7200 |
| 12 | 0 | 200 | 1900 | -1700 | 9100 |
| 13 | 0 | 200 | 2450 | -2250 | 11550 |

**动作明细：**

- R0（推导 0）：
（无经济动作）
- R1（推导 200）：
购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：爬虫(crawler), 狼蛛(tarantula), 狼蛛(tarantula)
- R2（推导 400）：
解锁凤凰(phoenix)(0)；购买凤凰(phoenix)(200)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
- R3（推导 550）：
升级凤凰(phoenix)Lv0→Lv1(50)；购买台风(typhoon)(300)；购买凤凰(phoenix)(200)
  ｜ 增援(免费)：爬虫(crawler), 爬虫(crawler)
- R4（推导 400）：
购买爬虫(crawler)(100)；购买凤凰(phoenix)(200)；科技凤凰(phoenix)/Launcher overload(50)；科技台风(typhoon)/Aerial specialization(50)
  ｜ 增援(免费)：毒牙(fang), 毒牙(fang), 毒牙(fang), 台风(typhoon)
- R5（推导 1000）：
升级台风(typhoon)Lv0→Lv1(50)；升级凤凰(phoenix)Lv1→Lv2(50)；购买凤凰(phoenix)(200)；购买狼蛛(tarantula)(200)；购买台风(typhoon)(300)；购买凤凰(phoenix)(200)
  ｜ 增援(免费)：爬虫(crawler)
- R6（推导 400）：
购买凤凰(phoenix)(200)；科技凤凰(phoenix)/Range enhancement(50)；升级台风(typhoon)Lv0→Lv1(50)；升级凤凰(phoenix)Lv0→Lv1(50)；升级凤凰(phoenix)Lv0→Lv1(50)
  ｜ 增援(免费)：台风(typhoon), 凤凰(phoenix)
- R7（推导 350）：
升级台风(typhoon)Lv0→Lv1(50)；升级凤凰(phoenix)Lv0→Lv1(50)；购买爬虫(crawler)(100)；科技台风(typhoon)/Energy shield(50)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：蝎子(scorpion), 凤凰(phoenix)
- R8（推导 750）：
升级凤凰(phoenix)Lv1→Lv2(50)；升级凤凰(phoenix)Lv1→Lv2(50)；升级凤凰(phoenix)Lv0→Lv1(50)；购买凤凰(phoenix)(200)；购买凤凰(phoenix)(200)；购买凤凰(phoenix)(200)
  ｜ 增援(免费)：爬虫(crawler), 爬虫(crawler)
- R9（推导 500）：
升级台风(typhoon)Lv1→Lv2(50)；升级台风(typhoon)Lv1→Lv2(50)；购买爬虫(crawler)(100)；科技台风(typhoon)/Energy shield(50)；购买爬虫(crawler)(100)；科技凤凰(phoenix)/Energy shield(50)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：剑齿虎(sabertooth), 凤凰(phoenix), 凤凰(phoenix), 凤凰(phoenix)
- R10（推导 850）：
升级凤凰(phoenix)Lv1→Lv2(50)；升级凤凰(phoenix)Lv2→Lv3(50)；升级凤凰(phoenix)Lv0→Lv1(50)；升级凤凰(phoenix)Lv1→Lv2(50)；升级台风(typhoon)Lv1→Lv2(50)；购买凤凰(phoenix)(200)；购买凤凰(phoenix)(200)；购买凤凰(phoenix)(200)
  ｜ 增援(免费)：爬虫(crawler), 爬虫(crawler), 爬虫(crawler)
- R11（推导 1800）：
升级凤凰(phoenix)Lv1→Lv2(50)；升级凤凰(phoenix)Lv2→Lv3(50)；升级凤凰(phoenix)Lv2→Lv3(50)；解锁深渊(abyss)(0)；购买深渊(abyss)(800)；购买深渊(abyss)(800)；科技深渊(abyss)/Swarm missiles(50)
  ｜ 增援(免费)：凤凰(phoenix), 凤凰(phoenix), 凤凰(phoenix)
- R12（推导 1900）：
升级凤凰(phoenix)Lv1→Lv2(50)；升级凤凰(phoenix)Lv1→Lv2(50)；升级凤凰(phoenix)Lv1→Lv2(50)；科技凤凰(phoenix)/Quantum reassembly(50)；购买深渊(abyss)(800)；购买深渊(abyss)(800)；购买爬虫(crawler)(100)
- R13（推导 2450）：
科技深渊(abyss)/Disintegration(50)；购买深渊(abyss)(800)；购买深渊(abyss)(800)；购买深渊(abyss)(800)
  ｜ 增援(免费)：爬虫(crawler)

**按兵种累计 O（Top10）**：深渊(abyss)=5700，凤凰(phoenix)=3500，爬虫(crawler)=1100，台风(typhoon)=1050，狼蛛(tarantula)=200

**小结**：推导累计 O = 11550；实际花费(反推)累计 = 2600；比值 ≈ 4.44（>1 表示推导偏高，疑似增援单位被误计为购买）。

### 对局 3：`2207_20260801--134267483_[TiNaAch]VS[[TUFF] Mathismight].grbr`

- 模式：`VS_1_1`

#### 玩家：TiNaAch

| 回合 | 剩余supply | 实际花费(反推) | 推导花费 | 差值 | 累计O |
|-----:|-----:|-----:|-----:|-----:|-----:|
| 0 | 0 | — | 0 |  | 0 |
| 1 | 0 | 200 | 400 | -200 | 400 |
| 2 | 0 | 200 | 300 | -100 | 700 |
| 3 | 50 | 150 | 750 | -600 | 1450 |
| 4 | 0 | 250 | 500 | -250 | 1950 |
| 5 | 50 | 150 | 750 | -600 | 2700 |
| 6 | 50 | 200 | 850 | -650 | 3550 |
| 7 | 50 | 200 | 500 | -300 | 4050 |
| 8 | 0 | 250 | 650 | -400 | 4700 |
| 9 | 0 | 200 | 650 | -450 | 5350 |
| 10 | 50 | 150 | 850 | -700 | 6200 |
| 11 | 50 | 200 | 800 | -600 | 7000 |
| 12 | 0 | 250 | 950 | -700 | 7950 |
| 13 | 50 | 150 | 1250 | -1100 | 9200 |

**动作明细：**

- R0（推导 0）：
（无经济动作）
- R1（推导 400）：
购买狼蛛(tarantula)(200)；解锁爬虫(crawler)(0)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：神射手(marksmen), 神射手(marksmen), 神射手(marksmen), 狼蛛(tarantula)
- R2（推导 300）：
购买爬虫(crawler)(100)；解锁毒牙(fang)(0)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
- R3（推导 750）：
升级神射手(marksmen)Lv0→Lv1(50)；解锁蝎子(scorpion)(0)；购买蝎子(scorpion)(300)；购买蝎子(scorpion)(300)；购买毒牙(fang)(100)
  ｜ 增援(免费)：爬虫(crawler), 爬虫(crawler), 爬虫(crawler)
- R4（推导 500）：
购买狼蛛(tarantula)(200)；科技狼蛛(tarantula)/High explosive ammo(50)；购买爬虫(crawler)(100)；解锁野马(mustang)(0)；升级神射手(marksmen)Lv0→Lv1(50)；升级神射手(marksmen)Lv0→Lv1(50)；升级神射手(marksmen)Lv0→Lv1(50)
  ｜ 增援(免费)：幽灵(wraith), 蝎子(scorpion), 蝎子(scorpion), 毒牙(fang)
- R5（推导 750）：
升级神射手(marksmen)Lv0→Lv1(50)；购买狼蛛(tarantula)(200)；购买蝎子(scorpion)(300)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；解锁大锤(sledgehammer)(0)
- R6（推导 850）：
购买蝎子(scorpion)(300)；升级狼蛛(tarantula)Lv0→Lv1(50)；购买爬虫(crawler)(100)；升级蝎子(scorpion)Lv0→Lv1(50)；升级蝎子(scorpion)Lv0→Lv1(50)；升级蝎子(scorpion)Lv0→Lv1(50)；购买狼蛛(tarantula)(200)；升级神射手(marksmen)Lv1→Lv2(50)
  ｜ 增援(免费)：爬虫(crawler)
- R7（推导 500）：
科技狼蛛(tarantula)/Range enhancement(50)；科技蝎子(scorpion)/Range enhancement(50)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；解锁火獾(fire badger)(0)；升级爬虫(crawler)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)
  ｜ 增援(免费)：台风(typhoon), 蝎子(scorpion), 狼蛛(tarantula)
- R8（推导 650）：
升级狼蛛(tarantula)Lv0→Lv1(50)；购买狼蛛(tarantula)(200)；升级爬虫(crawler)Lv0→Lv1(50)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；升级蝎子(scorpion)Lv1→Lv2(50)；升级神射手(marksmen)Lv2→Lv3(50)；升级神射手(marksmen)Lv2→Lv3(50)
  ｜ 增援(免费)：爬虫(crawler)
- R9（推导 650）：
升级爬虫(crawler)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)；科技先知(farseer)/Photon emission(50)；升级神射手(marksmen)Lv2→Lv3(50)；科技爬虫(crawler)/Subterranean blitz(50)；购买狼蛛(tarantula)(200)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
- R10（推导 850）：
升级狼蛛(tarantula)Lv0→Lv1(50)；升级狼蛛(tarantula)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)；购买蝎子(scorpion)(300)；升级蝎子(scorpion)Lv1→Lv2(50)；购买蝎子(scorpion)(300)；科技狼蛛(tarantula)/Mechanical rage(50)；解锁弧光(arclight)(0)
  ｜ 增援(免费)：先知(farseer), 先知(farseer), 先知(farseer), 狼蛛(tarantula), 爬虫(crawler), 爬虫(crawler)
- R11（推导 800）：
升级狼蛛(tarantula)Lv0→Lv1(50)；升级狼蛛(tarantula)Lv1→Lv2(50)；科技神射手(marksmen)/Electromagnetic shot(50)；升级蝎子(scorpion)Lv1→Lv2(50)；升级蝎子(scorpion)Lv2→Lv3(50)；科技蝎子(scorpion)/Field maintenance(50)；购买蝎子(scorpion)(300)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；解锁未知兵种(30)(0)
  ｜ 增援(免费)：蝎子(scorpion)
- R12（推导 950）：
购买先知(farseer)(300)；科技先知(farseer)/Scanning radar(50)；升级蝎子(scorpion)Lv2→Lv3(50)；升级蝎子(scorpion)Lv0→Lv1(50)；升级蝎子(scorpion)Lv0→Lv1(50)；升级神射手(marksmen)Lv3→Lv4(50)；升级神射手(marksmen)Lv3→Lv4(50)；升级神射手(marksmen)Lv3→Lv4(50)；升级先知(farseer)Lv0→Lv1(50)；升级先知(farseer)Lv0→Lv1(50)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：蝎子(scorpion)
- R13（推导 1250）：
升级蝎子(scorpion)Lv0→Lv1(50)；升级蝎子(scorpion)Lv2→Lv3(50)；升级蝎子(scorpion)Lv3→Lv4(50)；升级神射手(marksmen)Lv3→Lv4(50)；升级狼蛛(tarantula)Lv0→Lv1(50)；升级狼蛛(tarantula)Lv1→Lv2(50)；升级狼蛛(tarantula)Lv1→Lv2(50)；升级爬虫(crawler)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)；科技蝎子(scorpion)/Doubleshot(50)；购买神射手(marksmen)(100)；购买狼蛛(tarantula)(200)；购买蝎子(scorpion)(300)；升级先知(farseer)Lv0→Lv1(50)；解锁钢球(steel ball)(0)
  ｜ 增援(免费)：幽灵(wraith), 先知(farseer), 爬虫(crawler), 爬虫(crawler)

**按兵种累计 O（Top10）**：蝎子(scorpion)=3200，爬虫(crawler)=2550，狼蛛(tarantula)=2000，神射手(marksmen)=800，先知(farseer)=550，毒牙(fang)=100，野马(mustang)=0，大锤(sledgehammer)=0，火獾(fire badger)=0，弧光(arclight)=0

**小结**：推导累计 O = 9200；实际花费(反推)累计 = 2550；比值 ≈ 3.61（>1 表示推导偏高，疑似增援单位被误计为购买）。

#### 玩家：[TUFF] Mathismight

| 回合 | 剩余supply | 实际花费(反推) | 推导花费 | 差值 | 累计O |
|-----:|-----:|-----:|-----:|-----:|-----:|
| 0 | 0 | — | 0 |  | 0 |
| 1 | 0 | 200 | 200 | 0 | 200 |
| 2 | 0 | 200 | 350 | -150 | 550 |
| 3 | 0 | 200 | 800 | -600 | 1350 |
| 4 | 50 | 150 | 500 | -350 | 1850 |
| 5 | 0 | 250 | 1000 | -750 | 2850 |
| 6 | 0 | 200 | 600 | -400 | 3450 |
| 7 | 100 | 100 | 850 | -750 | 4300 |
| 8 | 0 | 300 | 700 | -400 | 5000 |
| 9 | 0 | 200 | 1050 | -850 | 6050 |
| 10 | 0 | 200 | 700 | -500 | 6750 |
| 11 | 0 | 200 | 850 | -650 | 7600 |
| 12 | 50 | 150 | 600 | -450 | 8200 |
| 13 | 50 | 200 | 1050 | -850 | 9250 |

**动作明细：**

- R0（推导 0）：
（无经济动作）
- R1（推导 200）：
解锁爬虫(crawler)(0)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：未知兵种(30), 未知兵种(30), 未知兵种(30), 火獾(fire badger), 火獾(fire badger)
- R2（推导 350）：
购买爬虫(crawler)(100)；解锁神射手(marksmen)(0)；购买爬虫(crawler)(100)；购买神射手(marksmen)(100)；科技未知兵种(30)/未知科技(10930)(50)
- R3（推导 800）：
升级未知兵种(30)Lv0→Lv1(50)；升级未知兵种(30)Lv0→Lv1(50)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；购买犀牛(rhino)(200)；购买未知兵种(30)(100)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：神射手(marksmen)
- R4（推导 500）：
升级火獾(fire badger)Lv0→Lv1(50)；科技火獾(fire badger)/Range enhancement(50)；购买火獾(fire badger)(200)；购买火獾(fire badger)(200)
  ｜ 增援(免费)：幽灵(wraith), 未知兵种(30), 爬虫(crawler), 爬虫(crawler)
- R5（推导 1000）：
升级未知兵种(30)Lv0→Lv1(50)；升级未知兵种(30)Lv0→Lv1(50)；科技火獾(fire badger)/Scorching fire(50)；升级神射手(marksmen)Lv0→Lv1(50)；购买火獾(fire badger)(200)；购买爬虫(crawler)(100)；升级神射手(marksmen)Lv0→Lv1(50)；购买火獾(fire badger)(200)；升级爬虫(crawler)Lv0→Lv1(50)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：犀牛(rhino)
- R6（推导 600）：
升级火獾(fire badger)Lv1→Lv2(50)；升级火獾(fire badger)Lv0→Lv1(50)；科技火獾(fire badger)/Scorching fire(50)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)；升级未知兵种(30)Lv1→Lv2(50)；购买凤凰(phoenix)(200)
  ｜ 增援(免费)：火獾(fire badger)
- R7（推导 850）：
科技爬虫(crawler)/Subterranean blitz(50)；升级神射手(marksmen)Lv1→Lv2(50)；升级火獾(fire badger)Lv0→Lv1(50)；升级火獾(fire badger)Lv0→Lv1(50)；购买火獾(fire badger)(200)；购买犀牛(rhino)(200)；购买爬虫(crawler)(100)；升级爬虫(crawler)Lv0→Lv1(50)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：凤凰(phoenix), 凤凰(phoenix)
- R8（推导 700）：
科技凤凰(phoenix)/Quantum reassembly(50)；升级火獾(fire badger)Lv2→Lv3(50)；升级火獾(fire badger)Lv0→Lv1(50)；升级凤凰(phoenix)Lv0→Lv1(50)；购买爬虫(crawler)(100)；科技未知兵种(30)/未知科技(10230)(50)；购买凤凰(phoenix)(200)；购买爬虫(crawler)(100)；升级爬虫(crawler)Lv1→Lv2(50)
  ｜ 增援(免费)：火獾(fire badger)
- R9（推导 1050）：
科技先知(farseer)/Photon emission(50)；升级火獾(fire badger)Lv1→Lv2(50)；升级火獾(fire badger)Lv0→Lv1(50)；升级凤凰(phoenix)Lv3→Lv4(50)；升级凤凰(phoenix)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)；升级未知兵种(30)Lv1→Lv2(50)；升级未知兵种(30)Lv1→Lv2(50)；购买火獾(fire badger)(200)；购买犀牛(rhino)(200)；购买爬虫(crawler)(100)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：凤凰(phoenix)
- R10（推导 700）：
科技未知兵种(30)/未知科技(180430)(50)；升级未知兵种(30)Lv1→Lv2(50)；升级未知兵种(30)Lv2→Lv3(50)；升级火獾(fire badger)Lv3→Lv4(50)；升级爬虫(crawler)Lv0→Lv1(50)；购买火獾(fire badger)(200)；购买未知兵种(30)(100)；购买未知兵种(30)(100)；升级爬虫(crawler)Lv0→Lv1(50)
  ｜ 增援(免费)：先知(farseer), 先知(farseer), 爬虫(crawler), 爬虫(crawler)
- R11（推导 850）：
升级凤凰(phoenix)Lv4→Lv5(50)；升级凤凰(phoenix)Lv1→Lv2(50)；升级未知兵种(30)Lv0→Lv1(50)；升级未知兵种(30)Lv2→Lv3(50)；升级未知兵种(30)Lv2→Lv3(50)；升级未知兵种(30)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)；升级爬虫(crawler)Lv0→Lv1(50)；科技火獾(fire badger)/Ignite(50)；解锁毒牙(fang)(0)；购买毒牙(fang)(100)；购买毒牙(fang)(100)；购买火獾(fire badger)(200)
  ｜ 增援(免费)：未知兵种(30), 未知兵种(30)
- R12（推导 600）：
科技钢球(steel ball)/Range enhancement(50)；科技钢球(steel ball)/Kinetic Charge(50)；升级火獾(fire badger)Lv0→Lv1(50)；升级火獾(fire badger)Lv1→Lv2(50)；升级火獾(fire badger)Lv1→Lv2(50)；科技凤凰(phoenix)/Range enhancement(50)；解锁黄蜂(wasp)(0)；购买黄蜂(wasp)(200)；购买爬虫(crawler)(100)
  ｜ 增援(免费)：毒牙(fang), 毒牙(fang), 火獾(fire badger)
- R13（推导 1050）：
升级火獾(fire badger)Lv4→Lv5(50)；升级火獾(fire badger)Lv1→Lv2(50)；升级火獾(fire badger)Lv1→Lv2(50)；升级火獾(fire badger)Lv1→Lv2(50)；升级凤凰(phoenix)Lv1→Lv2(50)；升级凤凰(phoenix)Lv2→Lv3(50)；升级未知兵种(30)Lv3→Lv4(50)；升级未知兵种(30)Lv3→Lv4(50)；升级未知兵种(30)Lv2→Lv3(50)；升级未知兵种(30)Lv2→Lv3(50)；科技凤凰(phoenix)/Electromagnetic shot(50)；购买凤凰(phoenix)(200)；升级爬虫(crawler)Lv0→Lv1(50)；升级钢球(steel ball)Lv0→Lv1(50)；购买黄蜂(wasp)(200)
  ｜ 增援(免费)：钢球(steel ball), 钢球(steel ball), 爬虫(crawler)

**按兵种累计 O（Top10）**：火獾(fire badger)=2650，爬虫(crawler)=2550，未知兵种(30)=1300，凤凰(phoenix)=1100，犀牛(rhino)=600，黄蜂(wasp)=400，神射手(marksmen)=250，毒牙(fang)=200，钢球(steel ball)=150，先知(farseer)=50

**小结**：推导累计 O = 9250；实际花费(反推)累计 = 2550；比值 ≈ 3.63（>1 表示推导偏高，疑似增援单位被误计为购买）。

## 三、需 owner 确认的开放问题

1. **增援单位是否误计为购买**：多局从 R2 起「推导花费」显著高于「实际花费(反推)」（差值为负且递增），主因是 unit 31（phantom ray 类）等单位既出现在 `PAD_BuyUnit` 又出现在免费增援列表。请确认增援单位的判定规则（是否免费、是否应排除计费）。

确认 增援花费也要算在单位花费中
另外回合0的时候（ 注意游戏开局只会提供3个100和2个200 的兵种）的花费也要计算

2. **科技费用数值**：`TECH_COST` 默认 50、个别 100，是否准确？

科技花费是严重错误的
并且同一个兵种升级第i个科技的时候会额外花费(i-1)*200

这里可能还是要通过金钱的前后去计算。

3. **兵种等级费用**：`UNIT_LEVEL_COST` 默认每级 50，是否准确？不同兵种/等级是否不同？

每级升级费用 是兵种原始购买费用的一半

4. **解锁费用**：`PAD_UnlockUnit` 当前计费 0，是否应等于首购基础价？

解锁费用也是每个兵种不同的
比如暴雨目前最新版本是50
犀牛也是50
但是更贵的单位往往解锁费用会更贵

这里可能还是要看log里面有没有前后金钱差来进行判断是最准的

5. **兵种中文名**：本数据集回放来自较新版本，部分兵种 ID（如 31、2002）超出既有 `UNIT_LOOKUP`（1–29），显示为「未知兵种(ID)」。成本计算不受影响（基础价来自数据驱动 SellSupply）；中文名需按当前版本补全。

你可以看看 reference_code/mechabellum_stats.py 有没有对应的中文名
你可以维护一个

如果找不到 可以显示为 未知兵种ID

## 四、产出文件

- 解析脚本：`reference_code/parse_match_investment.py`
- 结构化 JSON：`reference_code/parse_3_matches.json`（后续「批量解析」的种子）
- 本报告：`information/parse_3_matches_report.md`

# mechabellum-winrate-estimation
Machine learning estimate mechabellum's winrate. 

钢铁指挥官 局面胜率估计

## 运行前端模拟器与分享 Demo

开发时分别启动后端和前端：

```bash
python3 -m uvicorn backend.run:app --reload --port 8000
cd frontend && npm install && npm run dev
```

生产预览使用 FastAPI 同源托管构建后的前端：

```bash
cd frontend && npm run build
cd .. && python3 -m uvicorn backend.run:app --host 127.0.0.1 --port 8000
```

需要临时给外网用户演示时，先安装 `cloudflared`，再运行：

```bash
brew install cloudflared
./scripts/share_demo.sh
```

脚本会构建前端、启动后端和 Cloudflare Quick Tunnel，并打印一个临时的
`https://*.trycloudflare.com` 地址。保持终端运行即可持续提供 Demo，按 `Ctrl+C`
会同时停止后端和隧道。Quick Tunnel 适合短期测试，重启后地址会变化；不要在其中放置
敏感数据或管理接口。


## 原始动机

由于钢铁指挥官制作方相对积极的版本更新，使得一阵不玩之后，就会跟不上版本。

另一方面，我们也好奇有没有办法像围棋那样，产生相对强的AI，或者退而求其次，产生一个能够对对局有一定指导意义的AI

这个项目就是形成这样一个尝试

## 预期的交付物

- 回合胜率判断V模型
- 我方最佳兵种推荐
- 对方最佳兵种推荐
- 分三路的胜率判断模型（希望能够比不引入空间信息的略高一些）
- 一个方便去增减兵 记录等级、科技投资的一个前端，可以实时推荐兵种
- 分三路和全局对兵种进行推荐显示

### V模型 

这个模型可以看成是一个超简单版本的沙盒模拟

给定大致的双方在各个兵种的资源投入（第二版中可以考虑加入左中右的分路信息），

可以判断是哪一方会获得胜利

目前Logistic超简模型已经获得了显著超过乱猜（虽然只有55%+）的准确率

### 最佳兵种推荐

由于目前的模型是线性的，所以可以较快地推断出如果对方阵型固定（或者投入兵种的比例不变）

我方接下来出什么兵种是最合适的。

### 对方最佳兵种推荐

这里也是顺便的


# 当前最简模型的描述

由于我们只有1000盘对局，所以我对于模型的复杂度非常谨慎

这里使用了一个非常简单的Logistic拟合

认为

回合胜率 = sigmoid( w_ij a_i b_j + u_i a_i + v_j b_j )

这里a_i , b_j 表示 我方在兵种i的总投资百分比， 和对方在兵种j的总投资百分比

w_ij越大，表示如果 我方重仓了兵种i 对方重仓了兵种j，那么我方更容易赢

这里额外也考虑了单兵种有优势和劣势的可能性（这个预测模型表现得更好）

w_ij最大的40组展示

这里明显可以看到这个模型是有一定道理的，展现出了一定的兵种克制

```
先知A-鬼鳐B 4.8；弧光A-野马B 4.7；堡垒A-兵蜂B 4.4；堡垒A-凤凰B 4.3
野马A-兵蜂B 4.1；魔眼A-剑齿虎B 4.1；野马A-凤凰B 4.1；铁锤A-尖牙B 4.1
凤凰A-铁锤B 3.9；凤凰A-狼蛛B 3.9；磁暴A-野马B 3.8；凤凰A-磁暴B 3.6
火神A-魔眼B 3.6；剑齿虎A-火獾B 3.4；尖牙A-战争工厂B 3.4；火獾A-爬虫B 3.3
尖牙A-凤凰B 3.2；鬼鳐A-狂蝎B 3.2；野马A-雷霆B 3.2；剑齿虎A-铁锤B 3.2
鬼鳐A-暴雨B 3.1；犀牛A-狼蛛B 3.1；兵蜂A-狼蛛B 3.0；霸主A-铁锤B 3.0
狂蝎A-犀牛B 3.0；熔点A-恶灵B 2.9；爬虫A-熔点B 2.9；恶灵A-魔眼B 2.9
狂蝎A-长弓B 2.9；战争工厂A-磁暴B 2.9；弧光A-尖牙B 2.8；鬼鳐A-火獾B 2.8
长弓A-恶灵B 2.8；尖牙A-鬼鳐B 2.8；深渊A-战争工厂B 2.8；鬼鳐A-磁暴B 2.7
魔眼A-霸主B 2.7；恶灵A-战争工厂B 2.7；狼蛛A-尖牙B 2.7；兵蜂A-火獾B 2.7
```

u_i组，这里如果A更大表示是我们训的记录里面最近3000局（跨越了3个大版本应该是）的优势兵种

B的权值更大表示的是劣势兵种。感觉这个表格和锦标赛的胜率表能够有一定的对应关系。

不过长弓+1.0是什么鬼

```
尖牙B +1.3；凤凰A +1.2；铁锤B +1.2；鬼鳐A +1.1
暴雨B +1.0；长弓A +1.0；火獾B +0.8；狂蝎A +0.8
骇客A +0.8；钢球B +0.7；犀牛A +0.6；战争工厂B +0.6
熔点A +0.5；先知B +0.5；雷霆B +0.5；深渊A +0.5
磁暴A +0.4；沙虫B +0.4；兵蜂B +0.3；霸主B +0.3
```

这个我们之后分三路来增加这个模型对于空间感知的敏感性
另外当前考虑了百分比没有考虑经济差 之后的版本可以看看经济差如何引入
（有可能把大家的分母公共成两方总投资的调和平均数）

## TODO

- [x] 尝试解析3个对局的数据，打印两个玩家在各个回合投入的资源和owner确认是否解析正确 （干什么 花了多少钱）
- [x] 表格检查2 对于每个兵种形成一个表格，记录解锁费用、一级购买费用、升级费用和每个科技的解锁费用，可以进行检查
    - 有可能会有版本差异 记录到一个值就可以
    - 也就是说对于投资统计可能还是按照实际钱数是比较好的
- [x] 测试5个对局数据，打印玩家在每回合投入了什么兵种投资，以及总共有什么兵种总投资
    - 输出类似回合1 "玩家1 尖牙 +200 铁锤 -200 | 当前总投入 暴雨 400 ..." 记得考虑单位被售出的情况
    - 输出2个玩家信息后 然后输出当回合是哪个玩家赢了 记得输出的时候投入尽量从高到低排序
    - 注意有的时候改进型卡片 会加减 单位的费用，为了简单起见，暂时尽量以玩家的实际花费为主 如果算不清楚的话 查表也可以
    - 给我输出5个玩家复查
- [x] 将所有对局解析成一个相对密集存储的数据结构 （我可能要转移到其他有GPU的服务器上进行训练）
    - 仅记录每个玩家 每局在各个兵种上的投资， 以及局面结束后玩家在各个兵种上的总投资
    - 记录每回合结束后是哪个玩家赢了 注意区分是战斗胜利 一方投降或者null的情况
    - 赢了的玩家对没赢玩家造成的扣血 以及对方原始的总血量
- [x] 确定归一化常数
    - 在18个回合的对局上，我想知道 每回合的投入，以及总投入的 robust均值（去掉首尾各3%）、方差(防止奇异值方差至少为100) 以及多少数据落在了3 sigma之外，帮我画两个盒图来进行展示(jpg形式)等我确认之后 均值就作为transformer的输入
- [x] 预留10%数据作为测试数据，训练一个2/3/4层的transformer
    - Q、V 各完成 2、3、4 层 × 3 seed 的 18 次 CUDA 实验；Q 选 3 层，V 选 2 层
    - 固定切分、模型、散点图与完整指标见 `information/transformer_v1_report.md`
    - 注意有Q和V两种方式 Q是t时刻只能看到我自己策略的，V是t时刻能看到双方策略的
- [ ] 搭建demo，可以随机选取测试集的对局形成体验
- [ ] 制作一个更好的前端

## protocol

- local_data 存储不上传git的数据（帮我加入gitignore）
- information 只存储md文件

## 最终确定的归一化系数

回合投入这么计算是合理的
第一回合 初始兵种 200*2+100*3 = 700 + 回合投入200 = 900 ， 实际均值913 是接近的
第二回合 回合投入 400
第三回合 回合投入 600 均值 638.28 也符合
第四回合 回合投入 800
第五回合 回合投入 1000
之后按照每回合 200增长 其实就可以
按照我这个规则的就可以

总投入比如第三回合后的总投入就按照900+400+600 来计算 ，统计均值是 2,018
（因为大家会拿增援什么的 会偏高一些） 总体也是可以接受的。

## transformer训练

### Transformer v1 结果与复现

本次使用固定的 train/validation/test = 770/96/96 局切分；重复回放副本保留在同一集合，避免泄漏。完整实验结果在 `information/transformer_v1_report.md`：Q-value 最终选择 3 层，测试 RMSE 为 107.121；V-function 最终选择 2 层，测试 RMSE 为 106.034。两者均略优于按回合训练集均值的 RMSE 108.106 基线。

模型与测试散点图：

- `models/transformer_v1/q_best.pt`、`models/transformer_v1/v_best.pt`
- `artifacts/transformer_v1/q_gt_vs_pred_test.jpg`、`artifacts/transformer_v1/v_gt_vs_pred_test.jpg`
- `artifacts/transformer_v1/split_v1.json`（后续 demo 必须复用）

复现命令（优先 CUDA，自动回退 CPU）：

```bash
/www/wensi/robot/.env/venvs/lerobot-libero/bin/python \
  reference_code/train_transformer.py sweep --device auto
```

可单独生成切分、运行一个实验、恢复训练或评估 checkpoint：

```bash
python reference_code/train_transformer.py make-split
python reference_code/train_transformer.py run --task q --depth 3 --seed 20260810 --resume artifacts/transformer_v1/runs/q_depth3_seed20260810/last.pt
python reference_code/train_transformer.py evaluate --checkpoint models/transformer_v1/q_best.pt --split test
```

当前 dense v1 的 933 局末战结果不可知，按既定口径终局贡献为 0；29 局投降按 ±200 记录。正常可确认回合为 ±100，折扣因子为 0.5。

### Transformer v2：累计扣血终局修正

v2 不修改 dense v1，而是从 1v1 的 `winner_damage` 推导终局：112 行中恰有一方累计扣血达到最大血量，在首次越线回合改记 ±1000，并截断后续 152 个回合；2 个双方越线 1v1 和全部 2v2 保持未知。独立报告与审计清单在 `information/transformer_v2_damage_terminal_report.md` 和 `artifacts/transformer_v2_damage_terminal/terminal_inference_audit.json`。

在相同的修正标签上，v2 双视角模型没有整体超过 v1 checkpoint（Q 191.767 vs 191.361，V 192.210 vs 190.709 RMSE），但新增终局子集误差略降。关闭反转的 side0-only 对照具有更高的预测反对称误差，支持当前双视角反转实现。运行：

```bash
/www/wensi/robot/.env/venvs/lerobot-libero/bin/python \
  reference_code/train_transformer_v2.py sweep --device auto
```

### Logistic v1：兵种克制消融

Logistic v1 复用 Transformer v1 的固定 770/96/96 局切分，同时比较双方阵容主效应（86 维）、兵种两两交互（1849 维）和二者组合（1935 维），分别预测单回合胜负与 beta=0.3 累计 reward。完整结果见 `information/logistic_v1_report.md`。

单回合胜负的 interaction 模型测试 AUC 为 0.590（相对随机的 cluster-bootstrap 95% CI 为 `[+0.042, +0.137]`）；累计 reward 的 combined 模型软交叉熵为 0.6860，但映射回 reward 的 RMSE 改善不显著。兵种交互相对阵容主效应的增益同样未达到显著。

完整运行：

```bash
python3 reference_code/train_logistic.py run --config configs/logistic_v1.yaml
```

受限环境可先分别训练和 bootstrap，再自动生成相同报告：

```bash
python3 reference_code/train_logistic.py train --task round_winner --feature interaction
python3 reference_code/train_logistic.py bootstrap --task round_winner --feature interaction
python3 reference_code/train_logistic.py evaluate --model models/logistic_v1/round_winner_interaction.joblib --split test
```

训练两个独立transformer

Q-value （包含双方状态 和 我方策略）
每回合都可以看到双方的总投入O_1:{t-1}，以及我方当前回合的投入o_t
来预测t回合策略下的 最终的累积reward

V-func (仅仅包含双方状态)
每回合可以看到双方的总投入O_1:t，相当于买定离手之后，看双方战斗谁会赢

reward = 我方当回合输赢reward + 血量带来收益 + 累积回合收益

我方当回合输赢reward 为100/-100
这里扣血收益系数先定为0 ， 我还没想好怎么用 照理说 扣血多reward应该更大
累积回合收益就是向后看 beta r_t+1 + beta^2 r_t+2 ... 
注意如果最终游戏结果是确定的 按照 1000/-1000 来记录最终状态
如果是一方投降 按照对方+200 来计算
如果是null 最终状态是0

- 这里尽量使用一个transformer整体训练（T回合产生T个loss）。这样更scalable
- 注意Q-value训练是不对称的 一个战斗要正反都参与训练一次。

交付物产生一些合适的模型
并且找较好的结果，在测试集上绘制Q-value的gt vs pred散点图 和 V-func的gt vs pred 散点图

## 密集回放数据集

在仓库根目录运行：

```bash
python3 reference_code/build_dense_dataset.py
```

会生成可提交到 Git、可直接迁移的两个文件：

- `data/mechabellum_dense_v1.npz`：训练直接使用的数值数组。
- `data/mechabellum_dense_v1.json`：schema、兵种轴、回放清单、跳过原因和质检统计。
- `data/mechabellum_dense_v1.md`：字段、dtype 和标签编码说明。

数据集固定为 `[对局, 18 回合, 双方, 43 兵种维度]`。43 维是当前 33 个普通兵种加 10 个预留槽；逐回合净投资与累计投资均为 `float32`，出售为负数。`round_mask` 标识有效回合，`round_winner` 使用 `-1/null、0/左方、1/右方`，`round_outcome_type` 使用 `0/null、1/正常战斗、2/投降`。末回合投资保留动作日志回退，并由 `investment_source=2` 标识低置信度。

仅导出从 round 0 开始且所有玩家回合连续对齐的回放；round 0 不进入训练序列，round 1 包含开局编队投资。2v2 以玩家 `[0,1]` 和 `[2,3]` 分队，投资、扣血和 `MaxReactorCore` 均按队内平均。将 NPZ 和 JSON 一起复制到 GPU 服务器即可；本阶段不做 10% 切分、reward 计算或 Transformer 训练。

## 归一化常数统计

在仓库根目录运行：

```bash
python3 reference_code/analyze_normalization.py
```

脚本会以每个有效的“对局 × 回合 × 阵营”为样本，对当回合投入和盘面总价值分别按回合统计。每项先移除首尾各 3%（向下取整）样本，再计算均值和方差；归一化方差最小为 100，并报告原始样本落在 3σ 之外的数量。

- `data/mechabellum_normalization_v1.json`：供训练代码读取的 2 × 18 回合统计。
- `information/mechabellum_normalization_v1.md`：中文统计表和计算口径。
- `data/investment_delta_by_round_boxplot.jpg`：当回合投入盒图。
- `data/investment_cumulative_by_round_boxplot.jpg`：盘面总价值盒图。

当前数据集只有第 1–13 回合存在有效样本；第 14–18 回合的 JSON 统计为 `null`，训练时应继续使用 `round_mask` 跳过它们。确认这些常数前不将其接入 Transformer。

## 方法

这里的思路比较简单

由于我们能够获取的盘面是有限的

大约1000盘左右，乘以回合数大约有1万的对局回合

这里我们做极度简化

我们假设 O_A,B_i 是到第i回合双方在各个兵种上的投资总资金

这个总资金包括三个分量

解锁费用 - 兵种等级费用 - 总科技费用

的和

假设 o_A,i 是 玩家A在i回合的单独投入 那么

O_A, t = o_A,1 + ... o_A, t

O是o的累积总和

我们现在需要建立一个Q函数

这个Q函数可以估计 玩家A在 第t回合，进行o_A,t 投资之后
得到的期望reward

Q(o_A,t ; O_A,t-1 ; O_B,t-1 )

这个reward是一个累积值

我们假设对于中间的回合 赢1回合 是 + 100， 输一回合是 - 100

最终回合如果赢下的话 是 + 1000, 输了是-1000

（这里基本还没有考虑扣血的情况 之后做个带扣血的模型，数据记录的时候先把扣血记录下来）

整体会有一个累积收益

Q_groundtruth_t = r_t + beta r_t+1 + beta^2 r_t+2 ... 

beta先取0.5 之后可以在一个 yaml里面进行整体配置

## 回放数据

在local_data/humen_replay中

格式解析可以参考 reference_code/mechabellum_stats.py
或者参考 information/mechabellum_replay_format.md


## 新兵种预留

o预留10个dimension作为新兵种的预留

## 双人游戏

由于游戏里面有双人

如果对局中出现双人（应该比较少）
简化处理 直接一方两个玩家的资源相加 除以2作为o

## 胜率模拟器 MVP

项目现在包含一个本地前后端模拟器：

```bash
# 安装后端依赖（建议 Python 3.11，模型保存环境为 scikit-learn 1.7.2）
python3 -m pip install -r backend/requirements.txt

# 安装并构建前端
cd frontend && npm install && npm run build && cd ..

# 启动服务；构建后的前端会由 FastAPI 托管
python3 run_simulator.py
```

开发时也可以分别启动 `uvicorn backend.run:app --reload` 与 `cd frontend && npm run dev`。
打开 <http://127.0.0.1:8000>，即可进行买兵、卖兵、单个单位升级、科技投入、五栏拖动和三路 Top 5 推荐。素材刷新脚本为 `python3 scripts/download_unit_icons.py`，运行时不会访问 Wiki。

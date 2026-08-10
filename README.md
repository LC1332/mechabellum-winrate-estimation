# mechabellum-winrate-estimation
Machine learning estimate mechabellum's winrate. 

钢铁指挥官 局面胜率估计

## TODO

- [ ] 尝试解析3个对局的数据，打印两个玩家在各个回合投入的资源和owner确认是否解析正确 （干什么 花了多少钱）
- [ ] 批量解析所有数据 形成结构化存储
- [ ] 预留10%数据作为测试数据，训练一个2层的transformer
- [ ] 搭建demo，可以随机选取测试集的对局形成体验
- [ ] 制作一个更好的前端

## protocol

- local_data 存储不上传git的数据（帮我加入gitignore）
- information 只存储md文件

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
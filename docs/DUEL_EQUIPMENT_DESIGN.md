# 装备与切磋系统设计

> 状态：设计稿（未实现）
> 依赖：[ACTIVITY_POINTS_DESIGN.md](ACTIVITY_POINTS_DESIGN.md) 中的积分流水与等级体系

---

## 1. 玩法闭环

```
聊天赚积分 ──► 可用余额 ──► 买装备 / 买免战券
                    ▲                │
                    │                ▼
                切磋抢分 ◄──── 武力值 = 等级 + 装备
```

- 赚分负责「参与」，装备负责「成长」，切磋负责「博弈」，免战券负责「止损」。
- 切磋只转移**余额**，不影响**累计获得**，因此打架不会把活跃榜刷乱。
- 购买装备与免战券是积分回收口（sink），避免全群积分无限膨胀。

---

## 2. 货币模型：累计获得 vs 可用余额

这是整个经济系统的基础，必须先定死。

| 概念 | 计算方式 | 用途 | 会被扣减吗 |
|---|---|---|---|
| **累计获得** | `SUM(points) WHERE category IN ('earn','adjust')` | 总榜、等级、月赛季 | 只有管理员扣分和切磋以外的调整会变 |
| **可用余额** | `SUM(points)`（全部类别） | 买装备、买免战券、被切磋抢 | 会 |

流水类别：

| category | 典型 reason | 累计获得 | 余额 |
|---|---|---|---|
| `earn` | `base` / `length` / `media` / `daily_first` / `reply` | + | + |
| `transfer` | `steal_in` / `steal_out` | 0 | ± |
| `spend` | `buy_item` / `buy_shield` | 0 | − |
| `adjust` | `manual` | ±（管理员可正可负） | ± |

效果：

- 花 90 分买一把剑，总榜名次和等级**不变**，只是余额变少。
- 被人抢走 8 分，总榜名次**不变**，但买东西的钱少了。
- 月榜按「本月累计获得」排，切磋不影响赛季名次。

---

## 3. 武力值

```
武力值 = 10 + 等级 × 5 + Σ 已装备道具的武力加成
```

- 等级取本群「累计获得」推导的 Lv0~Lv6（见主文档 §5），因此 0~30 分。
- 装备总加成上限约 60，武力值实际区间 10 ~ 100。
- 武力值**只**由等级与装备决定，随机波动只作用于胜率判定，不体现在面板上。

`武力值` 指令展示完整拆解：

```
@苏辞 的武力值：48
基础 25（Lv3 活跃） + 武器 铁剑 9 + 护甲 皮甲 8 + 饰品 玉坠 6
今日切磋 4/10 · 余额 136
```

---

## 4. 装备系统

### 4.1 槽位与装备

| 槽位 id | 名称 | 说明 |
|---|---|---|
| `weapon` | 武器 | 主要武力来源 |
| `armor` | 护甲 | 次要武力来源 |
| `charm` | 饰品 | 少量武力 |

- 每个槽位同时只能装备一件；装备新道具会自动卸下同槽位的旧道具。
- 装备**永久拥有**，可随时切换，不消耗、不掉落、不随赛季重置。
- 购买时若目标槽位为空则自动装备，否则放入背包。

### 4.2 商品目录 `data/equipment.json`

沿用 `data/lots.json` 的「JSON 文件 + 内置默认值 + Web UI 编辑」模式：

```json
{
  "slots": [
    {"id": "weapon", "name": "武器"},
    {"id": "armor",  "name": "护甲"},
    {"id": "charm",  "name": "饰品"}
  ],
  "items": [
    {"id": "wood_sword",  "name": "木剑",   "slot": "weapon", "power": 4,  "price": 30},
    {"id": "iron_sword",  "name": "铁剑",   "slot": "weapon", "power": 9,  "price": 90},
    {"id": "black_blade", "name": "玄铁刀", "slot": "weapon", "power": 16, "price": 220},
    {"id": "dragon_sword","name": "龙纹剑", "slot": "weapon", "power": 24, "price": 480},

    {"id": "cloth_armor", "name": "布衣",   "slot": "armor",  "power": 3,  "price": 25},
    {"id": "leather",     "name": "皮甲",   "slot": "armor",  "power": 8,  "price": 80},
    {"id": "chainmail",   "name": "锁子甲", "slot": "armor",  "power": 14, "price": 200},
    {"id": "black_armor", "name": "玄铁甲", "slot": "armor",  "power": 21, "price": 450},

    {"id": "straw_rope",  "name": "草绳",   "slot": "charm",  "power": 2,  "price": 20},
    {"id": "jade",        "name": "玉坠",   "slot": "charm",  "power": 6,  "price": 70},
    {"id": "mirror",      "name": "护心镜", "slot": "charm",  "power": 12, "price": 180},
    {"id": "dragon_jade", "name": "龙纹佩", "slot": "charm",  "power": 18, "price": 400}
  ]
}
```

校验规则（保存与加载都做）：item id 唯一、`slot` 在 slots 中、`power ≥ 0`、`price > 0`、同槽位内价格与武力单调递增。

### 4.3 存储

```sql
-- 已购装备（每人每群独立）
CREATE TABLE IF NOT EXISTS equipment_owned (
    chat_id      TEXT    NOT NULL,
    sender_id    TEXT    NOT NULL,
    item_id      TEXT    NOT NULL,
    purchased_at INTEGER NOT NULL,
    equipped     INTEGER NOT NULL DEFAULT 0,   -- 0/1，同槽位最多一个 1
    PRIMARY KEY (chat_id, sender_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_equipment_member
    ON equipment_owned(chat_id, sender_id);
```

购买流程（单事务）：

1. `balance >= price`？
2. 是否已拥有同 `item_id`？
3. 写流水 `reason='buy_item', category='spend', points=-price`，`message_id='buy:<uuid>'`
4. 插入 `equipment_owned`；若该槽位当前无装备，`equipped=1`
5. 返回「购买成功 + 当前武力值变化」

---

## 5. 切磋系统

### 5.1 发起方式

必须能让机器人稳定拿到对方的 `sender_id`，因此支持两种：

| 方式 | 示例 | 解析 |
|---|---|---|
| @昵称 | `切磋 @莫小贝` 或 `@莫小贝 切磋` | 从聊天内容提取 `@昵称`，在 `user_last_message` 里按 `sender_name` 精确匹配 |
| 引用回复 | 回复某人消息，输入 `切磋` | 内容前缀 `wxid_xxx:` 直接给出对方 wxid，**优先使用** |

- 昵称匹配到 0 人 → 提示「没找到这位成员，确认一下昵称」。
- 匹配到多人 → 提示「有多个同名成员，请改用引用回复」。
- 不能切磋自己、机器人、从未在本群发言的人。
- 新手保护：对方累计获得 < `duel_novice_protection_points`（默认 20，即 Lv0）时不可切磋，提示「对方还在新手保护期」。

### 5.2 胜率公式

设攻击方武力 `A`、防守方武力 `D`：

```
x = 1.15 × ln(A / D)
p = clamp(0.5 + 0.5 × tanh(x), 0.08, 0.92)
```

性质（对应「武力高概率更大，但不是 100%」）：

| 武力对比 A vs D | 胜率 |
|---|---|
| 48 vs 52 | 45% |
| 60 vs 40（1.5×） | 72% |
| 80 vs 40（2×） | 83% |
| 120 vs 40（3×） | 92%（封顶） |
| 10 vs 100（0.1×） | 8%（保底） |

- 封顶 92%：武力碾压也有 8% 翻车概率，群里有话题。
- 保底 8%：弱者不是必输，保留以小博大的戏剧性。
- 双方武力相同时胜率 50%。

### 5.3 抢分公式

```
基础分值  base  = clamp(round(D余额 × 0.08), 2, 20)
差距衰减  mult  = clamp(1 / (1 + 1.2 × max(0, ln(A/D))), 0.2, 1.0)
实际抢分  steal = clamp(round(base × mult), 1, min(D余额, 20))
```

对应「武力值差距越大，抢到的积分越少」：

| 攻击方 vs 防守方 | 衰减系数 | 对方余额 100 时抢到 |
|---|---|---|
| 相当 | 1.00 | 8 |
| 1.5× | 0.67 | 5 |
| 2× | 0.55 | 4 |
| 3× | 0.43 | 3 |
| 10× | 0.27 | 2 |

- 只在**攻击方更强**时衰减；弱者逆袭（`A < D`）不衰减，抢满基础分，鼓励以弱胜强。
- 实际抢分不超过对方余额、不超过单次上限（默认 20）。
- 对方余额 < 2 时无法切磋，提示「对方囊中羞涩，改天再来」。

### 5.4 次数与上限

| 限制 | 默认值 | 说明 |
|---|---|---|
| 每人每日发起次数 | 10 次 | 对应「最多限制 10 次」 |
| 每人每日同一对手次数 | 5 次 | 攻击方→同一防守方每天最多 5 场；不同攻击方各自计数，不共享防守方额度 |
| 每人每日累计抢分 | 50 分 | 抢满后当天不能再抢到分 |
| 单次抢分上限 | 20 分 | |
| 同一对成员冷却 | 5 分钟 | 防止盯一个人打 |
| 失败后力竭 | 2 分钟 | 失败后短时间内不能再次发起 |
| 单日统计口径 | 本地 00:00 重置 | 用 `duels.day` 列统计 |

**失败不扣分**：发起方输了只消耗次数，不损失余额；防守方也不掉分。理由：群聊玩法以「敢玩」为主，掉分惩罚会让成员不敢参与；抢分收益本身已由每日上限和差距衰减控制。

> 若后续想改成对赌，只需加配置 `duel_attacker_loss_ratio`（默认 0），公式复用 §5.3 的防守方视角，流水加一条 `steal_out` 即可，不影响表结构。

### 5.5 免战券

| 项 | 值 |
|---|---|
| 价格 | 20 积分（从余额扣，`reason='buy_shield', category='spend'`） |
| 时长 | 4 小时 |
| 叠加 | 连续购买在原到期时间上累加，总时长封顶 24 小时 |
| 效果 | 期间被切磋一律拦截，**不消耗对方次数**，群里可见「免战中」 |

```sql
CREATE TABLE IF NOT EXISTS duel_shields (
    chat_id    TEXT    NOT NULL,
    sender_id  TEXT    NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id)
);
```

过期行不删除，读取时按 `expires_at > now` 判断，定期清理（每天健康检查时删除过期超过 7 天的行）。

### 5.6 结算流程

```
切磋 @某人
  ├── 开关 / 群白名单
  ├── 解析对手 sender_id（引用优先）
  ├── 自我 / 机器人 / 不在群 / 新手保护 检查
  ├── 免战券有效？ → 拦截，不消耗次数
  ├── 力竭冷却 / 同对冷却 / 每日次数 / 每日同对手次数 / 每日抢分上限
  ├── 计算双方武力 → 胜率 p
  ├── roll = random.random() < p
  ├── 胜：  attacker +steal（category=transfer, reason=steal_in）
  │        defender -steal（category=transfer, reason=steal_out）
  └── 负：  不转分，开始 2 分钟力竭
```

整个流程在 `PointStore._lock` + 单个 `with conn:` 事务内完成，余额校验与扣减原子化；Web UI 的调整接口共用同一个 store 与锁。

### 5.7 切磋日志

```sql
CREATE TABLE IF NOT EXISTS duels (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id        TEXT    NOT NULL,
    attacker_id    TEXT    NOT NULL,
    attacker_name  TEXT    NOT NULL DEFAULT '',
    defender_id    TEXT    NOT NULL,
    defender_name  TEXT    NOT NULL DEFAULT '',
    attacker_power INTEGER NOT NULL,
    defender_power INTEGER NOT NULL,
    win_prob       REAL    NOT NULL,
    attacker_win   INTEGER NOT NULL,
    points_moved   INTEGER NOT NULL DEFAULT 0,
    shield_blocked INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL,
    day            TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_duels_attacker_day ON duels(chat_id, day, attacker_id);
CREATE INDEX IF NOT EXISTS idx_duels_defender_day ON duels(chat_id, day, defender_id);
CREATE INDEX IF NOT EXISTS idx_duels_pair
    ON duels(chat_id, attacker_id, defender_id, created_at DESC);
```

### 5.8 结果文案

成功：

```
@苏辞 切磋 莫小贝
你 武力48 · 对手 武力52 · 胜率45%
结果：切磋成功！
从 莫小贝 处抢到 8 积分
余额 136 · 今日 4/10 次 · 已抢 23/50
```

失败：

```
@苏辞 切磋 莫小贝
你 武力48 · 对手 武力52 · 胜率45%
结果：惜败。对方稳如泰山，你这一下没占到便宜。
失败不扣分，2 分钟后可再次发起。
今日 4/10 次
```

被免战挡下：

```
@苏辞 莫小贝 正在免战中（剩余 3小时12分），切磋未发起，不消耗次数。
```

---

## 6. 群内指令

全部为裸文本，与「活跃查询」「积分」并列，在 `@mention` 分支之前处理。

| 输入 | 行为 |
|---|---|
| `商店` | 列出装备目录，标注已拥有 ✓ 与余额 |
| `购买 铁剑` | 扣余额购买，自动装备空槽位 |
| `装备 铁剑` | 切换到该装备 |
| `卸下 武器` | 卸下指定槽位 |
| `我的装备` / `背包` | 已拥有道具、当前装备、总加成 |
| `武力值` / `战力` | 武力值拆解 + 今日次数 + 余额 |
| `我的信息` | 一行总览：积分、余额、武力值 |
| `查询 <名字>` / `查询 我` | 一行查询他人：积分、武力值（**不需要 @**） |
| `战力榜` | 本群武力值 TOP10 |
| `切磋 @某人` / 引用后 `切磋` | 发起切磋 |
| `买免战券` / `免战` | 购买 4 小时护盾 |
| `切磋记录` | 自己最近 10 场结果 |

解析注意：

- `商店`、`背包`、`武力值`、`战力榜`、`我的信息` 只做完全匹配，防止误伤正常聊天。
- `购买` / `装备` / `卸下` 用前缀匹配并取剩余文本作为装备名。
- `切磋` 允许「`@昵称　切磋`」与「`切磋 @昵称`」两种顺序；从内容中剥离 `@昵称` 后再判断剩余部分是否为空。
- 昵称中可能含空格，按「`@` 到下一个不可见分隔符/空白/`切磋` 关键字」切分，并从最长候选开始匹配已有成员名。

### 6.1 简洁查询（不带 @，回复也不 @ 人）

成员在群里 @ 别人会产生提醒，用起来有心理负担，因此提供两条**纯文本、无 @** 的简洁指令。

**查询他人**

```
输入：查询 莫小贝
```

```
莫小贝 积分：96 武力值：52
```

- 输入**不需要也不建议** @；写了 `查询 @莫小贝` 也能识别，会自动剥离 `@`。
- 只显示对方的**积分（累计获得）与武力值**，不显示余额——余额属于个人资产，避免公开比较。
- 支持 `查询 我`，等价于 `我的信息` 的简版。
- **不误伤正常聊天**：`查询` 后必须能唯一匹配到本群成员才响应。`查询一下天气`、`查询快递到哪了` 这类匹配不到成员的句子，机器人保持沉默，不回复任何内容。
- 同名成员：回复「本群有多个「张三」：张三(2)、张三(3)，请用 我的信息 或引用回复区分」，不猜人。
- 查询未在群内发过言的陌生名字：不响应（与上一条同规则）。

**我的信息**

```
输入：我的信息
```

```
苏辞 积分：128 余额：136 武力值：48
```

- 固定三字段：累计获得积分、可用余额、武力值。
- 与 `积分`（详细卡片，含月榜名次、等级、距下一级）区分：`我的信息` 是**一行快照**，`积分` 是**详细卡片**。
- 回复**不带 @发起人**；需要指明是谁时直接以成员名开头即可，避免每次查询都提醒对方。

实现要点：

- 解析放在裸指令分支，`parse_points_command()` 返回 `Command(kind='query', target=...)`。
- 名字解析复用切磋的 `resolve_target()`：`user_last_message.sender_name` 精确匹配 + `NicknameService` 反向映射，唯一命中才有效。
- `查询` 属于**弱指令**：匹配失败必须返回 `None`（当作普通聊天继续走 AI 流程），不能返回「没找到」之类的提示，否则会频繁打断闲聊。
- 指令本身产生的这条消息仍正常计分，不特殊处理。

---

## 7. 配置项（`src/config.py`）

| 名称 | 默认值 | 说明 |
|---|---|---|
| `equipment_enabled` | `false` | 装备与商店总开关 |
| `equipment_path` | `data/equipment.json` | 商品目录路径 |
| `duel_enabled` | `false` | 切磋总开关 |
| `duel_groups` | `["*"]` | 生效群，`*` 全部 |
| `duel_prob_k` | `1.15` | 胜率曲线斜率，越大武力优势越明显 |
| `duel_min_prob` | `0.08` | 胜率下限 |
| `duel_max_prob` | `0.92` | 胜率上限 |
| `duel_base_steal_ratio` | `0.08` | 基础抢分比例（对方余额百分比） |
| `duel_reward_k` | `1.2` | 差距衰减强度，越大高手抢得越少 |
| `duel_per_fight_cap` | `20` | 单次抢分上限 |
| `duel_daily_attempts` | `10` | 每日发起次数 |
| `duel_daily_pair_attempts` | `5` | 每日同一攻击方→防守方次数 |
| `duel_daily_steal_cap` | `50` | 每日累计抢分上限 |
| `duel_pair_cooldown_sec` | `300` | 同一对冷却（5 分钟） |
| `duel_loss_cooldown_sec` | `120` | 失败后力竭时间（2 分钟） |
| `duel_novice_protection_points` | `20` | 新手保护门槛（累计获得） |
| `shield_price` | `20` | 免战券价格 |
| `shield_duration_sec` | `14400` | 单张免战时长 |
| `shield_max_sec` | `86400` | 免战最长可叠加时长 |

配置同步位置：`.env.example`、`/api/config` 字段白名单、`FeaturesPanel` 开关。

---

## 8. 代码结构

```
src/points/
    equipment.py   load_equipment_catalog() / save_equipment_catalog()
                   validate_catalog()
                   owned_items(chat_id, sender_id), equip(), buy()
    duel.py        power_of(member) -> PowerBreakdown
                   win_probability(a, d)
                   steal_amount(a, d, defender_balance)
                   resolve(store, ..., rng=None) -> DuelResult
    handler.py     在既有积分指令基础上扩展：商店/购买/装备/武力值/切磋/免战
    store.py       扩展：equipment_owned、duel_shields、duels、余额查询、事务化相互转账
```

`resolve()` 接收可注入的 `random.Random` 或 `roll` 回调，测试里固定结果，不做概率统计式断言。

### 8.1 接入点

| 位置 | 改动 |
|---|---|
| `src/db/schema.py` | 追加 §4.3 / §5.5 / §5.7 的 DDL |
| `src/points/store.py` | 新增余额、装备、护盾、切磋日志方法 |
| `src/points/handler.py` | 新增指令解析与文案 |
| `src/router.py` | 与 `积分` 指令同一处分流（`is_at` 之前） |
| `src/web/server.py` | POST 白名单加入 `/api/equipment/buy`、`/api/equipment/equip`、`/api/duels/config` |
| `ui/src/components/PointsPanel.jsx` | 新增「商店」「装备」「切磋记录」子页 |
| `CODEBASE_REFERENCE.md` | 新增 `src/points/` 各文件与函数条目 |

### 8.2 调用链（切磋）

```
MessageRouter.handle(msg)
  └── parse_points_command()  → Command(kind='duel', target=...)
        └── PointHandler.handle_duel()
              ├── resolve_target(msg, chat_id)        # 引用前缀 > @昵称 > 唯一匹配
              ├── DuelEngine.resolve(...)
              │     ├── power_of(attacker) / power_of(defender)   # 等级 + 装备
              │     ├── win_probability(A, D)
              │     ├── 次数 / 冷却 / 护盾 / 新手保护 校验
              │     ├── rng.random() < p
              │     └── PointStore.transfer(points, from, to, message_id)
              └── format_duel_result()
```

---

## 9. Web UI

在「积分榜」面板内新增三个子标签，共用群选择器：

| 子标签 | 内容 |
|---|---|
| 商店 | 商品卡片（槽位分组）、价格、武力、已拥有状态、购买按钮 |
| 装备 | 每人的当前装备与武力值拆解；管理员可查看全群战力排行 |
| 切磋 | 切磋流水（时间、攻守双方、武力和胜率、结果、抢分）、每日次数统计 |

新增 API：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/equipment?chat_id=` | 目录 + 我的已购/装备状态 |
| POST | `/api/equipment/buy` | `{chat_id, sender_id, item_id}` |
| POST | `/api/equipment/equip` | `{chat_id, sender_id, item_id, equipped}` |
| GET | `/api/equipment/catalog` / POST 同路径 | 商品目录读写（仿 `/api/lots`） |
| GET | `/api/duels?chat_id=&limit=50` | 切磋记录 |
| GET | `/api/duels/stats?chat_id=` | 今日次数、抢分、胜率、免战状态 |

POST 路径必须加入 `server.py:1164` 的白名单。

---

## 10. 测试计划

| 文件 | 覆盖 |
|---|---|
| `tests/test_duel_formulas.py` | 胜率表（相同/1.5×/2×/3×/10×）、上下限、抢分衰减、余额与单次上限 |
| `tests/test_equipment.py` | 目录校验、购买扣款、重复购买、自动装备、换装、余额不足 |
| `tests/test_duel_engine.py` | 注入 rng 验证胜负分支、免战拦截、同对冷却、每日次数、被挑战次数、每日抢分上限、新手保护 |
| `tests/test_points_store.py` | 转账事务原子性、`steal_in/steal_out` 成对写入、余额不为负 |
| `tests/test_points_handler.py` | 引用优先解析、同名歧义提示、@昵称两种顺序、指令与普通聊天不冲突；`查询` 弱指令：匹配到成员才响应、`查询一下天气` 必须返回 None |
| `tests/test_web_api.py` | 新 POST 白名单、目录读写校验、非法购买拒绝 |
| Playwright | 商店/装备/切磋子页渲染、空数据、暗色模式 |

手动验证：

1. 两个号互切，确认胜率与抢分符合公式（可用 `/api/duels` 对账）。
2. 买免战券后被人切磋，确认拦截且对方次数不减。
3. 连续切磋至 10 次，确认第 11 次被拒。
4. 同一对手连续切磋，确认 5 分钟冷却。
5. 花光余额后购买装备，确认拒绝且不产生流水。

---

## 11. 数值调参建议

上线首月建议先跑「温和档」，观察后再收紧：

| 参数 | 温和档（默认） | 激烈档 |
|---|---|---|
| 每日次数 | 10 | 15 |
| 单次抢分上限 | 20 | 30 |
| 每日抢分上限 | 50 | 100 |
| 每日同一对手次数 | 5 | 8 |
| 同对冷却 | 5 分钟 | 3 分钟 |
| 免战券价格 | 20 | 40 |

判断标准：如果一周内出现「某人余额被清零」或「无人敢发起切磋」，说明偏激烈；如果切磋记录一周不足 10 条，说明门槛太高，优先降低免战券价格或提高每日次数。

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| 高武力垄断抢分 | 差距越大抢得越少 + 每日被挑战次数上限 + 新手保护 |
| 小号互刷 | 切磋不进入累计获得与月榜；同对冷却；被挑战次数上限 |
| 战力质疑「氪金就赢」 | 胜率封顶 92%，并公示公式；装备武力上限受控 |
| 被抢生气退群 | 免战券 + 新手保护 + 失败不扣分 + 每日被抢次数上限 |
| 余额清零无法翻身 | 聊天正常赚分；装备永久不丢；抢分有每日上限 |
| 昵称歧义/改名 | 引用回复优先，直接拿 wxid；显示名始终走 `NicknameService` |
| 并发扣款超支 | 单事务 + `PointStore._lock`，余额检查与写入原子化 |
| 装备目录被改坏 | 加载时校验，失败回退内置默认值并记 warning |

---

## 13. 实施分期

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **D1 装备** | 目录 JSON、购买/装备/卸下、武力值、商店指令、测试 | 主设计 P1（积分流水） |
| **D2 切磋** | 胜率与抢分公式、次数/冷却/上限、切磋日志、结果文案 | D1 |
| **D3 免战** | 护盾购买与拦截、剩余时间展示 | D2 |
| **D4 Web** | 商店/装备/切磋记录子页、目录编辑器、统计接口 | D3 |

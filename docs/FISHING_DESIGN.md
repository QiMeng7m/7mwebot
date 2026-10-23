# 钓鱼系统设计

> 状态：已实现
> 依赖：[ACTIVITY_POINTS_DESIGN.md](ACTIVITY_POINTS_DESIGN.md) 的积分流水与余额模型

---

## 1. 定位与经济模型

钓鱼是**余额来源**，不是积分来源。它与聊天、切磋、装备商店共同构成群内经济闭环：

```
聊天 ──► 积分（累计获得）+ 余额 ──► 买装备 / 买免战券 / 买鱼竿鱼饵
                                        │
钓鱼 ──► 余额（不计积分）◄──────────────┘
```

| 概念 | 计算方式 | 钓鱼会影响吗 |
|---|---|---|
| **累计获得**（积分） | `SUM(points) WHERE category IN ('earn','adjust')` | ❌ 不会 |
| **可用余额** | `SUM(points)`（全部类别） | ✅ 增加 |
| 总榜 / 月榜 / 等级 | 只看累计获得 | ❌ 不会 |
| 余额榜 / 装备 / 切磋 / 免战 | 看余额 | ✅ 会 |

钓鱼流水使用新的 `category='fish'`：

```sql
-- 每次成功入账一条（amount > 0 时）
INSERT INTO point_events
  (chat_id, sender_id, sender_name, message_id, reason, category,
   points, day, season, created_at)
VALUES (?, ?, ?, 'fish:<message_id>:<cast_index>', 'fish_catch', 'fish',
        <amount>, ?, ?, ?);
```

因为 `balance_of()` 汇总全部类别，而 `earned_of()` / `board()` / `member_stats()`
只统计 `earn` 与 `adjust`，所以钓鱼天然满足「只加余额、不加积分」。

> 与切磋 `transfer` 的区别：`transfer` 是成员之间转移，`fish` 是系统增发。
> 两者都不进入累计获得，但分开统计便于对账和 Web UI 展示。

---

## 2. 玩法闭环

```
钓鱼
 ├── 消耗 1 次本小时次数（默认 30 次，整点重置）
 ├── 自动消耗 1 个鱼饵（有库存时；便宜的优先）
 ├── 摇稀有度 → 摇重量 → 计算价值
 ├── 立即入账余额（受每日收益上限约束）
 └── 按稀有度延迟 N 秒后在群里播报结果
```

- 稀有鱼不是高级装备的专利：低级鱼竿/鱼饵也能钓到传说鱼，只是权重低。
- 高级鱼竿/鱼饵提高**稀有鱼权重**和**价值加成**，并降低空竿率。
- 每日收益上限（默认 120 余额）防止钓鱼成为无限刷余额的入口。
- 多竿模式（`钓鱼 5`）一次结算多竿，但只发一条汇总播报，延迟取本次最稀有鱼的值。

---

## 3. 数值与配置

### 3.1 鱼竿 `rods`

| 名称 | 价格（余额） | 稀有加成 | 价值加成 | 空竿减免 |
|---|---|---|---|---|
| 竹竿（默认） | 0 | 0% | 0% | 0% |
| 玻璃钢竿 | 120 | +15% | +10% | 2% |
| 碳素竿 | 300 | +30% | +20% | 4% |
| 龙纹竿 | 700 | +50% | +35% | 6% |
| 龙渊竿 | 1500 | +80% | +50% | 8% |

- 每个成员同时只装备一根，购买后自动装备。
- 默认竹竿无需购买，永远可用；「换鱼竿 竹竿」可切回默认。
- 稀有加成只作用于 `rare / epic / legendary`，不放大垃圾和普通鱼。

### 3.2 鱼饵 `baits`

| 名称 | 价格/个 | 稀有加成 | 价值加成 |
|---|---|---|---|
| 蚯蚓 | 2 | +10% | 0% |
| 红虫 | 5 | +25% | +10% |
| 神秘饵 | 15 | +60% | +20% |

- 每次钓鱼自动消耗 1 个，**价格低的优先**，避免误烧高级饵。
- 没有鱼饵也能钓鱼，只是稀有概率低。

### 3.3 鱼种 `species`

| 稀有度 | 例子 | 基础价值 | 权重 |
|---|---|---|---|
| 垃圾 junk | 破靴子、水草、易拉罐 | 0 | 较高 |
| 普通 common | 鲫鱼、鲤鱼、草鱼、鲢鱼、泥鳅、小龙虾 | 3~9 | 高 |
| 稀有 rare | 鲈鱼、桂鱼、黄鳝、甲鱼 | 12~22 | 中 |
| 史诗 epic | 中华鲟、大闸蟹、河豚 | 35~45 | 低 |
| 传说 legendary | 锦鲤、龙鱼、黄金鲤鱼 | 88~200 | 极低 |

权重公式：

```
有效权重 = 基础权重 × (1 + 稀有加成 × 稀有度系数)
稀有度系数：junk 0 / common 0 / rare 1 / epic 1.5 / legendary 2
```

### 3.4 反馈延迟 `delays`

| 结果 | 延迟 |
|---|---|
| 空竿 / 垃圾 | 5 秒 |
| 普通 | 5 秒 |
| 稀有 | 15 秒 |
| 史诗 | 30 秒 |
| 传说 | 60 秒（上限） |

越稀有，机器人「遛鱼」越久，群里越有悬念。延迟只影响**播报时间**，
余额和次数在抛竿时已经立即结算。

### 3.5 价值公式

```
尺寸系数 = 0.6 + 0.8 × (重量 - 最小重量) / (最大重量 - 最小重量)
价值 = round(基础价值 × 尺寸系数 × (1+鱼竿价值加成) × (1+鱼饵价值加成))
价值 = clamp(价值, 1, 当日剩余上限)        # 基础价值为 0 的垃圾保持 0
```

### 3.6 目录文件 `data/fishing.json`

沿用 `data/equipment.json` / `data/lots.json` 的「JSON + 内置默认值 + Web UI 编辑」模式：

```json
{
  "rods": [
    {"id": "bamboo", "name": "竹竿", "price": 0,
     "rare_bonus": 0.0, "value_bonus": 0.0, "miss_reduction": 0.0,
     "default": true}
  ],
  "baits": [
    {"id": "worm", "name": "蚯蚓", "price": 2,
     "rare_bonus": 0.1, "value_bonus": 0.0}
  ],
  "species": [
    {"id": "carp", "name": "鲤鱼", "rarity": "common", "weight": 16,
     "min_weight_g": 300, "max_weight_g": 1500, "base_value": 7}
  ],
  "delays": {"miss": 5, "junk": 5, "common": 5,
             "rare": 15, "epic": 30, "legendary": 60}
}
```

校验规则：id/名称唯一；必须且只能有一根 `default` 鱼竿且价格为 0；
鱼饵价格 > 0；稀有度必须合法；重量范围合法；延迟 0~300 秒。
配置损坏时回退内置默认值并记 warning，不影响机器人启动。

---

## 4. 延迟播报机制

处理器是同步返回文本的，不能在回调线程里 `sleep(60)` —— WCDB 的回调池只有
4 个 worker，睡觉会阻塞其他群的消息处理。因此引入 `DelayedReply`：

```python
class DelayedReply(str):       # src/points/models.py
    delay_sec: int             # 继承 str，旧代码/测试仍然当作字符串用
```

调用链：

```
router.handle(msg)
  └── PointHandler.handle() → 「钓鱼」
        └── FishingService.cast(...)
              ├── 立即写 fishing_casts + point_events（余额到账）
              └── 返回 FishingResult(delay_sec = max(本次渔获))
        └── _format_fishing() → DelayedReply(text, delay_sec, ack_text)
  └── MessageRouter._schedule_delayed_reply(msg, reply)
        ├── 有 sender：
        │     ├── 立即返回 ack_text（「钓鱼中，请稍等…」）走正常回复路径
        │     └── 起 daemon 线程，sleep(delay) 后调用 backend.send_text()
        └── 无 sender（单测/CLI）：直接返回文本，立即发送
```

群聊里看到的效果：

```
用户：钓鱼
机器人（立即）：张三 钓鱼中，请稍等…
机器人（15 秒后）：张三 钓到 鲈鱼（1.50kg，稀有），余额 +16。
                  本次入账 +16 · 余额 165 · 今日 3/30 次 · 收益 23/120
```

多竿 `钓鱼 5` 的 ack 为「张三 正在钓 5 竿，请稍等…」，播报仍是一条汇总。

`Bot.run()` 在 backend 创建后注册发送器，并复用 backend 的 `_window_lock`：

```python
router.set_reply_sender(lambda chat_id, text: backend.send_text(chat_id, text))
```

注意：延迟发送只负责「播报」，不改变已结算的余额；机器人重启会丢失尚未发送的
延迟消息，但不会重复入账（`fishing_casts` 的 `UNIQUE(message_id, cast_index)`）。

---

## 5. 存储

```sql
CREATE TABLE IF NOT EXISTS fishing_casts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      TEXT    NOT NULL,
    sender_id    TEXT    NOT NULL,
    sender_name  TEXT    NOT NULL DEFAULT '',
    message_id   TEXT    NOT NULL,
    cast_index   INTEGER NOT NULL DEFAULT 0,
    rod_id       TEXT    NOT NULL DEFAULT '',
    bait_id      TEXT    NOT NULL DEFAULT '',
    species_id   TEXT    NOT NULL DEFAULT '',   -- 空 = 空竿
    species_name TEXT    NOT NULL DEFAULT '',
    rarity       TEXT    NOT NULL DEFAULT '',
    weight_g     INTEGER NOT NULL DEFAULT 0,
    amount       INTEGER NOT NULL DEFAULT 0,
    day          TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    UNIQUE(message_id, cast_index)
);

CREATE TABLE IF NOT EXISTS fishing_inventory (
    chat_id      TEXT    NOT NULL,
    sender_id    TEXT    NOT NULL,
    kind         TEXT    NOT NULL,             -- 'rod' | 'bait'
    item_id      TEXT    NOT NULL,
    quantity     INTEGER NOT NULL DEFAULT 0,
    equipped     INTEGER NOT NULL DEFAULT 0,
    purchased_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id, kind, item_id)
);
```

- `fishing_casts` 同时承担「每小时次数」「冷却」「图鉴」「榜单」「大鱼榜」。
- 空竿也会写一行（`species_id=''`、`amount=0`），因此次数和冷却不会被绕过。
- `fishing_inventory` 中鱼竿 `quantity=1`、`equipped=0/1`；鱼饵是叠加数量。

---

## 6. 群内指令

| 输入 | 行为 |
|---|---|
| `钓鱼` | 抛 1 竿 |
| `钓鱼 5` / `钓鱼 五` | 抛最多 5 竿（上限 `FISHING_MAX_BATCH`，默认 10） |
| `鱼竿` | 鱼竿商店：价格、稀有/价值加成、已拥有/已装备 |
| `买鱼竿 龙纹竿` | 扣余额购买并自动装备 |
| `换鱼竿 竹竿` | 切换已拥有的鱼竿（竹竿=默认） |
| `鱼饵` | 鱼饵商店与持有数量 |
| `买鱼饵 红虫 10` | 购买鱼饵（默认 10 个、默认 cheapest） |
| `鱼篓` / `我的鱼` | 今日次数/收益、鱼竿、鱼饵、累计渔获、图鉴进度、最近 5 条 |
| `图鉴` / `钓鱼图鉴` | 按稀有度展示鱼种，✓ 表示已钓到 |
| `钓鱼榜` | 按累计钓鱼余额排名 |
| `钓鱼榜 今日` | 今日钓鱼余额排名 |
| `钓鱼榜 大鱼` | 单条最重的大鱼榜 |
| `钓鱼榜 数量` | 按渔获条数排名 |
| `钓鱼记录` | 自己最近 10 条渔获 |

解析注意：

- 「钓鱼」是弱前缀指令：`钓鱼 5`、`钓鱼 五` 才进入多竿模式；
  「今天去钓鱼吗」不会匹配（`looks_like_command` 只认裸命令或前缀 + 参数）。
- 「钓鱼榜 今日」在裸指令分支单独解析，避免误伤聊天。
- 所有回复不带 @，避免给发起人额外提醒。

### 6.1 结果文案

抛竿后先立即回一条「钓鱼中，请稍等…」，到点后再播报渔获。

单竿普通鱼（立即 + 5 秒后）：

```
张三 钓鱼中，请稍等…

张三 钓到 鲤鱼（1.20kg，普通），余额 +9。
本次入账 +9 · 余额 149 · 今日 3/30 次 · 收益 23/120
```

多竿（立即 ack，取最稀有鱼的延迟后播报）：

```
张三 正在钓 3 竿，请稍等…

张三 抛了 3 竿：
1. 鲫鱼（220g，普通）+4
2. 空竿，鱼儿跑了
3. 鲈鱼（1.5kg，稀有）+16
本次入账 +20 · 余额 169 · 今日 6/30 次 · 收益 43/120
```

余额不足/次数用完/冷却等错误**立即返回**，不延迟。

---

## 7. 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `FISHING_ENABLED` | `true` | 钓鱼总开关 |
| `FISHING_GROUPS` | `*` | 生效群白名单 |
| `FISHING_CATALOG_PATH` | `data/fishing.json` | 鱼竿/鱼饵/鱼种/延迟目录 |
| `FISHING_HOURLY_ATTEMPTS` | `30` | 每人每小时抛竿次数（每个整点重置） |
| `FISHING_DAILY_CAP` | `120` | 每人每日钓鱼入账上限（0=不限） |
| `FISHING_COOLDOWN_SEC` | `5` | 两次抛竿之间的最短间隔 |
| `FISHING_MAX_BATCH` | `10` | 单条「钓鱼 N」最多竿数 |
| `FISHING_MISS_CHANCE` | `0.15` | 基础空竿率（鱼竿会减免，最低 2%） |

配置同步位置：`.env.example`、`src/config.py:load_config()`。

---

## 8. 代码结构

```
src/points/
    fishing_catalog.py  DEFAULT_RODS / DEFAULT_BAITS / DEFAULT_SPECIES / DEFAULT_DELAYS
                        validate_catalog(), FishingCatalog.load()/save()
                        find_rod()/find_bait()/by_species_id()/delay_for()
    fishing.py          species_weight(), pick_species(), roll_weight(),
                        catch_amount(), effective_miss_chance()
                        FishingService.cast(msg, count=1, rng=None, now_ts=None)
                        FishingService.stats(chat_id, sender_id)
    store.py            fishing_attempts_since / fishing_last_cast_at
                        fishing_earned_on_day / fishing_totals
                        fishing_inventory / fishing_equipped_rod
                        buy_fishing_rod / buy_fishing_bait / equip_fishing_rod
                        commit_fishing_cast（cast + 扣饵 + 入账，单事务）
                        fishing_recent / fishing_board / fishing_collection
    handler.py          _fish / _fish_rods / _buy_rod / _equip_rod
                        _fish_baits / _buy_bait / _fish_bag / _fish_book
                        _fish_board / _fish_log
    models.py           FishCatch, FishingResult, DelayedReply
```

接入点：

| 位置 | 改动 |
|---|---|
| `src/db/schema.py` | `fishing_casts`、`fishing_inventory` |
| `src/config.py` | 8 个 `FISHING_*` 字段 + 校验 |
| `src/router.py` | 构造 `FishingService`；`set_reply_sender()` / 延迟调度 |
| `src/bot.py` | backend 创建后注册延迟发送器（复用 `_window_lock`） |
| `src/web/server.py` | `/api/fishing*`、POST 白名单 |
| `ui/src/components/PointsPanel.jsx` | 「钓鱼」子页：鱼竿/鱼饵/榜单/大鱼/延迟说明 |

---

## 9. Web API / UI

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/fishing?chat_id=&sender_id=` | 目录、榜单、最近渔获、限额；带 `sender_id` 时返回个人统计与库存 |
| POST | `/api/fishing/cast` | 本地/测试用抛竿 `{chat_id,sender_id,count}` |
| POST | `/api/fishing/buy_rod` | `{chat_id,sender_id,rod_id}` |
| POST | `/api/fishing/buy_bait` | `{chat_id,sender_id,bait_id,quantity}` |
| POST | `/api/fishing/equip_rod` | `{chat_id,sender_id,rod_id}` |
| GET/POST | `/api/fishing/catalog` | 目录读写（仿 `/api/equipment/catalog`） |

前端「群活跃积分 → 钓鱼」子页展示：成员选择、今日次数/收益、鱼竿商店、
鱼饵商店、钓鱼榜、大鱼榜、稀有度延迟说明。样式与装备商店保持一致。

---

## 10. 测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_fishing.py` | 目录校验与回退、稀有度权重、重量/价值公式、空竿率夹取、每小时次数/整点重置/收益上限、冷却、幂等、扣饵、鱼竿加成、稀有度延迟、指令解析 |
| `tests/test_points_router.py` | 无 sender 时返回带 `delay_sec` 的字符串；注册 sender 后返回 `None` 并在 daemon 线程发送 |
| `tests/test_points_store.py` | 既有余额/榜单断言，确认 `fish` 类别不进入累计获得 |
| `tests/test_functional.py` | 前端渲染、暗色模式、侧边栏（需要 7327 端口有同一份服务） |

手动验证：

1. 发「钓鱼」观察 5 秒后播报普通鱼；连续抽到传说鱼时应等待约 60 秒。
2. 钓鱼前后发「积分」，确认累计积分/等级不变，余额增加。
3. 买高级鱼竿/鱼饵后观察稀有鱼出现频率与入账价值变化。
4. 连续「钓鱼 5」直到本小时次数用完，确认下一次被拒并提示整点重置；整点后再钓确认恢复。
5. 在 Web UI 修改 `data/fishing.json` 的 `delays.legendary`，确认播报延迟变化。

---

## 11. 调参与风险

| 风险 | 对策 |
|---|---|
| 钓鱼导致余额通胀 | 每日收益上限；鱼竿/鱼饵是余额回收口；稀有鱼权重受控 |
| 高级装备碾压 | 稀有加成只放大稀有鱼权重，低竿仍保留基础概率；价值加成有上限 |
| 多竿刷屏 | 单条指令最多 10 竿；只发一条汇总播报 |
| 延迟阻塞消息处理 | daemon 线程 + `DelayedReply`，不占用 WCDB 回调池 |
| 重启丢播报 | 只丢消息不丢余额；`UNIQUE(message_id, cast_index)` 防重复入账 |
| 目录被改坏 | 加载校验失败回退内置默认值并记 warning |
| 鱼饵误烧 | 自动消耗价格最低的鱼饵；高级饵需主动购买 |

首月建议先跑默认「温和档」：30 次/日、120 余额上限、15% 空竿率。
若一周内出现大量余额溢出，优先降低 `FISHING_DAILY_CAP`；若无人钓到稀有鱼，
优先提高鱼竿 `rare_bonus` 或降低 `FISHING_MISS_CHANCE`。

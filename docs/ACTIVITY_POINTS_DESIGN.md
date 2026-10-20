# 群活跃积分系统设计

> 状态：设计稿（未实现）
> 相关模块：`src/activity.py`（活跃查询）、`src/db/`、`src/router.py`、`src/admin.py`、`src/web/server.py`、`ui/src/components/`

---

## 1. 目标与边界

### 目标

1. 把「群成员发言」量化为积分，按群独立累计。
2. 总榜 + 月赛季双轨：总榜做长期等级，月榜制造短期竞争。
3. 积分只用于**排名与等级称号**，不消耗、不兑换（兑换留作后续扩展位）。
4. 群内纯文本指令即可查询，无需 @机器人。
5. Web UI 提供榜单、成员明细、规则配置。

### 明确不做

| 不做 | 原因 |
|---|---|
| 积分消耗 / 兑换 | 当前定位是荣誉体系；兑换需要库存、流水、审核，单独一期 |
| 跨群总榜 | 各群独立统计，避免「大群碾压小群」；`chat_id` 是天然隔离键 |
| AI 给消息打分 | 成本高、延迟高、结果不可复现，且每条消息都要过模型 |
| 撤回消息扣分 | 撤回后消息可能已入库，扣分口径难以自洽；简化为一律不扣 |
| 负数「惩罚」 | 只保留管理员手动调整，避免误伤 |

### 与已有功能的关系

- **活跃查询**（`src/activity.py`）保持不变，仍按「消息条数」统计近 N 天。
- 积分是**另一套口径**：条数 + 质量加分 + 连续活跃，两者可以同时存在、互不干扰。
- 复用 `messages` 表作为回溯数据源，但运行时以积分流水表为准（见 §4 选型说明）。

---

## 2. 计分规则

### 2.1 单条消息计分

对每条**非系统消息**（`msg_type != 10000`）计算：

| reason | 条件 | 分值 | 说明 |
|---|---|---|---|
| `base` | 任意正常消息 | +1 | 保底分 |
| `length` | 去掉空白后长度 ≥ 20 | +1 | 20 字以上算有内容 |
| `length_long` | 去掉空白后长度 ≥ 100 | +1 | 与 `length` 叠加 |
| `media` | `msg_type ∈ {3 图片, 34 语音, 43 视频}` | +1 | 表情包（47）、链接（49）不加 |
| `daily_first` | 该成员当天在本群的第一条计分消息 | +2 | 鼓励每天回来冒泡 |
| `reply` | 本条消息通过引用回复了某人 | +2 **给被回复者** | 见 2.2 |

**单条消息自身封顶 5 分**（`base + length + length_long + media + daily_first`），超出的部分截断。`reply` 奖励记在被回复者账上，不受此封顶约束。

### 2.2 回复奖励的识别

微信回复会在消息内容前插入 `wxid_xxx:\n` 前缀，现有代码已在 `src/router.py:246` 与 `src/activity.py:63` 处理该格式。计分时：

```
消息 B 由 B 发送，内容前缀为 "wxid_A:"
  → 给 A 记一条 reason='reply', points=+2，message_id 用 B 的 message_id
```

边界：

- A == B（自己回复自己）→ 不计
- A 是机器人 → 不计（无法拿到机器人 wxid 时，退化为按显示名匹配 `bot_display_name`）
- 前缀不是 wxid_ 格式（部分版本显示昵称）→ 本版本不计，记录 debug 日志

### 2.3 计分示例

| 场景 | 得分 |
|---|---|
| 发「好的」 | 1 |
| 发 25 字文本（当天首条） | 1 + 1 + 2 = 4 |
| 发一张图（不是首条） | 1 + 1 = 2 |
| 发 120 字文本并被 3 人回复 | 自身 1+1+1=3；自己额外收到 3×2=6 |
| 连续刷 6 条「哈哈」 | 仅第 1 条有效，其余触发防刷（见 §3） |

---

## 3. 防刷规则

| 规则 | 默认值 | 实现位置 |
|---|---|---|
| 每分钟计分消息上限 | 8 条 | `engine.py` 内存滑动窗口，按 `(chat_id, sender_id)` |
| 每人每群每日积分封顶 | 80 分 | `point_events` 当日 SUM（内存缓存） |
| 重复内容不计分 | 最近 3 条完全相同则跳过 | 规范化后（去空白/标点）比较 |
| 长度分要求内容多样 | 去重字符数 ≥ 6 | 防止「哈哈哈哈……」凑长度 |
| 过期消息不计分 | 消息年龄 > 300 秒 | 防止启动回放重复计分 |
| 机器人自己发言 | 匹配 `bot_display_name` | router 已在最前拦截 |

说明：

- 触发防刷的消息**仍然入库**，只是不产生积分流水；活跃查询、总结不受影响。
- 日封顶与频率限制只作用于自动计分，管理员手动调整不受限。
- 频率窗口与日累计是内存态，重启后从当日流水重建（首次计分时 SUM 一次）。

---

## 4. 数据模型

### 4.1 选型：流水表优先

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 每次从 `messages` 现算 | 无冗余 | 规则调整即历史全变、回复奖励无法归因、每次查询扫全表 | 否 |
| 每人一行累计 | 查询快 | 无法解释「这分怎么来的」、无法重算、并发更新易错 | 否 |
| **append-only 流水表** | 幂等、可审计、可重算、支持手动调整 | 多一张表 | **采用** |

流水表按 `message_id + reason` 唯一，重复投递天然幂等（`INSERT OR IGNORE`）。
当前群规模（万级消息）下，SUM 聚合查询毫秒级，**不引入汇总缓存表**，避免双写不一致。

### 4.2 DDL（加入 `src/db/schema.py` 的 `SCHEMA_SQL`）

```sql
-- 积分流水：一条消息的每个加分项一行；管理员调整也记在这里
CREATE TABLE IF NOT EXISTS point_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,              -- 积分归属人
    sender_name TEXT    NOT NULL DEFAULT '',   -- 冗余快照，显示优先走 nickname_service
    message_id  TEXT    NOT NULL,              -- 触发事件的消息；手动调整用 manual:<uuid>
    reason      TEXT    NOT NULL,              -- base|length|length_long|media|daily_first|reply|manual
    points      INTEGER NOT NULL,              -- 可为负（仅 manual）
    day         TEXT    NOT NULL,              -- 本地日期 YYYY-MM-DD
    season      TEXT    NOT NULL,              -- 本地月份 YYYY-MM
    created_at  INTEGER NOT NULL,
    UNIQUE(message_id, reason)
);

CREATE INDEX IF NOT EXISTS idx_points_chat_season
    ON point_events(chat_id, season, sender_id);
CREATE INDEX IF NOT EXISTS idx_points_chat_day
    ON point_events(chat_id, day, sender_id);
CREATE INDEX IF NOT EXISTS idx_points_chat_sender
    ON point_events(chat_id, sender_id, created_at DESC);

-- 已结算赛季（惰性写入），用于展示/公告历史冠军
CREATE TABLE IF NOT EXISTS point_seasons (
    chat_id       TEXT    NOT NULL,
    season        TEXT    NOT NULL,
    winner_id     TEXT    NOT NULL DEFAULT '',
    winner_name   TEXT    NOT NULL DEFAULT '',
    winner_points INTEGER NOT NULL DEFAULT 0,
    closed_at     INTEGER NOT NULL,
    PRIMARY KEY (chat_id, season)
);
```

### 4.3 常用查询

```sql
-- 总榜
SELECT sender_id, MAX(sender_name) AS sender_name, SUM(points) AS pts,
       MAX(created_at) AS last_at
FROM point_events WHERE chat_id = ?
GROUP BY sender_id ORDER BY pts DESC, last_at DESC LIMIT ?;

-- 月榜（赛季）
... WHERE chat_id = ? AND season = ? ...

-- 今日榜 / 今日已得（用于日封顶）
... WHERE chat_id = ? AND day = ? ...

-- 连续活跃天数：取该成员最近 N 天的 day 集合，在 Python 里倒推
SELECT DISTINCT day FROM point_events
WHERE chat_id = ? AND sender_id = ? ORDER BY day DESC LIMIT 60;
```

---

## 5. 等级与称号

等级由**本群总积分**推导，阈值存 `data/point_levels.json`（仿 `data/lots.json` 模式，可在 Web UI 编辑）：

| 等级 | 称号 | 门槛（总积分） |
|---|---|---|
| Lv0 | 潜水员 | 0 |
| Lv1 | 冒泡 | 20 |
| Lv2 | 常客 | 80 |
| Lv3 | 活跃 | 200 |
| Lv4 | 话痨 | 500 |
| Lv5 | 群柱 | 1200 |
| Lv6 | 传说 | 3000 |

```json
{
  "levels": [
    {"level": 0, "title": "潜水员", "min_points": 0},
    {"level": 1, "title": "冒泡",   "min_points": 20},
    {"level": 2, "title": "常客",   "min_points": 80},
    {"level": 3, "title": "活跃",   "min_points": 200},
    {"level": 4, "title": "话痨",   "min_points": 500},
    {"level": 5, "title": "群柱",   "min_points": 1200},
    {"level": 6, "title": "传说",   "min_points": 3000}
  ]
}
```

文件缺失时使用内置默认值并在首次运行时写回，行为与 `src/fun.py` 的 `load_lots_config()` 一致。

---

## 6. 群内指令

全部为**裸文本**（也接受 `@机器人` 前缀），与「活跃查询」同一接入位置，返回纯文本不经过 Markdown 清洗。

| 输入 | 行为 |
|---|---|
| `积分` / `我的积分` | 个人积分卡 |
| `积分榜` | 本群总榜 TOP10 |
| `积分榜 月` / `月榜` | 本月赛季榜 TOP10 |
| `积分榜 今日` / `今日榜` | 今日榜 TOP10 |
| `积分规则` | 计分规则摘要（给成员看） |
| `积分调整 <名字> +20 [理由]` | 管理员专用，支持 `+` / `-` |
| `积分重算` | 管理员专用，触发当前群回溯 |

### 6.1 回复样例

个人积分卡：

```
@苏辞 的积分卡 · 想亲想爱一家人
总积分 128 · Lv3 活跃
本月 32（第2名）· 今日 8
距 Lv4 话痨 还差 72 分
连续活跃 5 天
今日得分：长消息 +1 · 首条签到 +2 · 被回复 +4
```

总榜：

```
群积分榜 · 想亲想爱一家人（总榜）
1. 苏辞📷   128分  Lv3活跃   本月32
2. 莫小贝   96分   Lv2常客   本月12
3. 吕晓鹏❗  74分   Lv2常客   本月26
...
本群共 14 人参与 · 今日产生 23 分
```

月榜：

```
群积分榜 · 想亲想爱一家人（10-01 ~ 10-31）
1. 苏辞📷   32分
2. 吕晓鹏❗  26分
3. 莫小贝   12分
距赛季结束还有 11 天
上月冠军：莫小贝 260分
```

### 6.2 解析规则

```python
_POINTS_CMD = re.compile(
    r"^[\s ​﻿]*(积分|我的积分|积分卡|积分榜|月榜|今日榜|积分规则)"
    r"(?:[\s:：]+(月|今日|总))?[\s]*$"
)
```

- 只做**完全匹配**，避免把「积分怎么算」这类正常聊天误判为指令。
- 与 `parse_activity_query()` 并列，放在 `router.handle()` 的 `if is_at:` 分支之前。
- 同样应用 300 秒消息年龄保护（启动回放期不响应历史指令）。
- 管理员命令走已有 `AdminCommandHandler`，仅在 `is_at` + admin 身份时生效。

---

## 7. 赛季机制

- `season = YYYY-MM`（本地时区），在写入流水时计算，之后不再变化。
- **不重置任何数据**：总榜是全表 SUM，月榜按 `season` 过滤。
- 赛季结算为**惰性**：任意积分操作时，若发现存在更早赛季的流水且 `point_seasons` 无对应行，则计算该赛季冠军并写入（幂等）。
- 冠军公告：
  - P1 只在有人查看榜单时附带「上月冠军」一行；
  - P3 可选：在 `proactive` 开启时，由该群新赛季首条消息触发一次主动公告（复用 `src/proactive/` 的发送时机），需配置 `points_season_announce_enabled`。

---

## 8. 代码结构

```
src/points/
    __init__.py
    models.py     PointAward(reason, points, sender_id), MemberCard, BoardRow
    rules.py      score_message(msg, *, is_daily_first, now_ts) -> list[PointAward]
                  reply_target(content) -> str | None
                  normalize_text(content) -> str
    levels.py     load_levels(), level_for(points), next_level(points)   # 读写 data/point_levels.json
    store.py      PointStore(conn)      # 与 TodoStore 相同的连接注入方式
                    record_events(...)            INSERT OR IGNORE
                    board(chat_id, scope, limit)  total|season|day
                    member_card(chat_id, sender_id)
                    day_points(chat_id, sender_id, day)
                    recent_events(chat_id, sender_id, limit)
                    adjust(chat_id, sender_id, name, delta, reason)
                    rebuild(chat_id, messages)    # 删除非 manual 流水后重放
                    close_season(chat_id, season)
    engine.py     PointEngine(store, config, levels)
                    award_for_message(msg)  -> int   # 入口，内部做防刷/封顶
                    handle_stale_guard(msg)
                    backfill(chat_id, days)
    handler.py    parse_points_command(text) -> Command | None
                  format_member_card(), format_board(), format_rules()
```

### 8.1 接入点（精确位置）

| 位置 | 改动 |
|---|---|
| `src/db/schema.py` | `SCHEMA_SQL` 追加 §4.2 的 DDL |
| `src/router.py:127` 附近，`insert_message` 成功之后 | `self._points.award_for_message(msg)`（try/except 包裹，失败不影响主流程） |
| `src/router.py:171` 附近，「活跃查询」判断旁边 | `parse_points_command(clean_command_text(...))` → 分发到 handler |
| `src/bot.py:225` `MessageRouter(...)` | 构造 `PointStore(self._conn)` 与 `PointEngine`，作为参数注入 |
| `src/admin.py` | 增加 `积分调整` / `积分重算` 分支 |
| `src/config.py` | 增加 §9 配置项 |
| `src/web/server.py:1164` POST 白名单 | 加入 `/api/points/adjust`、`/api/points/levels` |
| `ui/src/App.jsx:33` 附近导航数组 | 新增「积分榜」标签页 |
| `ui/src/components/PointsPanel.jsx` | 新组件 |
| `CODEBASE_REFERENCE.md` | §1/§2/§3/§4/§5 同步 |

### 8.2 运行时调用链

```
MessageRouter.handle(msg)
  ├── store.insert_message(msg)                     # 已有
  ├── PointEngine.award_for_message(msg)            # 新增，紧随其后
  │     ├── 是否启用 / 是否该群 / 是否系统消息 / 是否过期
  │     ├── 频率窗口 + 重复内容 + 每日封顶（内存态）
  │     ├── rules.score_message() → [base, length, media, daily_first]
  │     └── PointStore.record_events()
  │
  └── 指令分流（@mention 之前）
        ├── parse_activity_query()  → 活跃报告（已有）
        └── parse_points_command()  → PointHandler
              ├── 积分 / 我的积分 → member_card + 等级 + 距下一级
              ├── 积分榜 [总|月|今日] → board
              └── 积分规则 → 静态文案
```

---

## 9. 配置项（`src/config.py`，均可由 .env 覆盖）

| 名称 | 默认值 | 说明 |
|---|---|---|
| `points_enabled` | `false` | 总开关，与 `fun_enabled` 同级风格 |
| `points_groups` | `["*"]` | 生效群，逗号分隔群名；`*` 全部 |
| `points_base_score` | `1` | 每条消息基础分 |
| `points_length_min_chars` | `20` | 长度加分门槛 |
| `points_length_bonus` | `1` | 长度加分值 |
| `points_long_length_min_chars` | `100` | 长文门槛 |
| `points_long_length_bonus` | `1` | 长文加分值 |
| `points_media_bonus` | `1` | 图片/语音/视频加分 |
| `points_reply_bonus` | `2` | 被回复加分 |
| `points_daily_first_bonus` | `2` | 当天首条加分 |
| `points_per_message_cap` | `5` | 单条封顶 |
| `points_rate_limit_per_min` | `8` | 每分钟计分消息上限 |
| `points_daily_cap` | `80` | 每人每群每日封顶 |
| `points_max_age_sec` | `300` | 超过此年龄的消息不计分 |
| `points_board_size` | `10` | 榜单展示人数 |
| `points_levels_path` | `data/point_levels.json` | 等级配置路径 |

配置接入需同步：`.env.example`、`/api/config` 的字段白名单、`FeaturesPanel` 开关。

---

## 10. Web UI

### 10.1 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/points?chat_id=&scope=total\|season\|day&limit=20` | 榜单 |
| GET | `/api/points/member?chat_id=&sender_id=` | 个人卡片 + 最近 30 条流水 |
| GET | `/api/points/overview?chat_id=` | 参与人数、今日总分、等级分布、近 14 天每日总分 |
| POST | `/api/points/adjust` | `{chat_id, sender_id, delta, reason}`，写 `manual` 流水 |
| GET | `/api/points/levels` | 读等级配置 |
| POST | `/api/points/levels` | 存等级配置（校验递增、Lv0 门槛为 0） |
| GET | `/api/points/seasons?chat_id=` | 赛季冠军历史 |

所有 POST 必须加入 `server.py` 的 allowlist（当前在 1164 行），否则会 405。

### 10.2 `PointsPanel.jsx`

- 顶部：群选择（复用昵称模块的群列表接口）、总开关、时间范围切换（总榜 / 本月 / 今日）。
- 主区：榜单表格 —— 名次、称号徽章、成员、总积分、本月、今日、最后活跃；点击行展开流水明细。
- 侧区：等级分布（条形/环形）、近 14 天积分趋势折线。
- 底部：等级阈值编辑器（增删改，保存到 `data/point_levels.json`）。
- 管理员操作：调整积分（选择成员、±分值、理由，二次确认）。

图表实现前先按 `dataviz` skill 的规范选色与布局，保证暗色模式可读。

---

## 11. 回溯与重建

- 新部署时历史消息不自动计分（避免第一次上线就把所有人的分算成历史量）。
- 提供工具 `tools/points_backfill.py`：

```bash
# 只补最近 30 天
python tools/points_backfill.py --chat 123@chatroom --days 30

# 规则调整后全量重算（保留 manual 调整）
python tools/points_backfill.py --chat 123@chatroom --rebuild

# 所有群
python tools/points_backfill.py --all --rebuild
```

- 回溯按 `timestamp` 升序重放，频率窗口与每日封顶按历史时间模拟，结果与实时计分一致。
- `--rebuild` 删除该群 `reason != 'manual'` 的流水，`manual` 保留并参与总计。

---

## 12. 测试计划

### 单元测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_points_rules.py` | 各加分项、单条封顶、去重字符阈值、回复目标解析、规范化 |
| `tests/test_points_store.py` | 幂等写入、榜单排序与并列、赛季/日过滤、日封顶 SUM、手动调整、重建 |
| `tests/test_points_engine.py` | 频率窗口、重复内容、过期消息、机器人/系统消息、群白名单 |
| `tests/test_points_handler.py` | 指令完全匹配、与「活跃查询」「待办」不冲突、卡片/榜单格式 |

### 集成 / 功能测试

| 层级 | 断言 |
|---|---|
| Router | 裸文本 `积分` 有回复；`@bot 积分` 有回复；`积分怎么算` 不拦截；`活跃查询` 仍正常 |
| Web API | 榜单 JSON 结构、POST 白名单、非法 delta 拒绝 |
| Playwright | 侧边栏「积分榜」渲染、切换群/榜单、空数据不白屏、暗色模式正常 |

### 手动验证

1. 真实群连续发言，确认积分增长且同一条消息不重复计分。
2. 引用回复他人，确认被回复者 +2。
3. 同一分钟连发 10 条，确认只有前 8 条计分。
4. 重启机器人，确认不会因回放重复计分。

---

## 13. 实施分期

| 阶段 | 内容 | 交付物 |
|---|---|---|
| **P1 基础** | schema、rules、store、engine、`积分`/`积分榜`/`月榜`/`积分规则`、回溯工具、单元测试 | 群内可用 |
| **P2 管理** | 管理员调整/重算、Web API、`PointsPanel` 基础榜单 | UI 可见 |
| **P3 体验** | 等级阈值编辑、趋势/分布图、赛季自动公告、称号自定义 | 完整形态 |
| **P4 扩展**（可选） | 积分兑换（抽签次数、自定义头衔）、跨群总榜 | 按需 |

---

## 14. 风险与对策

| 风险 | 对策 |
|---|---|
| 启动回放导致重复/异常计分 | `message_id + reason` 唯一 + 300 秒年龄保护 |
| 规则调整后历史分不一致 | `--rebuild` 重放；manual 流水保留 |
| 刷屏刷分 | 频率窗口 + 日封顶 + 重复内容 + 去重字符数 |
| 成员改名 | 积分按 `sender_id` 存，显示名实时走 `NicknameService` |
| 机器人回复被计分 | router 最前已按 `bot_display_name` 拦截；`daily_first` 需再按 sender 校验 |
| 群内隐私 | 仅显示群内成员；Web UI 默认本机访问，不对外暴露 |
| 指令误触发正常聊天 | 完全匹配 + 300 秒时效；`积分怎么算` 不匹配 |
| 数据增长 | 每条消息 1~2 行流水，量级与 `messages` 相同；索引控制查询成本 |

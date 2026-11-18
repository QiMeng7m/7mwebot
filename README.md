# webot

> 微信群里的 AI 助手 + 星盘玄学玩法。

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11%20%7C%20macOS-blue?style=flat-square&logo=windows" alt="Platform" />
  <img src="https://img.shields.io/badge/python-3.13%2B-green?style=flat-square&logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/AI-Claude%20%7C%20DeepSeek-purple?style=flat-square" alt="AI Backend" />
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=flat-square" alt="license" />
</p>

> ## 使用声明
>
> 本项目仅供学习交流使用。严禁用于发送垃圾信息、骚扰他人、诈骗欺诈等违法违规行为。使用者自行承担一切后果与风险。

---

## 它是什么

把 webot 加入微信群，它可以：

- **玩星盘**：求签、观星、放生、转运、开光、点星、锦鲤池，一套完整的玄学玩法；
- **总结聊天**：说一句「总结一下」，立刻知道忙的时候群里聊了什么；
- **管理待办**：@机器人 说「记一下 周五聚餐」，全群共享的待办清单就多一条；
- **回答问题**：@机器人 问任何问题，它会结合群聊上下文回答；
- **偶尔冒泡**：群聊热闹时，它也会插话接梗。

---

## 星盘玩法

### 一句话

> **每天求一签、观一次星、放生一条鱼，攒香火点亮二十八宿，成为群里的天选之子。**

### 三种资源

| 资源 | 作用 |
|---|---|
| **香火** | 唯一货币。求签、观星、聊天获得；放生、转运、开光、投池、点星消耗 |
| **气运** | 永久成就值，只增不减。决定段位与气运榜 |
| **欧气** | 每天 0~120 的运势值。影响开光、锦鲤池、放生 |

### 每天做什么

| 指令 | 作用 | 限制 |
|---|---|---|
| `求签` / `抽签` | 每日签文、欧气、今日宜、签谱收录 | 1/天 |
| `解签` | 解读今天的签 | 1/天 |
| `观星` | 今日星象 + 星骰 + 香火 | 1/天 |
| `放生` | 香火换气运 | 1/天 |
| `转运` | 重抽今天的签 | 1/天 |
| `开光` / `抽符` | 抽塔罗牌，30 抽保底传说牌 | 3/天，首抽免费 |
| `投池 50` | 投入锦鲤池，21:00 开奖 | 10~200/天 |
| `点星 青龙` | 点亮二十八宿，获得永久加成 | 满足香火/气运/行为门槛 |

### 长期目标

- **星盘 28 宿**：青龙（香火收益）、白虎（开光保底）、朱雀（欧气）、玄武（锦鲤池）。每象 7 颗，7/7 触发套装，28/28 可选择命格觉醒。
- **气运段位**：咸鱼 → 小虾 → 鲫鱼 → 鲤鱼 → 锦鲤 → 欧皇 → 天选之子 → 锦鲤王。
- **签谱 36 支**、**塔罗牌鉴 22 张**、**星骰图鉴 31 个**、**卦谱 64 卦**。
- **锦鲤池**：每天 21:00 全群开奖，中奖者独得 80%，其余返还 15%。
- **四象阵营周战**：按「本周新点亮星数 ÷ 活跃人数」计分，领先阵营成员求签欧气 +5。
- **社交**：`斗星 @某人` 比星数赢香火；`合盘 @某人` 看星座/MBTI 契合度；`群像` 看本群分布；`CP榜` 看契合度 Top5。

### 八字与八卦

先设置出生时间，再看命盘、批命、起卦：

| 指令 | 作用 |
|---|---|
| `设置八字 1995-08-15 08:30` | 记录出生年月日时；不知道时辰可只写年月日 |
| `设置生辰 1995年8月15日 辰时` | 支持中文日期与时辰 |
| `八字` / `命盘` | 四柱、五行、生肖、四象、本命宿、生命数字、天圆地方九宫盘 |
| `五维` / `分支评分` | 爱情、事业、学习、财富、人际五项评分、条形图与今日波动 |
| `批命` / `算命` | 每日命理解读 + 今日五维（1/天），香火 +10、欧气 +3 |
| `起卦` / `摇卦` | 每日一卦（1/天），香火 +10、欧气 +2，收入卦谱 |
| `卦谱` | 六十四卦收集进度 |

> 八字与卦象为娱乐向计算：不查真太阳时、不处理节气交接，只看趋势与氛围。
> 四柱、五行、卦象、五维全部走固定算法；批命与解签的文案优先走 AI，AI 关闭或失败时自动回退本地模板。


### 身份

- `我是 天蝎座` / `设置生日 10-24`：设置本命星、本命宿与生命数字；
- `我是 INFP`：设置 MBTI，决定起手四象；
- `星盘` / `命格`：查看太阳/月亮/上升、四象进度、觉醒与今日星象。

### 榜单与收集

`气运榜`（锦鲤榜）、`四象榜`（阵营榜）、`签谱`、`分享`（今日签分享卡）。

### 其他

`功德` / `我的信息` 查看个人卡；`星象` 查看今日星象；`周运` / `领取周运` 完成本周目标领奖励。

> 完整数值与设计见 [docs/JINLI_DESIGN.md](docs/JINLI_DESIGN.md)。

---

## 非游戏功能

### 群聊总结

群里直接说 `总结一下`、`前面说了什么`、`summarize`。

### 群聊待办

- `记一下 周五聚餐`
- `查看待办`
- `搞定 3`
- `删掉 2`
- `恢复待办 1`（仅群管理员）

### 对话问答

`@机器人 DeepSeek 和 Claude 哪个好？` —— 会结合群聊上下文回答。

### 活跃查询

`活跃查询` / `活跃查询 7`：最近 15 天发言 Top5、最后发言时间、未发言成员。

### 欢迎新人 / 主动发言 / 飞书同步

在网页控制台「功能开关」里配置。

---

## 控制台

打开 EXE 后会自动弹出网页控制台，左侧菜单：

- **运行状态**：机器人在不在线、处理了多少消息
- **系统配置**：AI 后端、Key、机器人名字、数据目录、测试提示词
- **功能开关**：总结、飞书、主动发言、粘性提及、欢迎新人、日志级别
- **群聊待办**
- **星盘**：成员香火/气运/段位/星盘进度，支持手动调整
- **群友昵称**
- **运行日志**

---

## 安装

### Windows（推荐）

1. 从 Releases 下载 `webot-setup.exe` 安装，或下载 `webot.exe` 免安装运行；
2. 首次打开跟着配置向导走；
3. 电脑上已登录微信，想让机器人进的群需要先开启「添加到通讯录」。

### 从源码运行（Windows）

```bash
pip install -r requirements.txt
cd ui && npm install && npm run build && cd ..
python desktop.py
```

> 仓库不包含 `native/windows/*.dll`，缺了会报 `wx_key.dll not found` 或 `wcdb_api.dll not found`。从 Releases 下载免安装版后执行：
>
> ```bash
> pip install pyinstaller
> python tools/extract_native_dlls.py --archive path/to/webot.exe --dest native/windows
> ```

### macOS（实验）

macOS 通过 WeFlow 读取微信数据库，不需要额外启动消息读取服务。WeFlow 会把数据库密钥导出为 `all_keys.json`，webot 直接读取该文件。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-macos.txt
cd ui && npm install && npm run build && cd ..

# 检查环境与 WeFlow 数据库
python tools/macos/weflow_setup.py diagnose
# 需要时提取 / 导入密钥（生成 all_keys.json）
python tools/macos/weflow_setup.py extract-keys
python tools/macos/weflow_setup.py import-keys
# 权限受限时可尝试重启钩子方式
python tools/macos/weflow_setup.py extract-keys-restart-hook

python desktop_mac.py
```

在 `.env` 中选择后端：

```bash
WECHAT_BACKEND=mac_ui       # UI 自动化，兼容性更好
# WECHAT_BACKEND=mac_hybrid # WCDB 直读，更快，依赖 all_keys.json
```

需要在系统设置中给终端授权「辅助功能」。

---

## 配置

所有设置都可以在网页控制台里改。必须填的只有一项：**AI 后端 API Key**（DeepSeek 或 Claude 二选一）。

暂时不想产生 AI 费用时，把「AI 功能总开关」关掉即可；关闭后总结、闲聊、解签、群记忆、飞书 AI 导出会停止，星盘玩法不受影响。

星盘玩法的主要配置：

```bash
KOI_ENABLED=true
KOI_GROUPS=*
KOI_WELCOME_MERIT=100
KOI_SIGN_MERIT=50
KOI_OBSERVE_MERIT=30
KOI_CHAT_MERIT_PER_MESSAGE=1
KOI_CHAT_MERIT_DAILY_CAP=20
KOI_RELEASE_COST=30
KOI_RELEASE_FORTUNE=3
KOI_REROLL_COST=50
KOI_DRAW_COST=100
KOI_DRAW_DAILY_LIMIT=3
KOI_DRAW_PITY=30
KOI_POOL_MIN=10
KOI_POOL_MAX_PER_DAY=200
KOI_POOL_HOUR=21
KOI_POOL_WINNER_RATIO=0.80
KOI_POOL_REFUND_RATIO=0.15
KOI_DUEL_STAR_DAILY_LIMIT=3
KOI_WEEKLY_REWARD_MERIT=100
KOI_WEEKLY_REWARD_FORTUNE=5
KOI_HEXAGRAM_MERIT=10
KOI_FORTUNE_MERIT=10
KOI_FORTUNE_LUCK=3
```

---

## 常见问题

**需要装什么？**
下载 EXE 双击就行，什么都不用装。

**会被封号吗？**
目前没有已知案例，但请自行评估风险。

**支持哪些 AI？费用多少？**
DeepSeek（推荐，极低价格）和 Claude。普通群一天不到一毛钱。

**微信能最小化吗？**
读消息不受影响，发消息时微信窗口需要可见。

**支持多群吗？**
支持，默认监控所有群，也可以指定。

---

## 许可证

MIT © [cancelGuMu](https://github.com/cancelGuMu)

---

<p align="center">
  <sub>Made with ❤️ by <a href="https://github.com/cancelGuMu">孤舟99</a></sub>
</p>

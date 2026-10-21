# webot

> 微信群的 AI 助手 —— 帮你总结聊天、管理待办、回答问题，像多了一个会做笔记的群友。

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11%20%7C%20macOS-blue?style=flat-square&logo=windows" alt="Platform" />
  <img src="https://img.shields.io/badge/python-3.13%2B-green?style=flat-square&logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/AI-Claude%20%7C%20DeepSeek-purple?style=flat-square" alt="AI Backend" />
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=flat-square" alt="License" />
</p>

> ## 使用声明
> 
> 本项目仅供学习交流使用。严禁用于发送垃圾信息、骚扰他人、诈骗钓鱼等违法违规行为。使用者自行承担一切后果与风险。

---

## 它是什么

把 webot 加入你的微信群，它会：

- **总结聊天记录**：说一句「总结一下」，立刻告诉你在忙的时候群里聊了什么。
- **管理待办事项**：@机器人 说「记一下 周五聚餐」，全群共享的待办清单就多了一条。完成了就「搞定」它，不想看了就「删掉」。
- **回答你的问题**：@机器人 问任何问题，它会结合群聊上下文回答你。
- **偶尔自己冒泡**：群聊热闹的时候，它也会插话接梗，像个普通群友。

所有操作都在一个网页控制台里完成，双击 EXE 就能用。

---

## 能做什么

### 1. 群聊待办

@机器人 说句话就能新建一条待办，全群都能看到。每个群独立管理，互不影响。

**怎么用**

群里直接 @机器人 说：
- `记一下 周五聚餐` —— 添加一条待办
- `查看待办` —— 看看还有哪些没做
- `搞定 3` —— 把第 3 项标记完成
- `删掉 2` —— 把第 2 项放进回收站
- `恢复待办 1` —— 从回收站捞回来（仅群管理员）

打开控制台的「群聊待办」页面，可以：
- 开关这个功能、选择哪些群启用
- 修改触发词（比如把「记一下」改成「备忘」）
- 设置每个群最多存多少条、多久后自动清理
- 查看所有群的待办列表，搜索、筛选、操作

### 2. 聊天总结

忙了几个小时没看群？直接说一句「总结一下」，机器人把刚才的聊天整理好发给你：谁说了什么、讨论了什么话题。

**怎么用**

群里直接说 `总结一下` 就行。也可以说 `前面说了什么`、`发生了什么`、`summarize`。触发词可以在控制台自定义。

控制台里可以调整：总结往前看多久（默认 8 小时）、用哪些词触发。

### 3. 对话问答

@机器人 然后问问题，它会结合最近的群聊内容来回答，而不是凭空瞎猜。

**怎么用**

- `@机器人 DeepSeek 和 Claude 哪个好？`
- `@机器人 刚才他们说的那个餐厅地址是什么？`

### 4. 群友昵称

群里的人默认显示的是微信号（wxid 开头的一串），很难认。控制台的「群友昵称」页面列出了每个群的全部成员，你可以给每个人取一个容易认的名字。

**怎么用**

打开控制台 → 群友昵称 → 选择群聊 → 在对应的人旁边填上你想叫的名字。

也可以在群里直接发送命令（仅管理员）：`@机器人 改名 wxid_xxx = 张三`

### 5. 趣味抽签 / 解签

说「抽签」（或 @机器人 抽签），机器人随机抽一支运势签（大吉到凶），配一句吐槽；再发「解签」，AI 会结合最近的群聊内容帮你解读这支签。签文内容、等级、概率、emoji 都可以在控制台的「功能开关 → 趣味抽签」里自定义。

**怎么用**

直接发 `抽签` 即可，不需要 @机器人；抽完发 `解签`，AI 会引用你最近抽到的那支签并结合上下文解读。每个群、每个人只保留最近一签。

### 6. 主动发言

不用 @它，群聊热闹到一定程度时，机器人会自己插话。

**怎么用**

在控制台的「功能开关 → 主动发言」里打开。可以调整它在多热闹的群里才开口（安静时闭嘴，热闹时活跃）。

### 7. 欢迎新人

有人刚进群，机器人会自动发一条欢迎消息。

**怎么用**

在控制台的「功能开关 → 欢迎新人」里打开。可以写多条欢迎词模板，不同的群用不同的模板。

### 8. 飞书沉淀

把群聊里的讨论自动整理到飞书表格里，按摘要、待办、需求、日常记录分类存好。

**怎么用**

在控制台的「功能开关 → 飞书同步」里填入飞书应用的 App ID 和 App Secret，开启后群聊内容会自动同步。也可以手动在群里说 `同步到飞书` 立即沉淀。

### 9. 群活跃查询

在群里直接说 `活跃查询`，机器人统计最近 15 天这个群的发言 Top5、每人的最后发言时间和窗口内未发言成员。也可以说 `活跃查询 7` 或 `活跃查询7天` 指定天数（最多 90 天）。

**怎么用**

群里直接发 `活跃查询` 即可，不需要 @机器人，任何人可用。数据来自机器人已接收并入库的群消息。

---

### 10. 群活跃积分 / 装备 / 切磋

群里发言自动攒积分，累计积分决定总榜、等级和月度赛季，余额可以买装备、买免战券，也可以和别人切磋抢分。

**常用指令**（都不需要 @机器人）

| 指令 | 作用 |
|---|---|
| `积分` | 自己的积分卡（等级、名次、余额、武力值） |
| `我的信息` | 一行快照：积分、余额、武力值 |
| `查询 莫小贝` | 查某人的积分和武力值（查不到人时不回复） |
| `积分榜` / `月榜` | 总榜 / 本月赛季榜 |
| `商店` / `购买 铁剑` / `我的装备` | 买装备，装备永久有效 |
| `武力值` / `战力榜` | 看自己的战力拆解 / 全群战力排名 |
| `切磋 @某人` | 发起切磋，或引用对方消息后发 `切磋` |
| `买免战券` | 花 20 分换 4 小时免受切磋 |
| `积分规则` | 查看当前计分规则 |

**计分**：每条消息 +1，长消息、图片语音视频、被引用回复都有额外加分；**每天发「签到」+10**（签到分不受单条封顶影响，普通聊天不再算签到）；普通聊天分单条最多 5 分，每天最多 80 分。

**切磋**：武力值高更容易赢，但胜率封顶 92%；武力差距越大，赢下后抢到的积分越少。每天最多 10 次、被别人切磋 3 次、累计抢分 50 分；输掉不扣分。

在控制台的「群活跃积分」页面可以看榜单、等级分布、商店和切磋流水，管理员也能直接调整成员积分。

---

## 控制台一览

打开 EXE 后会自动弹出网页控制台，左侧菜单依次是：

- **运行状态**：看机器人在不在线、处理了多少消息
- **系统配置**：设置 AI 后端和 Key、机器人名字、数据目录、测试提示词
- **功能开关**：各项功能的开关和参数调整
- **群聊待办**：待办功能配置 + 所有群的待办列表
- **群活跃积分**：积分榜、装备商店、切磋记录、等级分布与成员详情
- **群友昵称**：给群成员取别名
- **运行日志**：查看机器人运行记录

所有配置改完后点「保存配置」，重启机器人就生效。

---

## 安装

### Windows（推荐）

1. 从 [Releases](https://github.com/cancelGuMu/webot/releases) 下载 `webot-setup.exe`，双击安装
2. 或者下载 `webot.exe`，免安装直接双击运行
3. 首次打开会弹出配置向导，跟着走完就行

前提：电脑上已登录微信，想让机器人进的群需要先添加到通讯录（群聊右上角 `···` → 开启「添加到通讯录」）。

### macOS（实验）

macOS 通过 WeFlow 读取微信数据库，**不需要额外启动消息读取服务**。WeFlow 会把数据库密钥导出为 `all_keys.json`，webot 直接读取该文件。

```bash
git clone https://github.com/cancelGuMu/webot.git
cd webot
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

在 `.env` 中选择微信后端：

```bash
WECHAT_BACKEND=mac_ui       # UI 自动化，兼容性更好
# WECHAT_BACKEND=mac_hybrid # WCDB 直读，速度更快，依赖 all_keys.json
```

需要在系统设置中给终端授权「辅助功能」权限。

### 从源码运行（Windows）

```bash
git clone https://github.com/cancelGuMu/webot.git
cd webot
pip install -r requirements.txt
cd ui && npm install && npm run build && cd ..
python desktop.py
```

> **必须先准备原生 DLL**：仓库不包含 `native/windows/*.dll`（见 `.gitignore`），
> 缺了会报 `wx_key.dll not found` 或 `wcdb_api.dll not found`。
>
> 从 [Releases](https://github.com/cancelGuMu/webot/releases) 下载免安装版 `webot.exe`，然后解出所需 DLL：
>
> ```bash
> pip install pyinstaller
> python tools/extract_native_dlls.py --archive path/to/webot.exe --dest native/windows
> ```
>
> 也可以直接安装 `webot-setup.exe`，把安装目录下的 `native\windows\` 整个复制到本项目的 `native/` 下。

---

## 配置

所有设置都在控制台里直接改，不需要手动编辑文件。保存后重启机器人即可生效。

必须填的只有一项：在「系统配置 → AI 后端配置」里填入 API Key（DeepSeek 或 Claude 二选一）。

---

## 常见问题

**需要装什么？**
下载 EXE 双击就行，什么都不用装。

**会被封号吗？**
目前没有已知案例，但请自行评估风险。

**支持哪些 AI？费用多少？**
DeepSeek（推荐，极低价格）和 Claude。普通群一天用下来不到一毛钱。

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

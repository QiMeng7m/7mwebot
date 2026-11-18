# CLAUDE.md

## 每次修改必须提交 Git

每次对代码的修改完成后，必须执行 `git add` 和 `git commit`，写清楚改动内容和原因。

**只提交到本地，不推送到远端。** 只有用户明确要求时才执行 `git push`。

```bash
git add -A
git commit -m "<描述做了什么，为什么>"
```

## 每次修改必须重新打包

前端代码或 Python 代码修改后，必须重新构建并打包 EXE，确保 `dist/webot.exe` 包含最新改动。

```bash
# 1. 构建前端（如有前端改动）
cd ui && npm run build

# 2. 打包 EXE
pyinstaller build.spec

# 3. 复制到正确位置（如 PyInstaller 输出到了 ui/dist/）
cp ui/dist/webot.exe dist/webot.exe
```

## 每次修改必须进行功能测试

每次修改完成后，必须运行完整测试套件，并对修改涉及的功能做针对性验证。

### 运行全部测试

```bash
python -m pytest tests/ -v
```

测试分两层：
- **单元测试** (`test_config.py`, `test_trigger.py`, `test_web_api.py`, `test_window_controller.py`) — 毫秒级，验证逻辑正确性
- **功能测试** (`test_functional.py`) — 启动真实 HTTP 服务器 + Playwright 无头浏览器，模拟用户点击操作

### 功能测试覆盖要求

每次修改后，必须验证被修改功能在实际浏览器中可用：

| 测试类别 | 覆盖功能 |
|---------|---------|
| API 端点 | Diagnose 返回结构、Sandbox 路由注册、POST allowlist、配置字段默认值 |
| 页面加载 | 前端 SPA 正常渲染，不出现空白页或连接错误 |
| 主题系统 | 暗色模式默认启用、Sun/Moon 切换按钮可用、localStorage 持久化 |
| 侧边栏导航 | 运行状态/系统配置/运行日志 标签页渲染、沙箱子标签可见 |
| 配置功能 | 导入导出按钮可见 |

### 手动验证清单

部分功能无法自动化测试（需要微信客户端或真实 API Key），修改这些模块后需手动验证：

1. **AI 聊天/总结** — 需要真实 API Key + 微信运行
2. **密钥提取** — 需要微信客户端运行
3. **主动发言** — 需要微信客户端 + 真实群聊
4. **昵称管理** — 需要 data/messages.db 有数据
5. **消息发送** — 需要微信窗口可见

## 项目概述

webot — 微信群 AI 助手与星盘/八字玩法机器人，支持 WCDB 直读数据库、AI 自动总结/聊天/主动发言。

## 技术栈

- 后端：Python 3.13
- 前端：React + Vite (ui/)
- 桌面：PyWebView (WebView2)
- 打包：PyInstaller (build.spec)
- 数据库：WCDB (微信加密数据库)，通过 wcdb_api.dll + DRM patch 直读

## 项目结构

```
src/bot.py              - Bot 主控
src/config.py           - 配置加载 (.env)
src/router.py           - 消息路由
src/summarize/          - AI 后端 (DeepSeek/Claude)
src/wechat/             - 微信后端
  wcdb_backend.py       - WCDB 直读后端
  wcdb_client.py        - WCDB DLL 封装 (wcdb_api.dll)
  extract_key.py        - 密钥提取 (wx_key.dll Hook)
  window_controller.py  - 微信窗口操控 (键盘导航 + 消息发送)
  helpers.py            - 去重等工具
src/web/server.py       - Web UI 服务器 + WebSocket + API
src/koi/                - 星盘与八字玩法
  rules.py              - 签、欧气、段位、星盘、塔罗、星骰、周运
  signs.py              - 36 支签文
  bazi.py               - 八字、八卦、天圆地方、四象、五维评分
  store.py              - koi_* 表读写
  service.py            - 玩法逻辑
  handler.py            - 指令解析
src/game_utils.py       - 时间/群组/文本公共工具
src/memory/             - 聊天记忆
src/proactive/          - 主动发言
docs/JINLI_DESIGN.md    - 星盘与八字玩法设计
desktop.py              - 桌面入口 (PyWebView)
ui/                     - React 前端
native/                 - 平台原生库 (windows/, macos/)
dist/                   - 打包输出
```

## 构建命令

```bash
# 构建前端
cd ui && npm run build

# 打包 EXE
pyinstaller build.spec

# 输出: dist/webot.exe
```

## 日志位置

- 源码运行: `data/bot.log`
- EXE 运行: `dist/data/bot.log`

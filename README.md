# sosoFlow

轻量 Telegram 多任务转发机器人（Polling + APScheduler + SQLAlchemy），支持范围发布、源监听池、媒体组整体转发与失败重试。

## 项目定位

`sosoFlow` 面向“一个或多个源频道/群 -> 一个或多个目标频道/群”的持续分发场景：

- 支持历史范围导入（按消息 ID 区间）
- 支持实时监听新消息入池
- 支持任务级过滤、频率、时段、上限控制
- 支持媒体组按组发布（避免相册被打散）

官方频道：<https://t.me/sosoFlow>

## 核心能力

- 权限体系：`super` / `admin`
- 多任务管理：新建、改名、编辑来源/目标、启动/暂停、重置、删除
- 发布控制：
  - 模式：复制（copy）/ 转发（forward）
  - 间隔秒数、每日上限、发布时段
  - 范围任务自动完成（完成后自动暂停）
- 队列与状态：`pending / waiting / published / failed / skipped`
- 失败恢复：失败重试、等待重试回补
- 源监听池：源级开关、latest_seen 跟踪
- 媒体组：按 `media_group_id` 聚合后整体发布
- 过滤能力：
  - 必须包含图片 / 必须包含视频
  - 排除纯文字 / 排除链接
  - 包含关键词（命中任一即通过）
- 运维诊断：`/status`、`/debug_queue`、`/debug_media`、配置变更日志

## 运行要求

- Python 3.11+
- 可访问 Telegram Bot API 的网络环境
- 一个 Telegram Bot Token（由 BotFather 创建）

依赖见 `requirements.txt`。

## 快速开始（本地）

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# 填写 BOT_TOKEN / SUPER_ADMIN_IDS / DATABASE_URL 等
python main.py
```

验证：

```bash
pytest
```

## 环境变量

以 `.env.example` 为准，常用项如下：

- `BOT_TOKEN`：机器人 token（必填）
- `SUPER_ADMIN_IDS`：超级管理员 Telegram ID（逗号分隔）
- `ADMIN_USER_IDS`：初始化普通管理员 ID（可空）
- `DATABASE_URL`：默认 `sqlite:////mnt/sosoflow/sosoflow.db`
- `TZ`：默认 `Asia/Shanghai`
- `DEPLOY_VERSION`：部署版本号（最高优先）
- `STARTUP_NOTIFY_CHAT_IDS`：启动通知接收 ID（可空）
- `RESTART_STRATEGY`：`guide | exit | exec`

Neon PostgreSQL 示例：

```text
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DB?sslmode=require
```

版本号读取优先级（启动通知里显示）：

1. `DEPLOY_VERSION`
2. 根目录 `VERSION` 文件
3. `GIT_COMMIT`
4. `unknown`

## 首次使用流程（推荐）

1. `/start` 打开主菜单
2. 点 `➕ 新建任务`（或用 `/add_task`）
3. 设置来源与目标（可直接转发消息自动识别 ID）
4. 打开任务详情，配置模式/间隔/时段/上限/过滤
5. 导入范围（可选）并测试 `/publish_now`
6. 启动任务持续运行

## 命令清单（完整）

标记：`[A]=admin`，`[S]=super`

### 菜单与状态

- `/start` 打开主菜单
- `/help` 查看帮助
- `/status` 查看系统状态 `[A]`

### 任务管理

- `/add_task <name> <source_chat_id> <target_chat_id>` 新建任务 `[A]`
- `/tasks` 查看任务列表 `[A]`
- `/use_task <task_id>` 选择当前任务 `[A]`
- `/task_status` 查看当前任务详情 `[A]`
- `/rename_task <new_name>` 修改当前任务名称 `[A]`
- `/delete_task <task_id>` 删除任务 `[A]`
- `/start_task` 启动当前任务 `[A]`
- `/pause_task` 暂停当前任务 `[A]`

### 发布与队列

- `/import_range <start_id> <end_id>` 导入消息范围 `[A]`
- `/publish_now` 立即发布下一条/下一组 `[A]`
- `/skip [message_id]` 跳过队列项 `[A]`
- `/retry_failed` 重试失败项 `[A]`
- `/retry_waiting` 重试等待项 `[A]`

### 任务设置

- `/set_interval <seconds>` 设置发布间隔 `[A]`
- `/set_daily_limit <count>` 设置日上限 `[A]`
- `/set_time_window <HH:MM> <HH:MM>` 设置发布时段 `[A]`
- `/set_mode <copy|forward>` 设置发布模式 `[A]`
- `/set_auto_capture <on|off>` 已下线（保留兼容提示，不再生效）`[A]`
- `/set_delete_after_success <on|off>` 发布后删源开关 `[A]`
- `/set_tick <seconds>` 设置全局调度 tick `[S]`

### 过滤设置

- `/filters` 查看当前过滤配置 `[A]`
- `/set_filter <key> <value>` 设置过滤项 `[A]`

常用 `key`：
- `require_photo`（必须包含图片）
- `require_video`（必须包含视频）
- `require_text`（排除纯文字）
- `exclude_links`（含链接即排除）
- `include_keywords_enabled`（关键词过滤开关）
- `include_keywords`（关键词列表，逗号分隔）

### 源监听管理

- `/sources` 查看监听源列表 `[A]`
- `/set_source <source_chat_id> <on|off>` 启停指定源监听 `[A]`

### 诊断与运维

- `/debug_queue <message_id>` 查看队列元数据 `[A]`
- `/debug_media <on|off>` 媒体更新诊断开关 `[S]`
- `/restart` 重启流程（按 `RESTART_STRATEGY`）`[S]`

### 管理员管理

- `/add_admin <telegram_user_id>` 添加管理员 `[S]`
- `/remove_admin <telegram_user_id>` 移除管理员 `[S]`
- `/admins` 查看管理员列表 `[S]`

## JustRunMyApp 部署

1. 创建 Python App
2. 拉取本仓库代码
3. 安装命令：`pip install -r requirements.txt`
4. 启动命令：`python main.py`
5. 配置环境变量（至少 `BOT_TOKEN`、`SUPER_ADMIN_IDS`、`DATABASE_URL`）
6. 启动后查看日志，确认包含：
   - `startup self-check`
   - `sosoFlow started. polling + scheduler enabled`

说明：程序会自动创建 SQLite 目录 `/mnt/sosoflow`（如使用默认 sqlite 路径）。

## 常见问题

### 1) 为什么“暂停任务”后日志还在刷？

暂停的是“任务发布”，不是“机器人进程”。
只要主进程在运行，`getUpdates` 轮询与调度 tick 日志会继续出现，这是正常现象。

### 2) 为什么设置了范围，发完最新 ID 还继续发旧消息？

当前版本按“范围任务状态层”控制：范围内完成后会自动完成并暂停任务；重启任务可继续向原目标上限推进（无需重设目标上限）。

### 3) 媒体组为什么会被拆发？

请先确认源消息确实带 `media_group_id`。可用：

- `/debug_media on` 观察更新日志
- `/debug_queue <message_id>` 检查该消息是否已记录 `media_group_id/file_id`

若是历史占位消息，需等待监听补全元数据后再发布。

## 版本

当前版本：`0.1.0`（见根目录 `VERSION`）。

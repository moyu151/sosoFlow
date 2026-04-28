# sosoFlow

轻量 Telegram 多任务定时搬运机器人（Polling + APScheduler + SQLite/SQLAlchemy），适配 JustRunMyApp。

## 1. 项目介绍

sosoFlow 允许管理员管理多个搬运任务（源频道/群 → 目标频道/群），支持旧消息范围导入与新消息自动监听入队，并按任务配置自动发布。

## 2. 功能列表

- 超级管理员 / 普通管理员权限系统
- 多任务管理（新增、查看、选择、启动、暂停、删除二次确认）
- 队列管理（范围导入、立即发布、跳过、失败重试）
- 发布模式（copy / forward）
- 时间与频率控制（interval、time window、daily limit、round limit）
- 新消息自动监听入队（记录 metadata）
- 过滤系统（布尔过滤 + 文本长度限制）
- 发布/过滤/失败/删除日志记录
- InlineKeyboard 主菜单与任务详情交互

## 3. 环境变量说明

参考 `.env.example`：

- `BOT_TOKEN`：BotFather 生成的 Token
- `SUPER_ADMIN_IDS`：超级管理员 ID（逗号分隔）
- `ADMIN_USER_IDS`：普通管理员 ID（逗号分隔，启动时初始化）
- `DATABASE_URL`：默认 `sqlite:////mnt/sosoflow/sosoflow.db`（环境变量优先，未设置时才使用该默认值）
- `TZ`：默认 `Asia/Shanghai`
- `DEPLOY_VERSION`：部署版本标识（建议填 commit 或发布号）
- `STARTUP_NOTIFY_CHAT_IDS`：启动通知接收 chat_id（逗号分隔；留空时默认通知 `SUPER_ADMIN_IDS`）
- `RESTART_STRATEGY`：`guide|exit|exec`（`/restart` 策略，默认 `guide`）

### Neon PostgreSQL（推荐）

Neon 连接建议启用 SSL：

```text
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DB?sslmode=require
```

## 4. 本地运行方式

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# 填写 BOT_TOKEN、管理员 ID
python main.py
```

测试命令：

```bash
pytest
```

## 5. JustRunMyApp 部署方式

1. 在 JustRunMyApp 创建一个新的 App（Python 类型）。
2. 上传代码包，或连接 Git 仓库拉取代码。
3. 确认项目根目录包含：`main.py`、`requirements.txt`、`.env.example`、`README.md`。
4. 设置依赖安装命令：`pip install -r requirements.txt`。
5. 设置启动命令：`python main.py`。
6. 在平台环境变量中配置：
   - `BOT_TOKEN`
   - `SUPER_ADMIN_IDS`
   - `ADMIN_USER_IDS`（可空）
   - `DATABASE_URL`（推荐：`sqlite:////mnt/sosoflow/sosoflow.db`，用于持久化）
   - `TZ`（建议 `Asia/Shanghai`）
7. 启动应用后，进入日志页面查看 stdout。
8. 启动成功确认要点：
   - 出现 `startup self-check` 日志块
   - 输出 `admins total/super/admin`
   - 输出 `sosoFlow started. polling + scheduler enabled`
   - 无持续异常堆栈刷屏

说明：
- 程序启动时会自动执行 `os.makedirs("/mnt/sosoflow", exist_ok=True)`，确保 SQLite 目录存在。
- 若你在 JustRunMyApp 已配置 `DATABASE_URL`，将始终优先使用环境变量值。

## 6. BotFather 创建机器人步骤

1. 打开 Telegram 搜索 `@BotFather`
2. 执行 `/newbot`
3. 按提示设置机器人名称与用户名
4. 获得 `BOT_TOKEN`，填入 `.env`

## 7. 如何获取 Telegram user_id

- 与 `@userinfobot` 对话获取数字 ID
- 或先把你设为管理员后，给机器人发消息，再从日志/数据库读取 `effective_user.id`

## 8. 如何获取 source_chat_id / target_chat_id

- 频道/群通常是 `-100xxxxxxxxxx` 格式
- 可将机器人加入后，在群里发送消息，通过机器人捕获更新日志或使用 ID 工具机器人获取

## 9. 如何把机器人加入频道/群并设置管理员权限

1. 将机器人加入源频道/群与目标频道/群
2. 在目标频道/群授予“发消息”权限
3. 若启用 `delete_after_success`，在源频道/群授予“删除消息”权限

## 10. 常用命令示例

```text
/add_task test -1001111111111 -1002222222222
/use_task 1
/import_range 100 120
/publish_now
/set_interval 1800
/set_daily_limit 100
/set_time_window 09:00 23:30
/set_mode copy
/set_auto_capture on
/restart
/start_task
```

## 11. 推荐使用流程

1. 创建机器人并获取 `BOT_TOKEN`
2. 设置 `SUPER_ADMIN_IDS`
3. 将机器人加入源频道/群和目标频道/群并授予权限
4. `/start`
5. `/add_task test -1001111111111 -1002222222222`
6. `/use_task 1`
7. `/import_range 100 120`
8. `/publish_now`
9. `/start_task`

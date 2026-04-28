# PROJECT_LOG

## 2026-04-29

### 已完成

- 下线“轮次限制”功能（最小改动，保留数据库字段兼容历史数据）：
  - 发布逻辑已不再执行 `round_hours/round_limit` 限制判断
  - 任务详情已移除“当前轮发布/轮次”展示
  - 移除 `/set_round` 帮助文案、命令处理器与 handler 注册入口
  - `README.md` 常用命令示例移除 `/set_round`
- 本轮改动文件：
  - `main.py`
  - `README.md`
  - `PROJECT_LOG.md`

### 已验证项

- `python -m py_compile main.py` 通过
- `pytest` 通过（22 passed）

### 下一步建议（最高优先）

- 为任务详情补充“最近发布日志预览（最近5条）”按钮，并沿用“界面保持+单独提示”交互规则。

## 2026-04-28

### 已完成

- 初始化项目规则文件：`AGENTS.md`
- 搭建最小可运行机器人主程序：`main.py`
- 完成核心数据模型：
  - `admins`
  - `tasks`
  - `task_filters`
  - `queue`
  - `publish_logs`
  - `global_settings`
  - `user_states`
- 完成权限系统：
  - super/admin/非管理员拒绝逻辑
  - `/add_admin` `/remove_admin` `/admins`
- 完成任务命令：
  - `/add_task` `/tasks` `/use_task` `/task_status` `/delete_task`
- 完成队列命令：
  - `/import_range` `/publish_now` `/skip` `/retry_failed`
- 完成设置命令：
  - `/set_interval` `/set_round` `/set_daily_limit`
  - `/set_time_window` `/set_mode`
  - `/set_delete_after_success` `/set_auto_capture`
  - `/set_tick`（super）
- 完成运行命令：`/start_task` `/pause_task`
- 完成过滤命令：`/filters` `/set_filter`
- 完成按钮 UI：
  - `/start` 主菜单
  - 任务列表与详情页按钮
  - 删除任务二次确认
- 完成新消息自动监听入队（按 source_chat_id 匹配任务）
- 完成 APScheduler 发布调度（publish_tick）
- 完成部署必需文件：
  - `requirements.txt`
  - `.env.example`
  - `README.md`
- 新增启动前自检日志输出（不改变业务逻辑）：
  - 输出 TZ、DATABASE_URL、BOT_TOKEN 脱敏预览
  - 输出 tick_seconds 与管理员初始化统计（super/admin/total）
- 增强 `/status` 运维摘要：
  - 新增今日发布数
  - 新增今日失败数
  - 新增累计失败数
- 增强 `/task_status` 任务级运维摘要：
  - 新增今日发布计数（今日发布/日上限）
  - 新增当前轮发布计数（当前轮发布/轮上限）
  - 新增下次可发布剩余秒数（基于 last_published_at + interval_seconds）
- 完成 JustRunMyApp 部署适配补充：
  - README 增加完整部署步骤（创建 App、上传/连 Git、启动命令、环境变量、日志与成功判定）
  - README 增加首次使用流程（/start → /add_task → /use_task → /import_range → /publish_now → /start_task）
  - 新增 `.gitignore`（.env、data.db、__pycache__/、.pytest_cache/、*.pyc）
- 交互体验升级（按钮优先）：
  - 任务详情“⚙️ 设置”进入任务设置面板（按钮切换 mode / auto_capture / delete_after_success）
  - 任务设置支持快捷参数按钮（interval / daily_limit / time_window）
  - 新增过滤面板按钮化配置（布尔项开关 + min/max 快捷值）
  - 主菜单“➕ 新建任务”改为按钮引导输入
  - 任务详情“📥 导入范围”改为按钮引导输入
- 交互体验第2批：
  - 任务列表分页（上一页/下一页）
  - 主菜单新增“搜索任务”
  - 支持输入任务名或任务ID进行定位
  - 任务详情新增“🔄 刷新”按钮
- 交互体验第3批（设置向导输入）：
  - 任务设置新增“自定义间隔”
  - 任务设置新增“自定义日上限”
  - 任务设置新增“自定义时间窗”
  - 点击按钮后通过私聊文本输入完成参数更新，减少命令记忆成本
- 新增发布脚本：
  - `push_github.bat`：一键拉取、提交、推送到 `origin/main`
  - `push_justrunmy.bat`：一键推送 `HEAD:deploy` 到 JustRunMy
- 交互规范收敛（按钮+输入引导）：
  - 按钮触发设置/帮助/状态等操作时，默认保持当前界面不被覆盖
  - 通过单独消息提示用户下一步输入，减少界面跳转
  - 需要进入下一级页面时保留返回路径，并补充“返回主菜单”按钮
  - 引导文案改为“按提示直接输入参数文本”，降低命令记忆依赖
- 部署与重启运维增强：
  - 新增启动成功自动通知管理员（包含时间、版本标识、时区）
  - 新增 `/restart`（仅 super）安全重启流程，支持 `guide|exit|exec` 策略
  - 新增环境变量：`DEPLOY_VERSION`、`STARTUP_NOTIFY_CHAT_IDS`、`RESTART_STRATEGY`
  - README 与 `.env.example` 已同步更新说明
- 交互修复与引导增强：
  - `/start` 增加封面图（`img/b.png`）和常用操作文字引导
  - 修复“任务列表”在无任务时导致原面板消失的问题（改为单独提示 + 返回按钮）
  - “新建任务”改为分步输入：先输入来源ID，再输入目标ID，不再要求输入命令格式
  - 新增“转发消息自动识别频道/群ID”并回显，便于直接复制使用
  - 补全帮助菜单说明，覆盖当前命令和便捷交互能力
- 新增 Telegram 输入框左侧命令菜单（`setMyCommands`）：
  - 启动时自动注册常用命令（start/help/status/tasks/task_status/publish_now/retry_failed/restart）
- 主菜单微调：
  - 隐藏“搜索任务”按钮（保留命令能力，不在主菜单展示）
  - 在“帮助”左侧新增“📢 官方频道”按钮，链接 `https://t.me/sosoFlow`
- 新增底部快捷面板（Reply Keyboard）：
  - `/start` 时弹出底部按钮：`📋 任务列表`、`➕ 新建任务`
  - 点击底部“任务列表”直接打开任务列表
  - 点击底部“新建任务”直接进入分步输入流程（来源ID -> 目标ID）
- `/start` 展示样式优化：
  - 改为单条 `photo + caption + InlineKeyboard`，避免图片和文字分裂成两条气泡
  - 欢迎文案精简为要点式说明，增强“图文卡片”观感
- 提示文案规范优化：
  - “新建任务”输入引导改为纯文字提示，不再附带消息按钮
  - 单独文字提醒前统一增加醒目 emoji（如 `✍️/⚠️/ℹ️/💡`），提升视觉注意力
- 任务详情渲染稳定性修复：
  - 去除任务详情中的 Markdown 解析依赖，改为纯文本渲染
  - 避免任务名等字段包含 Markdown 特殊字符时导致“任务详情无法显示”
- 导航一致性与防混乱优化：
  - 主菜单统一为 `/start` 同款文本+按钮样式，`menu_home` 复用同一模板
  - 去除多数提示消息附带的“主菜单/任务列表”按钮，减少多消息同时可操作导致的界面混乱
  - 底部快捷面板继续保留为全局入口
- 任务详情可读性增强：
  - 任务详情字段中文化（模式/队列统计/自动监听/删除策略/下一条待发布/过滤摘要）
  - 源/目标支持显示 `chat_id + 频道/群组名称`（可获取时显示名称，失败时仅显示 ID）
- 文案去冗余优化：
  - 清理所有“当前界面保持不变”提示语
  - 动作反馈改为简洁结果提示（如“已启动任务/间隔已设为…”）
  - 输入提示统一保持 `✍️` 前缀
- 任务状态实时刷新修复：
  - 点击“启动/暂停”后，任务详情卡片即时刷新，状态行同步变更（运行中/暂停）
  - 避免出现“已启动但详情仍显示暂停”的不一致
- 导入范围按钮引导文案优化：
  - 点击“📥 导入范围”后，提示改为“请发送导入开始与结束帖子ID（示例：100 120 ，两个ID之间空格，单次最多 5000，如：100 5100）”
- 任务设置面板交互收敛（全自定义输入）：
  - 设置摘要文案改为中文示意（模式/间隔/日上限/时段/自动监听/发布后删源）
  - “间隔”改为单按钮，点击后引导输入秒数；更新后按钮文案实时显示最新值
  - “日上限”改为单按钮，点击后引导输入上限；更新后按钮文案实时显示最新值
  - 移除固定快捷值与“自定义”冗余按钮，保留直接输入链路
  - “时段”改为分步输入：先输入开始时间（HH:MM），再输入结束时间（HH:MM）
  - 模式/自动监听/发布后删源切换后，同步刷新设置面板内容与按钮状态
- 交互与发布稳定性修复（问题清单对齐）：
  - `/start` 恢复封面图发送逻辑（存在 `img/b.png` 时优先 `photo+caption+InlineKeyboard`，失败回退文字）
  - 新建任务来源/目标输入提示补充“可转发自动识别、点击数字复制发送确认”说明
  - 在来源/目标输入步骤中支持“直接转发自动确认”流程，避免手动复制输入
  - 修复目标ID误识别：在等待 `target_chat_id` 时，转发消息优先使用转发来源 chat_id 自动确认，不再把转发正文中的数字当目标ID
  - 任务设置新增“编辑来源ID/目标ID”按钮，支持在线重设源与目标
  - 发布失败包含 `Forbidden: bot is not a member of the channel chat` 时，追加“将机器人添加到群组”快捷按钮
  - 发布逻辑支持媒体组整体发布：同一 `media_group_id` 的 pending 项使用 `copy_messages/forward_messages` 批量发送，避免拆分成多次独立发布
  - 过滤文案澄清：`文字` 调整为 `仅保留纯文字`（摘要与按钮文案同步）
- 数据持久化默认路径优化（JustRunMyApp）：
  - 默认 `DATABASE_URL` 从 `sqlite:///data.db` 调整为 `sqlite:////mnt/sosoflow/sosoflow.db`
  - 保留环境变量优先级：若已设置 `DATABASE_URL`，继续优先使用环境变量
  - 启动初始化时自动执行 `os.makedirs("/mnt/sosoflow", exist_ok=True)`，确保 SQLite 目录存在
  - README 补充 JustRunMyApp 推荐配置 `DATABASE_URL=sqlite:////mnt/sosoflow/sosoflow.db`
- 时段与手动发布认知修复：
  - 任务默认时段由 `09:00-23:30` 调整为全天 `00:00-23:59`
  - 启动时自动迁移历史“旧默认时段”任务到全天（仅迁移 `09:00-23:30`）
  - 发布失败 `The message can't be copied` 增强说明：明确该错误通常不是时段导致，提示可改用 `forward` 或检查源权限/受保护内容
- 媒体组（media group）合并发布能力补全：
  - 发布入口仍先取最小 `message_id` 的 pending；若存在 `media_group_id`，则聚合同组 pending 并按 `message_id` 排序
  - 组发布路径：
    - `copy` 模式：批量 `copy_messages`，保持同组不拆分
    - `forward` 模式：优先 `forward_messages`，不支持时回退逐条 `forward_message`
  - 过滤逻辑升级为“整组判断”：任意一条命中过滤规则，整组 `skipped`
  - 失败逻辑升级为“整组失败”：任意异常时整组 `failed` 并写统一 `fail_reason`
  - 成功逻辑保持整组落库：每条记录写 `published/target_message_id/publish_logs`
  - 新消息捕获补充 `document` 类型识别（用于媒体组元数据完整性）
  - 非媒体组消息发布逻辑保持不变
- 媒体组“真正相册发布”增强（sendMediaGroup）：
  - `queue` 增加字段：`file_id`、`caption`（并在启动时对旧库做 `ALTER TABLE` 轻量补字段）
  - 新消息监听落库媒体 file_id：
    - `photo` 取 `photo[-1].file_id`
    - `video` 取 `video.file_id`
    - `document` 取 `document.file_id`
    - `caption` 同步存储
  - 对同 `media_group_id` 的 pending：
    - 若组内均有 `file_id` 且类型在 `photo/video/document`，使用 `send_media_group` 真正合并发送
    - caption 仅放第一条（其余为空）
  - `send_media_group` 成功后：按返回顺序回写每条 `target_message_id`，并整组标记 `published` + 写发布日志
  - 若组内缺 `file_id`（典型为 `/import_range` 历史消息）：
    - `copy` 模式 fallback 到 `copy_messages`，记录 `fallback_copy_messages_due_to_missing_file_id_may_split_album`
    - `forward` 模式 fallback 到 `forward_messages`（不支持则逐条 `forward_message`），记录 `fallback_forward_messages_due_to_missing_file_id_may_split_album`
  - 组过滤保持“任意一条不符合即整组 skip”，组失败保持“任意异常即整组 failed”

### 当前状态

- 项目已满足 `python main.py` 启动要求
- 支持 polling + scheduler + sqlite
- 可在 JustRunMyApp 直接部署
- 已完成命令参数健壮性补强：
  - 参数缺失统一返回用法提示
  - 数字参数范围校验（interval/daily/round/tick/import_range）
  - `chat_id` 支持负数（`int` 解析）
  - 时间格式严格校验 `HH:MM`
  - `set_mode` 限定 `copy|forward`
  - `set_delete_after_success`/`set_auto_capture` 限定 `on|off`
  - `use_task`/`delete_task`/`skip` 不存在对象明确提示
  - `publish_now` 无 pending 明确提示
  - 全局错误处理器避免未捕获异常中断主进程

### 已验证项

- 语法编译：`python -m py_compile main.py` 通过
- 纯函数测试：`pytest` 通过（`tests/test_core.py`，14 passed）
- 新增 `pytest.ini`（`asyncio_default_fixture_loop_scope=function`）后，deprecation 警告已消除
- 重新验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮交互改动后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮部署/重启功能后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮交互修复后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮命令菜单功能后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮主菜单微调后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮底部快捷面板后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮 `/start` 样式优化后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮提示文案规范优化后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮任务详情修复后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮导航一致性优化后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮任务详情中文化后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮文案去冗余后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮状态实时刷新修复后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮导入范围引导文案优化后再次验证：`python -m py_compile main.py` 通过
- 本轮任务设置面板交互收敛后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮交互与发布稳定性修复后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮数据持久化默认路径优化后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮时段与手动发布认知修复后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（14 passed）
- 本轮媒体组合并发布能力补全后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（18 passed）
- 本轮媒体组 sendMediaGroup 增强后再次验证：`python -m py_compile main.py` 通过，`pytest` 通过（22 passed）

### 下一步建议（最高优先）

- 为任务详情补充“最近发布日志预览（最近5条）”按钮，并沿用“界面保持+单独提示”交互规则。

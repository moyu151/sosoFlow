# PROJECT_LOG

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

### 下一步建议（最高优先）

- 增加轻量单元测试（优先覆盖参数解析和过滤判断函数）。

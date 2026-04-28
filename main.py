import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sosoFlow")


class Base(DeclarativeBase):
    pass


class RoleEnum(str, Enum):
    super = "super"
    admin = "admin"


class TaskModeEnum(str, Enum):
    copy = "copy"
    forward = "forward"


class QueueStatusEnum(str, Enum):
    pending = "pending"
    published = "published"
    failed = "failed"
    skipped = "skipped"


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    role: Mapped[RoleEnum] = mapped_column(SqlEnum(RoleEnum))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source_chat_id: Mapped[int] = mapped_column(Integer, index=True)
    target_chat_id: Mapped[int] = mapped_column(Integer)
    mode: Mapped[TaskModeEnum] = mapped_column(SqlEnum(TaskModeEnum), default=TaskModeEnum.copy)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    daily_limit: Mapped[int] = mapped_column(Integer, default=100)
    round_hours: Mapped[int] = mapped_column(Integer, default=24)
    round_limit: Mapped[int] = mapped_column(Integer, default=20)
    active_start_time: Mapped[str] = mapped_column(String(5), default="09:00")
    active_end_time: Mapped[str] = mapped_column(String(5), default="23:30")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_capture_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    delete_after_success: Mapped[bool] = mapped_column(Boolean, default=False)
    last_published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
    filters_rel: Mapped["TaskFilter"] = relationship(back_populates="task", uselist=False, cascade="all,delete")


class TaskFilter(Base):
    __tablename__ = "task_filters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), unique=True)
    require_photo: Mapped[bool] = mapped_column(Boolean, default=False)
    require_video: Mapped[bool] = mapped_column(Boolean, default=False)
    require_text: Mapped[bool] = mapped_column(Boolean, default=False)
    exclude_links: Mapped[bool] = mapped_column(Boolean, default=False)
    exclude_no_text: Mapped[bool] = mapped_column(Boolean, default=False)
    exclude_forwarded: Mapped[bool] = mapped_column(Boolean, default=False)
    exclude_sticker: Mapped[bool] = mapped_column(Boolean, default=False)
    exclude_poll: Mapped[bool] = mapped_column(Boolean, default=False)
    min_text_length: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_text_length: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    task: Mapped[Task] = relationship(back_populates="filters_rel")


class QueueItem(Base):
    __tablename__ = "queue"
    __table_args__ = (UniqueConstraint("task_id", "message_id", name="uq_task_msg"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    message_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[QueueStatusEnum] = mapped_column(SqlEnum(QueueStatusEnum), default=QueueStatusEnum.pending)
    target_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    message_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    text_preview: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    has_text: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_photo: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_video: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_links: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_forwarded: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    media_group_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    fail_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    filter_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class PublishLog(Base):
    __tablename__ = "publish_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    source_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(20))
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class GlobalSetting(Base):
    __tablename__ = "global_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    tick_seconds: Mapped[int] = mapped_column(Integer, default=60)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class UserState(Base):
    __tablename__ = "user_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    current_task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


@dataclass
class AppEnv:
    bot_token: str
    super_admin_ids: list[int]
    admin_user_ids: list[int]
    database_url: str
    tz: str


def parse_ids(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def load_env() -> AppEnv:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is required")
    return AppEnv(
        bot_token=token,
        super_admin_ids=parse_ids(os.getenv("SUPER_ADMIN_IDS", "")),
        admin_user_ids=parse_ids(os.getenv("ADMIN_USER_IDS", "")),
        database_url=os.getenv("DATABASE_URL", "sqlite:///data.db"),
        tz=os.getenv("TZ", "Asia/Shanghai"),
    )


env = load_env()
engine = create_engine(env.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
scheduler = AsyncIOScheduler(timezone=env.tz)
MAX_IMPORT_RANGE = 5000


def parse_hhmm(value: str) -> time:
    if not re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value):
        raise ValueError("时间格式必须为 HH:MM")
    hh, mm = value.split(":")
    return time(hour=int(hh), minute=int(mm))


def now() -> datetime:
    return datetime.now()


def is_admin(user_id: int) -> bool:
    with SessionLocal() as session:
        found = session.scalar(select(Admin).where(Admin.telegram_user_id == user_id))
        return found is not None


def get_role(user_id: int) -> Optional[RoleEnum]:
    with SessionLocal() as session:
        found = session.scalar(select(Admin).where(Admin.telegram_user_id == user_id))
        return found.role if found else None


def denied_text() -> str:
    return "无权限，请联系 @sosoFlow"


def parse_int(value: str, field_name: str) -> int:
    try:
        return int(value)
    except Exception as exc:
        raise ValueError(f"{field_name} 必须是整数") from exc


def parse_on_off(value: str, field_name: str) -> bool:
    normalized = value.lower().strip()
    if normalized not in {"on", "off"}:
        raise ValueError(f"{field_name} 仅支持 on/off")
    return normalized == "on"


def require_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else 0
        if not is_admin(uid):
            if update.message:
                await update.message.reply_text(denied_text())
            elif update.callback_query:
                await update.callback_query.answer(denied_text(), show_alert=True)
            return
        return await func(update, context)

    return wrapper


def require_super(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else 0
        role = get_role(uid)
        if role != RoleEnum.super:
            if update.message:
                await update.message.reply_text(denied_text())
            elif update.callback_query:
                await update.callback_query.answer(denied_text(), show_alert=True)
            return
        return await func(update, context)

    return wrapper


def get_or_create_user_state(user_id: int) -> UserState:
    with SessionLocal() as session:
        state = session.scalar(select(UserState).where(UserState.user_id == user_id))
        if state:
            return state
        state = UserState(user_id=user_id)
        session.add(state)
        session.commit()
        session.refresh(state)
        return state


def get_current_task(user_id: int) -> Optional[Task]:
    state = get_or_create_user_state(user_id)
    if not state.current_task_id:
        return None
    with SessionLocal() as session:
        return session.get(Task, state.current_task_id)


def ensure_task_filter(session, task_id: int) -> TaskFilter:
    task_filter = session.scalar(select(TaskFilter).where(TaskFilter.task_id == task_id))
    if task_filter:
        return task_filter
    task_filter = TaskFilter(task_id=task_id)
    session.add(task_filter)
    session.commit()
    session.refresh(task_filter)
    return task_filter


def task_stats(session, task_id: int) -> dict[str, int]:
    data = {}
    for status in [QueueStatusEnum.pending, QueueStatusEnum.published, QueueStatusEnum.failed, QueueStatusEnum.skipped]:
        count = session.scalar(select(func.count()).select_from(QueueItem).where(QueueItem.task_id == task_id, QueueItem.status == status)) or 0
        data[status.value] = count
    return data


def filter_summary(task_filter: TaskFilter) -> str:
    return (
        f"photo:{'on' if task_filter.require_photo else 'off'} | "
        f"video:{'on' if task_filter.require_video else 'off'} | "
        f"text:{'on' if task_filter.require_text else 'off'} | "
        f"exclude_links:{'on' if task_filter.exclude_links else 'off'} | "
        f"exclude_no_text:{'on' if task_filter.exclude_no_text else 'off'} | "
        f"exclude_forwarded:{'on' if task_filter.exclude_forwarded else 'off'} | "
        f"exclude_sticker:{'on' if task_filter.exclude_sticker else 'off'} | "
        f"exclude_poll:{'on' if task_filter.exclude_poll else 'off'} | "
        f"min:{task_filter.min_text_length or '-'} max:{task_filter.max_text_length or '-'}"
    )


def build_task_detail_text(session, task: Task) -> str:
    stats = task_stats(session, task.id)
    task_filter = ensure_task_filter(session, task.id)
    today_start = datetime.combine(now().date(), time.min)
    today_published = session.scalar(
        select(func.count()).select_from(QueueItem).where(
            QueueItem.task_id == task.id,
            QueueItem.status == QueueStatusEnum.published,
            QueueItem.published_at >= today_start,
        )
    ) or 0
    round_start = now() - timedelta(hours=task.round_hours)
    round_published = session.scalar(
        select(func.count()).select_from(QueueItem).where(
            QueueItem.task_id == task.id,
            QueueItem.status == QueueStatusEnum.published,
            QueueItem.published_at >= round_start,
        )
    ) or 0
    next_pending = session.scalar(
        select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.status == QueueStatusEnum.pending).order_by(QueueItem.message_id.asc())
    )
    next_publish_in_seconds = 0
    if task.last_published_at:
        elapsed = int((now() - task.last_published_at).total_seconds())
        next_publish_in_seconds = max(task.interval_seconds - elapsed, 0)
    return (
        f"🧩 任务详情\n"
        f"ID: {task.id}\n"
        f"名称: {task.name}\n"
        f"源: `{task.source_chat_id}`\n"
        f"目标: `{task.target_chat_id}`\n"
        f"模式: {task.mode.value}\n"
        f"状态: {'🟢运行中' if task.enabled else '⏸暂停'}\n"
        f"pending: {stats['pending']} | published: {stats['published']} | failed: {stats['failed']} | skipped: {stats['skipped']}\n"
        f"今日发布: {today_published}/{task.daily_limit}\n"
        f"当前轮发布: {round_published}/{task.round_limit} (窗口{task.round_hours}h)\n"
        f"间隔: {task.interval_seconds}s\n"
        f"下次可发布剩余: {next_publish_in_seconds}s\n"
        f"日上限: {task.daily_limit}\n"
        f"轮次: {task.round_hours}h / {task.round_limit}\n"
        f"时段: {task.active_start_time}-{task.active_end_time}\n"
        f"auto_capture: {'on' if task.auto_capture_enabled else 'off'}\n"
        f"delete_after_success: {'on' if task.delete_after_success else 'off'}\n"
        f"过滤: {filter_summary(task_filter)}\n"
        f"下一条 pending: {next_pending.message_id if next_pending else '无'}"
    )


def task_detail_keyboard(task_id: int):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶️ 启动", callback_data=f"task_start:{task_id}"), InlineKeyboardButton("⏸ 暂停", callback_data=f"task_pause:{task_id}")],
            [InlineKeyboardButton("🚀 立即发布", callback_data=f"task_publish:{task_id}"), InlineKeyboardButton("📥 导入范围", callback_data=f"task_import_hint:{task_id}")],
            [InlineKeyboardButton("⚙️ 设置", callback_data=f"task_setting_hint:{task_id}"), InlineKeyboardButton("🔁 重试失败", callback_data=f"task_retry:{task_id}")],
            [InlineKeyboardButton("🗑 删除任务", callback_data=f"task_delete_ask:{task_id}")],
            [InlineKeyboardButton("⬅️ 返回任务列表", callback_data="tasks_list")],
        ]
    )


def in_time_window(task: Task) -> bool:
    current = now().time()
    start = parse_hhmm(task.active_start_time)
    end = parse_hhmm(task.active_end_time)
    return is_time_in_window(current, start, end)


def is_time_in_window(current: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def reached_daily_limit(published_count: int, daily_limit: int) -> bool:
    return published_count >= daily_limit


def reached_round_limit(published_count: int, round_limit: int) -> bool:
    return published_count >= round_limit


def extract_links(text: str) -> bool:
    return bool(re.search(r"https?://|t\.me/", text or "", flags=re.IGNORECASE))


def apply_filters(item: QueueItem, task_filter: TaskFilter) -> Optional[str]:
    if item.has_text is None and item.has_photo is None and item.has_video is None and item.has_links is None:
        return None
    text_length = len(item.text_preview or "")
    checks = [
        (task_filter.require_photo and not bool(item.has_photo), "需要图片"),
        (task_filter.require_video and not bool(item.has_video), "需要视频"),
        (task_filter.require_text and not bool(item.has_text), "需要文字"),
        (task_filter.exclude_links and bool(item.has_links), "包含链接"),
        (task_filter.exclude_no_text and not bool(item.has_text), "无文字"),
        (task_filter.exclude_forwarded and bool(item.is_forwarded), "转发消息"),
        (task_filter.exclude_sticker and item.message_type == "sticker", "贴纸"),
        (task_filter.exclude_poll and item.message_type == "poll", "投票"),
        (task_filter.min_text_length is not None and text_length < task_filter.min_text_length, "文字过短"),
        (task_filter.max_text_length is not None and text_length > task_filter.max_text_length, "文字过长"),
    ]
    for flag, reason in checks:
        if flag:
            return reason
    return None


def write_log(session, task_id: int, source_message_id: Optional[int], target_message_id: Optional[int], action: str, message: str):
    session.add(
        PublishLog(
            task_id=task_id,
            source_message_id=source_message_id,
            target_message_id=target_message_id,
            action=action,
            message=message,
        )
    )


async def publish_one(application: Application, task: Task, ignore_interval: bool = False, ignore_window: bool = False) -> str:
    with SessionLocal() as session:
        db_task = session.get(Task, task.id)
        if not db_task:
            return "任务不存在"
        if not ignore_window and not in_time_window(db_task):
            return "不在允许时间段"
        today_start = datetime.combine(now().date(), time.min)
        today_published = session.scalar(
            select(func.count()).select_from(QueueItem).where(
                QueueItem.task_id == db_task.id,
                QueueItem.status == QueueStatusEnum.published,
                QueueItem.published_at >= today_start,
            )
        ) or 0
        if reached_daily_limit(today_published, db_task.daily_limit):
            return "达到每日上限"
        round_start = now() - timedelta(hours=db_task.round_hours)
        round_published = session.scalar(
            select(func.count()).select_from(QueueItem).where(
                QueueItem.task_id == db_task.id,
                QueueItem.status == QueueStatusEnum.published,
                QueueItem.published_at >= round_start,
            )
        ) or 0
        if reached_round_limit(round_published, db_task.round_limit):
            return "达到轮次上限"
        if not ignore_interval and db_task.last_published_at:
            elapsed = (now() - db_task.last_published_at).total_seconds()
            if elapsed < db_task.interval_seconds:
                return "未到发布间隔"
        item = session.scalar(
            select(QueueItem).where(QueueItem.task_id == db_task.id, QueueItem.status == QueueStatusEnum.pending).order_by(QueueItem.message_id.asc())
        )
        if not item:
            return "无 pending 消息"
        task_filter = ensure_task_filter(session, db_task.id)
        reason = apply_filters(item, task_filter)
        if reason:
            item.status = QueueStatusEnum.skipped
            item.filter_reason = reason
            write_log(session, db_task.id, item.message_id, None, "filter", reason)
            session.commit()
            return f"已过滤 message_id={item.message_id} ({reason})"
        try:
            if db_task.mode == TaskModeEnum.copy:
                sent = await application.bot.copy_message(chat_id=db_task.target_chat_id, from_chat_id=db_task.source_chat_id, message_id=item.message_id)
                target_id = sent.message_id if sent else None
            else:
                sent = await application.bot.forward_message(chat_id=db_task.target_chat_id, from_chat_id=db_task.source_chat_id, message_id=item.message_id)
                target_id = sent.message_id if sent else None
            item.status = QueueStatusEnum.published
            item.target_message_id = target_id
            item.published_at = now()
            db_task.last_published_at = now()
            write_log(session, db_task.id, item.message_id, target_id, "publish", "发布成功")
            if db_task.delete_after_success:
                try:
                    await application.bot.delete_message(chat_id=db_task.source_chat_id, message_id=item.message_id)
                    item.deleted_at = now()
                    write_log(session, db_task.id, item.message_id, target_id, "delete", "删除源消息成功")
                except Exception as delete_exc:
                    msg = f"删除失败: {delete_exc}"
                    item.fail_reason = msg
                    write_log(session, db_task.id, item.message_id, target_id, "fail", msg)
            session.commit()
            return f"发布成功 message_id={item.message_id}"
        except Exception as exc:
            item.status = QueueStatusEnum.failed
            item.fail_reason = str(exc)
            write_log(session, db_task.id, item.message_id, None, "fail", str(exc))
            session.commit()
            return f"发布失败 message_id={item.message_id}: {exc}"


async def publish_tick(application: Application):
    with SessionLocal() as session:
        tasks = session.scalars(select(Task).where(Task.enabled.is_(True))).all()
    for task in tasks:
        try:
            result = await publish_one(application, task, ignore_interval=False, ignore_window=False)
            logger.info("publish_tick task=%s result=%s", task.id, result)
        except Exception:
            logger.exception("publish_tick task=%s unexpected error", task.id)


def main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 任务列表", callback_data="tasks_list"), InlineKeyboardButton("➕ 新建任务", callback_data="create_task_hint")],
            [InlineKeyboardButton("📊 当前状态", callback_data="global_status"), InlineKeyboardButton("👤 管理员", callback_data="admins_list")],
            [InlineKeyboardButton("❓ 帮助", callback_data="help_menu")],
        ]
    )


@require_admin
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("欢迎使用 sosoFlow 🚚", reply_markup=main_menu_keyboard())


@require_admin
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📘 sosoFlow 帮助\n\n"
        "【通用】(admin/super)\n"
        "/start 主菜单\n/help 查看本帮助\n/status 系统状态\n\n"
        "【任务】(admin/super)\n"
        "/add_task <name> <source_chat_id> <target_chat_id>  例: /add_task test -1001 -1002\n"
        "/tasks 查看任务列表\n/use_task <task_id> 选择当前任务\n/task_status 当前任务详情\n/delete_task <task_id> 删除(二次确认)\n\n"
        "【队列】(admin/super)\n"
        f"/import_range <start_message_id> <end_message_id> (单次最多 {MAX_IMPORT_RANGE})\n"
        "/publish_now 立即发布下一条 pending（忽略时间窗与间隔）\n"
        "/skip <message_id> 跳过当前任务队列消息\n/retry_failed 重试 failed 消息\n\n"
        "【设置】(admin/super)\n"
        "/set_interval <seconds> (必须 > 0)\n"
        "/set_round <hours> <limit> (hours>0, limit>=0)\n"
        "/set_daily_limit <count> (count>=0)\n"
        "/set_time_window <HH:MM> <HH:MM>\n"
        "/set_mode copy|forward\n"
        "/set_delete_after_success on|off\n"
        "/set_auto_capture on|off\n"
        "/set_tick <seconds> (仅 super, 10-3600)\n\n"
        "【过滤】(admin/super)\n"
        "/filters 查看过滤\n"
        "/set_filter <bool_key> on|off\n"
        "/set_filter min_text_length <number>\n"
        "/set_filter max_text_length <number>\n\n"
        "【管理员】\n"
        "/admins 查看管理员(admin/super)\n"
        "/add_admin <telegram_user_id> (仅 super)\n"
        "/remove_admin <telegram_user_id> (仅 super)\n\n"
        "非管理员统一返回：无权限，请联系 @sosoFlow"
    )
    await update.message.reply_text(text)


@require_admin
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today_start = datetime.combine(now().date(), time.min)
    with SessionLocal() as session:
        task_count = session.scalar(select(func.count()).select_from(Task)) or 0
        enabled_count = session.scalar(select(func.count()).select_from(Task).where(Task.enabled.is_(True))) or 0
        queue_pending = session.scalar(select(func.count()).select_from(QueueItem).where(QueueItem.status == QueueStatusEnum.pending)) or 0
        today_published = session.scalar(
            select(func.count()).select_from(QueueItem).where(
                QueueItem.status == QueueStatusEnum.published,
                QueueItem.published_at >= today_start,
            )
        ) or 0
        today_failed = session.scalar(
            select(func.count()).select_from(QueueItem).where(
                QueueItem.status == QueueStatusEnum.failed,
                QueueItem.updated_at >= today_start,
            )
        ) or 0
        failed_total = session.scalar(select(func.count()).select_from(QueueItem).where(QueueItem.status == QueueStatusEnum.failed)) or 0
        tick = session.get(GlobalSetting, 1).tick_seconds
    await update.message.reply_text(
        f"📊 系统状态\n"
        f"任务总数: {task_count}\n"
        f"运行中: {enabled_count}\n"
        f"pending: {queue_pending}\n"
        f"今日发布: {today_published}\n"
        f"今日失败: {today_failed}\n"
        f"累计失败: {failed_total}\n"
        f"tick_seconds: {tick}"
    )


@require_admin
async def add_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("用法: /add_task <name> <source_chat_id> <target_chat_id>")
        return
    try:
        name = context.args[0]
        source_chat_id = parse_int(context.args[1], "source_chat_id")
        target_chat_id = parse_int(context.args[2], "target_chat_id")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /add_task <name> <source_chat_id> <target_chat_id>")
        return
    with SessionLocal() as session:
        task = Task(name=name, source_chat_id=source_chat_id, target_chat_id=target_chat_id)
        session.add(task)
        session.commit()
        session.refresh(task)
        session.add(TaskFilter(task_id=task.id))
        session.commit()
    await update.message.reply_text(f"✅ 任务已创建 id={task.id}")


@require_admin
async def tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with SessionLocal() as session:
        tasks = session.scalars(select(Task).order_by(Task.id.asc())).all()
    if not tasks:
        await update.message.reply_text("暂无任务")
        return
    kb = [[InlineKeyboardButton(f"{'🟢' if t.enabled else '⏸'} {t.id} {t.name}", callback_data=f"task_view:{t.id}")] for t in tasks]
    await update.message.reply_text("📋 任务列表", reply_markup=InlineKeyboardMarkup(kb))


@require_admin
async def use_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("用法: /use_task <task_id>")
        return
    try:
        task_id = parse_int(context.args[0], "task_id")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /use_task <task_id>")
        return
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if not task:
            await update.message.reply_text("任务不存在")
            return
        state = session.scalar(select(UserState).where(UserState.user_id == update.effective_user.id))
        if not state:
            state = UserState(user_id=update.effective_user.id, current_task_id=task_id)
            session.add(state)
        else:
            state.current_task_id = task_id
        session.commit()
    await update.message.reply_text(f"✅ 已选择任务 {task_id}")


@require_admin
async def task_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    with SessionLocal() as session:
        db_task = session.get(Task, task.id)
        await update.message.reply_text(build_task_detail_text(session, db_task), parse_mode=ParseMode.MARKDOWN)


@require_admin
async def delete_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("用法: /delete_task <task_id>")
        return
    try:
        task_id = parse_int(context.args[0], "task_id")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /delete_task <task_id>")
        return
    with SessionLocal() as session:
        if not session.get(Task, task_id):
            await update.message.reply_text(f"任务不存在: {task_id}")
            return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("确认删除", callback_data=f"task_delete_yes:{task_id}")]])
    await update.message.reply_text(f"⚠️ 确认删除任务 {task_id}？", reply_markup=kb)


@require_admin
async def import_range_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    if len(context.args) != 2:
        await update.message.reply_text("用法: /import_range <start_message_id> <end_message_id>")
        return
    try:
        start_id = parse_int(context.args[0], "start_message_id")
        end_id = parse_int(context.args[1], "end_message_id")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /import_range <start_message_id> <end_message_id>")
        return
    if start_id > end_id:
        await update.message.reply_text("start_message_id 必须 <= end_message_id")
        return
    if end_id - start_id + 1 > MAX_IMPORT_RANGE:
        await update.message.reply_text(f"单次导入最多 {MAX_IMPORT_RANGE} 条，请缩小范围后重试")
        return
    inserted = 0
    duplicated = 0
    with SessionLocal() as session:
        for msg_id in range(start_id, end_id + 1):
            exists = session.scalar(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.message_id == msg_id))
            if exists:
                duplicated += 1
                continue
            session.add(QueueItem(task_id=task.id, message_id=msg_id, message_type="unknown"))
            inserted += 1
        session.commit()
    await update.message.reply_text(f"✅ 导入完成\n新增: {inserted}\n重复跳过: {duplicated}")


@require_admin
async def publish_now_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    result = await publish_one(context.application, task, ignore_interval=True, ignore_window=True)
    await update.message.reply_text(f"🚀 {result}")


@require_admin
async def skip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    if len(context.args) != 1:
        await update.message.reply_text("用法: /skip <message_id>")
        return
    try:
        message_id = parse_int(context.args[0], "message_id")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /skip <message_id>")
        return
    with SessionLocal() as session:
        item = session.scalar(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.message_id == message_id))
        if not item:
            await update.message.reply_text("消息不在当前任务队列")
            return
        item.status = QueueStatusEnum.skipped
        write_log(session, task.id, item.message_id, None, "skip", "手动跳过")
        session.commit()
    await update.message.reply_text("✅ 已跳过")


@require_admin
async def retry_failed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    with SessionLocal() as session:
        rows = session.scalars(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.status == QueueStatusEnum.failed)).all()
        for row in rows:
            row.status = QueueStatusEnum.pending
            row.fail_reason = None
        session.commit()
    await update.message.reply_text(f"✅ 已重置 failed -> pending: {len(rows)}")


async def set_current_task_simple(update: Update, context: ContextTypes.DEFAULT_TYPE, field: str, value):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return None
    with SessionLocal() as session:
        db_task = session.get(Task, task.id)
        setattr(db_task, field, value)
        session.commit()
    return task.id


@require_admin
async def set_interval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("用法: /set_interval <seconds>")
        return
    try:
        seconds = parse_int(context.args[0], "seconds")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /set_interval <seconds>")
        return
    if seconds <= 0:
        await update.message.reply_text("interval 必须 >= 1")
        return
    task_id = await set_current_task_simple(update, context, "interval_seconds", seconds)
    if task_id:
        await update.message.reply_text(f"✅ 任务 {task_id} interval={seconds}")


@require_admin
async def set_round_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("用法: /set_round <hours> <limit>")
        return
    try:
        hours = parse_int(context.args[0], "hours")
        limit = parse_int(context.args[1], "limit")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /set_round <hours> <limit>")
        return
    if hours <= 0 or limit < 0:
        await update.message.reply_text("hours 必须 > 0，limit 必须 >= 0")
        return
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    with SessionLocal() as session:
        db_task = session.get(Task, task.id)
        db_task.round_hours = hours
        db_task.round_limit = limit
        session.commit()
    await update.message.reply_text(f"✅ round={hours}h/{limit}")


@require_admin
async def set_daily_limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("用法: /set_daily_limit <count>")
        return
    try:
        count = parse_int(context.args[0], "count")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /set_daily_limit <count>")
        return
    if count < 0:
        await update.message.reply_text("daily_limit 必须 >= 0")
        return
    task_id = await set_current_task_simple(update, context, "daily_limit", count)
    if task_id:
        await update.message.reply_text(f"✅ daily_limit={count}")


@require_admin
async def set_time_window_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("用法: /set_time_window <HH:MM> <HH:MM>")
        return
    start, end = context.args[0], context.args[1]
    try:
        parse_hhmm(start)
        parse_hhmm(end)
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /set_time_window <HH:MM> <HH:MM>")
        return
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    with SessionLocal() as session:
        db_task = session.get(Task, task.id)
        db_task.active_start_time = start
        db_task.active_end_time = end
        session.commit()
    await update.message.reply_text(f"✅ time_window={start}-{end}")


@require_admin
async def set_mode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("用法: /set_mode copy|forward")
        return
    mode = context.args[0]
    if mode not in ["copy", "forward"]:
        await update.message.reply_text("mode 仅支持 copy|forward")
        return
    task_id = await set_current_task_simple(update, context, "mode", TaskModeEnum(mode))
    if task_id:
        await update.message.reply_text(f"✅ mode={mode}")


@require_admin
async def set_toggle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, field: str, name: str):
    if len(context.args) != 1:
        await update.message.reply_text(f"用法: /{name} on|off")
        return
    try:
        value = parse_on_off(context.args[0], name)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    task_id = await set_current_task_simple(update, context, field, value)
    if task_id:
        await update.message.reply_text(f"✅ {name}={'on' if value else 'off'}")


@require_admin
async def set_delete_after_success_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_toggle_cmd(update, context, "delete_after_success", "set_delete_after_success")


@require_admin
async def set_auto_capture_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_toggle_cmd(update, context, "auto_capture_enabled", "set_auto_capture")


@require_super
async def set_tick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("用法: /set_tick <seconds>")
        return
    try:
        seconds = parse_int(context.args[0], "seconds")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /set_tick <seconds>")
        return
    if not 10 <= seconds <= 3600:
        await update.message.reply_text("tick_seconds 允许范围 10-3600")
        return
    with SessionLocal() as session:
        setting = session.get(GlobalSetting, 1)
        setting.tick_seconds = seconds
        session.commit()
    scheduler.reschedule_job("publish_tick", trigger="interval", seconds=seconds)
    await update.message.reply_text(f"✅ tick_seconds={seconds}")


@require_admin
async def start_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = await set_current_task_simple(update, context, "enabled", True)
    if task_id:
        await update.message.reply_text("✅ 任务已启动")


@require_admin
async def pause_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = await set_current_task_simple(update, context, "enabled", False)
    if task_id:
        await update.message.reply_text("✅ 任务已暂停")


@require_admin
async def filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    with SessionLocal() as session:
        task_filter = ensure_task_filter(session, task.id)
        await update.message.reply_text(f"🔎 当前过滤规则\n{filter_summary(task_filter)}")


@require_admin
async def set_filter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    if len(context.args) != 2:
        await update.message.reply_text("用法: /set_filter <key> on|off|number")
        return
    key, value = context.args[0], context.args[1]
    allowed_bool = {
        "require_photo",
        "require_video",
        "require_text",
        "exclude_links",
        "exclude_no_text",
        "exclude_forwarded",
        "exclude_sticker",
        "exclude_poll",
    }
    with SessionLocal() as session:
        task_filter = ensure_task_filter(session, task.id)
        if key in allowed_bool:
            try:
                setattr(task_filter, key, parse_on_off(value, key))
            except ValueError as exc:
                await update.message.reply_text(str(exc))
                return
        elif key in {"min_text_length", "max_text_length"}:
            try:
                parsed = parse_int(value, key)
            except ValueError as exc:
                await update.message.reply_text(str(exc))
                return
            if parsed < 0:
                await update.message.reply_text(f"{key} 必须 >= 0")
                return
            setattr(task_filter, key, parsed)
        else:
            await update.message.reply_text("未知过滤键")
            return
        session.commit()
    await update.message.reply_text(f"✅ 过滤已更新 {key}={value}")


@require_super
async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("用法: /add_admin <telegram_user_id>")
        return
    try:
        uid = parse_int(context.args[0], "telegram_user_id")
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    with SessionLocal() as session:
        existing = session.scalar(select(Admin).where(Admin.telegram_user_id == uid))
        if existing:
            await update.message.reply_text("该用户已是管理员")
            return
        session.add(Admin(telegram_user_id=uid, role=RoleEnum.admin))
        session.commit()
    await update.message.reply_text(f"✅ 已添加管理员 {uid}")


@require_super
async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("用法: /remove_admin <telegram_user_id>")
        return
    try:
        uid = parse_int(context.args[0], "telegram_user_id")
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    with SessionLocal() as session:
        row = session.scalar(select(Admin).where(Admin.telegram_user_id == uid, Admin.role == RoleEnum.admin))
        if not row:
            await update.message.reply_text("未找到普通管理员")
            return
        session.delete(row)
        session.commit()
    await update.message.reply_text(f"✅ 已移除管理员 {uid}")


@require_admin
async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with SessionLocal() as session:
        rows = session.scalars(select(Admin).order_by(Admin.role.asc(), Admin.id.asc())).all()
    text = "👤 管理员列表\n" + "\n".join([f"- {a.telegram_user_id} ({a.role.value})" for a in rows])
    await update.message.reply_text(text)


@require_admin
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "tasks_list":
        with SessionLocal() as session:
            tasks = session.scalars(select(Task).order_by(Task.id.asc())).all()
        if not tasks:
            await query.edit_message_text("暂无任务")
            return
        kb = [[InlineKeyboardButton(f"{'🟢' if t.enabled else '⏸'} {t.id} {t.name}", callback_data=f"task_view:{t.id}")] for t in tasks]
        await query.edit_message_text("📋 任务列表", reply_markup=InlineKeyboardMarkup(kb))
        return
    if data == "create_task_hint":
        await query.edit_message_text("请使用 /add_task <name> <source_chat_id> <target_chat_id>")
        return
    if data == "global_status":
        with SessionLocal() as session:
            task_count = session.scalar(select(func.count()).select_from(Task)) or 0
        await query.edit_message_text(f"📊 当前任务总数: {task_count}")
        return
    if data == "admins_list":
        with SessionLocal() as session:
            rows = session.scalars(select(Admin)).all()
        await query.edit_message_text("👤 管理员\n" + "\n".join([f"{x.telegram_user_id} ({x.role.value})" for x in rows]))
        return
    if data == "help_menu":
        await query.edit_message_text("请使用 /help 查看完整命令说明")
        return
    if ":" not in data:
        return
    action, raw_task_id = data.split(":", 1)
    try:
        task_id = parse_int(raw_task_id, "task_id")
    except ValueError:
        await query.edit_message_text("回调参数错误")
        return
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if not task:
            await query.edit_message_text("任务不存在")
            return
        if action == "task_view":
            await query.edit_message_text(build_task_detail_text(session, task), parse_mode=ParseMode.MARKDOWN, reply_markup=task_detail_keyboard(task_id))
            return
        if action == "task_start":
            task.enabled = True
            session.commit()
        elif action == "task_pause":
            task.enabled = False
            session.commit()
        elif action == "task_publish":
            result = await publish_one(context.application, task, ignore_interval=True, ignore_window=True)
            await query.message.reply_text(f"🚀 {result}")
        elif action == "task_retry":
            rows = session.scalars(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.status == QueueStatusEnum.failed)).all()
            for row in rows:
                row.status = QueueStatusEnum.pending
                row.fail_reason = None
            session.commit()
        elif action == "task_import_hint":
            await query.message.reply_text(f"请先 /use_task {task.id}，再 /import_range <start> <end>")
        elif action == "task_setting_hint":
            await query.message.reply_text("设置请使用 /set_interval /set_round /set_daily_limit /set_time_window /set_mode")
        elif action == "task_delete_ask":
            await query.message.reply_text(f"⚠️ 二次确认删除任务 {task.id}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("确认删除", callback_data=f"task_delete_yes:{task.id}")]]))
            return
        elif action == "task_delete_yes":
            session.delete(task)
            session.commit()
            await query.edit_message_text(f"🗑 已删除任务 {task_id}")
            return
        await query.edit_message_text(build_task_detail_text(session, task), parse_mode=ParseMode.MARKDOWN, reply_markup=task_detail_keyboard(task_id))


@require_admin
async def capture_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.chat:
        return
    source_chat_id = msg.chat_id
    text_value = msg.text or msg.caption or ""
    message_type = "text"
    if msg.photo:
        message_type = "photo"
    elif msg.video:
        message_type = "video"
    elif msg.sticker:
        message_type = "sticker"
    elif msg.poll:
        message_type = "poll"
    with SessionLocal() as session:
        tasks = session.scalars(select(Task).where(Task.source_chat_id == source_chat_id, Task.auto_capture_enabled.is_(True))).all()
        for task in tasks:
            exists = session.scalar(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.message_id == msg.message_id))
            if exists:
                continue
            session.add(
                QueueItem(
                    task_id=task.id,
                    message_id=msg.message_id,
                    status=QueueStatusEnum.pending,
                    message_type=message_type,
                    text_preview=text_value[:280] if text_value else None,
                    has_text=bool(text_value),
                    has_photo=bool(msg.photo),
                    has_video=bool(msg.video),
                    has_links=extract_links(text_value),
                    is_forwarded=bool(msg.forward_origin),
                    media_group_id=msg.media_group_id,
                )
            )
        session.commit()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled handler error: %s", context.error)


def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        setting = session.get(GlobalSetting, 1)
        if not setting:
            session.add(GlobalSetting(id=1, tick_seconds=60))
        for uid in env.super_admin_ids:
            existing = session.scalar(select(Admin).where(Admin.telegram_user_id == uid))
            if not existing:
                session.add(Admin(telegram_user_id=uid, role=RoleEnum.super))
        for uid in env.admin_user_ids:
            existing = session.scalar(select(Admin).where(Admin.telegram_user_id == uid))
            if not existing:
                session.add(Admin(telegram_user_id=uid, role=RoleEnum.admin))
        session.commit()


def token_preview(token: str) -> str:
    if len(token) <= 10:
        return "***"
    return f"{token[:6]}...{token[-4:]}"


def startup_self_check():
    with SessionLocal() as session:
        setting = session.get(GlobalSetting, 1)
        tick_seconds = setting.tick_seconds if setting else 60
        total_admins = session.scalar(select(func.count()).select_from(Admin)) or 0
        super_admins = session.scalar(select(func.count()).select_from(Admin).where(Admin.role == RoleEnum.super)) or 0
        normal_admins = session.scalar(select(func.count()).select_from(Admin).where(Admin.role == RoleEnum.admin)) or 0
    logger.info("===== sosoFlow startup self-check =====")
    logger.info("TZ=%s", env.tz)
    logger.info("DATABASE_URL=%s", env.database_url)
    logger.info("BOT_TOKEN=%s", token_preview(env.bot_token))
    logger.info("tick_seconds=%s", tick_seconds)
    logger.info("admins total=%s super=%s admin=%s", total_admins, super_admins, normal_admins)
    logger.info("=======================================")


def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("add_task", add_task_cmd))
    app.add_handler(CommandHandler("tasks", tasks_cmd))
    app.add_handler(CommandHandler("use_task", use_task_cmd))
    app.add_handler(CommandHandler("task_status", task_status_cmd))
    app.add_handler(CommandHandler("delete_task", delete_task_cmd))
    app.add_handler(CommandHandler("import_range", import_range_cmd))
    app.add_handler(CommandHandler("publish_now", publish_now_cmd))
    app.add_handler(CommandHandler("skip", skip_cmd))
    app.add_handler(CommandHandler("retry_failed", retry_failed_cmd))
    app.add_handler(CommandHandler("set_interval", set_interval_cmd))
    app.add_handler(CommandHandler("set_round", set_round_cmd))
    app.add_handler(CommandHandler("set_daily_limit", set_daily_limit_cmd))
    app.add_handler(CommandHandler("set_time_window", set_time_window_cmd))
    app.add_handler(CommandHandler("set_mode", set_mode_cmd))
    app.add_handler(CommandHandler("set_delete_after_success", set_delete_after_success_cmd))
    app.add_handler(CommandHandler("set_auto_capture", set_auto_capture_cmd))
    app.add_handler(CommandHandler("set_tick", set_tick_cmd))
    app.add_handler(CommandHandler("start_task", start_task_cmd))
    app.add_handler(CommandHandler("pause_task", pause_task_cmd))
    app.add_handler(CommandHandler("filters", filters_cmd))
    app.add_handler(CommandHandler("set_filter", set_filter_cmd))
    app.add_handler(CommandHandler("add_admin", add_admin_cmd))
    app.add_handler(CommandHandler("remove_admin", remove_admin_cmd))
    app.add_handler(CommandHandler("admins", admins_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), capture_new_message))


def main():
    init_db()
    startup_self_check()
    application = Application.builder().token(env.bot_token).build()
    register_handlers(application)
    application.add_error_handler(on_error)
    with SessionLocal() as session:
        tick_seconds = session.get(GlobalSetting, 1).tick_seconds
    scheduler.add_job(publish_tick, "interval", seconds=tick_seconds, args=[application], id="publish_tick", replace_existing=True)
    scheduler.start()
    logger.info("sosoFlow started. polling + scheduler enabled")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

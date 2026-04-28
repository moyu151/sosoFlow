import logging
import os
import re
import sys
import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram import InputMediaDocument, InputMediaPhoto, InputMediaVideo
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
    BigInteger,
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
    inspect,
    or_,
    select,
    text as sql_text,
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
    waiting = "waiting"


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    role: Mapped[RoleEnum] = mapped_column(SqlEnum(RoleEnum))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_chat_id: Mapped[int] = mapped_column(BigInteger)
    mode: Mapped[TaskModeEnum] = mapped_column(SqlEnum(TaskModeEnum), default=TaskModeEnum.copy)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    daily_limit: Mapped[int] = mapped_column(Integer, default=100)
    round_hours: Mapped[int] = mapped_column(Integer, default=24)
    round_limit: Mapped[int] = mapped_column(Integer, default=20)
    active_start_time: Mapped[str] = mapped_column(String(5), default="00:00")
    active_end_time: Mapped[str] = mapped_column(String(5), default="23:59")
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
    message_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[QueueStatusEnum] = mapped_column(SqlEnum(QueueStatusEnum), default=QueueStatusEnum.pending)
    target_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    message_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    file_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    filter_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class PublishLog(Base):
    __tablename__ = "publish_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    source_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    target_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
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
    deploy_version: str
    startup_notify_chat_ids: list[int]
    restart_strategy: str


def parse_ids(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]

def normalize_database_url(value: str) -> str:
    raw = (value or "").strip()
    if raw.startswith("postgres://"):
        return "postgresql://" + raw[len("postgres://") :]
    return raw


def load_env() -> AppEnv:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is required")
    raw_database_url = os.getenv("DATABASE_URL", "").strip()
    return AppEnv(
        bot_token=token,
        super_admin_ids=parse_ids(os.getenv("SUPER_ADMIN_IDS", "")),
        admin_user_ids=parse_ids(os.getenv("ADMIN_USER_IDS", "")),
        database_url=normalize_database_url(raw_database_url) or "sqlite:////mnt/sosoflow/sosoflow.db",
        tz=os.getenv("TZ", "Asia/Shanghai"),
        deploy_version=os.getenv("DEPLOY_VERSION", "").strip() or os.getenv("GIT_COMMIT", "").strip() or "unknown",
        startup_notify_chat_ids=parse_ids(os.getenv("STARTUP_NOTIFY_CHAT_IDS", "")),
        restart_strategy=os.getenv("RESTART_STRATEGY", "guide").strip().lower(),
    )


env = load_env()
engine = create_engine(env.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
scheduler = AsyncIOScheduler(timezone=env.tz)
MAX_IMPORT_RANGE = 5000
TASKS_PAGE_SIZE = 8
COVER_IMAGE_PATH = os.path.join("img", "b.png")
WAITING_RETRY_INTERVAL_MINUTES = 10
WAITING_MAX_RETRY_COUNT = 20


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

def extract_forward_chat_id(msg) -> Optional[int]:
    origin = getattr(msg, "forward_origin", None)
    if origin:
        origin_chat = getattr(origin, "chat", None)
        if origin_chat and getattr(origin_chat, "id", None) is not None:
            return origin_chat.id
        sender_chat = getattr(origin, "sender_chat", None)
        if sender_chat and getattr(sender_chat, "id", None) is not None:
            return sender_chat.id
    legacy_chat = getattr(msg, "forward_from_chat", None)
    if legacy_chat and getattr(legacy_chat, "id", None) is not None:
        return legacy_chat.id
    return None


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
    for status in [QueueStatusEnum.pending, QueueStatusEnum.waiting, QueueStatusEnum.published, QueueStatusEnum.failed, QueueStatusEnum.skipped]:
        count = session.scalar(select(func.count()).select_from(QueueItem).where(QueueItem.task_id == task_id, QueueItem.status == status)) or 0
        data[status.value] = count
    return data


def filter_summary(task_filter: TaskFilter) -> str:
    return (
        f"图片:{'开' if task_filter.require_photo else '关'} | "
        f"视频:{'开' if task_filter.require_video else '关'} | "
        f"仅保留纯文字:{'开' if task_filter.require_text else '关'} | "
        f"排除链接:{'开' if task_filter.exclude_links else '关'} | "
        f"排除无字:{'开' if task_filter.exclude_no_text else '关'} | "
        f"排除转发:{'开' if task_filter.exclude_forwarded else '关'} | "
        f"排除贴纸:{'开' if task_filter.exclude_sticker else '关'} | "
        f"排除投票:{'开' if task_filter.exclude_poll else '关'} | "
        f"最短:{task_filter.min_text_length or '无'} 最长:{task_filter.max_text_length or '无'}"
    )


def mode_label(mode: TaskModeEnum) -> str:
    return "复制" if mode == TaskModeEnum.copy else "转发"


def bool_cn(value: bool) -> str:
    return "开启" if value else "关闭"


async def resolve_chat_display_name(application: Application, chat_id: int) -> Optional[str]:
    try:
        chat = await application.bot.get_chat(chat_id=chat_id)
    except Exception:
        return None
    title = getattr(chat, "title", None)
    if title:
        return title
    username = getattr(chat, "username", None)
    if username:
        return f"@{username}"
    full_name = getattr(chat, "full_name", None)
    if full_name:
        return full_name
    return None


def build_task_detail_text(session, task: Task, source_name: Optional[str] = None, target_name: Optional[str] = None) -> str:
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
    next_pending = session.scalar(
        select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.status == QueueStatusEnum.pending).order_by(QueueItem.message_id.asc())
    )
    next_publish_in_seconds = 0
    if task.last_published_at:
        elapsed = int((now() - task.last_published_at).total_seconds())
        next_publish_in_seconds = max(task.interval_seconds - elapsed, 0)
    source_line = f"{task.source_chat_id}" if not source_name else f"{task.source_chat_id}（{source_name}）"
    target_line = f"{task.target_chat_id}" if not target_name else f"{task.target_chat_id}（{target_name}）"
    return (
        f"🧩 任务详情\n"
        f"ID: {task.id}\n"
        f"名称: {task.name}\n"
        f"源: {source_line}\n"
        f"目标: {target_line}\n"
        f"模式: {mode_label(task.mode)}\n"
        f"状态: {'🟢运行中' if task.enabled else '⏸暂停'}\n"
        f"队列统计: 待发布 {stats['pending']} | 等待重试 {stats['waiting']} | 已发布 {stats['published']} | 失败 {stats['failed']} | 跳过 {stats['skipped']}\n"
        f"今日发布: {today_published}/{task.daily_limit}\n"
        f"间隔: {task.interval_seconds}s\n"
        f"下次可发布剩余: {next_publish_in_seconds}s\n"
        f"日上限: {task.daily_limit}\n"
        f"时段: {task.active_start_time}-{task.active_end_time}\n"
        f"自动监听: {bool_cn(task.auto_capture_enabled)}\n"
        f"发布后删除源消息: {bool_cn(task.delete_after_success)}\n"
        f"过滤: {filter_summary(task_filter)}\n"
        f"下一条待发布: {next_pending.message_id if next_pending else '无'}"
    )


def task_detail_keyboard(task_id: int):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶️ 启动", callback_data=f"task_start:{task_id}"), InlineKeyboardButton("⏸ 暂停", callback_data=f"task_pause:{task_id}")],
            [InlineKeyboardButton("🚀 立即发布", callback_data=f"task_publish:{task_id}"), InlineKeyboardButton("📥 导入范围", callback_data=f"task_import_hint:{task_id}")],
            [InlineKeyboardButton("⚙️ 设置", callback_data=f"task_settings:{task_id}"), InlineKeyboardButton("🔁 重试失败", callback_data=f"task_retry:{task_id}")],
            [InlineKeyboardButton("🔄 刷新", callback_data=f"task_view:{task_id}")],
            [InlineKeyboardButton("🗑 删除任务", callback_data=f"task_delete_ask:{task_id}")],
            [InlineKeyboardButton("⬅️ 返回任务列表", callback_data="tasks_list")],
        ]
    )


def task_settings_keyboard(task: Task):
    mode_text = "🧭 模式: 复制" if task.mode == TaskModeEnum.copy else "🧭 模式: 转发"
    auto_capture_text = f"📡 自动监听: {bool_cn(task.auto_capture_enabled)}"
    delete_text = f"🧹 发布后删源: {bool_cn(task.delete_after_success)}"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(mode_text, callback_data=f"task_toggle_mode:{task.id}")],
            [InlineKeyboardButton(auto_capture_text, callback_data=f"task_toggle_auto_capture:{task.id}")],
            [InlineKeyboardButton(delete_text, callback_data=f"task_toggle_delete:{task.id}")],
            [InlineKeyboardButton(f"⏱ 间隔: {task.interval_seconds} 秒", callback_data=f"task_input_interval:{task.id}")],
            [InlineKeyboardButton(f"📊 日上限: {task.daily_limit}", callback_data=f"task_input_daily:{task.id}")],
            [InlineKeyboardButton(f"🕒 时段: {task.active_start_time}-{task.active_end_time}", callback_data=f"task_input_window:{task.id}")],
            [
                InlineKeyboardButton(f"🧷 来源ID: {task.source_chat_id}", callback_data=f"task_edit_source:{task.id}"),
                InlineKeyboardButton(f"🎯 目标ID: {task.target_chat_id}", callback_data=f"task_edit_target:{task.id}"),
            ],
            [
                InlineKeyboardButton("🔍 过滤设置", callback_data=f"task_filters:{task.id}"),
                InlineKeyboardButton("⬅️ 返回任务详情", callback_data=f"task_view:{task.id}"),
            ],
            [InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")],
        ]
    )


def build_task_settings_text(task: Task) -> str:
    return (
        f"⚙️ 任务设置（ID={task.id}）\n"
        f"模式: {mode_label(task.mode)}\n"
        f"间隔: {task.interval_seconds}秒\n"
        f"日上限: {task.daily_limit}\n"
        f"时段: {task.active_start_time}-{task.active_end_time}\n"
        f"自动监听: {bool_cn(task.auto_capture_enabled)}\n"
        f"发布后删源: {bool_cn(task.delete_after_success)}"
    )


def task_filters_keyboard(task_id: int, task_filter: TaskFilter):
    def on_off(value: bool) -> str:
        return "on" if value else "off"

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"🖼 图片 {on_off(task_filter.require_photo)}", callback_data=f"task_filter_toggle:{task_id}:require_photo"),
                InlineKeyboardButton(f"🎬 视频 {on_off(task_filter.require_video)}", callback_data=f"task_filter_toggle:{task_id}:require_video"),
            ],
            [
                InlineKeyboardButton(f"📝 仅保留纯文字 {on_off(task_filter.require_text)}", callback_data=f"task_filter_toggle:{task_id}:require_text"),
                InlineKeyboardButton(f"🔗 排链 {on_off(task_filter.exclude_links)}", callback_data=f"task_filter_toggle:{task_id}:exclude_links"),
            ],
            [
                InlineKeyboardButton(f"🙈 无字 {on_off(task_filter.exclude_no_text)}", callback_data=f"task_filter_toggle:{task_id}:exclude_no_text"),
                InlineKeyboardButton(f"↪️ 转发 {on_off(task_filter.exclude_forwarded)}", callback_data=f"task_filter_toggle:{task_id}:exclude_forwarded"),
            ],
            [
                InlineKeyboardButton(f"🏷 贴纸 {on_off(task_filter.exclude_sticker)}", callback_data=f"task_filter_toggle:{task_id}:exclude_sticker"),
                InlineKeyboardButton(f"📊 投票 {on_off(task_filter.exclude_poll)}", callback_data=f"task_filter_toggle:{task_id}:exclude_poll"),
            ],
            [
                InlineKeyboardButton("min=10", callback_data=f"task_filter_min:{task_id}:10"),
                InlineKeyboardButton("min=30", callback_data=f"task_filter_min:{task_id}:30"),
                InlineKeyboardButton("min=关闭", callback_data=f"task_filter_min_off:{task_id}"),
            ],
            [
                InlineKeyboardButton("max=100", callback_data=f"task_filter_max:{task_id}:100"),
                InlineKeyboardButton("max=300", callback_data=f"task_filter_max:{task_id}:300"),
                InlineKeyboardButton("max=关闭", callback_data=f"task_filter_max_off:{task_id}"),
            ],
            [InlineKeyboardButton("⬅️ 返回任务设置", callback_data=f"task_settings:{task_id}")],
            [InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")],
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


def extract_links(text: str) -> bool:
    return bool(re.search(r"https?://|t\.me/", text or "", flags=re.IGNORECASE))


def apply_filters(item: QueueItem, task_filter: TaskFilter) -> Optional[str]:
    if item.has_text is None and item.has_photo is None and item.has_video is None and item.has_links is None:
        return None
    text_length = len(item.text_preview or "")
    checks = [
        (task_filter.require_photo and not bool(item.has_photo), "需要图片"),
        (task_filter.require_video and not bool(item.has_video), "需要视频"),
        (task_filter.require_text and not bool(item.has_text), "仅保留纯文字"),
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


def is_retryable_missing_message_error(raw_error: str) -> bool:
    text = (raw_error or "").lower()
    keywords = [
        "message to copy not found",
        "message_id invalid",
        "message not found",
        "wrong message identifier",
    ]
    return any(keyword in text for keyword in keywords)


def mark_item_waiting_or_failed(session, db_task: Task, queue_item: QueueItem, fail_msg: str):
    queue_item.fail_reason = fail_msg
    queue_item.retry_count = (queue_item.retry_count or 0) + 1
    if queue_item.retry_count > WAITING_MAX_RETRY_COUNT:
        queue_item.status = QueueStatusEnum.failed
        queue_item.next_retry_at = None
        write_log(session, db_task.id, queue_item.message_id, None, "fail", fail_msg)
        return
    queue_item.status = QueueStatusEnum.waiting
    queue_item.next_retry_at = now() + timedelta(minutes=WAITING_RETRY_INTERVAL_MINUTES)
    write_log(session, db_task.id, queue_item.message_id, None, "waiting", fail_msg)


def pick_next_publish_item(session, task_id: int) -> Optional[QueueItem]:
    pending_item = session.scalar(
        select(QueueItem).where(QueueItem.task_id == task_id, QueueItem.status == QueueStatusEnum.pending).order_by(QueueItem.message_id.asc())
    )
    if pending_item:
        return pending_item
    return session.scalar(
        select(QueueItem).where(
            QueueItem.task_id == task_id,
            QueueItem.status == QueueStatusEnum.waiting,
            QueueItem.next_retry_at.is_not(None),
            QueueItem.next_retry_at <= now(),
        ).order_by(QueueItem.message_id.asc())
    )


def add_bot_to_chat_keyboard(application: Application) -> Optional[InlineKeyboardMarkup]:
    bot_user = getattr(application.bot, "username", None)
    if not bot_user:
        return None
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ 将机器人添加到群组", url=f"https://t.me/{bot_user}?startgroup=1")],
            [InlineKeyboardButton("📘 添加到频道说明", callback_data="help_menu")],
        ]
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
        if not ignore_interval and db_task.last_published_at:
            elapsed = (now() - db_task.last_published_at).total_seconds()
            if elapsed < db_task.interval_seconds:
                return "未到发布间隔"
        item = pick_next_publish_item(session, db_task.id)
        if not item:
            return "无可发布消息（pending/waiting）"
        logger.info(
            "publish_pick task=%s message_id=%s status=%s media_group_id=%s",
            db_task.id,
            item.message_id,
            item.status.value,
            item.media_group_id,
        )
        task_filter = ensure_task_filter(session, db_task.id)
        try:
            same_album = []
            if item.media_group_id:
                same_album = session.scalars(
                    select(QueueItem).where(
                        QueueItem.task_id == db_task.id,
                        or_(
                            QueueItem.status == QueueStatusEnum.pending,
                            (QueueItem.status == QueueStatusEnum.waiting) & QueueItem.next_retry_at.is_not(None) & (QueueItem.next_retry_at <= now()),
                        ),
                        QueueItem.media_group_id == item.media_group_id,
                    ).order_by(QueueItem.message_id.asc())
                ).all()
                logger.info(
                    "publish_album_candidates task=%s media_group_id=%s count=%s",
                    db_task.id,
                    item.media_group_id,
                    len(same_album),
                )
            else:
                logger.info("publish_single_item task=%s message_id=%s reason=no_media_group_id", db_task.id, item.message_id)
            if same_album:
                # 整组过滤：任意一条不符合，则整组跳过
                group_reason = None
                for album_item in same_album:
                    reason = apply_filters(album_item, task_filter)
                    if reason:
                        group_reason = reason
                        break
                if group_reason:
                    for album_item in same_album:
                        album_item.status = QueueStatusEnum.skipped
                        album_item.fail_reason = None
                        album_item.next_retry_at = None
                        album_item.filter_reason = group_reason
                        write_log(session, db_task.id, album_item.message_id, None, "filter", group_reason)
                    session.commit()
                    return f"已过滤 media_group={item.media_group_id} count={len(same_album)} ({group_reason})"

                message_ids = [x.message_id for x in same_album]
                can_send_media_group = all(
                    x.file_id and x.message_type in {"photo", "video", "document"} for x in same_album
                )
                sent_ids = []
                if can_send_media_group:
                    logger.info(
                        "publish_album_mode task=%s media_group_id=%s path=send_media_group count=%s",
                        db_task.id,
                        item.media_group_id,
                        len(same_album),
                    )
                    caption_text = next((x.caption for x in same_album if x.caption), None)
                    media = []
                    for idx, album_item in enumerate(same_album):
                        caption = caption_text if idx == 0 else None
                        if album_item.message_type == "photo":
                            media.append(InputMediaPhoto(media=album_item.file_id, caption=caption))
                        elif album_item.message_type == "video":
                            media.append(InputMediaVideo(media=album_item.file_id, caption=caption))
                        else:
                            media.append(InputMediaDocument(media=album_item.file_id, caption=caption))
                    sent_messages = await application.bot.send_media_group(
                        chat_id=db_task.target_chat_id,
                        media=media,
                    )
                    sent_ids = [m for m in sent_messages]
                else:
                    missing_details = []
                    for album_item in same_album[:10]:
                        if not album_item.file_id or album_item.message_type not in {"photo", "video", "document"}:
                            missing_details.append(
                                f"{album_item.message_id}:{album_item.message_type or 'unknown'}:{'file_id_missing' if not album_item.file_id else 'ok'}"
                            )
                    logger.info(
                        "publish_album_mode task=%s media_group_id=%s path=fallback reason=cannot_send_media_group details=%s",
                        db_task.id,
                        item.media_group_id,
                        ",".join(missing_details) if missing_details else "unknown",
                    )
                    if db_task.mode == TaskModeEnum.forward:
                        fallback_action = "fallback_forward_messages_due_to_missing_file_id_may_split_album"
                        write_log(session, db_task.id, item.message_id, None, "fallback", fallback_action)
                        if hasattr(application.bot, "forward_messages"):
                            sent_ids = await application.bot.forward_messages(
                                chat_id=db_task.target_chat_id,
                                from_chat_id=db_task.source_chat_id,
                                message_ids=message_ids,
                            )
                        else:
                            sent_ids = []
                            for mid in message_ids:
                                forwarded = await application.bot.forward_message(
                                    chat_id=db_task.target_chat_id,
                                    from_chat_id=db_task.source_chat_id,
                                    message_id=mid,
                                )
                                sent_ids.append(forwarded)
                    else:
                        fallback_action = "fallback_copy_messages_due_to_missing_file_id_may_split_album"
                        write_log(session, db_task.id, item.message_id, None, "fallback", fallback_action)
                        sent_ids = await application.bot.copy_messages(
                            chat_id=db_task.target_chat_id,
                            from_chat_id=db_task.source_chat_id,
                            message_ids=message_ids,
                        )
                published_at = now()
                for i, album_item in enumerate(same_album):
                    target_id = sent_ids[i].message_id if i < len(sent_ids) else None
                    album_item.status = QueueStatusEnum.published
                    album_item.target_message_id = target_id
                    album_item.published_at = published_at
                    album_item.fail_reason = None
                    album_item.next_retry_at = None
                    write_log(session, db_task.id, album_item.message_id, target_id, "publish", "发布成功")
                    if db_task.delete_after_success:
                        try:
                            await application.bot.delete_message(chat_id=db_task.source_chat_id, message_id=album_item.message_id)
                            album_item.deleted_at = now()
                            write_log(session, db_task.id, album_item.message_id, target_id, "delete", "删除源消息成功")
                        except Exception as delete_exc:
                            msg = f"删除失败: {delete_exc}"
                            album_item.fail_reason = msg
                            write_log(session, db_task.id, album_item.message_id, target_id, "fail", msg)
                db_task.last_published_at = published_at
                session.commit()
                return f"发布成功 media_group={item.media_group_id} count={len(same_album)}"
            reason = apply_filters(item, task_filter)
            if reason:
                item.status = QueueStatusEnum.skipped
                item.fail_reason = None
                item.next_retry_at = None
                item.filter_reason = reason
                write_log(session, db_task.id, item.message_id, None, "filter", reason)
                session.commit()
                return f"已过滤 message_id={item.message_id} ({reason})"
            if db_task.mode == TaskModeEnum.copy:
                sent = await application.bot.copy_message(chat_id=db_task.target_chat_id, from_chat_id=db_task.source_chat_id, message_id=item.message_id)
                target_id = sent.message_id if sent else None
            else:
                sent = await application.bot.forward_message(chat_id=db_task.target_chat_id, from_chat_id=db_task.source_chat_id, message_id=item.message_id)
                target_id = sent.message_id if sent else None
            item.status = QueueStatusEnum.published
            item.target_message_id = target_id
            item.published_at = now()
            item.fail_reason = None
            item.next_retry_at = None
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
            raw_err = str(exc)
            fail_msg = raw_err
            if "can't be copied" in raw_err.lower():
                fail_msg = (
                    f"{raw_err}（该错误通常不是时段导致；可能是源消息受保护/复制受限，"
                    "可改用 forward 模式或检查源频道权限）"
                )
            should_waiting = is_retryable_missing_message_error(raw_err)
            if same_album:
                for album_item in same_album:
                    if should_waiting:
                        mark_item_waiting_or_failed(session, db_task, album_item, fail_msg)
                    else:
                        album_item.status = QueueStatusEnum.failed
                        album_item.fail_reason = fail_msg
                        album_item.next_retry_at = None
                        write_log(session, db_task.id, album_item.message_id, None, "fail", fail_msg)
            else:
                if should_waiting:
                    mark_item_waiting_or_failed(session, db_task, item, fail_msg)
                else:
                    item.status = QueueStatusEnum.failed
                    item.fail_reason = fail_msg
                    item.next_retry_at = None
                    write_log(session, db_task.id, item.message_id, None, "fail", fail_msg)
            session.commit()
            if should_waiting:
                if same_album:
                    if all(album_item.status == QueueStatusEnum.failed for album_item in same_album):
                        return f"发布失败（重试超限） message_id={item.message_id}: {fail_msg}"
                    return f"等待重试 message_id={item.message_id}: {fail_msg}"
                if item.status == QueueStatusEnum.failed:
                    return f"发布失败（重试超限） message_id={item.message_id}: {fail_msg}"
                return f"等待重试 message_id={item.message_id}: {fail_msg}"
            return f"发布失败 message_id={item.message_id}: {fail_msg}"


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
            [InlineKeyboardButton("📢 官方频道", url="https://t.me/sosoFlow"), InlineKeyboardButton("❓ 帮助", callback_data="help_menu")],
        ]
    )


def main_menu_text() -> str:
    return (
        "欢迎使用 sosoFlow 🚚\n\n"
        "常用功能：\n"
        "• 任务列表：查看并进入任务详情\n"
        "• 新建任务：按提示输入来源ID和目标ID（也可直接转发自动识别）\n"
        "• 获取频道/群ID：转发消息给我自动识别"
    )


def quick_panel_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📋 任务列表"), KeyboardButton("➕ 新建任务")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def simple_back_home_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 主菜单", callback_data="menu_home"), InlineKeyboardButton("📋 任务列表", callback_data="tasks_list")]]
    )


async def edit_query_message_text_or_caption(query, text: str, reply_markup=None):
    message = query.message
    if not message:
        return
    try:
        if getattr(message, "photo", None) or getattr(message, "video", None) or getattr(message, "document", None):
            await query.edit_message_caption(caption=text, reply_markup=reply_markup)
            return
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception:
        await message.reply_text(text, reply_markup=reply_markup)


def build_tasks_list_keyboard(tasks: list[Task], page: int):
    total = len(tasks)
    max_page = max((total - 1) // TASKS_PAGE_SIZE, 0)
    page = max(0, min(page, max_page))
    start = page * TASKS_PAGE_SIZE
    end = start + TASKS_PAGE_SIZE
    page_items = tasks[start:end]
    rows = [[InlineKeyboardButton(f"{'🟢' if t.enabled else '⏸'} {t.id} {t.name}", callback_data=f"task_view:{t.id}")] for t in page_items]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"tasks_page:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{max_page + 1}", callback_data="noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"tasks_page:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🔎 搜索任务", callback_data="task_search_hint"), InlineKeyboardButton("🏠 主菜单", callback_data="menu_home")])
    return InlineKeyboardMarkup(rows)


@require_admin
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        if os.path.exists(COVER_IMAGE_PATH):
            try:
                with open(COVER_IMAGE_PATH, "rb") as fp:
                    await update.message.reply_photo(photo=fp, caption=main_menu_text(), reply_markup=main_menu_keyboard())
            except Exception:
                await update.message.reply_text(main_menu_text(), reply_markup=main_menu_keyboard())
        else:
            await update.message.reply_text(main_menu_text(), reply_markup=main_menu_keyboard())
        await update.message.reply_text("ℹ️ 底部快捷面板已启用", reply_markup=quick_panel_keyboard())


@require_admin
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📘 sosoFlow 帮助\n\n"
        "【通用】(admin/super)\n"
        "/start 主菜单（带封面）\n"
        "/help 查看本帮助\n"
        "/status 系统状态\n\n"
        "【任务】(admin/super)\n"
        "/add_task <name> <source_chat_id> <target_chat_id> 新建任务（命令方式）\n"
        "/tasks 查看任务列表\n"
        "/use_task <task_id> 选择当前任务\n"
        "/task_status 当前任务详情\n"
        "/delete_task <task_id> 删除(二次确认)\n\n"
        "【队列】(admin/super)\n"
        f"/import_range <start_message_id> <end_message_id> (单次最多 {MAX_IMPORT_RANGE})\n"
        "/publish_now 立即发布下一条 pending（忽略时间窗与间隔）\n"
        "/skip <message_id> 跳过当前任务队列消息\n"
        "/retry_failed 重试 failed 消息\n\n"
        "/retry_waiting 重试 waiting 消息\n\n"
        "【设置】(admin/super)\n"
        "/set_interval <seconds> (必须 > 0)\n"
        "/set_daily_limit <count> (count>=0)\n"
        "/set_time_window <HH:MM> <HH:MM>\n"
        "/set_mode copy|forward\n"
        "/set_delete_after_success on|off\n"
        "/set_auto_capture on|off\n"
        "/set_tick <seconds> (仅 super, 10-3600)\n"
        "/restart (仅 super，触发重启流程)\n\n"
        "【过滤】(admin/super)\n"
        "/filters 查看过滤\n"
        "/set_filter <bool_key> on|off\n"
        "/set_filter min_text_length <number>\n"
        "/set_filter max_text_length <number>\n\n"
        "【管理员】\n"
        "/admins 查看管理员(admin/super)\n"
        "/add_admin <telegram_user_id> (仅 super)\n"
        "/remove_admin <telegram_user_id> (仅 super)\n\n"
        "【便捷功能】\n"
        "按钮“新建任务”：分步输入 source_chat_id -> target_chat_id\n"
        "转发任意频道/群消息给机器人：自动回显频道/群ID\n\n"
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
        queue_waiting = session.scalar(select(func.count()).select_from(QueueItem).where(QueueItem.status == QueueStatusEnum.waiting)) or 0
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
        f"waiting: {queue_waiting}\n"
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
    await update.message.reply_text("📋 任务列表", reply_markup=build_tasks_list_keyboard(tasks, page=0))


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
        source_name = await resolve_chat_display_name(context.application, db_task.source_chat_id)
        target_name = await resolve_chat_display_name(context.application, db_task.target_chat_id)
        await update.message.reply_text(build_task_detail_text(session, db_task, source_name=source_name, target_name=target_name))


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
    await update.message.reply_text(
        f"✅ 导入完成\n"
        f"入队新增: {inserted}（仅按ID范围入队，未校验消息是否实际存在）\n"
        f"重复跳过: {duplicated}"
    )


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
        rows = session.scalars(
            select(QueueItem).where(
                QueueItem.task_id == task.id,
                QueueItem.status.in_([QueueStatusEnum.failed, QueueStatusEnum.waiting]),
            )
        ).all()
        for row in rows:
            row.status = QueueStatusEnum.pending
            row.fail_reason = None
            row.next_retry_at = None
        session.commit()
    await update.message.reply_text(f"✅ 已重置 failed/waiting -> pending: {len(rows)}")


@require_admin
async def retry_waiting_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    with SessionLocal() as session:
        rows = session.scalars(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.status == QueueStatusEnum.waiting)).all()
        for row in rows:
            row.status = QueueStatusEnum.pending
            row.fail_reason = None
            row.next_retry_at = None
        session.commit()
    await update.message.reply_text(f"✅ 已重置 waiting -> pending: {len(rows)}")


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


@require_super
async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    strategy = env.restart_strategy
    if strategy not in {"guide", "exit", "exec"}:
        strategy = "guide"
    await update.message.reply_text(
        f"♻️ 收到重启请求\n策略: {strategy}\n"
        "说明：重启机器人就是重启当前进程。"
    )
    if strategy == "guide":
        await update.message.reply_text(
            "当前为引导模式，不自动停进程。\n"
            "请在部署平台执行 Restart/重启实例；重启成功后会自动收到启动通知。"
        )
        return
    if strategy == "exit":
        await update.message.reply_text("即将退出当前进程，请确认平台已配置自动拉起。")
        asyncio.create_task(_delayed_restart_or_exit(exec_mode=False))
        return
    await update.message.reply_text("即将执行进程内重启（exec）。")
    asyncio.create_task(_delayed_restart_or_exit(exec_mode=True))


async def _delayed_restart_or_exit(exec_mode: bool):
    await asyncio.sleep(1.0)
    if exec_mode:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    raise SystemExit(0)


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
            await query.message.reply_text("ℹ️ 暂无任务")
            return
        await edit_query_message_text_or_caption(query, "📋 任务列表", reply_markup=build_tasks_list_keyboard(tasks, page=0))
        return
    if data == "task_search_hint":
        context.user_data["pending_input_action"] = "search_task"
        await query.message.reply_text("✍️ 请输入任务关键词（任务名或任务ID）")
        return
    if data == "menu_home":
        await edit_query_message_text_or_caption(query, main_menu_text(), reply_markup=main_menu_keyboard())
        return
    if data == "noop":
        return
    if data.startswith("tasks_page:"):
        try:
            page = parse_int(data.split(":")[1], "page")
        except ValueError:
            await query.answer("页码错误", show_alert=True)
            return
        with SessionLocal() as session:
            tasks = session.scalars(select(Task).order_by(Task.id.asc())).all()
        if not tasks:
            await edit_query_message_text_or_caption(query, "暂无任务")
            return
        await edit_query_message_text_or_caption(query, "📋 任务列表", reply_markup=build_tasks_list_keyboard(tasks, page=page))
        return
    if data == "create_task_hint":
        context.user_data["pending_input_action"] = "create_task_source"
        await query.message.reply_text(
            "✍️ 请输入来源频道/群组ID\n示例：-1001111111111\n💡 可转发来源频道/群消息给机器人，点击识别出的数字复制后发送确认。",
        )
        return
    if data == "global_status":
        with SessionLocal() as session:
            task_count = session.scalar(select(func.count()).select_from(Task)) or 0
        await query.message.reply_text(f"📊 当前任务总数: {task_count}")
        return
    if data == "admins_list":
        with SessionLocal() as session:
            rows = session.scalars(select(Admin)).all()
        await query.message.reply_text(
            "👤 管理员\n" + "\n".join([f"{x.telegram_user_id} ({x.role.value})" for x in rows]),
        )
        return
    if data == "help_menu":
        await query.message.reply_text(
            "💡 可直接用按钮完成主要操作：\n"
            "1) 主菜单选任务列表/新建任务\n"
            "2) 按页面提示直接输入参数文本\n"
            "3) 各层级都可用“返回/主菜单”按钮返回",
        )
        return
    if ":" not in data:
        return
    parts = data.split(":")
    action = parts[0]
    raw_task_id = parts[1] if len(parts) > 1 else ""
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
            source_name = await resolve_chat_display_name(context.application, task.source_chat_id)
            target_name = await resolve_chat_display_name(context.application, task.target_chat_id)
            await query.edit_message_text(
                build_task_detail_text(session, task, source_name=source_name, target_name=target_name),
                reply_markup=task_detail_keyboard(task_id),
            )
            return
        if action == "task_start":
            task.enabled = True
            session.commit()
            source_name = await resolve_chat_display_name(context.application, task.source_chat_id)
            target_name = await resolve_chat_display_name(context.application, task.target_chat_id)
            await query.edit_message_text(
                build_task_detail_text(session, task, source_name=source_name, target_name=target_name),
                reply_markup=task_detail_keyboard(task_id),
            )
            await query.message.reply_text("✅ 已启动任务")
            return
        elif action == "task_pause":
            task.enabled = False
            session.commit()
            source_name = await resolve_chat_display_name(context.application, task.source_chat_id)
            target_name = await resolve_chat_display_name(context.application, task.target_chat_id)
            await query.edit_message_text(
                build_task_detail_text(session, task, source_name=source_name, target_name=target_name),
                reply_markup=task_detail_keyboard(task_id),
            )
            await query.message.reply_text("✅ 已暂停任务")
            return
        elif action == "task_publish":
            result = await publish_one(context.application, task, ignore_interval=True, ignore_window=True)
            await query.message.reply_text(f"🚀 {result}")
            if "Forbidden: bot is not a member of the channel chat" in result:
                kb = add_bot_to_chat_keyboard(context.application)
                if kb:
                    await query.message.reply_text("⚠️ 机器人不在目标频道/群组，请先添加机器人后重试。", reply_markup=kb)
            return
        elif action == "task_retry":
            rows = session.scalars(
                select(QueueItem).where(
                    QueueItem.task_id == task.id,
                    QueueItem.status.in_([QueueStatusEnum.failed, QueueStatusEnum.waiting]),
                )
            ).all()
            for row in rows:
                row.status = QueueStatusEnum.pending
                row.fail_reason = None
                row.next_retry_at = None
            session.commit()
            await query.message.reply_text("✅ 已将 failed/waiting 队列重置为待发布")
            return
        elif action == "task_import_hint":
            context.user_data["pending_input_action"] = "import_range"
            context.user_data["pending_task_id"] = task.id
            await query.message.reply_text(
                f"请发送导入开始与结束帖子ID（示例：100 120 ，两个ID之间空格，单次最多 {MAX_IMPORT_RANGE}，如：100 5100）",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("⬅️ 返回任务详情", callback_data=f"task_view:{task.id}")],
                        [InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")],
                    ]
                ),
            )
            return
        elif action == "task_settings":
            await query.edit_message_text(
                build_task_settings_text(task),
                reply_markup=task_settings_keyboard(task),
            )
            return
        elif action == "task_toggle_mode":
            task.mode = TaskModeEnum.forward if task.mode == TaskModeEnum.copy else TaskModeEnum.copy
            session.commit()
            await query.edit_message_text(build_task_settings_text(task), reply_markup=task_settings_keyboard(task))
            await query.message.reply_text(f"✅ 模式已切换为 {mode_label(task.mode)}")
            return
        elif action == "task_toggle_auto_capture":
            task.auto_capture_enabled = not task.auto_capture_enabled
            session.commit()
            await query.edit_message_text(build_task_settings_text(task), reply_markup=task_settings_keyboard(task))
            await query.message.reply_text(f"✅ 自动监听已设为 {bool_cn(task.auto_capture_enabled)}")
            return
        elif action == "task_toggle_delete":
            task.delete_after_success = not task.delete_after_success
            session.commit()
            await query.edit_message_text(build_task_settings_text(task), reply_markup=task_settings_keyboard(task))
            await query.message.reply_text(f"✅ 发布后删源已设为 {bool_cn(task.delete_after_success)}")
            return
        elif action == "task_input_interval":
            context.user_data["pending_input_action"] = "set_interval_custom"
            context.user_data["pending_task_id"] = task.id
            await query.message.reply_text("✍️ 请输入间隔秒数（整数，>0）\n示例：1800")
            return
        elif action == "task_input_daily":
            context.user_data["pending_input_action"] = "set_daily_custom"
            context.user_data["pending_task_id"] = task.id
            await query.message.reply_text("✍️ 请输入日上限（整数，>=0）\n示例：100")
            return
        elif action == "task_input_window":
            context.user_data["pending_input_action"] = "set_window_start"
            context.user_data["pending_task_id"] = task.id
            context.user_data.pop("pending_window_start", None)
            await query.message.reply_text("✍️ 请输入开始时间（HH:MM）\n示例：09:00")
            return
        elif action == "task_edit_source":
            context.user_data["pending_input_action"] = "edit_task_source"
            context.user_data["pending_task_id"] = task.id
            await query.message.reply_text(
                "✍️ 请输入新的来源频道/群组ID\n示例：-1001111111111\n💡 可转发来源频道/群消息给机器人，点击识别出的数字复制后发送确认。"
            )
            return
        elif action == "task_edit_target":
            context.user_data["pending_input_action"] = "edit_task_target"
            context.user_data["pending_task_id"] = task.id
            await query.message.reply_text(
                "✍️ 请输入新的目标频道/群组ID\n示例：-1002222222222\n💡 可转发目标频道/群消息给机器人，点击识别出的数字复制后发送确认。"
            )
            return
        elif action == "task_filters":
            task_filter = ensure_task_filter(session, task.id)
            await query.edit_message_text(
                f"🔍 过滤设置（任务 {task.id}）\n{filter_summary(task_filter)}",
                reply_markup=task_filters_keyboard(task.id, task_filter),
            )
            return
        elif action == "task_filter_toggle":
            if len(parts) != 3:
                await query.answer("参数错误", show_alert=True)
                return
            key = parts[2]
            allowed = {
                "require_photo",
                "require_video",
                "require_text",
                "exclude_links",
                "exclude_no_text",
                "exclude_forwarded",
                "exclude_sticker",
                "exclude_poll",
            }
            if key not in allowed:
                await query.answer("未知过滤键", show_alert=True)
                return
            task_filter = ensure_task_filter(session, task.id)
            setattr(task_filter, key, not bool(getattr(task_filter, key)))
            session.commit()
            await query.edit_message_reply_markup(reply_markup=task_filters_keyboard(task.id, task_filter))
            await query.message.reply_text(f"✅ 已更新过滤项：{key}")
            return
        elif action == "task_filter_min":
            if len(parts) != 3:
                await query.answer("参数错误", show_alert=True)
                return
            task_filter = ensure_task_filter(session, task.id)
            task_filter.min_text_length = parse_int(parts[2], "min_text_length")
            session.commit()
            await query.message.reply_text(f"✅ 最短字数已设为 {task_filter.min_text_length}")
            return
        elif action == "task_filter_max":
            if len(parts) != 3:
                await query.answer("参数错误", show_alert=True)
                return
            task_filter = ensure_task_filter(session, task.id)
            task_filter.max_text_length = parse_int(parts[2], "max_text_length")
            session.commit()
            await query.message.reply_text(f"✅ 最长字数已设为 {task_filter.max_text_length}")
            return
        elif action == "task_filter_min_off":
            task_filter = ensure_task_filter(session, task.id)
            task_filter.min_text_length = None
            session.commit()
            await query.message.reply_text("✅ 最短字数限制已关闭")
            return
        elif action == "task_filter_max_off":
            task_filter = ensure_task_filter(session, task.id)
            task_filter.max_text_length = None
            session.commit()
            await query.message.reply_text("✅ 最长字数限制已关闭")
            return
        elif action == "task_delete_ask":
            await query.message.reply_text(f"⚠️ 二次确认删除任务 {task.id}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("确认删除", callback_data=f"task_delete_yes:{task.id}")]]))
            return
        elif action == "task_delete_yes":
            session.delete(task)
            session.commit()
            tasks = session.scalars(select(Task).order_by(Task.id.asc())).all()
            if not tasks:
                await query.edit_message_text("🗑 已删除任务，当前暂无任务", reply_markup=simple_back_home_keyboard())
            else:
                await query.edit_message_text("🗑 已删除任务，返回任务列表", reply_markup=build_tasks_list_keyboard(tasks, page=0))
            return
        await query.message.reply_text("✅ 操作完成。")


async def handle_pending_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.effective_message
    if not msg:
        return False
    if msg.chat and msg.chat.type != "private":
        return False
    action = context.user_data.get("pending_input_action")
    if not action:
        return False
    if msg.text:
        quick_text = msg.text.strip()
        if quick_text in {"📋 任务列表", "➕ 新建任务"}:
            return False
    # 允许“转发自动识别ID”优先于文本解析，避免转发消息在等待输入时被误判为参数错误
    if action in {"create_task_source", "create_task_target", "edit_task_source", "edit_task_target"}:
        if extract_forward_chat_id(msg) is not None:
            return False
    if not msg.text:
        return False
    text = msg.text.strip()
    if action == "create_task_source":
        try:
            source_chat_id = parse_int(text, "source_chat_id")
        except ValueError as exc:
            await msg.reply_text(f"⚠️ 参数错误：{exc}\n请重新输入来源频道/群组ID")
            return True
        context.user_data["pending_task_source_chat_id"] = source_chat_id
        context.user_data["pending_input_action"] = "create_task_target"
        await msg.reply_text("✍️ 请输入目标频道/群组ID\n示例：-1002222222222\n💡 可转发目标频道/群消息给机器人，点击识别出的数字复制后发送确认。")
        return True
    if action == "create_task_target":
        source_chat_id = context.user_data.get("pending_task_source_chat_id")
        if source_chat_id is None:
            context.user_data.pop("pending_input_action", None)
            await msg.reply_text("⚠️ 创建流程已失效，请重新点击“➕ 新建任务”。")
            return True
        try:
            target_chat_id = parse_int(text, "target_chat_id")
        except ValueError as exc:
            await msg.reply_text(f"⚠️ 参数错误：{exc}\n请重新输入目标频道/群组ID")
            return True
        task_name = f"task_{abs(source_chat_id)}_{abs(target_chat_id)}"
        with SessionLocal() as session:
            task = Task(name=task_name, source_chat_id=source_chat_id, target_chat_id=target_chat_id)
            session.add(task)
            session.commit()
            session.refresh(task)
            session.add(TaskFilter(task_id=task.id))
            state = session.scalar(select(UserState).where(UserState.user_id == update.effective_user.id))
            if not state:
                state = UserState(user_id=update.effective_user.id, current_task_id=task.id)
                session.add(state)
            else:
                state.current_task_id = task.id
            session.commit()
            kb = task_detail_keyboard(task.id)
            await msg.reply_text(
                f"✅ 任务创建成功并已选中\nID={task.id}\n名称: {task_name}\n源: {source_chat_id}\n目标: {target_chat_id}",
                reply_markup=kb,
            )
        context.user_data.pop("pending_input_action", None)
        context.user_data.pop("pending_task_source_chat_id", None)
        return True
    if action == "search_task":
        keyword = text.strip()
        with SessionLocal() as session:
            tasks = session.scalars(select(Task).order_by(Task.id.asc())).all()
        if not tasks:
            await msg.reply_text("暂无任务")
            context.user_data.pop("pending_input_action", None)
            return True
        matched = []
        for task in tasks:
            if keyword.isdigit() and task.id == int(keyword):
                matched.append(task)
                continue
            if keyword.lower() in task.name.lower():
                matched.append(task)
        if not matched:
            await msg.reply_text("未找到匹配任务，请重试关键词")
            return True
        kb = [[InlineKeyboardButton(f"{'🟢' if t.enabled else '⏸'} {t.id} {t.name}", callback_data=f"task_view:{t.id}")] for t in matched[:20]]
        await msg.reply_text(f"🔎 搜索结果：{len(matched)} 条", reply_markup=InlineKeyboardMarkup(kb))
        context.user_data.pop("pending_input_action", None)
        return True
    if action == "import_range":
        task_id = context.user_data.get("pending_task_id")
        if not task_id:
            await msg.reply_text("未找到目标任务，请重新在任务详情点击“📥 导入范围”")
            context.user_data.pop("pending_input_action", None)
            return True
        parts = text.split()
        if len(parts) != 2:
            await msg.reply_text("格式错误，请输入：<start_message_id> <end_message_id>")
            return True
        try:
            start_id = parse_int(parts[0], "start_message_id")
            end_id = parse_int(parts[1], "end_message_id")
        except ValueError as exc:
            await msg.reply_text(f"参数错误：{exc}")
            return True
        if start_id > end_id:
            await msg.reply_text("start_message_id 必须 <= end_message_id")
            return True
        if end_id - start_id + 1 > MAX_IMPORT_RANGE:
            await msg.reply_text(f"单次导入最多 {MAX_IMPORT_RANGE} 条，请缩小范围")
            return True
        inserted = 0
        duplicated = 0
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if not task:
                await msg.reply_text("任务不存在，请重新选择任务")
                context.user_data.pop("pending_input_action", None)
                context.user_data.pop("pending_task_id", None)
                return True
            for mid in range(start_id, end_id + 1):
                exists = session.scalar(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.message_id == mid))
                if exists:
                    duplicated += 1
                    continue
                session.add(QueueItem(task_id=task.id, message_id=mid, message_type="unknown"))
                inserted += 1
            session.commit()
        await msg.reply_text(
            f"✅ 导入完成\n"
            f"入队新增: {inserted}（仅按ID范围入队，未校验消息是否实际存在）\n"
            f"重复跳过: {duplicated}"
        )
        context.user_data.pop("pending_input_action", None)
        context.user_data.pop("pending_task_id", None)
        return True
    if action == "set_interval_custom":
        task_id = context.user_data.get("pending_task_id")
        if not task_id:
            await msg.reply_text("未找到任务，请重新进入任务设置")
            context.user_data.pop("pending_input_action", None)
            return True
        try:
            seconds = parse_int(text, "interval_seconds")
        except ValueError as exc:
            await msg.reply_text(f"参数错误：{exc}")
            return True
        if seconds <= 0:
            await msg.reply_text("interval_seconds 必须 > 0")
            return True
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if not task:
                await msg.reply_text("任务不存在")
                context.user_data.pop("pending_input_action", None)
                context.user_data.pop("pending_task_id", None)
                return True
            task.interval_seconds = seconds
            session.commit()
            session.refresh(task)
            await msg.reply_text(
                f"✅ 间隔已更新为 {seconds} 秒",
                reply_markup=task_settings_keyboard(task),
            )
        context.user_data.pop("pending_input_action", None)
        context.user_data.pop("pending_task_id", None)
        return True
    if action == "set_daily_custom":
        task_id = context.user_data.get("pending_task_id")
        if not task_id:
            await msg.reply_text("未找到任务，请重新进入任务设置")
            context.user_data.pop("pending_input_action", None)
            return True
        try:
            daily_limit = parse_int(text, "daily_limit")
        except ValueError as exc:
            await msg.reply_text(f"参数错误：{exc}")
            return True
        if daily_limit < 0:
            await msg.reply_text("daily_limit 必须 >= 0")
            return True
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if not task:
                await msg.reply_text("任务不存在")
                context.user_data.pop("pending_input_action", None)
                context.user_data.pop("pending_task_id", None)
                return True
            task.daily_limit = daily_limit
            session.commit()
            session.refresh(task)
            await msg.reply_text(
                f"✅ 日上限已更新为 {daily_limit}",
                reply_markup=task_settings_keyboard(task),
            )
        context.user_data.pop("pending_input_action", None)
        context.user_data.pop("pending_task_id", None)
        return True
    if action == "set_window_start":
        task_id = context.user_data.get("pending_task_id")
        if not task_id:
            await msg.reply_text("未找到任务，请重新进入任务设置")
            context.user_data.pop("pending_input_action", None)
            return True
        try:
            parse_hhmm(text)
        except ValueError as exc:
            await msg.reply_text(f"参数错误：{exc}")
            return True
        context.user_data["pending_window_start"] = text
        context.user_data["pending_input_action"] = "set_window_end"
        await msg.reply_text("✍️ 请输入结束时间（HH:MM）\n示例：23:30")
        return True
    if action == "set_window_end":
        task_id = context.user_data.get("pending_task_id")
        start_time = context.user_data.get("pending_window_start")
        if not task_id or not start_time:
            await msg.reply_text("未找到任务或开始时间，请重新进入任务设置")
            context.user_data.pop("pending_input_action", None)
            context.user_data.pop("pending_task_id", None)
            context.user_data.pop("pending_window_start", None)
            return True
        end_time = text
        try:
            parse_hhmm(start_time)
            parse_hhmm(end_time)
        except ValueError as exc:
            await msg.reply_text(f"参数错误：{exc}")
            return True
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if not task:
                await msg.reply_text("任务不存在")
                context.user_data.pop("pending_input_action", None)
                context.user_data.pop("pending_task_id", None)
                context.user_data.pop("pending_window_start", None)
                return True
            task.active_start_time = start_time
            task.active_end_time = end_time
            session.commit()
            session.refresh(task)
            await msg.reply_text(
                f"✅ 时段已更新为 {start_time}-{end_time}",
                reply_markup=task_settings_keyboard(task),
            )
        context.user_data.pop("pending_input_action", None)
        context.user_data.pop("pending_task_id", None)
        context.user_data.pop("pending_window_start", None)
        return True
    if action == "edit_task_source":
        task_id = context.user_data.get("pending_task_id")
        if not task_id:
            await msg.reply_text("未找到任务，请重新进入任务设置")
            context.user_data.pop("pending_input_action", None)
            return True
        try:
            source_chat_id = parse_int(text, "source_chat_id")
        except ValueError as exc:
            await msg.reply_text(f"⚠️ 参数错误：{exc}\n请重新输入来源频道/群组ID")
            return True
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if not task:
                await msg.reply_text("任务不存在")
                context.user_data.pop("pending_input_action", None)
                context.user_data.pop("pending_task_id", None)
                return True
            task.source_chat_id = source_chat_id
            session.commit()
            session.refresh(task)
            await msg.reply_text(
                f"✅ 来源ID已更新为 {source_chat_id}",
                reply_markup=task_settings_keyboard(task),
            )
        context.user_data.pop("pending_input_action", None)
        context.user_data.pop("pending_task_id", None)
        return True
    if action == "edit_task_target":
        task_id = context.user_data.get("pending_task_id")
        if not task_id:
            await msg.reply_text("未找到任务，请重新进入任务设置")
            context.user_data.pop("pending_input_action", None)
            return True
        try:
            target_chat_id = parse_int(text, "target_chat_id")
        except ValueError as exc:
            await msg.reply_text(f"⚠️ 参数错误：{exc}\n请重新输入目标频道/群组ID")
            return True
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if not task:
                await msg.reply_text("任务不存在")
                context.user_data.pop("pending_input_action", None)
                context.user_data.pop("pending_task_id", None)
                return True
            task.target_chat_id = target_chat_id
            session.commit()
            session.refresh(task)
            await msg.reply_text(
                f"✅ 目标ID已更新为 {target_chat_id}",
                reply_markup=task_settings_keyboard(task),
            )
        context.user_data.pop("pending_input_action", None)
        context.user_data.pop("pending_task_id", None)
        return True
    return False


@require_admin
async def capture_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.chat:
        return
    if msg.chat.type == "private" and msg.text:
        text = msg.text.strip()
        if text == "📋 任务列表":
            context.user_data.pop("pending_input_action", None)
            context.user_data.pop("pending_task_id", None)
            context.user_data.pop("pending_task_source_chat_id", None)
            context.user_data.pop("pending_window_start", None)
            with SessionLocal() as session:
                tasks = session.scalars(select(Task).order_by(Task.id.asc())).all()
            if not tasks:
                await msg.reply_text("ℹ️ 暂无任务")
            else:
                await msg.reply_text("📋 任务列表", reply_markup=build_tasks_list_keyboard(tasks, page=0))
            return
        if text == "➕ 新建任务":
            context.user_data.pop("pending_task_id", None)
            context.user_data.pop("pending_task_source_chat_id", None)
            context.user_data.pop("pending_window_start", None)
            context.user_data["pending_input_action"] = "create_task_source"
            await msg.reply_text("✍️ 请输入来源频道/群组ID\n示例：-1001111111111\n💡 可转发来源频道/群消息给机器人，点击识别出的数字复制后发送确认。")
            return
    handled = await handle_pending_input(update, context)
    if handled:
        return
    forward_chat_id = extract_forward_chat_id(msg)
    if forward_chat_id is not None:
        if context.user_data.get("pending_input_action") == "create_task_source":
            context.user_data["pending_task_source_chat_id"] = forward_chat_id
            context.user_data["pending_input_action"] = "create_task_target"
            await msg.reply_text(
                f"✅ 已自动确认来源ID：{forward_chat_id}\n"
                "✍️ 请输入目标频道/群组ID\n示例：-1002222222222\n"
                "💡 可转发目标频道/群消息给机器人，点击识别出的数字复制后发送确认。"
            )
            return
        if context.user_data.get("pending_input_action") == "create_task_target":
            source_chat_id = context.user_data.get("pending_task_source_chat_id")
            if source_chat_id is None:
                context.user_data.pop("pending_input_action", None)
                await msg.reply_text("⚠️ 创建流程已失效，请重新点击“➕ 新建任务”。")
                return
            target_chat_id = forward_chat_id
            task_name = f"task_{abs(source_chat_id)}_{abs(target_chat_id)}"
            with SessionLocal() as session:
                task = Task(name=task_name, source_chat_id=source_chat_id, target_chat_id=target_chat_id)
                session.add(task)
                session.commit()
                session.refresh(task)
                session.add(TaskFilter(task_id=task.id))
                state = session.scalar(select(UserState).where(UserState.user_id == update.effective_user.id))
                if not state:
                    state = UserState(user_id=update.effective_user.id, current_task_id=task.id)
                    session.add(state)
                else:
                    state.current_task_id = task.id
                session.commit()
            await msg.reply_text(
                f"✅ 已自动确认目标ID并创建任务\nID={task.id}\n名称: {task_name}\n源: {source_chat_id}\n目标: {target_chat_id}",
                reply_markup=task_detail_keyboard(task.id),
            )
            context.user_data.pop("pending_input_action", None)
            context.user_data.pop("pending_task_source_chat_id", None)
            return
        if context.user_data.get("pending_input_action") == "edit_task_source":
            task_id = context.user_data.get("pending_task_id")
            if not task_id:
                context.user_data.pop("pending_input_action", None)
                await msg.reply_text("未找到任务，请重新进入任务设置")
                return
            with SessionLocal() as session:
                task = session.get(Task, task_id)
                if not task:
                    await msg.reply_text("任务不存在")
                    context.user_data.pop("pending_input_action", None)
                    context.user_data.pop("pending_task_id", None)
                    return
                task.source_chat_id = forward_chat_id
                session.commit()
                session.refresh(task)
                await msg.reply_text(
                    f"✅ 已自动确认并更新来源ID：{forward_chat_id}",
                    reply_markup=task_settings_keyboard(task),
                )
            context.user_data.pop("pending_input_action", None)
            context.user_data.pop("pending_task_id", None)
            return
        if context.user_data.get("pending_input_action") == "edit_task_target":
            task_id = context.user_data.get("pending_task_id")
            if not task_id:
                context.user_data.pop("pending_input_action", None)
                await msg.reply_text("未找到任务，请重新进入任务设置")
                return
            with SessionLocal() as session:
                task = session.get(Task, task_id)
                if not task:
                    await msg.reply_text("任务不存在")
                    context.user_data.pop("pending_input_action", None)
                    context.user_data.pop("pending_task_id", None)
                    return
                task.target_chat_id = forward_chat_id
                session.commit()
                session.refresh(task)
                await msg.reply_text(
                    f"✅ 已自动确认并更新目标ID：{forward_chat_id}",
                    reply_markup=task_settings_keyboard(task),
                )
            context.user_data.pop("pending_input_action", None)
            context.user_data.pop("pending_task_id", None)
            return
        await msg.reply_text(
            f"📌 已识别转发来源ID：`{forward_chat_id}`\n"
            "可直接用于新建任务的来源或目标ID。\n"
            "💡 点击数字可复制，然后发送给我确认；也可以在输入步骤直接转发，我会自动确认。",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    source_chat_id = msg.chat_id
    text_value = msg.text or msg.caption or ""
    message_type = "text"
    file_id = None
    if msg.photo:
        message_type = "photo"
        file_id = msg.photo[-1].file_id if msg.photo else None
    elif msg.video:
        message_type = "video"
        file_id = msg.video.file_id
    elif msg.document:
        message_type = "document"
        file_id = msg.document.file_id
    elif msg.sticker:
        message_type = "sticker"
    elif msg.poll:
        message_type = "poll"
    with SessionLocal() as session:
        tasks = session.scalars(select(Task).where(Task.source_chat_id == source_chat_id, Task.auto_capture_enabled.is_(True))).all()
        for task in tasks:
            exists = session.scalar(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.message_id == msg.message_id))
            if exists:
                if exists.status in {QueueStatusEnum.waiting, QueueStatusEnum.failed}:
                    exists.status = QueueStatusEnum.pending
                    exists.fail_reason = None
                    exists.next_retry_at = None
                    exists.message_type = message_type
                    exists.file_id = file_id
                    exists.caption = msg.caption
                    exists.text_preview = text_value[:280] if text_value else None
                    exists.has_text = bool(text_value)
                    exists.has_photo = bool(msg.photo)
                    exists.has_video = bool(msg.video)
                    exists.has_links = extract_links(text_value)
                    exists.is_forwarded = bool(msg.forward_origin)
                    exists.media_group_id = msg.media_group_id
                continue
            session.add(
                QueueItem(
                    task_id=task.id,
                    message_id=msg.message_id,
                    status=QueueStatusEnum.pending,
                    message_type=message_type,
                    file_id=file_id,
                    caption=msg.caption,
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


def build_startup_notify_text() -> str:
    return (
        "✅ sosoFlow 启动成功\n"
        f"时间: {now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"版本: {env.deploy_version}\n"
        f"TZ: {env.tz}"
    )


async def notify_startup(application: Application):
    targets = set(env.startup_notify_chat_ids)
    if not targets:
        targets.update(env.super_admin_ids)
    if not targets:
        logger.info("startup notify skipped: no target chat ids")
        return
    text = build_startup_notify_text()
    for chat_id in sorted(targets):
        try:
            await application.bot.send_message(chat_id=chat_id, text=text)
            logger.info("startup notify sent to chat_id=%s", chat_id)
        except Exception as exc:
            logger.warning("startup notify failed chat_id=%s err=%s", chat_id, exc)


async def post_init_hook(application: Application):
    await application.bot.set_my_commands(
        [
            BotCommand("start", "打开主菜单"),
            BotCommand("help", "查看完整帮助"),
            BotCommand("status", "查看系统状态"),
            BotCommand("tasks", "查看任务列表"),
            BotCommand("task_status", "查看当前任务详情"),
            BotCommand("publish_now", "立即发布下一条"),
            BotCommand("retry_failed", "重试失败消息"),
            BotCommand("retry_waiting", "重试等待消息"),
            BotCommand("restart", "重启流程（仅super）"),
        ]
    )
    await notify_startup(application)


def init_db():
    os.makedirs("/mnt/sosoflow", exist_ok=True)
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
        # 历史默认时段迁移：将旧默认 09:00-23:30 统一迁移为全天
        legacy_default_tasks = session.scalars(
            select(Task).where(Task.active_start_time == "09:00", Task.active_end_time == "23:30")
        ).all()
        for t in legacy_default_tasks:
            t.active_start_time = "00:00"
            t.active_end_time = "23:59"
        session.commit()
    # 轻量自迁移：为旧库补齐 queue 新字段（SQLite 允许 ADD COLUMN）
    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("queue")}
    with engine.begin() as conn:
        if "file_id" not in columns:
            conn.execute(sql_text("ALTER TABLE queue ADD COLUMN file_id VARCHAR(512)"))
        if "caption" not in columns:
            conn.execute(sql_text("ALTER TABLE queue ADD COLUMN caption TEXT"))
        if "retry_count" not in columns:
            conn.execute(sql_text("ALTER TABLE queue ADD COLUMN retry_count INTEGER DEFAULT 0"))
            conn.execute(sql_text("UPDATE queue SET retry_count = 0 WHERE retry_count IS NULL"))
        if "next_retry_at" not in columns:
            next_retry_type = "TIMESTAMP" if database_type(env.database_url) == "postgresql" else "DATETIME"
            conn.execute(sql_text(f"ALTER TABLE queue ADD COLUMN next_retry_at {next_retry_type}"))


def token_preview(token: str) -> str:
    if len(token) <= 10:
        return "***"
    return f"{token[:6]}...{token[-4:]}"

def database_type(database_url: str) -> str:
    url = (database_url or "").strip().lower()
    if url.startswith("sqlite:"):
        return "sqlite"
    if url.startswith("postgresql:") or url.startswith("postgres:"):
        return "postgresql"
    scheme = url.split(":", 1)[0] if ":" in url else ""
    return scheme or "unknown"


def startup_self_check():
    with SessionLocal() as session:
        setting = session.get(GlobalSetting, 1)
        tick_seconds = setting.tick_seconds if setting else 60
        total_admins = session.scalar(select(func.count()).select_from(Admin)) or 0
        super_admins = session.scalar(select(func.count()).select_from(Admin).where(Admin.role == RoleEnum.super)) or 0
        normal_admins = session.scalar(select(func.count()).select_from(Admin).where(Admin.role == RoleEnum.admin)) or 0
    logger.info("===== sosoFlow startup self-check =====")
    logger.info("TZ=%s", env.tz)
    logger.info("Database Type: %s", database_type(env.database_url))
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
    app.add_handler(CommandHandler("retry_waiting", retry_waiting_cmd))
    app.add_handler(CommandHandler("set_interval", set_interval_cmd))
    app.add_handler(CommandHandler("set_daily_limit", set_daily_limit_cmd))
    app.add_handler(CommandHandler("set_time_window", set_time_window_cmd))
    app.add_handler(CommandHandler("set_mode", set_mode_cmd))
    app.add_handler(CommandHandler("set_delete_after_success", set_delete_after_success_cmd))
    app.add_handler(CommandHandler("set_auto_capture", set_auto_capture_cmd))
    app.add_handler(CommandHandler("set_tick", set_tick_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))
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
    application = Application.builder().token(env.bot_token).post_init(post_init_hook).build()
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

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


class SourceMessageStateEnum(str, Enum):
    observed = "observed"
    missing = "missing"
    deleted = "deleted"


class TaskMessageStatusEnum(str, Enum):
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
    range_start_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    range_end_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
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
    include_keywords_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    include_keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    has_document: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
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


class SourceRegistry(Base):
    __tablename__ = "source_registry"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    latest_seen_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class SourceMessage(Base):
    __tablename__ = "source_messages"
    __table_args__ = (UniqueConstraint("source_chat_id", "message_id", name="uq_source_msg"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    state: Mapped[SourceMessageStateEnum] = mapped_column(SqlEnum(SourceMessageStateEnum), default=SourceMessageStateEnum.observed)
    message_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    file_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    text_preview: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    has_text: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_photo: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_video: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_document: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_links: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_forwarded: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    media_group_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class TaskMessageState(Base):
    __tablename__ = "task_message_state"
    __table_args__ = (UniqueConstraint("task_id", "source_chat_id", "message_id", name="uq_task_source_msg"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[TaskMessageStatusEnum] = mapped_column(SqlEnum(TaskMessageStatusEnum), default=TaskMessageStatusEnum.pending)
    target_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    fail_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class GlobalSetting(Base):
    __tablename__ = "global_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    tick_seconds: Mapped[int] = mapped_column(Integer, default=60)
    debug_media_updates: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class UserState(Base):
    __tablename__ = "user_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
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
    debug_media_updates: bool


def parse_ids(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def parse_bool_env(value: str, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}

def normalize_database_url(value: str) -> str:
    raw = (value or "").strip()
    if raw.startswith("postgres://"):
        return "postgresql://" + raw[len("postgres://") :]
    return raw


def read_version_file() -> str:
    version_path = os.path.join(os.getcwd(), "VERSION")
    try:
        if not os.path.exists(version_path):
            return ""
        with open(version_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as exc:
        logger.warning("read VERSION file failed: %s", exc)
        return ""


def resolve_deploy_version() -> str:
    explicit = os.getenv("DEPLOY_VERSION", "").strip()
    if explicit:
        return explicit
    version_file = read_version_file()
    if version_file:
        return version_file
    git_commit = os.getenv("GIT_COMMIT", "").strip()
    if git_commit:
        return git_commit
    return "unknown"


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
        deploy_version=resolve_deploy_version(),
        startup_notify_chat_ids=parse_ids(os.getenv("STARTUP_NOTIFY_CHAT_IDS", "")),
        restart_strategy=os.getenv("RESTART_STRATEGY", "guide").strip().lower(),
        debug_media_updates=parse_bool_env(os.getenv("DEBUG_MEDIA_UPDATES", ""), default=False),
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
MEDIA_GROUP_SETTLE_SECONDS = 4


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


def classify_update_message(update: Update) -> tuple[str, Optional[object]]:
    if getattr(update, "message", None):
        return "message", update.message
    if getattr(update, "channel_post", None):
        return "channel_post", update.channel_post
    if getattr(update, "edited_message", None):
        return "edited_message", update.edited_message
    if getattr(update, "edited_channel_post", None):
        return "edited_channel_post", update.edited_channel_post
    return "other", None


def log_debug_media_update(update_type: str, msg):
    if not msg or not getattr(msg, "chat", None):
        logger.info("debug_media_update type=%s no_message_payload", update_type)
        return
    has_photo = bool(getattr(msg, "photo", None))
    has_video = bool(getattr(msg, "video", None))
    has_document = bool(getattr(msg, "document", None))
    message_type = "text"
    if has_photo:
        message_type = "photo"
    elif has_video:
        message_type = "video"
    elif has_document:
        message_type = "document"
    elif getattr(msg, "sticker", None):
        message_type = "sticker"
    elif getattr(msg, "poll", None):
        message_type = "poll"
    caption = getattr(msg, "caption", None)
    text = getattr(msg, "text", None)
    photo_file_id = msg.photo[-1].file_id if has_photo and msg.photo else None
    video_file_id = msg.video.file_id if has_video else None
    document_file_id = msg.document.file_id if has_document else None
    logger.info(
        "debug_media_update type=%s chat_id=%s chat_type=%s message_id=%s media_group_id=%s message_type=%s has_photo=%s has_video=%s has_document=%s has_caption=%s has_text=%s photo_file_id=%s video_file_id=%s document_file_id=%s",
        update_type,
        msg.chat_id,
        msg.chat.type,
        msg.message_id,
        msg.media_group_id,
        message_type,
        has_photo,
        has_video,
        has_document,
        bool(caption),
        bool(text),
        bool(photo_file_id),
        bool(video_file_id),
        bool(document_file_id),
    )
    if msg.media_group_id:
        logger.info("media_group_detected chat_id=%s media_group_id=%s message_id=%s", msg.chat_id, msg.media_group_id, msg.message_id)
    elif has_photo or has_video or has_document:
        logger.info("single_media_or_group_missing chat_id=%s message_id=%s", msg.chat_id, msg.message_id)


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


def get_or_create_task_by_pair(session, source_chat_id: int, target_chat_id: int) -> tuple[Task, bool]:
    existing = session.scalar(
        select(Task).where(Task.source_chat_id == source_chat_id, Task.target_chat_id == target_chat_id).order_by(Task.id.asc())
    )
    if existing:
        return existing, False
    task_name = f"task_{abs(source_chat_id)}_{abs(target_chat_id)}"
    task = Task(name=task_name, source_chat_id=source_chat_id, target_chat_id=target_chat_id)
    session.add(task)
    session.commit()
    session.refresh(task)
    session.add(TaskFilter(task_id=task.id))
    session.commit()
    return task, True


def ensure_auto_source_capture_task(session, source_chat_id: int) -> Task:
    existing = session.scalar(select(Task).where(Task.source_chat_id == source_chat_id).order_by(Task.id.asc()))
    if existing:
        return existing
    task = Task(
        name=f"auto_source_{abs(source_chat_id)}",
        source_chat_id=source_chat_id,
        # 默认目标先占位为 source，任务默认暂停，不会误发布；管理员后续改目标后再启动。
        target_chat_id=source_chat_id,
        enabled=False,
        auto_capture_enabled=True,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    session.add(TaskFilter(task_id=task.id))
    session.commit()
    logger.info("auto_source_task_created task_id=%s source_chat_id=%s", task.id, source_chat_id)
    return task


def upsert_source_registry_and_message(
    session,
    source_chat_id: int,
    message_id: int,
    message_type: str,
    file_id: Optional[str],
    caption: Optional[str],
    text_value: str,
    has_photo: bool,
    has_video: bool,
    has_document: bool,
    is_forwarded: bool,
    media_group_id: Optional[str],
):
    registry = session.scalar(select(SourceRegistry).where(SourceRegistry.source_chat_id == source_chat_id))
    if not registry:
        registry = SourceRegistry(source_chat_id=source_chat_id, enabled=True, latest_seen_message_id=message_id)
        session.add(registry)
    else:
        if registry.latest_seen_message_id is None or message_id > registry.latest_seen_message_id:
            registry.latest_seen_message_id = message_id
    row = session.scalar(select(SourceMessage).where(SourceMessage.source_chat_id == source_chat_id, SourceMessage.message_id == message_id))
    if row:
        row.state = SourceMessageStateEnum.observed
        row.message_type = message_type
        row.file_id = file_id
        row.caption = caption
        row.text_preview = text_value[:280] if text_value else None
        row.has_text = bool(text_value)
        row.has_photo = has_photo
        row.has_video = has_video
        row.has_document = has_document
        row.has_links = extract_links(text_value)
        row.is_forwarded = is_forwarded
        row.media_group_id = media_group_id
        return
    session.add(
        SourceMessage(
            source_chat_id=source_chat_id,
            message_id=message_id,
            state=SourceMessageStateEnum.observed,
            message_type=message_type,
            file_id=file_id,
            caption=caption,
            text_preview=text_value[:280] if text_value else None,
            has_text=bool(text_value),
            has_photo=has_photo,
            has_video=has_video,
            has_document=has_document,
            has_links=extract_links(text_value),
            is_forwarded=is_forwarded,
            media_group_id=media_group_id,
        )
    )


def is_source_enabled(session, source_chat_id: int) -> bool:
    row = session.scalar(select(SourceRegistry).where(SourceRegistry.source_chat_id == source_chat_id))
    if not row:
        return True
    return bool(row.enabled)


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


def task_message_stats(session, task: Task) -> dict[str, int]:
    data = {}
    for status in [
        TaskMessageStatusEnum.pending,
        TaskMessageStatusEnum.waiting,
        TaskMessageStatusEnum.published,
        TaskMessageStatusEnum.failed,
        TaskMessageStatusEnum.skipped,
    ]:
        query = select(func.count()).select_from(TaskMessageState).where(
            TaskMessageState.task_id == task.id,
            TaskMessageState.source_chat_id == task.source_chat_id,
            TaskMessageState.status == status,
        )
        if has_task_range(task):
            query = query.where(
                TaskMessageState.message_id >= task.range_start_message_id,
                TaskMessageState.message_id <= task.range_end_message_id,
            )
        count = session.scalar(query) or 0
        data[status.value] = count
    return data


def today_published_count(session, task: Task) -> int:
    today_start = datetime.combine(now().date(), time.min)
    if has_task_range(task):
        return session.scalar(
            select(func.count()).select_from(TaskMessageState).where(
                TaskMessageState.task_id == task.id,
                TaskMessageState.source_chat_id == task.source_chat_id,
                TaskMessageState.status == TaskMessageStatusEnum.published,
                TaskMessageState.published_at.is_not(None),
                TaskMessageState.published_at >= today_start,
            )
        ) or 0
    return session.scalar(
        select(func.count()).select_from(QueueItem).where(
            QueueItem.task_id == task.id,
            QueueItem.status == QueueStatusEnum.published,
            QueueItem.published_at >= today_start,
        )
    ) or 0


def next_pending_message_id(session, task: Task) -> Optional[int]:
    if has_task_range(task):
        row = session.scalar(
            select(TaskMessageState).where(
                TaskMessageState.task_id == task.id,
                TaskMessageState.source_chat_id == task.source_chat_id,
                TaskMessageState.message_id >= task.range_start_message_id,
                TaskMessageState.message_id <= task.range_end_message_id,
                TaskMessageState.status == TaskMessageStatusEnum.pending,
            ).order_by(TaskMessageState.message_id.asc())
        )
        return row.message_id if row else None
    row = session.scalar(
        select(QueueItem).where(
            QueueItem.task_id == task.id,
            QueueItem.status == QueueStatusEnum.pending,
        ).order_by(QueueItem.message_id.asc())
    )
    return row.message_id if row else None


def task_publish_unit_stats(session, task_id: int) -> tuple[int, int]:
    pending_single = session.scalar(
        select(func.count()).select_from(QueueItem).where(
            QueueItem.task_id == task_id,
            QueueItem.status == QueueStatusEnum.pending,
            QueueItem.media_group_id.is_(None),
        )
    ) or 0
    pending_groups = session.scalar(
        select(func.count(func.distinct(QueueItem.media_group_id))).where(
            QueueItem.task_id == task_id,
            QueueItem.status == QueueStatusEnum.pending,
            QueueItem.media_group_id.is_not(None),
        )
    ) or 0
    return pending_single, pending_groups


def task_publish_unit_stats_v2(session, task: Task) -> tuple[int, int]:
    if not has_task_range(task):
        return task_publish_unit_stats(session, task.id)
    pending_rows = session.scalars(
        select(TaskMessageState).where(
            TaskMessageState.task_id == task.id,
            TaskMessageState.source_chat_id == task.source_chat_id,
            TaskMessageState.message_id >= task.range_start_message_id,
            TaskMessageState.message_id <= task.range_end_message_id,
            TaskMessageState.status == TaskMessageStatusEnum.pending,
        )
    ).all()
    if not pending_rows:
        return 0, 0
    pending_ids = [x.message_id for x in pending_rows]
    grouped_pending = session.scalar(
        select(func.count(func.distinct(SourceMessage.media_group_id))).where(
            SourceMessage.source_chat_id == task.source_chat_id,
            SourceMessage.message_id.in_(pending_ids),
            SourceMessage.state == SourceMessageStateEnum.observed,
            SourceMessage.media_group_id.is_not(None),
        )
    ) or 0
    single_pending = session.scalar(
        select(func.count()).select_from(SourceMessage).where(
            SourceMessage.source_chat_id == task.source_chat_id,
            SourceMessage.message_id.in_(pending_ids),
            SourceMessage.state == SourceMessageStateEnum.observed,
            SourceMessage.media_group_id.is_(None),
        )
    ) or 0
    return single_pending, grouped_pending


def parse_include_keywords(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,\n]", raw)
    return [p.strip() for p in parts if p.strip()]


def filter_summary(task_filter: TaskFilter) -> str:
    kw_count = len(parse_include_keywords(task_filter.include_keywords))
    return (
        f"图片:{'开' if task_filter.require_photo else '关'} | "
        f"视频:{'开' if task_filter.require_video else '关'} | "
        f"排除纯文字:{'开' if task_filter.require_text else '关'} | "
        f"排除链接:{'开' if task_filter.exclude_links else '关'} | "
        f"排除无字:{'开' if task_filter.exclude_no_text else '关'} | "
        f"排除转发:{'开' if task_filter.exclude_forwarded else '关'} | "
        f"排除贴纸:{'开' if task_filter.exclude_sticker else '关'} | "
        f"排除投票:{'开' if task_filter.exclude_poll else '关'} | "
        f"包含关键词:{'开' if task_filter.include_keywords_enabled else '关'}({kw_count})"
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
    stats = task_message_stats(session, task) if has_task_range(task) else task_stats(session, task.id)
    pending_single_units, pending_group_units = task_publish_unit_stats_v2(session, task)
    task_filter = ensure_task_filter(session, task.id)
    today_published = today_published_count(session, task)
    next_pending_id = next_pending_message_id(session, task)
    next_publish_in_seconds = 0
    if task.last_published_at:
        elapsed = int((now() - task.last_published_at).total_seconds())
        next_publish_in_seconds = max(task.interval_seconds - elapsed, 0)
    source_line = f"{task.source_chat_id}" if not source_name else f"{task.source_chat_id}（{source_name}）"
    target_line = f"{task.target_chat_id}" if not target_name else f"{task.target_chat_id}（{target_name}）"
    status_text = "✅已完成" if task.is_completed else ("🟢运行中" if task.enabled else "⏸暂停")
    range_text = (
        f"{task.range_start_message_id}-{task.range_end_message_id}"
        if task.range_start_message_id is not None and task.range_end_message_id is not None
        else "未设置"
    )
    return (
        f"🧩 任务详情\n\n"
        f"任务ID: {task.id}\n"
        f"任务名称: {task.name}\n"
        f"源频道/群组: {source_line}\n"
        f"目标频道/群组: {target_line}\n\n"
        f"模式: {mode_label(task.mode)}\n"
        f"状态: {status_text}\n"
        f"发布范围: {range_text}\n"
        f"队列统计: 待发布 {stats['pending']} | 等待重试 {stats['waiting']} | 已发布 {stats['published']} | 失败 {stats['failed']} | 跳过 {stats['skipped']}\n"
        f"发布类型: 单条待发 {pending_single_units} | 媒体组待发 {pending_group_units}\n"
        f"今日发布: {today_published}/{task.daily_limit}\n"
        f"发布间隔: {task.interval_seconds}s\n"
        f"下次可发布剩余: {next_publish_in_seconds}s\n\n"
        f"日上限: {task.daily_limit}\n"
        f"发布时段: {task.active_start_time}-{task.active_end_time}\n"
        f"任务监听源消息: {bool_cn(task.auto_capture_enabled)}\n"
        f"发布后删除源消息: {bool_cn(task.delete_after_success)}\n"
        f"下一条待发布: {next_pending_id if next_pending_id is not None else '无'}\n\n"
        f"过滤: {filter_summary(task_filter)}"
    )


def task_detail_keyboard(task_id: int):
    with_name = "✏️ 名称"
    with SessionLocal() as session:
        task = session.get(Task, task_id)
    mode_btn = "🧭 切换模式"
    capture_btn = "📡 监听状态"
    delete_btn = "🧹 是否删源"
    interval_btn = "⏱ 间隔"
    daily_btn = "📊 日上限"
    if task:
        mode_btn = "🧭 复制模式（当前）" if task.mode == TaskModeEnum.copy else "🧭 转载模式（当前）"
        capture_btn = f"📡 监听状态（{'开' if task.auto_capture_enabled else '关'}）"
        delete_btn = f"🧹 是否删源（{'是' if task.delete_after_success else '否'}）"
        interval_btn = f"⏱ 间隔（{task.interval_seconds}秒）"
        daily_btn = f"📊 日上限（{task.daily_limit}）"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("—— 高频操作 ——", callback_data="noop")],
            [InlineKeyboardButton("▶️ 启动", callback_data=f"task_start:{task_id}"), InlineKeyboardButton("⏸ 暂停", callback_data=f"task_pause:{task_id}")],
            [InlineKeyboardButton("🚀 立即发布", callback_data=f"task_publish:{task_id}"), InlineKeyboardButton("📥 导入范围", callback_data=f"task_import_hint:{task_id}")],
            [InlineKeyboardButton("—— 配置操作 ——", callback_data="noop")],
            [InlineKeyboardButton(with_name, callback_data=f"task_edit_name:{task_id}"), InlineKeyboardButton("🔁 重试失败", callback_data=f"task_retry:{task_id}")],
            [InlineKeyboardButton(mode_btn, callback_data=f"task_toggle_mode:{task_id}"), InlineKeyboardButton(capture_btn, callback_data=f"task_toggle_auto_capture:{task_id}")],
            [InlineKeyboardButton(delete_btn, callback_data=f"task_toggle_delete:{task_id}"), InlineKeyboardButton("🔍 过滤设置", callback_data=f"task_filters:{task_id}")],
            [InlineKeyboardButton(interval_btn, callback_data=f"task_input_interval:{task_id}"), InlineKeyboardButton(daily_btn, callback_data=f"task_input_daily:{task_id}")],
            [InlineKeyboardButton("🕒 时段", callback_data=f"task_input_window:{task_id}")],
            [InlineKeyboardButton("🧷 来源ID修改", callback_data=f"task_edit_source:{task_id}"), InlineKeyboardButton("🎯 目标ID修改", callback_data=f"task_edit_target:{task_id}")],
            [InlineKeyboardButton("🧾 最近日志(5条)", callback_data=f"task_recent_logs:{task_id}"), InlineKeyboardButton("🔄 刷新", callback_data=f"task_view:{task_id}")],
            [InlineKeyboardButton("—— 危险操作 ——", callback_data="noop")],
            [InlineKeyboardButton("♻️ 重置任务", callback_data=f"task_reset_ask:{task_id}"), InlineKeyboardButton("🗑 删除任务", callback_data=f"task_delete_ask:{task_id}")],
            [InlineKeyboardButton("⬅️ 返回任务列表", callback_data="tasks_list"), InlineKeyboardButton("🏠 主菜单", callback_data="menu_home")],
        ]
    )


def task_filters_keyboard(task_id: int, task_filter: TaskFilter):
    def on_off(value: bool) -> str:
        return "🟢" if value else "⚪️"
    kw_count = len(parse_include_keywords(task_filter.include_keywords))

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"{on_off(task_filter.require_photo)} 🖼 必须含图片", callback_data=f"task_filter_toggle:{task_id}:require_photo"),
                InlineKeyboardButton(f"{on_off(task_filter.require_video)} 🎬 必须含视频", callback_data=f"task_filter_toggle:{task_id}:require_video"),
            ],
            [
                InlineKeyboardButton(f"{on_off(task_filter.require_text)} 📝 排除纯文字", callback_data=f"task_filter_toggle:{task_id}:require_text"),
                InlineKeyboardButton(f"{on_off(task_filter.exclude_links)} 🔗 排除链接", callback_data=f"task_filter_toggle:{task_id}:exclude_links"),
            ],
            [
                InlineKeyboardButton(f"{on_off(task_filter.include_keywords_enabled)} 🏷 包含关键词({kw_count})", callback_data=f"task_filter_toggle:{task_id}:include_keywords_enabled"),
                InlineKeyboardButton("✍️ 设置关键词", callback_data=f"task_filter_keywords_input:{task_id}"),
            ],
            [
                InlineKeyboardButton(f"{on_off(task_filter.exclude_no_text)} 🙈 排除无字", callback_data=f"task_filter_toggle:{task_id}:exclude_no_text"),
                InlineKeyboardButton(f"{on_off(task_filter.exclude_forwarded)} ↪️ 排除转发", callback_data=f"task_filter_toggle:{task_id}:exclude_forwarded"),
            ],
            [InlineKeyboardButton("⬅️ 返回任务详情", callback_data=f"task_view:{task_id}"), InlineKeyboardButton("🏠 主菜单", callback_data="menu_home")],
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
        (task_filter.require_text and item.message_type == "text", "纯文字"),
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
    if task_filter.include_keywords_enabled:
        keywords = parse_include_keywords(task_filter.include_keywords)
        if keywords:
            haystack = (item.text_preview or "").lower()
            if not any(kw.lower() in haystack for kw in keywords):
                return "未命中包含关键词"
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


def write_config_log(session, task_id: int, message: str):
    write_log(session, task_id, None, None, "config", message)


def get_publish_unit_rows(session, base_item: QueueItem) -> list[QueueItem]:
    if not base_item.media_group_id:
        return [base_item]
    rows = session.scalars(
        select(QueueItem).where(
            QueueItem.task_id == base_item.task_id,
            QueueItem.media_group_id == base_item.media_group_id,
            QueueItem.status.in_([QueueStatusEnum.pending, QueueStatusEnum.waiting]),
        ).order_by(QueueItem.message_id.asc())
    ).all()
    return rows or [base_item]


def is_group_settled(rows: list[QueueItem]) -> bool:
    if not rows:
        return True
    if not rows[0].media_group_id:
        return True
    latest_created = max(row.created_at for row in rows if row.created_at)
    if not latest_created:
        return True
    return (now() - latest_created).total_seconds() >= MEDIA_GROUP_SETTLE_SECONDS


def expand_rows_by_media_group(session, rows: list[QueueItem]) -> list[QueueItem]:
    result_map: dict[int, QueueItem] = {}
    for row in rows:
        if row.media_group_id:
            grouped = session.scalars(
                select(QueueItem).where(
                    QueueItem.task_id == row.task_id,
                    QueueItem.media_group_id == row.media_group_id,
                    QueueItem.status.in_([QueueStatusEnum.failed, QueueStatusEnum.waiting]),
                )
            ).all()
            for group_row in grouped:
                result_map[group_row.id] = group_row
        else:
            result_map[row.id] = row
    return list(result_map.values())


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


def to_task_message_status(status: QueueStatusEnum) -> TaskMessageStatusEnum:
    mapping = {
        QueueStatusEnum.pending: TaskMessageStatusEnum.pending,
        QueueStatusEnum.published: TaskMessageStatusEnum.published,
        QueueStatusEnum.failed: TaskMessageStatusEnum.failed,
        QueueStatusEnum.skipped: TaskMessageStatusEnum.skipped,
        QueueStatusEnum.waiting: TaskMessageStatusEnum.waiting,
    }
    return mapping[status]


def to_queue_status(status: TaskMessageStatusEnum) -> QueueStatusEnum:
    mapping = {
        TaskMessageStatusEnum.pending: QueueStatusEnum.pending,
        TaskMessageStatusEnum.published: QueueStatusEnum.published,
        TaskMessageStatusEnum.failed: QueueStatusEnum.failed,
        TaskMessageStatusEnum.skipped: QueueStatusEnum.skipped,
        TaskMessageStatusEnum.waiting: QueueStatusEnum.waiting,
    }
    return mapping[status]


def upsert_task_message_state_from_queue_item(session, task_id: int, source_chat_id: int, item: QueueItem):
    row = session.scalar(
        select(TaskMessageState).where(
            TaskMessageState.task_id == task_id,
            TaskMessageState.source_chat_id == source_chat_id,
            TaskMessageState.message_id == item.message_id,
        )
    )
    mapped_status = to_task_message_status(item.status)
    if not row:
        session.add(
            TaskMessageState(
                task_id=task_id,
                source_chat_id=source_chat_id,
                message_id=item.message_id,
                status=mapped_status,
                target_message_id=item.target_message_id,
                fail_reason=item.fail_reason,
                retry_count=item.retry_count or 0,
                next_retry_at=item.next_retry_at,
                published_at=item.published_at,
            )
        )
        return
    row.status = mapped_status
    row.target_message_id = item.target_message_id
    row.fail_reason = item.fail_reason
    row.retry_count = item.retry_count or 0
    row.next_retry_at = item.next_retry_at
    row.published_at = item.published_at


def is_terminal_tms_status(status: TaskMessageStatusEnum) -> bool:
    return status in {TaskMessageStatusEnum.published, TaskMessageStatusEnum.failed, TaskMessageStatusEnum.skipped}


async def try_delete_source_message_if_ready(application: Application, session, source_chat_id: int, message_id: int) -> tuple[bool, str]:
    tasks = session.scalars(select(Task).where(Task.source_chat_id == source_chat_id)).all()
    if not tasks:
        return False, "no_tasks"
    delete_required = any(t.delete_after_success for t in tasks)
    if not delete_required:
        return False, "delete_not_required"
    for t in tasks:
        # 仅检查会消费该源的任务，已彻底关闭自动监听的任务不参与删源门槛。
        if not t.auto_capture_enabled:
            continue
        tms = session.scalar(
            select(TaskMessageState).where(
                TaskMessageState.task_id == t.id,
                TaskMessageState.source_chat_id == source_chat_id,
                TaskMessageState.message_id == message_id,
            )
        )
        if not tms:
            return False, f"task_{t.id}_no_state"
        if not is_terminal_tms_status(tms.status):
            return False, f"task_{t.id}_not_terminal:{tms.status.value}"
    try:
        await application.bot.delete_message(chat_id=source_chat_id, message_id=message_id)
    except Exception as exc:
        return False, f"delete_fail:{exc}"
    now_ts = now()
    queue_rows = session.scalars(
        select(QueueItem).join(Task, QueueItem.task_id == Task.id).where(
            Task.source_chat_id == source_chat_id,
            QueueItem.message_id == message_id,
        )
    ).all()
    for row in queue_rows:
        row.deleted_at = now_ts
    for t in tasks:
        write_log(session, t.id, message_id, None, "delete", "删除源消息成功（按多任务门槛）")
    session.commit()
    return True, "deleted"


def ensure_queue_item_for_task_message(session, task: Task, tms: TaskMessageState) -> Optional[QueueItem]:
    item = session.scalar(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.message_id == tms.message_id))
    if item:
        item.status = to_queue_status(tms.status)
        item.retry_count = tms.retry_count or 0
        item.next_retry_at = tms.next_retry_at
        item.fail_reason = tms.fail_reason
        item.target_message_id = tms.target_message_id
        item.published_at = tms.published_at
        return item
    src = session.scalar(
        select(SourceMessage).where(
            SourceMessage.source_chat_id == task.source_chat_id,
            SourceMessage.message_id == tms.message_id,
            SourceMessage.state == SourceMessageStateEnum.observed,
        )
    )
    if not src:
        return None
    item = QueueItem(
        task_id=task.id,
        message_id=src.message_id,
        status=to_queue_status(tms.status),
        target_message_id=tms.target_message_id,
        message_type=src.message_type or "unknown",
        file_id=src.file_id,
        caption=src.caption,
        text_preview=src.text_preview,
        has_text=src.has_text,
        has_photo=src.has_photo,
        has_video=src.has_video,
        has_document=src.has_document,
        has_links=src.has_links,
        is_forwarded=src.is_forwarded,
        media_group_id=src.media_group_id,
        published_at=tms.published_at,
        fail_reason=tms.fail_reason,
        retry_count=tms.retry_count or 0,
        next_retry_at=tms.next_retry_at,
    )
    session.add(item)
    return item


def pick_next_publish_item(session, task_id: int) -> Optional[QueueItem]:
    task = session.get(Task, task_id)
    if task and has_task_range(task):
        pending_tms_rows = session.scalars(
            select(TaskMessageState).where(
                TaskMessageState.task_id == task_id,
                TaskMessageState.source_chat_id == task.source_chat_id,
                TaskMessageState.message_id >= task.range_start_message_id,
                TaskMessageState.message_id <= task.range_end_message_id,
                TaskMessageState.status == TaskMessageStatusEnum.pending,
            ).order_by(TaskMessageState.message_id.asc())
        ).all()
        for pending_tms in pending_tms_rows:
            candidate = ensure_queue_item_for_task_message(session, task, pending_tms)
            if candidate:
                return candidate
            pending_tms.status = TaskMessageStatusEnum.failed
            pending_tms.fail_reason = "源消息未观测或已删除，无法构建发布载荷"
        waiting_tms_rows = session.scalars(
            select(TaskMessageState).where(
                TaskMessageState.task_id == task_id,
                TaskMessageState.source_chat_id == task.source_chat_id,
                TaskMessageState.message_id >= task.range_start_message_id,
                TaskMessageState.message_id <= task.range_end_message_id,
                TaskMessageState.status == TaskMessageStatusEnum.waiting,
                TaskMessageState.next_retry_at.is_not(None),
                TaskMessageState.next_retry_at <= now(),
            ).order_by(TaskMessageState.message_id.asc())
        ).all()
        for waiting_tms in waiting_tms_rows:
            candidate = ensure_queue_item_for_task_message(session, task, waiting_tms)
            if candidate:
                return candidate
            waiting_tms.status = TaskMessageStatusEnum.failed
            waiting_tms.fail_reason = "源消息未观测或已删除，无法构建发布载荷"
            waiting_tms.next_retry_at = None
        if pending_tms_rows or waiting_tms_rows:
            session.commit()
        # 兼容过渡：若 task_message_state 尚未建全，回退到旧 queue 选择。
        fallback_pending = session.scalar(
            select(QueueItem).where(
                QueueItem.task_id == task_id,
                QueueItem.message_id >= task.range_start_message_id,
                QueueItem.message_id <= task.range_end_message_id,
                QueueItem.status == QueueStatusEnum.pending,
            ).order_by(QueueItem.message_id.asc())
        )
        if fallback_pending:
            return fallback_pending
        return session.scalar(
            select(QueueItem).where(
                QueueItem.task_id == task_id,
                QueueItem.message_id >= task.range_start_message_id,
                QueueItem.message_id <= task.range_end_message_id,
                QueueItem.status == QueueStatusEnum.waiting,
                QueueItem.next_retry_at.is_not(None),
                QueueItem.next_retry_at <= now(),
            ).order_by(QueueItem.message_id.asc())
        )
    range_start = task.range_start_message_id if task else None
    range_end = task.range_end_message_id if task else None
    base_pending = select(QueueItem).where(QueueItem.task_id == task_id, QueueItem.status == QueueStatusEnum.pending)
    if range_start is not None and range_end is not None:
        base_pending = base_pending.where(QueueItem.message_id >= range_start, QueueItem.message_id <= range_end)
    # 优先发布实时捕获的消息（有元数据），避免 /import_range 大范围占位ID阻塞新消息。
    pending_item = session.scalar(
        base_pending.where(
            QueueItem.message_type != "unknown",
        ).order_by(QueueItem.message_id.asc())
    )
    if pending_item:
        return pending_item
    pending_item = session.scalar(
        base_pending.order_by(QueueItem.message_id.asc())
    )
    if pending_item:
        return pending_item
    waiting_query = select(QueueItem).where(
        QueueItem.task_id == task_id,
        QueueItem.status == QueueStatusEnum.waiting,
        QueueItem.next_retry_at.is_not(None),
        QueueItem.next_retry_at <= now(),
    )
    if range_start is not None and range_end is not None:
        waiting_query = waiting_query.where(QueueItem.message_id >= range_start, QueueItem.message_id <= range_end)
    return session.scalar(
        waiting_query.order_by(QueueItem.message_id.asc())
    )


def has_task_range(task: Task) -> bool:
    return task.range_start_message_id is not None and task.range_end_message_id is not None


def finalize_task_as_completed(session, task: Task, reason: str):
    task.enabled = False
    task.is_completed = True
    task.completed_at = now()
    write_log(session, task.id, None, None, "complete", reason)


def try_auto_complete_task_range(session, task: Task) -> bool:
    if not has_task_range(task):
        return False
    unfinished = session.scalar(
        select(func.count()).select_from(TaskMessageState).where(
            TaskMessageState.task_id == task.id,
            TaskMessageState.source_chat_id == task.source_chat_id,
            TaskMessageState.message_id >= task.range_start_message_id,
            TaskMessageState.message_id <= task.range_end_message_id,
            TaskMessageState.status.in_([TaskMessageStatusEnum.pending, TaskMessageStatusEnum.waiting]),
        )
    ) or 0
    if unfinished > 0:
        return False
    if task.is_completed and not task.enabled:
        return True
    finalize_task_as_completed(session, task, "范围消息处理完成，任务自动停止")
    session.commit()
    return True


def sync_task_range_queue_from_source_messages(session, task: Task) -> tuple[int, int]:
    if not has_task_range(task):
        return 0, 0
    src_rows = session.scalars(
        select(SourceMessage).where(
            SourceMessage.source_chat_id == task.source_chat_id,
            SourceMessage.state == SourceMessageStateEnum.observed,
            SourceMessage.message_id >= task.range_start_message_id,
            SourceMessage.message_id <= task.range_end_message_id,
        )
    ).all()
    inserted = 0
    existed = 0
    for src in src_rows:
        tms = session.scalar(
            select(TaskMessageState).where(
                TaskMessageState.task_id == task.id,
                TaskMessageState.source_chat_id == task.source_chat_id,
                TaskMessageState.message_id == src.message_id,
            )
        )
        if not tms:
            session.add(
                TaskMessageState(
                    task_id=task.id,
                    source_chat_id=task.source_chat_id,
                    message_id=src.message_id,
                    status=TaskMessageStatusEnum.pending,
                )
            )
        exists = session.scalar(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.message_id == src.message_id))
        if exists:
            existed += 1
            if exists.status in {QueueStatusEnum.pending, QueueStatusEnum.waiting, QueueStatusEnum.failed}:
                # 持续补全元数据，保障媒体组信息完整。
                exists.message_type = src.message_type or exists.message_type
                exists.file_id = src.file_id or exists.file_id
                exists.caption = src.caption if src.caption is not None else exists.caption
                exists.text_preview = src.text_preview if src.text_preview is not None else exists.text_preview
                exists.has_text = src.has_text if src.has_text is not None else exists.has_text
                exists.has_photo = src.has_photo if src.has_photo is not None else exists.has_photo
                exists.has_video = src.has_video if src.has_video is not None else exists.has_video
                exists.has_document = src.has_document if src.has_document is not None else exists.has_document
                exists.has_links = src.has_links if src.has_links is not None else exists.has_links
                exists.is_forwarded = src.is_forwarded if src.is_forwarded is not None else exists.is_forwarded
                exists.media_group_id = src.media_group_id or exists.media_group_id
            upsert_task_message_state_from_queue_item(session, task.id, task.source_chat_id, exists)
            continue
        new_item = QueueItem(
            task_id=task.id,
            message_id=src.message_id,
            message_type=src.message_type or "unknown",
            file_id=src.file_id,
            caption=src.caption,
            text_preview=src.text_preview,
            has_text=src.has_text,
            has_photo=src.has_photo,
            has_video=src.has_video,
            has_document=src.has_document,
            has_links=src.has_links,
            is_forwarded=src.is_forwarded,
            media_group_id=src.media_group_id,
            status=QueueStatusEnum.pending,
        )
        session.add(new_item)
        upsert_task_message_state_from_queue_item(session, task.id, task.source_chat_id, new_item)
        inserted += 1
    if inserted > 0:
        session.commit()
    return inserted, existed


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


def upsert_queue_item_from_capture(
    session,
    task_id: int,
    message_id: int,
    message_type: str,
    file_id: Optional[str],
    caption: Optional[str],
    text_value: str,
    has_photo: bool,
    has_video: bool,
    has_document: bool,
    is_forwarded: bool,
    media_group_id: Optional[str],
):
    exists = session.scalar(select(QueueItem).where(QueueItem.task_id == task_id, QueueItem.message_id == message_id))
    if exists:
        # published/skipped 视为终态，不再覆盖，避免历史记录被新更新污染。
        if exists.status in {QueueStatusEnum.published, QueueStatusEnum.skipped}:
            return
        if exists.status in {QueueStatusEnum.waiting, QueueStatusEnum.failed}:
            exists.status = QueueStatusEnum.pending
            exists.fail_reason = None
            exists.next_retry_at = None
        # 关键修复：pending 占位项也要被真实更新“补全元数据”，否则 media_group_id/file_id 会长期缺失。
        exists.message_type = message_type
        exists.file_id = file_id
        exists.caption = caption
        exists.text_preview = text_value[:280] if text_value else None
        exists.has_text = bool(text_value)
        exists.has_photo = has_photo
        exists.has_video = has_video
        exists.has_document = has_document
        exists.has_links = extract_links(text_value)
        exists.is_forwarded = is_forwarded
        exists.media_group_id = media_group_id
        return
    session.add(
        QueueItem(
            task_id=task_id,
            message_id=message_id,
            status=QueueStatusEnum.pending,
            message_type=message_type,
            file_id=file_id,
            caption=caption,
            text_preview=text_value[:280] if text_value else None,
            has_text=bool(text_value),
            has_photo=has_photo,
            has_video=has_video,
            has_document=has_document,
            has_links=extract_links(text_value),
            is_forwarded=is_forwarded,
            media_group_id=media_group_id,
        )
    )


async def publish_one(application: Application, task: Task, ignore_interval: bool = False, ignore_window: bool = False) -> str:
    with SessionLocal() as session:
        db_task = session.get(Task, task.id)
        if not db_task:
            return "任务不存在"
        sync_task_range_queue_from_source_messages(session, db_task)
        if try_auto_complete_task_range(session, db_task):
            return "范围消息处理完成，任务已自动停止"
        if not ignore_window and not in_time_window(db_task):
            return "不在允许时间段"
        today_published = today_published_count(session, db_task)
        if reached_daily_limit(today_published, db_task.daily_limit):
            return "达到每日上限"
        if not ignore_interval and db_task.last_published_at:
            elapsed = (now() - db_task.last_published_at).total_seconds()
            if elapsed < db_task.interval_seconds:
                return "未到发布间隔"
        item = pick_next_publish_item(session, db_task.id)
        if not item:
            if try_auto_complete_task_range(session, db_task):
                return "范围消息处理完成，任务已自动停止"
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
            publish_unit_rows = get_publish_unit_rows(session, item)
            is_media_group_unit = bool(item.media_group_id and len(publish_unit_rows) > 0)
            if is_media_group_unit:
                logger.info(
                    "publish_unit task=%s kind=media_group media_group_id=%s group_size=%s",
                    db_task.id,
                    item.media_group_id,
                    len(publish_unit_rows),
                )
                if not is_group_settled(publish_unit_rows):
                    return f"媒体组等待收齐 media_group_id={item.media_group_id}"
            else:
                logger.info("publish_unit task=%s kind=message message_id=%s", db_task.id, item.message_id)
            if is_media_group_unit:
                group_reason = None
                for album_item in publish_unit_rows:
                    reason = apply_filters(album_item, task_filter)
                    if reason:
                        group_reason = reason
                        break
                if group_reason:
                    for album_item in publish_unit_rows:
                        album_item.status = QueueStatusEnum.skipped
                        album_item.fail_reason = None
                        album_item.next_retry_at = None
                        album_item.filter_reason = group_reason
                        write_log(session, db_task.id, album_item.message_id, None, "filter", group_reason)
                        upsert_task_message_state_from_queue_item(session, db_task.id, db_task.source_chat_id, album_item)
                    session.commit()
                    return f"已过滤 media_group={item.media_group_id} count={len(publish_unit_rows)} ({group_reason})"

                message_ids = [x.message_id for x in publish_unit_rows]
                has_file_id_count = len([x for x in publish_unit_rows if x.file_id])
                can_send_media_group = all(x.file_id and x.message_type in {"photo", "video", "document"} for x in publish_unit_rows)
                sent_ids = []
                publish_method = "send_media_group"
                if can_send_media_group:
                    caption_text = next((x.caption for x in publish_unit_rows if x.caption), None)
                    media = []
                    for idx, album_item in enumerate(publish_unit_rows):
                        caption = caption_text if idx == 0 else None
                        if album_item.message_type == "photo":
                            media.append(InputMediaPhoto(media=album_item.file_id, caption=caption))
                        elif album_item.message_type == "video":
                            media.append(InputMediaVideo(media=album_item.file_id, caption=caption))
                        else:
                            media.append(InputMediaDocument(media=album_item.file_id, caption=caption))
                    sent_messages = await application.bot.send_media_group(chat_id=db_task.target_chat_id, media=media)
                    sent_ids = [m for m in sent_messages]
                else:
                    publish_method = "copy_messages_fallback"
                    write_log(session, db_task.id, item.message_id, None, "fallback", "fallback_copy_messages_due_to_missing_file_id")
                    sent_ids = await application.bot.copy_messages(
                        chat_id=db_task.target_chat_id,
                        from_chat_id=db_task.source_chat_id,
                        message_ids=message_ids,
                    )
                logger.info(
                    "publish_unit_result task=%s media_group_id=%s group_size=%s has_file_id_count=%s publish_method=%s",
                    db_task.id,
                    item.media_group_id,
                    len(publish_unit_rows),
                    has_file_id_count,
                    publish_method,
                )
                published_at = now()
                for i, album_item in enumerate(publish_unit_rows):
                    target_id = sent_ids[i].message_id if i < len(sent_ids) else None
                    album_item.status = QueueStatusEnum.published
                    album_item.target_message_id = target_id
                    album_item.published_at = published_at
                    album_item.fail_reason = None
                    album_item.next_retry_at = None
                    write_log(session, db_task.id, album_item.message_id, target_id, "publish", f"发布成功 publish_method={publish_method}")
                    upsert_task_message_state_from_queue_item(session, db_task.id, db_task.source_chat_id, album_item)
                if db_task.delete_after_success:
                    for album_item in publish_unit_rows:
                        deleted, reason = await try_delete_source_message_if_ready(
                            application=application,
                            session=session,
                            source_chat_id=db_task.source_chat_id,
                            message_id=album_item.message_id,
                        )
                        if not deleted:
                            write_log(session, db_task.id, album_item.message_id, None, "delete_defer", f"暂不删源: {reason}")
                db_task.last_published_at = published_at
                session.commit()
                return f"发布成功 media_group={item.media_group_id} count={len(publish_unit_rows)} method={publish_method}"
            reason = apply_filters(item, task_filter)
            if reason:
                item.status = QueueStatusEnum.skipped
                item.fail_reason = None
                item.next_retry_at = None
                item.filter_reason = reason
                write_log(session, db_task.id, item.message_id, None, "filter", reason)
                upsert_task_message_state_from_queue_item(session, db_task.id, db_task.source_chat_id, item)
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
                deleted, reason = await try_delete_source_message_if_ready(
                    application=application,
                    session=session,
                    source_chat_id=db_task.source_chat_id,
                    message_id=item.message_id,
                )
                if not deleted:
                    write_log(session, db_task.id, item.message_id, None, "delete_defer", f"暂不删源: {reason}")
            upsert_task_message_state_from_queue_item(session, db_task.id, db_task.source_chat_id, item)
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
            if is_media_group_unit:
                for album_item in publish_unit_rows:
                    if should_waiting:
                        mark_item_waiting_or_failed(session, db_task, album_item, fail_msg)
                    else:
                        album_item.status = QueueStatusEnum.failed
                        album_item.fail_reason = fail_msg
                        album_item.next_retry_at = None
                        write_log(session, db_task.id, album_item.message_id, None, "fail", fail_msg)
                    upsert_task_message_state_from_queue_item(session, db_task.id, db_task.source_chat_id, album_item)
            else:
                if should_waiting:
                    mark_item_waiting_or_failed(session, db_task, item, fail_msg)
                else:
                    item.status = QueueStatusEnum.failed
                    item.fail_reason = fail_msg
                    item.next_retry_at = None
                    write_log(session, db_task.id, item.message_id, None, "fail", fail_msg)
                upsert_task_message_state_from_queue_item(session, db_task.id, db_task.source_chat_id, item)
            session.commit()
            if should_waiting:
                if is_media_group_unit:
                    if all(album_item.status == QueueStatusEnum.failed for album_item in publish_unit_rows):
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
        "机器人简介：\n"
        "• 轻量 Telegram 多任务转发机器人\n"
        "• 支持源监听池、范围发布、媒体组整体转发、失败重试\n\n"
        "官方频道：\n"
        "• https://t.me/sosoFlow\n\n"
        "常用功能：\n"
        "• 任务列表：查看并进入任务详情\n"
        "• 新建任务：按提示输入来源ID和目标ID（也可直接转发自动识别）\n"
        "• 获取频道/群ID：转发消息给我自动识别\n\n"
        "常用命令：\n"
        "• /start 新建任务\n"
        "• /tasks 查看任务列表\n"
        "• /task_status 查看当前任务详情\n"
        "• /publish_now 立即发布下一条\n"
        "• /status 查看系统状态"
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
    rows = [[InlineKeyboardButton(f"{'✅' if t.is_completed else ('🟢' if t.enabled else '⏸')} {t.id} {t.name}", callback_data=f"task_view:{t.id}")] for t in page_items]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"tasks_page:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{max_page + 1}", callback_data="noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"tasks_page:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("➕ 新建任务", callback_data="create_task_hint"), InlineKeyboardButton("🔎 搜索任务", callback_data="task_search_hint")])
    rows.append([InlineKeyboardButton("🏠 返回主菜单", callback_data="menu_home")])
    rows.append([InlineKeyboardButton("🧹 清空全部任务", callback_data="tasks_clear_all_ask")])
    return InlineKeyboardMarkup(rows)


def tasks_list_intro_text() -> str:
    return (
        "📋 任务列表\n"
        "• 点击任务按钮可进入任务详情（查看状态、发布、设置、重试）\n"
        "• 可使用“新建任务 / 搜索任务”快速管理任务\n"
        "• ⚠️ 清空全部任务会删除所有任务及相关记录（队列/日志）"
    )


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
    text = build_full_help_text()
    await update.message.reply_text(text)


def build_full_help_text() -> str:
    return (
        "📘 sosoFlow 完整命令帮助\n"
        "说明：`[A]`=admin/super，`[S]`=仅super\n\n"
        "【通用】\n"
        "/start [A] 打开主菜单\n"
        "/help [A] 查看完整帮助\n"
        "/status [A] 查看系统总览统计\n\n"
        "【任务管理】\n"
        "/add_task <name> <source_chat_id> <target_chat_id> [A] 新建任务\n"
        "/tasks [A] 查看任务列表\n"
        "/use_task <task_id> [A] 选择当前任务\n"
        "/task_status [A] 查看当前任务详情\n"
        "/start_task [A] 启动当前任务\n"
        "/pause_task [A] 暂停当前任务\n"
        "/delete_task <task_id> [A] 删除任务（二次确认）\n\n"
        "【发布与队列】\n"
        f"/import_range <start_id> <end_id> [A] 设定发布范围（单次最多 {MAX_IMPORT_RANGE}）\n"
        "/publish_now [A] 立即发布下一条（忽略时段/间隔）\n"
        "/skip <message_id> [A] 手动跳过一条消息\n"
        "/retry_failed [A] 重置 failed/waiting 为 pending\n"
        "/retry_waiting [A] 仅重置 waiting 为 pending\n\n"
        "【任务配置】\n"
        "/set_interval <seconds> [A] 设置发布间隔（>=1）\n"
        "/set_daily_limit <count> [A] 设置日上限（>=0）\n"
        "/set_time_window <HH:MM> <HH:MM> [A] 设置发布时间窗\n"
        "/set_mode copy|forward [A] 设置发布模式\n"
        "/rename_task <new_name> [A] 修改当前任务名称\n"
        "/set_delete_after_success on|off [A] 设置发布后删源\n"
        "/set_auto_capture on|off [A] 设置自动监听入队\n\n"
        "【过滤】\n"
        "/filters [A] 查看当前过滤规则\n"
        "/set_filter <key> on|off [A] 设置布尔过滤项\n"
        "/set_filter min_text_length <number> [A] 设置最短字数\n"
        "/set_filter max_text_length <number> [A] 设置最长字数\n"
        "可用布尔 key：require_photo, require_video, require_text, exclude_links, exclude_no_text, exclude_forwarded, exclude_sticker, exclude_poll\n\n"
        "【监听源】\n"
        "/sources [A] 查看监听源列表（状态+latest）\n"
        "/set_source <source_chat_id> on|off [A] 启停指定源监听\n\n"
        "【诊断与运维】\n"
        "/debug_queue <message_id> [A] 查看当前任务该消息元数据\n"
        "/set_tick <seconds> [S] 设置全局调度 tick（1-3600）\n"
        "/debug_media on|off [S] 开关媒体更新诊断日志\n"
        "/restart [S] 触发重启流程（guide/exit/exec）\n\n"
        "【管理员】\n"
        "/admins [A] 查看管理员列表\n"
        "/add_admin <telegram_user_id> [S] 添加普通管理员\n"
        "/remove_admin <telegram_user_id> [S] 移除普通管理员\n\n"
        "【交互建议】\n"
        "优先用按钮操作；需要输入参数时按提示直接发送文本。\n"
        "所有二次确认均提供取消返回路径。"
    )


@require_admin
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today_start = datetime.combine(now().date(), time.min)
    with SessionLocal() as session:
        tasks = session.scalars(select(Task)).all()
        task_count = len(tasks)
        enabled_count = len([t for t in tasks if t.enabled])
        queue_pending = 0
        pending_group_units = 0
        queue_waiting = 0
        today_published = 0
        today_failed = 0
        failed_total = 0
        for t in tasks:
            if has_task_range(t):
                queue_pending += session.scalar(
                    select(func.count()).select_from(TaskMessageState).where(
                        TaskMessageState.task_id == t.id,
                        TaskMessageState.source_chat_id == t.source_chat_id,
                        TaskMessageState.message_id >= t.range_start_message_id,
                        TaskMessageState.message_id <= t.range_end_message_id,
                        TaskMessageState.status == TaskMessageStatusEnum.pending,
                    )
                ) or 0
                queue_waiting += session.scalar(
                    select(func.count()).select_from(TaskMessageState).where(
                        TaskMessageState.task_id == t.id,
                        TaskMessageState.source_chat_id == t.source_chat_id,
                        TaskMessageState.message_id >= t.range_start_message_id,
                        TaskMessageState.message_id <= t.range_end_message_id,
                        TaskMessageState.status == TaskMessageStatusEnum.waiting,
                    )
                ) or 0
                today_published += session.scalar(
                    select(func.count()).select_from(TaskMessageState).where(
                        TaskMessageState.task_id == t.id,
                        TaskMessageState.source_chat_id == t.source_chat_id,
                        TaskMessageState.message_id >= t.range_start_message_id,
                        TaskMessageState.message_id <= t.range_end_message_id,
                        TaskMessageState.status == TaskMessageStatusEnum.published,
                        TaskMessageState.published_at.is_not(None),
                        TaskMessageState.published_at >= today_start,
                    )
                ) or 0
                today_failed += session.scalar(
                    select(func.count()).select_from(TaskMessageState).where(
                        TaskMessageState.task_id == t.id,
                        TaskMessageState.source_chat_id == t.source_chat_id,
                        TaskMessageState.message_id >= t.range_start_message_id,
                        TaskMessageState.message_id <= t.range_end_message_id,
                        TaskMessageState.status == TaskMessageStatusEnum.failed,
                        TaskMessageState.updated_at >= today_start,
                    )
                ) or 0
                failed_total += session.scalar(
                    select(func.count()).select_from(TaskMessageState).where(
                        TaskMessageState.task_id == t.id,
                        TaskMessageState.source_chat_id == t.source_chat_id,
                        TaskMessageState.message_id >= t.range_start_message_id,
                        TaskMessageState.message_id <= t.range_end_message_id,
                        TaskMessageState.status == TaskMessageStatusEnum.failed,
                    )
                ) or 0
                pending_group_units += session.scalar(
                    select(func.count(func.distinct(SourceMessage.media_group_id))).where(
                        SourceMessage.source_chat_id == t.source_chat_id,
                        SourceMessage.message_id >= t.range_start_message_id,
                        SourceMessage.message_id <= t.range_end_message_id,
                        SourceMessage.state == SourceMessageStateEnum.observed,
                        SourceMessage.media_group_id.is_not(None),
                        SourceMessage.message_id.in_(
                            select(TaskMessageState.message_id).where(
                                TaskMessageState.task_id == t.id,
                                TaskMessageState.source_chat_id == t.source_chat_id,
                                TaskMessageState.status == TaskMessageStatusEnum.pending,
                            )
                        ),
                    )
                ) or 0
                continue
            queue_pending += session.scalar(
                select(func.count()).select_from(QueueItem).where(QueueItem.task_id == t.id, QueueItem.status == QueueStatusEnum.pending)
            ) or 0
            pending_group_units += session.scalar(
                select(func.count(func.distinct(QueueItem.media_group_id))).where(
                    QueueItem.task_id == t.id,
                    QueueItem.status == QueueStatusEnum.pending,
                    QueueItem.media_group_id.is_not(None),
                )
            ) or 0
            queue_waiting += session.scalar(
                select(func.count()).select_from(QueueItem).where(QueueItem.task_id == t.id, QueueItem.status == QueueStatusEnum.waiting)
            ) or 0
            today_published += session.scalar(
                select(func.count()).select_from(QueueItem).where(
                    QueueItem.task_id == t.id,
                    QueueItem.status == QueueStatusEnum.published,
                    QueueItem.published_at >= today_start,
                )
            ) or 0
            today_failed += session.scalar(
                select(func.count()).select_from(QueueItem).where(
                    QueueItem.task_id == t.id,
                    QueueItem.status == QueueStatusEnum.failed,
                    QueueItem.updated_at >= today_start,
                )
            ) or 0
            failed_total += session.scalar(
                select(func.count()).select_from(QueueItem).where(
                    QueueItem.task_id == t.id,
                    QueueItem.status == QueueStatusEnum.failed,
                )
            ) or 0
        tick = session.get(GlobalSetting, 1).tick_seconds
    await update.message.reply_text(
        f"📊 系统状态\n"
        f"任务总数: {task_count}\n"
        f"运行中: {enabled_count}\n"
        f"待发布: {queue_pending}\n"
        f"待发布媒体组: {pending_group_units}\n"
        f"等待重试: {queue_waiting}\n"
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
    await update.message.reply_text(tasks_list_intro_text(), reply_markup=build_tasks_list_keyboard(tasks, page=0))


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
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("确认删除", callback_data=f"task_delete_yes:{task_id}")],
            [InlineKeyboardButton("取消并返回任务列表", callback_data="tasks_list")],
        ]
    )
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
    with SessionLocal() as session:
        db_task = session.get(Task, task.id)
        if not db_task:
            await update.message.reply_text("任务不存在")
            return
        registry = session.scalar(select(SourceRegistry).where(SourceRegistry.source_chat_id == db_task.source_chat_id))
        latest_seen = registry.latest_seen_message_id if registry and registry.latest_seen_message_id is not None else None
        if latest_seen is not None and end_id > latest_seen:
            end_id = latest_seen
        if start_id > end_id:
            await update.message.reply_text("范围无可导入消息（当前源最新消息ID更小）")
            return
        src_rows = session.scalars(
            select(SourceMessage).where(
                SourceMessage.source_chat_id == db_task.source_chat_id,
                SourceMessage.state == SourceMessageStateEnum.observed,
                SourceMessage.message_id >= start_id,
                SourceMessage.message_id <= end_id,
            ).order_by(SourceMessage.message_id.asc())
        ).all()
        db_task.range_start_message_id = start_id
        db_task.range_end_message_id = end_id
        db_task.is_completed = False
        db_task.completed_at = None
        write_config_log(session, db_task.id, f"设置发布范围 {start_id}-{end_id}")
        session.commit()
    observed_count = len(src_rows)
    missing_known = (end_id - start_id + 1) - observed_count
    await update.message.reply_text(
        f"✅ 导入完成\n"
        f"范围已设定: {start_id}-{end_id}\n"
        f"监听池已观测: {observed_count}\n"
        f"未观测跳过: {missing_known}"
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
        db_task = session.get(Task, task.id)
        item = session.scalar(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.message_id == message_id))
        if not item:
            await update.message.reply_text("消息不在当前任务队列")
            return
        item.status = QueueStatusEnum.skipped
        item.fail_reason = None
        item.next_retry_at = None
        if has_task_range(db_task):
            tms = session.scalar(
                select(TaskMessageState).where(
                    TaskMessageState.task_id == db_task.id,
                    TaskMessageState.source_chat_id == db_task.source_chat_id,
                    TaskMessageState.message_id == message_id,
                )
            )
            if tms:
                tms.status = TaskMessageStatusEnum.skipped
                tms.fail_reason = None
                tms.next_retry_at = None
            else:
                session.add(
                    TaskMessageState(
                        task_id=db_task.id,
                        source_chat_id=db_task.source_chat_id,
                        message_id=message_id,
                        status=TaskMessageStatusEnum.skipped,
                    )
                )
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
        db_task = session.get(Task, task.id)
        if has_task_range(db_task):
            tms_rows = session.scalars(
                select(TaskMessageState).where(
                    TaskMessageState.task_id == db_task.id,
                    TaskMessageState.source_chat_id == db_task.source_chat_id,
                    TaskMessageState.message_id >= db_task.range_start_message_id,
                    TaskMessageState.message_id <= db_task.range_end_message_id,
                    TaskMessageState.status.in_([TaskMessageStatusEnum.failed, TaskMessageStatusEnum.waiting]),
                )
            ).all()
            for row in tms_rows:
                row.status = TaskMessageStatusEnum.pending
                row.fail_reason = None
                row.next_retry_at = None
            base_rows = session.scalars(
                select(QueueItem).where(
                    QueueItem.task_id == db_task.id,
                    QueueItem.message_id >= db_task.range_start_message_id,
                    QueueItem.message_id <= db_task.range_end_message_id,
                    QueueItem.status.in_([QueueStatusEnum.failed, QueueStatusEnum.waiting]),
                )
            ).all()
            rows = expand_rows_by_media_group(session, base_rows)
            for row in rows:
                row.status = QueueStatusEnum.pending
                row.fail_reason = None
                row.next_retry_at = None
            session.commit()
            await update.message.reply_text(f"✅ 已重置失败/等待为待发布: {len(tms_rows)}")
            return
        base_rows = session.scalars(
            select(QueueItem).where(
                QueueItem.task_id == task.id,
                QueueItem.status.in_([QueueStatusEnum.failed, QueueStatusEnum.waiting]),
            )
        ).all()
        rows = expand_rows_by_media_group(session, base_rows)
        for row in rows:
            row.status = QueueStatusEnum.pending
            row.fail_reason = None
            row.next_retry_at = None
        session.commit()
    await update.message.reply_text(f"✅ 已重置失败/等待为待发布: {len(rows)}")


@require_admin
async def retry_waiting_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    with SessionLocal() as session:
        db_task = session.get(Task, task.id)
        if has_task_range(db_task):
            tms_rows = session.scalars(
                select(TaskMessageState).where(
                    TaskMessageState.task_id == db_task.id,
                    TaskMessageState.source_chat_id == db_task.source_chat_id,
                    TaskMessageState.message_id >= db_task.range_start_message_id,
                    TaskMessageState.message_id <= db_task.range_end_message_id,
                    TaskMessageState.status == TaskMessageStatusEnum.waiting,
                )
            ).all()
            for row in tms_rows:
                row.status = TaskMessageStatusEnum.pending
                row.fail_reason = None
                row.next_retry_at = None
            base_rows = session.scalars(
                select(QueueItem).where(
                    QueueItem.task_id == db_task.id,
                    QueueItem.message_id >= db_task.range_start_message_id,
                    QueueItem.message_id <= db_task.range_end_message_id,
                    QueueItem.status == QueueStatusEnum.waiting,
                )
            ).all()
            rows = expand_rows_by_media_group(session, base_rows)
            for row in rows:
                row.status = QueueStatusEnum.pending
                row.fail_reason = None
                row.next_retry_at = None
            session.commit()
            await update.message.reply_text(f"✅ 已重置等待为待发布: {len(tms_rows)}")
            return
        base_rows = session.scalars(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.status == QueueStatusEnum.waiting)).all()
        rows = expand_rows_by_media_group(session, base_rows)
        for row in rows:
            row.status = QueueStatusEnum.pending
            row.fail_reason = None
            row.next_retry_at = None
        session.commit()
    await update.message.reply_text(f"✅ 已重置等待为待发布: {len(rows)}")


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
        with SessionLocal() as session:
            write_config_log(session, task_id, f"设置间隔 interval_seconds={seconds}")
            session.commit()
        with SessionLocal() as session:
            setting = session.get(GlobalSetting, 1)
            tick_seconds = setting.tick_seconds if setting else 60
        if seconds < tick_seconds:
            await update.message.reply_text(
                f"✅ 任务 {task_id} interval={seconds}\n⚠️ 当前 tick_seconds={tick_seconds}，实际触发频率不会快于 tick。"
            )
        else:
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
        with SessionLocal() as session:
            write_config_log(session, task_id, f"设置日上限 daily_limit={count}")
            session.commit()
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
        write_config_log(session, db_task.id, f"设置时段 {start}-{end}")
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
        with SessionLocal() as session:
            write_config_log(session, task_id, f"设置模式 mode={mode}")
            session.commit()
        await update.message.reply_text(f"✅ mode={mode}")


@require_admin
async def rename_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    if len(context.args) < 1:
        await update.message.reply_text("用法: /rename_task <new_name>")
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("任务名称不能为空")
        return
    if len(name) > 200:
        await update.message.reply_text("任务名称过长（最多200字符）")
        return
    with SessionLocal() as session:
        db_task = session.get(Task, task.id)
        db_task.name = name
        write_config_log(session, db_task.id, f"命令修改任务名称 name={name}")
        session.commit()
    await update.message.reply_text(f"✅ 任务名称已更新为：{name}")


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
        with SessionLocal() as session:
            write_config_log(session, task_id, f"设置{name}={'on' if value else 'off'}")
            session.commit()
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
    if not 1 <= seconds <= 3600:
        await update.message.reply_text("tick_seconds 允许范围 1-3600")
        return
    with SessionLocal() as session:
        setting = session.get(GlobalSetting, 1)
        setting.tick_seconds = seconds
        session.commit()
    scheduler.reschedule_job("publish_tick", trigger="interval", seconds=seconds)
    await update.message.reply_text(f"✅ tick_seconds={seconds}")


@require_super
async def debug_media_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("用法: /debug_media on|off")
        return
    try:
        enabled = parse_on_off(context.args[0], "debug_media")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /debug_media on|off")
        return
    with SessionLocal() as session:
        setting = session.get(GlobalSetting, 1)
        if not setting:
            setting = GlobalSetting(id=1, tick_seconds=60, debug_media_updates=enabled)
            session.add(setting)
        else:
            setting.debug_media_updates = enabled
        session.commit()
    await update.message.reply_text(f"✅ debug_media_updates={'on' if enabled else 'off'}")


@require_admin
async def debug_queue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = get_current_task(update.effective_user.id)
    if not task:
        await update.message.reply_text("请先 /use_task <task_id>")
        return
    if len(context.args) != 1:
        await update.message.reply_text("用法: /debug_queue <message_id>")
        return
    try:
        message_id = parse_int(context.args[0], "message_id")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /debug_queue <message_id>")
        return
    with SessionLocal() as session:
        row = session.scalar(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.message_id == message_id))
        if not row:
            await update.message.reply_text("未找到该消息ID的队列记录")
            return
        await update.message.reply_text(
            "🔎 debug_queue\n"
            f"task_id={row.task_id}\n"
            f"message_id={row.message_id}\n"
            f"media_group_id={row.media_group_id or 'None'}\n"
            f"file_id_exists={bool(row.file_id)}\n"
            f"状态={row.status.value}\n"
            f"message_type={row.message_type or 'unknown'}\n"
            f"caption_exists={bool(row.caption)}\n"
            f"retry_count={row.retry_count}\n"
            f"next_retry_at={row.next_retry_at or 'None'}"
        )


@require_admin
async def sources_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with SessionLocal() as session:
        rows = session.scalars(select(SourceRegistry).order_by(SourceRegistry.updated_at.desc(), SourceRegistry.id.desc())).all()
    if not rows:
        await update.message.reply_text("📡 监听源列表为空")
        return
    lines = ["📡 监听源列表"]
    for row in rows[:50]:
        latest = row.latest_seen_message_id if row.latest_seen_message_id is not None else "-"
        source_name = await resolve_chat_display_name(context.application, row.source_chat_id)
        source_line = f"{row.source_chat_id}" if not source_name else f"{row.source_chat_id}（{source_name}）"
        lines.append(f"{'🟢' if row.enabled else '⏸'} {source_line} latest={latest}")
    await update.message.reply_text("\n".join(lines))


@require_admin
async def set_source_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("用法: /set_source <source_chat_id> on|off")
        return
    try:
        source_chat_id = parse_int(context.args[0], "source_chat_id")
        enabled = parse_on_off(context.args[1], "set_source")
    except ValueError as exc:
        await update.message.reply_text(f"参数错误：{exc}\n用法: /set_source <source_chat_id> on|off")
        return
    with SessionLocal() as session:
        row = session.scalar(select(SourceRegistry).where(SourceRegistry.source_chat_id == source_chat_id))
        if not row:
            row = SourceRegistry(source_chat_id=source_chat_id, enabled=enabled)
            session.add(row)
        else:
            row.enabled = enabled
        session.commit()
    await update.message.reply_text(f"✅ source {source_chat_id} 已设为 {'on' if enabled else 'off'}")


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
        with SessionLocal() as session:
            db_task = session.get(Task, task_id)
            if db_task:
                db_task.is_completed = False
                db_task.completed_at = None
                write_config_log(session, task_id, "手动启动任务")
                session.commit()
        await update.message.reply_text("✅ 任务已启动")


@require_admin
async def pause_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = await set_current_task_simple(update, context, "enabled", False)
    if task_id:
        with SessionLocal() as session:
            write_config_log(session, task_id, "手动暂停任务")
            session.commit()
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
        "include_keywords_enabled",
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
        await edit_query_message_text_or_caption(query, tasks_list_intro_text(), reply_markup=build_tasks_list_keyboard(tasks, page=0))
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
        await edit_query_message_text_or_caption(query, tasks_list_intro_text(), reply_markup=build_tasks_list_keyboard(tasks, page=page))
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
    if data == "tasks_clear_all_ask":
        await query.message.reply_text(
            "⚠️ 二次确认清空全部任务（会清除任务、队列、发布日志与任务选择状态）",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("确认清空全部任务", callback_data="tasks_clear_all_yes")],
                    [InlineKeyboardButton("取消并返回任务列表", callback_data="tasks_list")],
                ]
            ),
        )
        return
    if data == "tasks_clear_all_yes":
        try:
            with SessionLocal() as session:
                queue_rows = session.scalars(select(QueueItem)).all()
                for row in queue_rows:
                    session.delete(row)
                log_rows = session.scalars(select(PublishLog)).all()
                for row in log_rows:
                    session.delete(row)
                filter_rows = session.scalars(select(TaskFilter)).all()
                for row in filter_rows:
                    session.delete(row)
                task_rows = session.scalars(select(Task)).all()
                for row in task_rows:
                    session.delete(row)
                state_rows = session.scalars(select(UserState)).all()
                for row in state_rows:
                    row.current_task_id = None
                session.commit()
            await edit_query_message_text_or_caption(query, "🧹 已清空全部任务", reply_markup=simple_back_home_keyboard())
        except Exception as exc:
            logger.exception("tasks_clear_all_yes failed err=%s", exc)
            await query.message.reply_text("⚠️ 清空失败，请稍后重试")
        return
    if data == "admins_list":
        with SessionLocal() as session:
            rows = session.scalars(select(Admin)).all()
        await query.message.reply_text(
            "👤 管理员\n" + "\n".join([f"{x.telegram_user_id} ({x.role.value})" for x in rows]),
        )
        return
    if data == "help_menu":
        await query.message.reply_text(build_full_help_text())
        return
    if ":" not in data:
        return
    parts = data.split(":")
    action = parts[0]
    raw_task_id = parts[1] if len(parts) > 1 else ""
    try:
        task_id = parse_int(raw_task_id, "task_id")
    except ValueError:
        await edit_query_message_text_or_caption(query, "回调参数错误")
        return
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if not task:
            await edit_query_message_text_or_caption(query, "任务不存在")
            return
        if action == "task_view":
            source_name = await resolve_chat_display_name(context.application, task.source_chat_id)
            target_name = await resolve_chat_display_name(context.application, task.target_chat_id)
            await edit_query_message_text_or_caption(
                query,
                build_task_detail_text(session, task, source_name=source_name, target_name=target_name),
                reply_markup=task_detail_keyboard(task_id),
            )
            return
        if action == "task_start":
            task.enabled = True
            task.is_completed = False
            task.completed_at = None
            write_config_log(session, task.id, "按钮启动任务")
            session.commit()
            source_name = await resolve_chat_display_name(context.application, task.source_chat_id)
            target_name = await resolve_chat_display_name(context.application, task.target_chat_id)
            await edit_query_message_text_or_caption(
                query,
                build_task_detail_text(session, task, source_name=source_name, target_name=target_name),
                reply_markup=task_detail_keyboard(task_id),
            )
            await query.message.reply_text("✅ 已启动任务")
            return
        elif action == "task_pause":
            task.enabled = False
            write_config_log(session, task.id, "按钮暂停任务")
            session.commit()
            source_name = await resolve_chat_display_name(context.application, task.source_chat_id)
            target_name = await resolve_chat_display_name(context.application, task.target_chat_id)
            await edit_query_message_text_or_caption(
                query,
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
            if has_task_range(task):
                tms_rows = session.scalars(
                    select(TaskMessageState).where(
                        TaskMessageState.task_id == task.id,
                        TaskMessageState.source_chat_id == task.source_chat_id,
                        TaskMessageState.message_id >= task.range_start_message_id,
                        TaskMessageState.message_id <= task.range_end_message_id,
                        TaskMessageState.status.in_([TaskMessageStatusEnum.failed, TaskMessageStatusEnum.waiting]),
                    )
                ).all()
                for row in tms_rows:
                    row.status = TaskMessageStatusEnum.pending
                    row.fail_reason = None
                    row.next_retry_at = None
                base_rows = session.scalars(
                    select(QueueItem).where(
                        QueueItem.task_id == task.id,
                        QueueItem.message_id >= task.range_start_message_id,
                        QueueItem.message_id <= task.range_end_message_id,
                        QueueItem.status.in_([QueueStatusEnum.failed, QueueStatusEnum.waiting]),
                    )
                ).all()
                rows = expand_rows_by_media_group(session, base_rows)
                for row in rows:
                    row.status = QueueStatusEnum.pending
                    row.fail_reason = None
                    row.next_retry_at = None
                session.commit()
                await query.message.reply_text(f"✅ 已将 failed/waiting 重置为待发布（{len(tms_rows)}）")
                return
            base_rows = session.scalars(
                select(QueueItem).where(
                    QueueItem.task_id == task.id,
                    QueueItem.status.in_([QueueStatusEnum.failed, QueueStatusEnum.waiting]),
                )
            ).all()
            rows = expand_rows_by_media_group(session, base_rows)
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
            source_name = await resolve_chat_display_name(context.application, task.source_chat_id)
            target_name = await resolve_chat_display_name(context.application, task.target_chat_id)
            await edit_query_message_text_or_caption(
                query,
                build_task_detail_text(session, task, source_name=source_name, target_name=target_name),
                reply_markup=task_detail_keyboard(task.id),
            )
            return
        elif action == "task_recent_logs":
            rows = session.scalars(
                select(PublishLog).where(PublishLog.task_id == task.id).order_by(PublishLog.created_at.desc()).limit(5)
            ).all()
            if not rows:
                await query.message.reply_text("🧾 最近发布日志（5条）\n暂无记录")
                return
            lines = []
            for row in rows:
                ts = row.created_at.strftime("%m-%d %H:%M:%S") if row.created_at else "-"
                src = row.source_message_id if row.source_message_id is not None else "-"
                tgt = row.target_message_id if row.target_message_id is not None else "-"
                msg = (row.message or "").replace("\n", " ")
                if len(msg) > 80:
                    msg = msg[:80] + "..."
                lines.append(f"[{ts}] {row.action} src={src} tgt={tgt} {msg}")
            await query.message.reply_text("🧾 最近发布日志（5条）\n" + "\n".join(lines))
            return
        elif action == "task_toggle_mode":
            task.mode = TaskModeEnum.forward if task.mode == TaskModeEnum.copy else TaskModeEnum.copy
            write_config_log(session, task.id, f"按钮切换模式 mode={task.mode.value}")
            session.commit()
            source_name = await resolve_chat_display_name(context.application, task.source_chat_id)
            target_name = await resolve_chat_display_name(context.application, task.target_chat_id)
            await edit_query_message_text_or_caption(query, build_task_detail_text(session, task, source_name=source_name, target_name=target_name), reply_markup=task_detail_keyboard(task.id))
            await query.message.reply_text(f"✅ 模式已切换为 {mode_label(task.mode)}")
            return
        elif action == "task_edit_name":
            context.user_data["pending_input_action"] = "edit_task_name"
            context.user_data["pending_task_id"] = task.id
            await query.message.reply_text("✍️ 请输入新的任务名称（最多200字符）")
            return
        elif action == "task_toggle_auto_capture":
            task.auto_capture_enabled = not task.auto_capture_enabled
            write_config_log(session, task.id, f"按钮设置自动监听 auto_capture={'on' if task.auto_capture_enabled else 'off'}")
            session.commit()
            source_name = await resolve_chat_display_name(context.application, task.source_chat_id)
            target_name = await resolve_chat_display_name(context.application, task.target_chat_id)
            await edit_query_message_text_or_caption(query, build_task_detail_text(session, task, source_name=source_name, target_name=target_name), reply_markup=task_detail_keyboard(task.id))
            await query.message.reply_text(f"✅ 任务接收源消息已设为 {bool_cn(task.auto_capture_enabled)}")
            return
        elif action == "task_toggle_delete":
            task.delete_after_success = not task.delete_after_success
            write_config_log(session, task.id, f"按钮设置发布后删源 delete_after_success={'on' if task.delete_after_success else 'off'}")
            session.commit()
            source_name = await resolve_chat_display_name(context.application, task.source_chat_id)
            target_name = await resolve_chat_display_name(context.application, task.target_chat_id)
            await edit_query_message_text_or_caption(query, build_task_detail_text(session, task, source_name=source_name, target_name=target_name), reply_markup=task_detail_keyboard(task.id))
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
            await edit_query_message_text_or_caption(
                query,
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
                "include_keywords_enabled",
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
        elif action == "task_filter_keywords_input":
            context.user_data["pending_input_action"] = "set_include_keywords"
            context.user_data["pending_task_id"] = task.id
            task_filter = ensure_task_filter(session, task.id)
            current = parse_include_keywords(task_filter.include_keywords)
            preview = "、".join(current[:8]) if current else "无"
            await query.message.reply_text(
                "✍️ 请输入包含关键词，多个关键词用逗号或换行分隔。\n"
                "示例：抽奖,活动,新品\n"
                f"当前关键词：{preview}"
            )
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
            await query.message.reply_text(
                f"⚠️ 二次确认删除任务 {task.id}",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("确认删除", callback_data=f"task_delete_yes:{task.id}")],
                        [InlineKeyboardButton("取消并返回任务详情", callback_data=f"task_view:{task.id}")],
                    ]
                ),
            )
            return
        elif action == "task_reset_ask":
            await query.message.reply_text(
                f"⚠️ 二次确认重置任务 {task.id}（清空队列与日志，重置设置并暂停）",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("确认重置任务", callback_data=f"task_reset_yes:{task.id}")],
                        [InlineKeyboardButton("取消并返回任务详情", callback_data=f"task_view:{task.id}")],
                    ]
                ),
            )
            return
        elif action == "task_reset_yes":
            try:
                queue_rows = session.scalars(select(QueueItem).where(QueueItem.task_id == task.id)).all()
                for row in queue_rows:
                    session.delete(row)
                log_rows = session.scalars(select(PublishLog).where(PublishLog.task_id == task.id)).all()
                for row in log_rows:
                    session.delete(row)
                task_filter = ensure_task_filter(session, task.id)
                task_filter.require_photo = False
                task_filter.require_video = False
                task_filter.require_text = False
                task_filter.exclude_links = False
                task_filter.exclude_no_text = False
                task_filter.exclude_forwarded = False
                task_filter.exclude_sticker = False
                task_filter.exclude_poll = False
                task_filter.min_text_length = None
                task_filter.max_text_length = None
                task.mode = TaskModeEnum.copy
                task.interval_seconds = 1800
                task.daily_limit = 100
                task.active_start_time = "00:00"
                task.active_end_time = "23:59"
                task.enabled = False
                task.auto_capture_enabled = True
                task.delete_after_success = False
                task.last_published_at = None
                session.commit()
            except Exception as exc:
                session.rollback()
                logger.exception("task_reset_yes failed task_id=%s err=%s", task.id, exc)
                await query.message.reply_text("⚠️ 重置失败，请稍后重试")
                return
            source_name = await resolve_chat_display_name(context.application, task.source_chat_id)
            target_name = await resolve_chat_display_name(context.application, task.target_chat_id)
            await edit_query_message_text_or_caption(
                query,
                build_task_detail_text(session, task, source_name=source_name, target_name=target_name),
                reply_markup=task_detail_keyboard(task.id),
            )
            await query.message.reply_text(f"✅ 任务 {task.id} 已重置并暂停")
            return
        elif action == "task_delete_yes":
            try:
                # 先删除关联数据，避免 PostgreSQL 外键约束导致“确认删除无响应”
                queue_rows = session.scalars(select(QueueItem).where(QueueItem.task_id == task.id)).all()
                for row in queue_rows:
                    session.delete(row)
                log_rows = session.scalars(select(PublishLog).where(PublishLog.task_id == task.id)).all()
                for row in log_rows:
                    session.delete(row)
                session.delete(task)
                session.commit()
            except Exception as exc:
                session.rollback()
                logger.exception("task_delete_yes failed task_id=%s err=%s", task.id, exc)
                await query.message.reply_text("⚠️ 删除失败，请稍后重试")
                return
            tasks = session.scalars(select(Task).order_by(Task.id.asc())).all()
            if not tasks:
                await edit_query_message_text_or_caption(query, "🗑 已删除任务，当前暂无任务", reply_markup=simple_back_home_keyboard())
            else:
                await edit_query_message_text_or_caption(query, "🗑 已删除任务，返回任务列表", reply_markup=build_tasks_list_keyboard(tasks, page=0))
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
        if source_chat_id == target_chat_id:
            await msg.reply_text("⚠️ 来源ID和目标ID不能相同，请重新输入目标频道/群组ID。")
            return True
        with SessionLocal() as session:
            task, created = get_or_create_task_by_pair(session, source_chat_id, target_chat_id)
            state = session.scalar(select(UserState).where(UserState.user_id == update.effective_user.id))
            if not state:
                state = UserState(user_id=update.effective_user.id, current_task_id=task.id)
                session.add(state)
            else:
                state.current_task_id = task.id
            session.commit()
            context.user_data.pop("pending_input_action", None)
            context.user_data.pop("pending_task_source_chat_id", None)
            kb = task_detail_keyboard(task.id)
            if created:
                await msg.reply_text(
                    f"✅ 任务创建成功并已选中\nID={task.id}\n名称: {task.name}\n源: {source_chat_id}\n目标: {target_chat_id}",
                    reply_markup=kb,
                )
            else:
                await msg.reply_text(
                    f"✅ 已使用现有任务并选中\nID={task.id}\n名称: {task.name}\n源: {source_chat_id}\n目标: {target_chat_id}",
                    reply_markup=kb,
                )
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
        kb = [[InlineKeyboardButton(f"{'✅' if t.is_completed else ('🟢' if t.enabled else '⏸')} {t.id} {t.name}", callback_data=f"task_view:{t.id}")] for t in matched[:20]]
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
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if not task:
                await msg.reply_text("任务不存在，请重新选择任务")
                context.user_data.pop("pending_input_action", None)
                context.user_data.pop("pending_task_id", None)
                return True
            registry = session.scalar(select(SourceRegistry).where(SourceRegistry.source_chat_id == task.source_chat_id))
            latest_seen = registry.latest_seen_message_id if registry and registry.latest_seen_message_id is not None else None
            if latest_seen is not None and end_id > latest_seen:
                end_id = latest_seen
            if start_id > end_id:
                await msg.reply_text("范围无可导入消息（当前源最新消息ID更小）")
                return True
            src_rows = session.scalars(
                select(SourceMessage).where(
                    SourceMessage.source_chat_id == task.source_chat_id,
                    SourceMessage.state == SourceMessageStateEnum.observed,
                    SourceMessage.message_id >= start_id,
                    SourceMessage.message_id <= end_id,
                ).order_by(SourceMessage.message_id.asc())
            ).all()
            task.range_start_message_id = start_id
            task.range_end_message_id = end_id
            task.is_completed = False
            task.completed_at = None
            write_config_log(session, task.id, f"输入设置发布范围 {start_id}-{end_id}")
            session.commit()
        observed_count = len(src_rows)
        missing_known = (end_id - start_id + 1) - observed_count
        await msg.reply_text(
            f"✅ 导入完成\n"
            f"范围已设定: {start_id}-{end_id}\n"
            f"监听池已观测: {observed_count}\n"
            f"未观测跳过: {missing_known}"
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
            write_config_log(session, task.id, f"输入设置间隔 interval_seconds={seconds}")
            session.commit()
            session.refresh(task)
            setting = session.get(GlobalSetting, 1)
            tick_seconds = setting.tick_seconds if setting else 60
            if seconds < tick_seconds:
                reply_text = f"✅ 间隔已更新为 {seconds} 秒\n⚠️ 当前 tick_seconds={tick_seconds}，实际触发频率不会快于 tick。"
            else:
                reply_text = f"✅ 间隔已更新为 {seconds} 秒"
            await msg.reply_text(reply_text, reply_markup=task_detail_keyboard(task.id))
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
            write_config_log(session, task.id, f"输入设置日上限 daily_limit={daily_limit}")
            session.commit()
            session.refresh(task)
            await msg.reply_text(
                f"✅ 日上限已更新为 {daily_limit}",
                reply_markup=task_detail_keyboard(task.id),
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
            write_config_log(session, task.id, f"输入设置时段 {start_time}-{end_time}")
            session.commit()
            session.refresh(task)
            await msg.reply_text(
                f"✅ 时段已更新为 {start_time}-{end_time}",
                reply_markup=task_detail_keyboard(task.id),
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
            write_config_log(session, task.id, f"输入设置来源ID source_chat_id={source_chat_id}")
            session.commit()
            session.refresh(task)
            await msg.reply_text(
                f"✅ 来源ID已更新为 {source_chat_id}",
                reply_markup=task_detail_keyboard(task.id),
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
            write_config_log(session, task.id, f"输入设置目标ID target_chat_id={target_chat_id}")
            session.commit()
            session.refresh(task)
            await msg.reply_text(
                f"✅ 目标ID已更新为 {target_chat_id}",
                reply_markup=task_detail_keyboard(task.id),
            )
        context.user_data.pop("pending_input_action", None)
        context.user_data.pop("pending_task_id", None)
        return True
    if action == "edit_task_name":
        task_id = context.user_data.get("pending_task_id")
        if not task_id:
            await msg.reply_text("未找到任务，请重新进入任务设置")
            context.user_data.pop("pending_input_action", None)
            return True
        name = text.strip()
        if not name:
            await msg.reply_text("⚠️ 任务名称不能为空，请重新输入")
            return True
        if len(name) > 200:
            await msg.reply_text("⚠️ 任务名称过长（最多200字符），请重新输入")
            return True
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if not task:
                await msg.reply_text("任务不存在")
                context.user_data.pop("pending_input_action", None)
                context.user_data.pop("pending_task_id", None)
                return True
            task.name = name
            write_config_log(session, task.id, f"输入修改任务名称 name={name}")
            session.commit()
            session.refresh(task)
            await msg.reply_text(f"✅ 任务名称已更新为：{name}", reply_markup=task_detail_keyboard(task.id))
        context.user_data.pop("pending_input_action", None)
        context.user_data.pop("pending_task_id", None)
        return True
    if action == "set_include_keywords":
        task_id = context.user_data.get("pending_task_id")
        if not task_id:
            await msg.reply_text("未找到任务，请重新进入过滤设置")
            context.user_data.pop("pending_input_action", None)
            return True
        keywords = parse_include_keywords(text)
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if not task:
                await msg.reply_text("任务不存在")
                context.user_data.pop("pending_input_action", None)
                context.user_data.pop("pending_task_id", None)
                return True
            task_filter = ensure_task_filter(session, task.id)
            task_filter.include_keywords = "\n".join(keywords) if keywords else None
            write_config_log(session, task.id, f"输入设置包含关键词 count={len(keywords)}")
            session.commit()
            session.refresh(task_filter)
            await msg.reply_text(
                f"✅ 包含关键词已更新（{len(keywords)}）",
                reply_markup=task_filters_keyboard(task.id, task_filter),
            )
        context.user_data.pop("pending_input_action", None)
        context.user_data.pop("pending_task_id", None)
        return True
    return False


async def capture_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_type, typed_msg = classify_update_message(update)
    with SessionLocal() as session:
        debug_media = is_debug_media_enabled(session)
    if debug_media:
        logger.info("raw_update_type=%s", update_type)
        if update_type in {"message", "channel_post", "edited_message", "edited_channel_post"}:
            log_debug_media_update(update_type, typed_msg)
    msg = typed_msg or update.effective_message
    if not msg or not msg.chat:
        return
    # 频道消息(channel_post)没有 effective_user，不能走 require_admin 拦截。
    # 仅私聊入口需要管理员校验，频道/群消息按 source_chat_id + auto_capture 规则入队。
    if msg.chat.type == "private":
        uid = update.effective_user.id if update.effective_user else 0
        if not is_admin(uid):
            await msg.reply_text(denied_text())
            return
    if msg.chat.type == "private" and msg.text:
        text = msg.text.strip()
        if text == "📋 任务列表":
            context.user_data.pop("pending_input_action", None)
            context.user_data.pop("pending_task_id", None)
            context.user_data.pop("pending_task_source_chat_id", None)
            context.user_data.pop("pending_window_start", None)
            context.user_data.pop("pending_forward_media_group_id", None)
            with SessionLocal() as session:
                tasks = session.scalars(select(Task).order_by(Task.id.asc())).all()
            if not tasks:
                await msg.reply_text("ℹ️ 暂无任务")
            else:
                await msg.reply_text(tasks_list_intro_text(), reply_markup=build_tasks_list_keyboard(tasks, page=0))
            return
        if text == "➕ 新建任务":
            context.user_data.pop("pending_task_id", None)
            context.user_data.pop("pending_task_source_chat_id", None)
            context.user_data.pop("pending_window_start", None)
            context.user_data.pop("pending_forward_media_group_id", None)
            context.user_data["pending_input_action"] = "create_task_source"
            await msg.reply_text("✍️ 请输入来源频道/群组ID\n示例：-1001111111111\n💡 可转发来源频道/群消息给机器人，点击识别出的数字复制后发送确认。")
            return
    handled = await handle_pending_input(update, context)
    if handled:
        return
    forward_chat_id = extract_forward_chat_id(msg)
    if forward_chat_id is not None:
        pending_action = context.user_data.get("pending_input_action")
        if pending_action in {"create_task_source", "create_task_target", "edit_task_source", "edit_task_target"} and msg.media_group_id:
            last_group_id = context.user_data.get("pending_forward_media_group_id")
            if last_group_id == msg.media_group_id:
                return
            context.user_data["pending_forward_media_group_id"] = msg.media_group_id
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
            if source_chat_id == target_chat_id:
                warn_key = f"{source_chat_id}:{target_chat_id}:{msg.media_group_id or msg.message_id}"
                if context.user_data.get("last_same_pair_warn_key") != warn_key:
                    context.user_data["last_same_pair_warn_key"] = warn_key
                    await msg.reply_text("⚠️ 识别到来源ID与目标ID相同，请转发目标频道/群组消息或直接输入目标ID。")
                return
            with SessionLocal() as session:
                task, created = get_or_create_task_by_pair(session, source_chat_id, target_chat_id)
                state = session.scalar(select(UserState).where(UserState.user_id == update.effective_user.id))
                if not state:
                    state = UserState(user_id=update.effective_user.id, current_task_id=task.id)
                    session.add(state)
                else:
                    state.current_task_id = task.id
                session.commit()
            context.user_data.pop("pending_input_action", None)
            context.user_data.pop("pending_task_source_chat_id", None)
            context.user_data.pop("last_same_pair_warn_key", None)
            context.user_data.pop("pending_forward_media_group_id", None)
            if created:
                await msg.reply_text(
                    f"✅ 已自动确认目标ID并创建任务\nID={task.id}\n名称: {task.name}\n源: {source_chat_id}\n目标: {target_chat_id}",
                    reply_markup=task_detail_keyboard(task.id),
                )
            else:
                await msg.reply_text(
                    f"✅ 已自动确认目标ID并选中现有任务\nID={task.id}\n名称: {task.name}\n源: {source_chat_id}\n目标: {target_chat_id}",
                    reply_markup=task_detail_keyboard(task.id),
                )
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
                    reply_markup=task_detail_keyboard(task.id),
                )
            context.user_data.pop("pending_input_action", None)
            context.user_data.pop("pending_task_id", None)
            context.user_data.pop("pending_forward_media_group_id", None)
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
                    reply_markup=task_detail_keyboard(task.id),
                )
            context.user_data.pop("pending_input_action", None)
            context.user_data.pop("pending_task_id", None)
            context.user_data.pop("pending_forward_media_group_id", None)
            return
        await msg.reply_text(
            f"📌 已识别转发来源ID：`{forward_chat_id}`\n"
            "可直接用于新建任务的来源或目标ID。\n"
            "💡 点击数字可复制，然后发送给我确认；也可以在输入步骤直接转发，我会自动确认。",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    if msg.chat.type == "private" and context.user_data.get("pending_input_action") == "create_task_target" and not msg.text:
        await msg.reply_text("⚠️ 未识别到目标ID。请直接输入目标频道/群组ID，或转发一条来自目标频道/群组的消息。")
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
        if msg.chat.type != "private" and not is_source_enabled(session, source_chat_id):
            return
        upsert_source_registry_and_message(
            session=session,
            source_chat_id=source_chat_id,
            message_id=msg.message_id,
            message_type=message_type,
            file_id=file_id,
            caption=msg.caption,
            text_value=text_value,
            has_photo=bool(msg.photo),
            has_video=bool(msg.video),
            has_document=bool(msg.document),
            is_forwarded=bool(msg.forward_origin),
            media_group_id=msg.media_group_id,
        )
        tasks = session.scalars(select(Task).where(Task.source_chat_id == source_chat_id, Task.auto_capture_enabled.is_(True))).all()
        if not tasks and msg.chat.type != "private":
            ensure_auto_source_capture_task(session, source_chat_id)
            tasks = session.scalars(select(Task).where(Task.source_chat_id == source_chat_id, Task.auto_capture_enabled.is_(True))).all()
        for task in tasks:
            upsert_queue_item_from_capture(
                session=session,
                task_id=task.id,
                message_id=msg.message_id,
                message_type=message_type,
                file_id=file_id,
                caption=msg.caption,
                text_value=text_value,
                has_photo=bool(msg.photo),
                has_video=bool(msg.video),
                has_document=bool(msg.document),
                is_forwarded=bool(msg.forward_origin),
                media_group_id=msg.media_group_id,
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
            BotCommand("rename_task", "修改当前任务名称"),
            BotCommand("debug_media", "媒体更新诊断（仅super）"),
            BotCommand("debug_queue", "查看队列元数据"),
            BotCommand("sources", "查看监听源列表"),
            BotCommand("set_source", "启停监听源"),
            BotCommand("restart", "重启流程（仅super）"),
        ]
    )
    await notify_startup(application)


def init_db():
    os.makedirs("/mnt/sosoflow", exist_ok=True)
    Base.metadata.create_all(engine)
    # PostgreSQL 枚举自迁移：补齐 queue.status 的 waiting 枚举值（兼容旧库）
    if database_type(env.database_url) == "postgresql":
        with engine.begin() as conn:
            conn.execute(
                sql_text(
                    """
                    DO $$
                    DECLARE enum_name text;
                    BEGIN
                        SELECT t.typname INTO enum_name
                        FROM pg_type t
                        JOIN pg_enum e ON t.oid = e.enumtypid
                        WHERE e.enumlabel = 'pending'
                        LIMIT 1;
                        IF enum_name IS NOT NULL THEN
                            EXECUTE format('ALTER TYPE %I ADD VALUE IF NOT EXISTS %L', enum_name, 'waiting');
                        END IF;
                    END
                    $$;
                    """
                )
            )
    # 轻量自迁移：为旧库补齐 queue/global_settings 新字段
    inspector = inspect(engine)
    task_columns = {col["name"] for col in inspector.get_columns("tasks")}
    with engine.begin() as conn:
        if "range_start_message_id" not in task_columns:
            conn.execute(sql_text("ALTER TABLE tasks ADD COLUMN range_start_message_id BIGINT"))
        if "range_end_message_id" not in task_columns:
            conn.execute(sql_text("ALTER TABLE tasks ADD COLUMN range_end_message_id BIGINT"))
        if "is_completed" not in task_columns:
            conn.execute(sql_text("ALTER TABLE tasks ADD COLUMN is_completed BOOLEAN DEFAULT FALSE"))
            conn.execute(sql_text("UPDATE tasks SET is_completed = FALSE WHERE is_completed IS NULL"))
        if "completed_at" not in task_columns:
            completed_at_type = "TIMESTAMP" if database_type(env.database_url) == "postgresql" else "DATETIME"
            conn.execute(sql_text(f"ALTER TABLE tasks ADD COLUMN completed_at {completed_at_type}"))
    columns = {col["name"] for col in inspector.get_columns("queue")}
    with engine.begin() as conn:
        if "file_id" not in columns:
            conn.execute(sql_text("ALTER TABLE queue ADD COLUMN file_id VARCHAR(512)"))
        if "caption" not in columns:
            conn.execute(sql_text("ALTER TABLE queue ADD COLUMN caption TEXT"))
        if "has_document" not in columns:
            conn.execute(sql_text("ALTER TABLE queue ADD COLUMN has_document BOOLEAN"))
        if "retry_count" not in columns:
            conn.execute(sql_text("ALTER TABLE queue ADD COLUMN retry_count INTEGER DEFAULT 0"))
            conn.execute(sql_text("UPDATE queue SET retry_count = 0 WHERE retry_count IS NULL"))
        if "next_retry_at" not in columns:
            next_retry_type = "TIMESTAMP" if database_type(env.database_url) == "postgresql" else "DATETIME"
            conn.execute(sql_text(f"ALTER TABLE queue ADD COLUMN next_retry_at {next_retry_type}"))
    task_filter_columns = {col["name"] for col in inspector.get_columns("task_filters")}
    with engine.begin() as conn:
        if "include_keywords_enabled" not in task_filter_columns:
            conn.execute(sql_text("ALTER TABLE task_filters ADD COLUMN include_keywords_enabled BOOLEAN DEFAULT FALSE"))
            conn.execute(sql_text("UPDATE task_filters SET include_keywords_enabled = FALSE WHERE include_keywords_enabled IS NULL"))
        if "include_keywords" not in task_filter_columns:
            conn.execute(sql_text("ALTER TABLE task_filters ADD COLUMN include_keywords TEXT"))
    gs_columns = {col["name"] for col in inspector.get_columns("global_settings")}
    with engine.begin() as conn:
        if "debug_media_updates" not in gs_columns:
            conn.execute(sql_text("ALTER TABLE global_settings ADD COLUMN debug_media_updates BOOLEAN DEFAULT FALSE"))
            conn.execute(sql_text("UPDATE global_settings SET debug_media_updates = FALSE WHERE debug_media_updates IS NULL"))
    if database_type(env.database_url) == "postgresql":
        with engine.begin() as conn:
            conn.execute(
                sql_text(
                    """
                    DO $$
                    DECLARE col_type text;
                    BEGIN
                        SELECT data_type INTO col_type
                        FROM information_schema.columns
                        WHERE table_name = 'user_states' AND column_name = 'user_id'
                        LIMIT 1;
                        IF col_type IS NOT NULL AND col_type <> 'bigint' THEN
                            EXECUTE 'ALTER TABLE user_states ALTER COLUMN user_id TYPE BIGINT';
                        END IF;
                    END
                    $$;
                    """
                )
            )
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


def is_debug_media_enabled(session) -> bool:
    setting = session.get(GlobalSetting, 1)
    db_flag = bool(setting.debug_media_updates) if setting else False
    return db_flag or bool(env.debug_media_updates)


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
    app.add_handler(CommandHandler("rename_task", rename_task_cmd))
    app.add_handler(CommandHandler("set_delete_after_success", set_delete_after_success_cmd))
    app.add_handler(CommandHandler("set_auto_capture", set_auto_capture_cmd))
    app.add_handler(CommandHandler("set_tick", set_tick_cmd))
    app.add_handler(CommandHandler("debug_media", debug_media_cmd))
    app.add_handler(CommandHandler("debug_queue", debug_queue_cmd))
    app.add_handler(CommandHandler("sources", sources_cmd))
    app.add_handler(CommandHandler("set_source", set_source_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))
    app.add_handler(CommandHandler("start_task", start_task_cmd))
    app.add_handler(CommandHandler("pause_task", pause_task_cmd))
    app.add_handler(CommandHandler("filters", filters_cmd))
    app.add_handler(CommandHandler("set_filter", set_filter_cmd))
    app.add_handler(CommandHandler("add_admin", add_admin_cmd))
    app.add_handler(CommandHandler("remove_admin", remove_admin_cmd))
    app.add_handler(CommandHandler("admins", admins_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & (~filters.COMMAND), capture_new_message))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST & (~filters.COMMAND), capture_new_message))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & (~filters.COMMAND), capture_new_message))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_CHANNEL_POST & (~filters.COMMAND), capture_new_message))


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

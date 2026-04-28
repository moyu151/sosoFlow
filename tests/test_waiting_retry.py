import os
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import main  # noqa: E402
from main import (  # noqa: E402
    Admin,
    Base,
    QueueItem,
    QueueStatusEnum,
    RoleEnum,
    Task,
    TaskModeEnum,
    capture_new_message,
    now,
    publish_one,
)


@pytest.fixture(autouse=True)
def _fresh_db():
    test_engine = create_engine("sqlite:///:memory:", future=True)
    test_session_local = sessionmaker(bind=test_engine, expire_on_commit=False, future=True)
    old_engine = main.engine
    old_session_local = main.SessionLocal
    main.engine = test_engine
    main.SessionLocal = test_session_local
    Base.metadata.drop_all(main.engine)
    Base.metadata.create_all(main.engine)
    with main.SessionLocal() as session:
        session.add(Admin(telegram_user_id=1, role=RoleEnum.super))
        session.commit()
    yield
    main.engine = old_engine
    main.SessionLocal = old_session_local


def _mk_task():
    with main.SessionLocal() as session:
        task = Task(
            name="t1",
            source_chat_id=-1001,
            target_chat_id=-1002,
            mode=TaskModeEnum.copy,
            interval_seconds=1,
            daily_limit=999999,
            active_start_time="00:00",
            active_end_time="23:59",
            enabled=True,
            auto_capture_enabled=True,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def _mk_message(message_id: int, chat_id: int = -1001, text: str | None = None):
    async def _reply_text(*args, **kwargs):
        return None

    return SimpleNamespace(
        chat=SimpleNamespace(type="channel"),
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        caption=None,
        photo=None,
        video=None,
        document=None,
        sticker=None,
        poll=None,
        media_group_id=None,
        forward_origin=None,
        reply_text=_reply_text,
    )


@pytest.mark.asyncio
async def test_missing_message_moves_to_waiting():
    task_id = _mk_task()
    with main.SessionLocal() as session:
        session.add(QueueItem(task_id=task_id, message_id=51, status=QueueStatusEnum.pending, message_type="unknown"))
        session.commit()

    class Bot:
        async def copy_message(self, chat_id, from_chat_id, message_id):
            raise Exception("message to copy not found")

    result = await publish_one(SimpleNamespace(bot=Bot()), SimpleNamespace(id=task_id), ignore_interval=True, ignore_window=True)
    assert "等待重试" in result
    with main.SessionLocal() as session:
        item = session.scalar(select(QueueItem).where(QueueItem.task_id == task_id, QueueItem.message_id == 51))
        assert item.status == QueueStatusEnum.waiting
        assert item.retry_count == 1
        assert item.next_retry_at is not None


@pytest.mark.asyncio
async def test_waiting_before_next_retry_not_picked():
    task_id = _mk_task()
    with main.SessionLocal() as session:
        session.add(
            QueueItem(
                task_id=task_id,
                message_id=52,
                status=QueueStatusEnum.waiting,
                message_type="unknown",
                next_retry_at=now() + timedelta(minutes=5),
                retry_count=1,
            )
        )
        session.commit()

    class Bot:
        async def copy_message(self, chat_id, from_chat_id, message_id):
            return SimpleNamespace(message_id=999)

    result = await publish_one(SimpleNamespace(bot=Bot()), SimpleNamespace(id=task_id), ignore_interval=True, ignore_window=True)
    assert result == "无可发布消息（pending/waiting）"


@pytest.mark.asyncio
async def test_capture_new_message_recover_waiting_to_pending():
    task_id = _mk_task()
    with main.SessionLocal() as session:
        session.add(
            QueueItem(
                task_id=task_id,
                message_id=51,
                status=QueueStatusEnum.waiting,
                message_type="unknown",
                retry_count=2,
                next_retry_at=now() + timedelta(minutes=10),
                fail_reason="message not found",
            )
        )
        session.commit()

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_message=_mk_message(message_id=51),
    )
    context = SimpleNamespace(user_data={})
    await capture_new_message(update, context)

    with main.SessionLocal() as session:
        item = session.scalar(select(QueueItem).where(QueueItem.task_id == task_id, QueueItem.message_id == 51))
        assert item.status == QueueStatusEnum.pending
        assert item.fail_reason is None
        assert item.next_retry_at is None


@pytest.mark.asyncio
async def test_waiting_retry_count_over_limit_moves_to_failed():
    task_id = _mk_task()
    with main.SessionLocal() as session:
        session.add(
            QueueItem(
                task_id=task_id,
                message_id=53,
                status=QueueStatusEnum.pending,
                message_type="unknown",
                retry_count=20,
            )
        )
        session.commit()

    class Bot:
        async def copy_message(self, chat_id, from_chat_id, message_id):
            raise Exception("wrong message identifier")

    result = await publish_one(SimpleNamespace(bot=Bot()), SimpleNamespace(id=task_id), ignore_interval=True, ignore_window=True)
    assert "重试超限" in result
    with main.SessionLocal() as session:
        item = session.scalar(select(QueueItem).where(QueueItem.task_id == task_id, QueueItem.message_id == 53))
        assert item.status == QueueStatusEnum.failed
        assert item.retry_count == 21


@pytest.mark.asyncio
async def test_capture_new_message_does_not_override_published_or_skipped():
    task_id = _mk_task()
    with main.SessionLocal() as session:
        session.add(QueueItem(task_id=task_id, message_id=54, status=QueueStatusEnum.published, message_type="text", text_preview="old"))
        session.add(QueueItem(task_id=task_id, message_id=55, status=QueueStatusEnum.skipped, message_type="text", text_preview="old"))
        session.commit()

    update1 = SimpleNamespace(effective_user=SimpleNamespace(id=1), effective_message=_mk_message(message_id=54, text="new"))
    update2 = SimpleNamespace(effective_user=SimpleNamespace(id=1), effective_message=_mk_message(message_id=55, text="new"))
    context = SimpleNamespace(user_data={})
    await capture_new_message(update1, context)
    await capture_new_message(update2, context)

    with main.SessionLocal() as session:
        item54 = session.scalar(select(QueueItem).where(QueueItem.task_id == task_id, QueueItem.message_id == 54))
        item55 = session.scalar(select(QueueItem).where(QueueItem.task_id == task_id, QueueItem.message_id == 55))
        assert item54.status == QueueStatusEnum.published
        assert item55.status == QueueStatusEnum.skipped

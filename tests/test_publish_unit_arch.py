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
    Base,
    QueueItem,
    QueueStatusEnum,
    Task,
    TaskModeEnum,
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
    yield
    main.engine = old_engine
    main.SessionLocal = old_session_local


def _mk_task():
    with main.SessionLocal() as session:
        task = Task(
            name="arch",
            source_chat_id=-1001,
            target_chat_id=-1002,
            mode=TaskModeEnum.copy,
            interval_seconds=1,
            daily_limit=999999,
            active_start_time="00:00",
            active_end_time="23:59",
            enabled=True,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def _mk_group(task_id: int, mids: list[int], *, file_id=True, created_offset_sec=10):
    created_at = now() - timedelta(seconds=created_offset_sec)
    with main.SessionLocal() as session:
        for idx, mid in enumerate(mids):
            session.add(
                QueueItem(
                    task_id=task_id,
                    message_id=mid,
                    status=QueueStatusEnum.pending,
                    message_type="photo",
                    file_id=f"p_{mid}" if file_id else None,
                    caption="cap" if idx == 0 else None,
                    media_group_id="g_arch",
                    has_photo=True,
                    created_at=created_at,
                )
            )
        session.commit()


@pytest.mark.asyncio
async def test_group_publish_calls_send_media_group_once():
    task_id = _mk_task()
    _mk_group(task_id, [11, 12, 13], file_id=True, created_offset_sec=10)

    class Bot:
        def __init__(self):
            self.send_media_group_calls = 0
            self.copy_message_calls = 0

        async def send_media_group(self, chat_id, media):
            self.send_media_group_calls += 1
            return [SimpleNamespace(message_id=301), SimpleNamespace(message_id=302), SimpleNamespace(message_id=303)]

        async def copy_message(self, *args, **kwargs):
            self.copy_message_calls += 1
            raise AssertionError("group path should not call copy_message")

    app = SimpleNamespace(bot=Bot())
    result = await publish_one(app, SimpleNamespace(id=task_id), ignore_interval=True, ignore_window=True)
    assert "media_group" in result
    assert app.bot.send_media_group_calls == 1
    assert app.bot.copy_message_calls == 0


@pytest.mark.asyncio
async def test_group_settle_not_ready_should_wait():
    task_id = _mk_task()
    _mk_group(task_id, [21, 22, 23], file_id=True, created_offset_sec=0)

    class Bot:
        async def send_media_group(self, chat_id, media):
            raise AssertionError("should not publish before settle")

    app = SimpleNamespace(bot=Bot())
    result = await publish_one(app, SimpleNamespace(id=task_id), ignore_interval=True, ignore_window=True)
    assert "媒体组等待收齐" in result


@pytest.mark.asyncio
async def test_group_settle_ready_then_publish_all():
    task_id = _mk_task()
    _mk_group(task_id, [31, 32, 33], file_id=True, created_offset_sec=10)

    class Bot:
        async def send_media_group(self, chat_id, media):
            return [SimpleNamespace(message_id=401), SimpleNamespace(message_id=402), SimpleNamespace(message_id=403)]

    app = SimpleNamespace(bot=Bot())
    await publish_one(app, SimpleNamespace(id=task_id), ignore_interval=True, ignore_window=True)
    with main.SessionLocal() as session:
        rows = session.scalars(select(QueueItem).where(QueueItem.task_id == task_id).order_by(QueueItem.message_id.asc())).all()
        assert all(row.status == QueueStatusEnum.published for row in rows)
        assert [row.target_message_id for row in rows] == [401, 402, 403]


@pytest.mark.asyncio
async def test_group_failure_marks_all_waiting_or_failed():
    task_id = _mk_task()
    _mk_group(task_id, [41, 42, 43], file_id=True, created_offset_sec=10)

    class Bot:
        async def send_media_group(self, chat_id, media):
            raise Exception("message not found")

    app = SimpleNamespace(bot=Bot())
    await publish_one(app, SimpleNamespace(id=task_id), ignore_interval=True, ignore_window=True)
    with main.SessionLocal() as session:
        rows = session.scalars(select(QueueItem).where(QueueItem.task_id == task_id)).all()
        assert all(row.status in {QueueStatusEnum.waiting, QueueStatusEnum.failed} for row in rows)
        reasons = {row.fail_reason for row in rows}
        assert len(reasons) == 1


@pytest.mark.asyncio
async def test_single_message_publish_unchanged():
    task_id = _mk_task()
    with main.SessionLocal() as session:
        session.add(
            QueueItem(
                task_id=task_id,
                message_id=51,
                status=QueueStatusEnum.pending,
                message_type="text",
                media_group_id=None,
            )
        )
        session.commit()

    class Bot:
        def __init__(self):
            self.send_media_group_calls = 0

        async def send_media_group(self, chat_id, media):
            self.send_media_group_calls += 1
            raise AssertionError("single message should not use send_media_group")

        async def copy_message(self, chat_id, from_chat_id, message_id):
            return SimpleNamespace(message_id=777)

    app = SimpleNamespace(bot=Bot())
    result = await publish_one(app, SimpleNamespace(id=task_id), ignore_interval=True, ignore_window=True)
    assert "发布成功 message_id=51" in result
    assert app.bot.send_media_group_calls == 0


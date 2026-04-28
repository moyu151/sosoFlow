import os
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
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
    upsert_queue_item_from_capture,
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


def _mk_task(mode=TaskModeEnum.copy):
    with main.SessionLocal() as s:
        t = Task(
            name="t1",
            source_chat_id=-1001,
            target_chat_id=-1002,
            mode=mode,
            interval_seconds=1,
            daily_limit=999999,
            round_hours=24,
            round_limit=999999,
            active_start_time="00:00",
            active_end_time="23:59",
            enabled=True,
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _mk_group(task_id, kind="photo", with_file_id=True):
    with main.SessionLocal() as s:
        rows = []
        created_at = now() - timedelta(seconds=10)
        for idx, mid in enumerate([10, 11]):
            file_id = f"{kind}_{mid}" if with_file_id else None
            rows.append(
                QueueItem(
                    task_id=task_id,
                    message_id=mid,
                    status=QueueStatusEnum.pending,
                    message_type=kind,
                    file_id=file_id,
                    caption="cap" if idx == 0 else None,
                    media_group_id="g1",
                    has_text=True if idx == 0 else False,
                    has_photo=(kind == "photo"),
                    has_video=(kind == "video"),
                    has_links=False,
                    created_at=created_at,
                )
            )
        s.add_all(rows)
        s.commit()


@pytest.mark.asyncio
async def test_media_group_photo_uses_send_media_group():
    task_id = _mk_task(TaskModeEnum.copy)
    _mk_group(task_id, kind="photo", with_file_id=True)

    class Bot:
        def __init__(self):
            self.send_media_group_called = False

        async def send_media_group(self, chat_id, media):
            self.send_media_group_called = True
            assert len(media) == 2
            assert media[0].caption == "cap"
            assert media[1].caption is None
            return [SimpleNamespace(message_id=100), SimpleNamespace(message_id=101)]

    app = SimpleNamespace(bot=Bot())
    with main.SessionLocal() as s:
        t = s.get(Task, task_id)
    result = await publish_one(app, t, ignore_interval=True, ignore_window=True)
    assert "发布成功 media_group" in result
    assert app.bot.send_media_group_called is True
    with main.SessionLocal() as s:
        rows = s.query(QueueItem).order_by(QueueItem.message_id.asc()).all()
        assert [r.target_message_id for r in rows] == [100, 101]
        assert all(r.status == QueueStatusEnum.published for r in rows)


@pytest.mark.asyncio
async def test_media_group_video_uses_send_media_group():
    task_id = _mk_task(TaskModeEnum.copy)
    _mk_group(task_id, kind="video", with_file_id=True)

    class Bot:
        async def send_media_group(self, chat_id, media):
            assert len(media) == 2
            return [SimpleNamespace(message_id=200), SimpleNamespace(message_id=201)]

    app = SimpleNamespace(bot=Bot())
    with main.SessionLocal() as s:
        t = s.get(Task, task_id)
    result = await publish_one(app, t, ignore_interval=True, ignore_window=True)
    assert "发布成功 media_group" in result
    with main.SessionLocal() as s:
        rows = s.query(QueueItem).order_by(QueueItem.message_id.asc()).all()
        assert [r.target_message_id for r in rows] == [200, 201]


@pytest.mark.asyncio
async def test_media_group_missing_file_id_fallback_copy_messages():
    task_id = _mk_task(TaskModeEnum.copy)
    _mk_group(task_id, kind="photo", with_file_id=False)

    class Bot:
        def __init__(self):
            self.copy_messages_called = False

        async def copy_messages(self, chat_id, from_chat_id, message_ids):
            self.copy_messages_called = True
            return [SimpleNamespace(message_id=300), SimpleNamespace(message_id=301)]

    app = SimpleNamespace(bot=Bot())
    with main.SessionLocal() as s:
        t = s.get(Task, task_id)
    result = await publish_one(app, t, ignore_interval=True, ignore_window=True)
    assert "发布成功 media_group" in result
    assert app.bot.copy_messages_called is True
    with main.SessionLocal() as s:
        rows = s.query(QueueItem).order_by(QueueItem.message_id.asc()).all()
        assert [r.target_message_id for r in rows] == [300, 301]


@pytest.mark.asyncio
async def test_media_group_forward_missing_file_id_fallback_copy_messages():
    task_id = _mk_task(TaskModeEnum.forward)
    _mk_group(task_id, kind="photo", with_file_id=False)

    class Bot:
        def __init__(self):
            self.copy_messages_called = False

        async def copy_messages(self, chat_id, from_chat_id, message_ids):
            self.copy_messages_called = True
            return [SimpleNamespace(message_id=400), SimpleNamespace(message_id=401)]

    app = SimpleNamespace(bot=Bot())
    with main.SessionLocal() as s:
        t = s.get(Task, task_id)
    result = await publish_one(app, t, ignore_interval=True, ignore_window=True)
    assert "发布成功 media_group" in result
    assert app.bot.copy_messages_called is True
    with main.SessionLocal() as s:
        rows = s.query(QueueItem).order_by(QueueItem.message_id.asc()).all()
        assert [r.target_message_id for r in rows] == [400, 401]


@pytest.mark.asyncio
async def test_import_placeholder_pending_can_be_hydrated_then_publish_as_album():
    task_id = _mk_task(TaskModeEnum.copy)
    with main.SessionLocal() as s:
        created_at = now() - timedelta(seconds=10)
        s.add(
            QueueItem(
                task_id=task_id,
                message_id=100,
                status=QueueStatusEnum.pending,
                message_type="unknown",
                created_at=created_at,
            )
        )
        s.add(
            QueueItem(
                task_id=task_id,
                message_id=101,
                status=QueueStatusEnum.pending,
                message_type="unknown",
                created_at=created_at,
            )
        )
        s.commit()
        upsert_queue_item_from_capture(
            session=s,
            task_id=task_id,
            message_id=100,
            message_type="photo",
            file_id="photo_100",
            caption="cap",
            text_value="cap",
            has_photo=True,
            has_video=False,
            has_document=False,
            is_forwarded=False,
            media_group_id="g100",
        )
        upsert_queue_item_from_capture(
            session=s,
            task_id=task_id,
            message_id=101,
            message_type="photo",
            file_id="photo_101",
            caption=None,
            text_value="",
            has_photo=True,
            has_video=False,
            has_document=False,
            is_forwarded=False,
            media_group_id="g100",
        )
        s.commit()

    class Bot:
        def __init__(self):
            self.send_media_group_called = False

        async def send_media_group(self, chat_id, media):
            self.send_media_group_called = True
            assert len(media) == 2
            return [SimpleNamespace(message_id=501), SimpleNamespace(message_id=502)]

    app = SimpleNamespace(bot=Bot())
    with main.SessionLocal() as s:
        t = s.get(Task, task_id)
    result = await publish_one(app, t, ignore_interval=True, ignore_window=True)
    assert "发布成功 media_group=g100" in result
    assert app.bot.send_media_group_called is True

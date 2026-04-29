import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import main  # noqa: E402
from main import Base, QueueItem, SourceMessage, SourceRegistry, Task, capture_new_message  # noqa: E402


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


@pytest.mark.asyncio
async def test_channel_post_without_effective_user_still_captured_with_media_group():
    with main.SessionLocal() as session:
        task = Task(
            name="t-capture",
            source_chat_id=-100123,
            target_chat_id=-100456,
            enabled=True,
            auto_capture_enabled=True,
        )
        session.add(task)
        session.commit()
        session.refresh(task)

    photo = [SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")]
    channel_post = SimpleNamespace(
        chat=SimpleNamespace(type="channel"),
        chat_id=-100123,
        message_id=501,
        media_group_id="mg_501",
        text=None,
        caption="album",
        photo=photo,
        video=None,
        document=None,
        sticker=None,
        poll=None,
        forward_origin=None,
    )
    update = SimpleNamespace(
        channel_post=channel_post,
        message=None,
        edited_message=None,
        edited_channel_post=None,
        effective_message=channel_post,
        effective_user=None,
    )
    context = SimpleNamespace(user_data={})

    await capture_new_message(update, context)

    with main.SessionLocal() as session:
        row = session.scalar(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.message_id == 501))
        assert row is not None
        assert row.media_group_id == "mg_501"
        assert row.file_id == "large"
        assert row.message_type == "photo"


@pytest.mark.asyncio
async def test_channel_post_auto_creates_source_capture_task_when_missing():
    photo = [SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")]
    channel_post = SimpleNamespace(
        chat=SimpleNamespace(type="channel"),
        chat_id=-100777,
        message_id=601,
        media_group_id="mg_601",
        text=None,
        caption="album",
        photo=photo,
        video=None,
        document=None,
        sticker=None,
        poll=None,
        forward_origin=None,
    )
    update = SimpleNamespace(
        channel_post=channel_post,
        message=None,
        edited_message=None,
        edited_channel_post=None,
        effective_message=channel_post,
        effective_user=None,
    )
    context = SimpleNamespace(user_data={})

    await capture_new_message(update, context)

    with main.SessionLocal() as session:
        task = session.scalar(select(Task).where(Task.source_chat_id == -100777))
        assert task is not None
        assert task.enabled is False
        row = session.scalar(select(QueueItem).where(QueueItem.task_id == task.id, QueueItem.message_id == 601))
        assert row is not None
        assert row.media_group_id == "mg_601"


@pytest.mark.asyncio
async def test_channel_post_writes_source_registry_and_source_messages():
    photo = [SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")]
    channel_post = SimpleNamespace(
        chat=SimpleNamespace(type="channel"),
        chat_id=-100888,
        message_id=701,
        media_group_id="mg_701",
        text=None,
        caption="album",
        photo=photo,
        video=None,
        document=None,
        sticker=None,
        poll=None,
        forward_origin=None,
    )
    update = SimpleNamespace(
        channel_post=channel_post,
        message=None,
        edited_message=None,
        edited_channel_post=None,
        effective_message=channel_post,
        effective_user=None,
    )
    context = SimpleNamespace(user_data={})

    await capture_new_message(update, context)

    with main.SessionLocal() as session:
        reg = session.scalar(select(SourceRegistry).where(SourceRegistry.source_chat_id == -100888))
        assert reg is not None
        assert reg.latest_seen_message_id == 701
        src = session.scalar(select(SourceMessage).where(SourceMessage.source_chat_id == -100888, SourceMessage.message_id == 701))
        assert src is not None
        assert src.media_group_id == "mg_701"
        assert src.file_id == "large"

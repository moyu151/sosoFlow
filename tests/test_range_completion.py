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
from main import Base, QueueItem, QueueStatusEnum, SourceMessage, SourceMessageStateEnum, Task, TaskMessageState, TaskModeEnum, pick_next_publish_item, publish_one  # noqa: E402


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


def _mk_task_with_range():
    with main.SessionLocal() as session:
        task = Task(
            name="range-task",
            source_chat_id=-10001,
            target_chat_id=-10002,
            mode=TaskModeEnum.copy,
            interval_seconds=1,
            daily_limit=999999,
            active_start_time="00:00",
            active_end_time="23:59",
            enabled=True,
            range_start_message_id=100,
            range_end_message_id=102,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def test_pick_next_publish_item_respects_task_range():
    task_id = _mk_task_with_range()
    with main.SessionLocal() as session:
        session.add(QueueItem(task_id=task_id, message_id=90, status=QueueStatusEnum.pending, message_type="text"))
        session.add(QueueItem(task_id=task_id, message_id=100, status=QueueStatusEnum.pending, message_type="text"))
        session.commit()
        picked = pick_next_publish_item(session, task_id)
        assert picked is not None
        assert picked.message_id == 100


@pytest.mark.asyncio
async def test_publish_one_auto_completes_task_when_range_done():
    task_id = _mk_task_with_range()
    with main.SessionLocal() as session:
        session.add(QueueItem(task_id=task_id, message_id=100, status=QueueStatusEnum.published, message_type="text"))
        session.add(QueueItem(task_id=task_id, message_id=101, status=QueueStatusEnum.failed, message_type="text"))
        session.add(QueueItem(task_id=task_id, message_id=102, status=QueueStatusEnum.skipped, message_type="text"))
        session.commit()
        task = session.get(Task, task_id)

    app = SimpleNamespace(bot=SimpleNamespace())
    result = await publish_one(app, task, ignore_interval=True, ignore_window=True)
    assert "范围消息处理完成" in result
    with main.SessionLocal() as session:
        db_task = session.get(Task, task_id)
        assert db_task.enabled is False
        assert db_task.is_completed is True
        assert db_task.completed_at is not None
        complete_logs = session.scalars(select(main.PublishLog).where(main.PublishLog.task_id == task_id, main.PublishLog.action == "complete")).all()
        assert len(complete_logs) >= 1


@pytest.mark.asyncio
async def test_publish_sync_creates_task_message_state_from_source_messages():
    task_id = _mk_task_with_range()
    with main.SessionLocal() as session:
        session.add(
            SourceMessage(
                source_chat_id=-10001,
                message_id=100,
                state=SourceMessageStateEnum.observed,
                message_type="text",
                text_preview="hello",
                has_text=True,
            )
        )
        session.commit()
        task = session.get(Task, task_id)

    class Bot:
        async def copy_message(self, chat_id, from_chat_id, message_id):
            return SimpleNamespace(message_id=9001)

    app = SimpleNamespace(bot=Bot())
    result = await publish_one(app, task, ignore_interval=True, ignore_window=True)
    assert "发布成功 message_id=100" in result
    with main.SessionLocal() as session:
        tms = session.scalar(select(TaskMessageState).where(TaskMessageState.task_id == task_id, TaskMessageState.message_id == 100))
        assert tms is not None
        assert tms.status.value == "published"


def test_pick_next_publish_item_marks_unresolvable_tms_failed():
    task_id = _mk_task_with_range()
    with main.SessionLocal() as session:
        session.add(
            TaskMessageState(
                task_id=task_id,
                source_chat_id=-10001,
                message_id=100,
                status=main.TaskMessageStatusEnum.pending,
            )
        )
        session.commit()
        picked = pick_next_publish_item(session, task_id)
        assert picked is None
        row = session.scalar(select(TaskMessageState).where(TaskMessageState.task_id == task_id, TaskMessageState.message_id == 100))
        assert row is not None
        assert row.status.value == "failed"
        assert "无法构建发布载荷" in (row.fail_reason or "")

from datetime import time
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BOT_TOKEN", "test-token")

from main import (
    TaskFilter,
    apply_filters,
    extract_forward_chat_id,
    is_time_in_window,
    parse_hhmm,
    parse_int,
    parse_on_off,
    reached_daily_limit,
)


class DummyQueueItem:
    def __init__(
        self,
        has_text=None,
        has_photo=None,
        has_video=None,
        has_links=None,
        is_forwarded=None,
        message_type=None,
        text_preview=None,
    ):
        self.has_text = has_text
        self.has_photo = has_photo
        self.has_video = has_video
        self.has_links = has_links
        self.is_forwarded = is_forwarded
        self.message_type = message_type
        self.text_preview = text_preview


def test_parse_int_ok():
    assert parse_int("-100123", "chat_id") == -100123


def test_parse_int_error():
    with pytest.raises(ValueError):
        parse_int("abc", "chat_id")


def test_parse_on_off_ok():
    assert parse_on_off("on", "switch") is True
    assert parse_on_off("off", "switch") is False


def test_parse_on_off_error():
    with pytest.raises(ValueError):
        parse_on_off("yes", "switch")


def test_parse_hhmm_ok():
    parsed = parse_hhmm("09:30")
    assert parsed.hour == 9 and parsed.minute == 30


@pytest.mark.parametrize("bad_value", ["9:30", "24:00", "12:60", "aa:bb"])
def test_parse_hhmm_error(bad_value):
    with pytest.raises(ValueError):
        parse_hhmm(bad_value)


def test_apply_filters_skip_reason():
    task_filter = TaskFilter(require_text=True)
    item = DummyQueueItem(has_text=False, has_photo=False, has_video=False, has_links=False, text_preview="")
    reason = apply_filters(item, task_filter)
    assert reason == "仅保留纯文字"


def test_apply_filters_unknown_metadata_pass():
    task_filter = TaskFilter(require_photo=True, exclude_links=True)
    item = DummyQueueItem(has_text=None, has_photo=None, has_video=None, has_links=None, text_preview=None)
    reason = apply_filters(item, task_filter)
    assert reason is None


def test_is_time_in_window_normal():
    assert is_time_in_window(time(10, 0), time(9, 0), time(23, 0)) is True
    assert is_time_in_window(time(8, 59), time(9, 0), time(23, 0)) is False


def test_is_time_in_window_cross_midnight():
    assert is_time_in_window(time(23, 30), time(22, 0), time(2, 0)) is True
    assert is_time_in_window(time(1, 30), time(22, 0), time(2, 0)) is True
    assert is_time_in_window(time(12, 0), time(22, 0), time(2, 0)) is False


def test_limit_helpers():
    assert reached_daily_limit(10, 10) is True
    assert reached_daily_limit(9, 10) is False


def test_album_like_single_photo_filter_pass():
    task_filter = TaskFilter(require_photo=True)
    item = DummyQueueItem(has_text=True, has_photo=True, has_video=False, has_links=False, message_type="photo", text_preview="cap")
    reason = apply_filters(item, task_filter)
    assert reason is None


def test_album_like_multi_photo_filter_pass():
    task_filter = TaskFilter(require_photo=True)
    items = [
        DummyQueueItem(has_text=True, has_photo=True, has_video=False, has_links=False, message_type="photo", text_preview="a"),
        DummyQueueItem(has_text=False, has_photo=True, has_video=False, has_links=False, message_type="photo", text_preview=""),
    ]
    assert all(apply_filters(i, task_filter) is None for i in items)


def test_album_like_photo_video_filter_pass():
    task_filter = TaskFilter()
    items = [
        DummyQueueItem(has_text=True, has_photo=True, has_video=False, has_links=False, message_type="photo", text_preview="a"),
        DummyQueueItem(has_text=False, has_photo=False, has_video=True, has_links=False, message_type="video", text_preview=""),
    ]
    assert all(apply_filters(i, task_filter) is None for i in items)


def test_album_like_caption_present_or_absent():
    task_filter = TaskFilter(min_text_length=1)
    with_caption = DummyQueueItem(has_text=True, has_photo=True, has_video=False, has_links=False, message_type="photo", text_preview="x")
    without_caption = DummyQueueItem(has_text=False, has_photo=True, has_video=False, has_links=False, message_type="photo", text_preview="")
    assert apply_filters(with_caption, task_filter) is None
    assert apply_filters(without_caption, task_filter) == "文字过短"


def test_extract_forward_chat_id_from_origin_chat():
    message = SimpleNamespace(forward_origin=SimpleNamespace(chat=SimpleNamespace(id=-1001112223334)))
    assert extract_forward_chat_id(message) == -1001112223334


def test_extract_forward_chat_id_from_legacy_chat():
    message = SimpleNamespace(forward_origin=None, forward_from_chat=SimpleNamespace(id=-1004445556667))
    assert extract_forward_chat_id(message) == -1004445556667

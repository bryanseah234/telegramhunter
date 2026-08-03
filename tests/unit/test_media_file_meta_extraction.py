from types import SimpleNamespace

import pytest

from app.services import scraper_srv


def test_bot_api_media_info_extracts_largest_photo_file_id():
    media_type, file_meta = scraper_srv._bot_api_media_info(
        {
            "photo": [
                {"file_id": "small", "file_unique_id": "u-small"},
                {
                    "file_id": "large",
                    "file_unique_id": "u-large",
                    "width": 1280,
                    "height": 720,
                    "file_size": 12345,
                },
            ]
        }
    )

    assert media_type == "photo"
    assert file_meta == {
        "source": "bot_api",
        "file_id": "large",
        "file_unique_id": "u-large",
        "file_size": 12345,
        "width": 1280,
        "height": 720,
    }


@pytest.mark.parametrize(
    ("key", "expected_type"),
    [
        ("document", "document"),
        ("video", "video"),
        ("audio", "audio"),
    ],
)
def test_bot_api_media_info_extracts_file_id_for_file_payloads(key, expected_type):
    media_type, file_meta = scraper_srv._bot_api_media_info(
        {
            key: {
                "file_id": f"{key}-file",
                "file_unique_id": f"{key}-unique",
                "file_name": f"{key}.bin",
                "mime_type": "application/octet-stream",
                "file_size": 2048,
            }
        }
    )

    assert media_type == expected_type
    assert file_meta == {
        "source": "bot_api",
        "file_id": f"{key}-file",
        "file_unique_id": f"{key}-unique",
        "file_name": f"{key}.bin",
        "mime": "application/octet-stream",
        "file_size": 2048,
    }


def test_telethon_media_info_packs_file_id_and_classifies_video(monkeypatch):
    class _FakeDocumentMedia:
        def __init__(self):
            self.document = SimpleNamespace(id=987, mime_type="video/mp4")

    monkeypatch.setattr(scraper_srv, "MessageMediaDocument", _FakeDocumentMedia)

    from telethon import utils as telethon_utils

    monkeypatch.setattr(telethon_utils, "pack_bot_file_id", lambda _media: "packed-file-id")

    message = SimpleNamespace(
        media=_FakeDocumentMedia(),
        file=SimpleNamespace(name="clip.mp4", mime_type="video/mp4"),
    )

    media_type, file_meta = scraper_srv._telethon_media_info(message)

    assert media_type == "video"
    assert file_meta == {
        "source": "telethon",
        "file_id": "packed-file-id",
        "mime": "video/mp4",
        "file_name": "clip.mp4",
        "id": 987,
        "access_hash": 0,
        "file_reference": "",
    }

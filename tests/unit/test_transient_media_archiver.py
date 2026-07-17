from types import SimpleNamespace

import pytest

from app.services import user_agent_srv
from app.services.user_agent_srv import UserAgentService


class _FakeArchiveClient:
    def __init__(self, *, send_raises: bool = False):
        self.send_raises = send_raises
        self.download_calls = []
        self.send_calls = []
        self.message = SimpleNamespace(
            media=object(),
            file=SimpleNamespace(name="evidence.pdf"),
        )

    async def get_messages(self, entity_or_chat_id, ids):
        self.get_messages_call = (entity_or_chat_id, ids)
        return self.message

    async def download_media(self, message, file):
        self.download_calls.append((message, file))
        return file

    async def send_file(self, target_chat_id, temp_path, caption="", reply_to=None):
        self.send_calls.append(
            {
                "target_chat_id": target_chat_id,
                "temp_path": temp_path,
                "caption": caption,
                "reply_to": reply_to,
            }
        )
        if self.send_raises:
            raise RuntimeError("upload failed")


def _service_with_client(client):
    service = UserAgentService()
    service.client = client

    async def start():
        return True

    async def disconnect():
        service.disconnected = True

    service.start = start
    service._disconnect = disconnect
    return service


@pytest.mark.asyncio
async def test_archive_media_transiently_downloads_reuploads_and_cleans_up(monkeypatch):
    client = _FakeArchiveClient()
    service = _service_with_client(client)
    existing_paths = set()
    removed_paths = []

    def fake_exists(path):
        return path in existing_paths

    def fake_remove(path):
        removed_paths.append(path)
        existing_paths.discard(path)

    original_download = client.download_media

    async def tracked_download(message, file):
        existing_paths.add(file)
        return await original_download(message, file)

    client.download_media = tracked_download
    monkeypatch.setattr(user_agent_srv.os.path, "exists", fake_exists)
    monkeypatch.setattr(user_agent_srv.os, "remove", fake_remove)

    ok = await service.archive_media_transiently(
        -100123,
        42,
        target_chat_id=-100999,
        topic_id=77,
        caption="A" * 1100,
    )

    assert ok is True
    assert client.get_messages_call == (-100123, 42)
    assert len(client.send_calls) == 1
    sent = client.send_calls[0]
    assert sent["target_chat_id"] == -100999
    assert sent["reply_to"] == 77
    assert sent["caption"] == "A" * 1024
    assert sent["temp_path"].startswith("/tmp/archive_")
    assert sent["temp_path"].endswith("_42_evidence.pdf")
    assert removed_paths == [sent["temp_path"]]
    assert service.disconnected is True


@pytest.mark.asyncio
async def test_archive_media_transiently_cleans_up_when_send_file_fails(monkeypatch):
    client = _FakeArchiveClient(send_raises=True)
    service = _service_with_client(client)
    existing_paths = set()
    removed_paths = []

    def fake_exists(path):
        return path in existing_paths

    def fake_remove(path):
        removed_paths.append(path)
        existing_paths.discard(path)

    original_download = client.download_media

    async def tracked_download(message, file):
        existing_paths.add(file)
        return await original_download(message, file)

    client.download_media = tracked_download
    monkeypatch.setattr(user_agent_srv.os.path, "exists", fake_exists)
    monkeypatch.setattr(user_agent_srv.os, "remove", fake_remove)

    ok = await service.archive_media_transiently(
        -100123,
        43,
        target_chat_id=-100999,
        topic_id=1,
        caption="Archived Attachment",
    )

    assert ok is False
    assert len(client.send_calls) == 1
    sent = client.send_calls[0]
    assert sent["reply_to"] is None
    assert sent["temp_path"].endswith("_43_evidence.pdf")
    assert removed_paths == [sent["temp_path"]]
    assert service.disconnected is True

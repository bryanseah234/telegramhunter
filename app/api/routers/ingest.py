import asyncio
import hashlib
import logging
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.database import db
from app.core.security import security

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["Ingest"])


def _looks_like_bot_token(token: str) -> bool:
    # Minimal guardrail; Telegram bot tokens are usually "<digits>:<secret>"
    if ":" not in token:
        return False
    prefix = token.split(":", 1)[0]
    return prefix.isdigit()


async def _exec(query_builder):
    return await asyncio.to_thread(query_builder.execute)


class ExtensionCredential(BaseModel):
    token: str
    chat_id: Optional[int] = None
    chat_name: Optional[str] = None
    chat_type: Optional[str] = None
    bot_id: Optional[str] = None
    bot_username: Optional[str] = None
    valid: Optional[bool] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ExtensionIngestRequest(BaseModel):
    source: str = "extension"
    domain: Optional[str] = None
    query: Optional[str] = None
    results: list[ExtensionCredential]


class ExtensionIngestResponse(BaseModel):
    inserted: int
    updated: int
    skipped: int


@router.post("/extension/credentials", response_model=ExtensionIngestResponse)
async def ingest_extension_credentials(
    payload: ExtensionIngestRequest,
    x_monitor_key: str | None = Header(None),
):
    """
    Ingest endpoint for server-side tooling. Requires X-Monitor-Key header.
    The Chrome extension writes directly to Supabase and does not use this endpoint.
    """
    if not settings.MONITOR_API_KEY or x_monitor_key != settings.MONITOR_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")

    inserted = 0
    updated = 0
    skipped = 0
    seen_hashes: set[str] = set()

    for item in payload.results:
        token = (item.token or "").strip()
        if not token or not _looks_like_bot_token(token):
            skipped += 1
            continue

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        if token_hash in seen_hashes:
            skipped += 1
            continue
        seen_hashes.add(token_hash)

        try:
            existing = await _exec(
                db.table("discovered_credentials")
                .select("id, chat_id, meta")
                .eq("token_hash", token_hash)
                .limit(1)
            )
        except Exception as e:
            logger.warning(f"Supabase lookup failed for token_hash {token_hash[:12]}...: {e}")
            skipped += 1
            continue

        enc_token = security.encrypt(token)
        base_meta: dict[str, Any] = {
            "ingested_via": "extension",
        }
        if payload.domain:
            base_meta["domain"] = payload.domain
        if payload.query:
            base_meta["query"] = payload.query
        if item.valid is not None:
            base_meta["valid"] = item.valid
        if item.meta:
            base_meta.update(item.meta)

        if existing.data:
            cred = existing.data[0]
            cred_id = cred["id"]

            merged_meta = {}
            if cred.get("meta"):
                merged_meta.update(cred["meta"])
            merged_meta.update(base_meta)

            update_data: dict[str, Any] = {
                "bot_token": enc_token,
                "meta": merged_meta,
            }

            if item.bot_id:
                update_data["bot_id"] = str(item.bot_id)
            if item.bot_username:
                update_data["bot_username"] = item.bot_username
            if item.chat_name:
                update_data["chat_name"] = item.chat_name
            if item.chat_type:
                update_data["chat_type"] = item.chat_type

            existing_chat_id = cred.get("chat_id")
            if item.chat_id and (existing_chat_id is None or int(existing_chat_id) != int(item.chat_id)):
                update_data["chat_id"] = item.chat_id
                update_data["status"] = "active"

            try:
                await _exec(db.table("discovered_credentials").update(update_data).eq("id", cred_id))
                updated += 1
            except Exception as e:
                logger.warning(f"Supabase update failed for cred_id={cred_id}: {e}")
                skipped += 1
            continue

        new_data: dict[str, Any] = {
            "bot_token": enc_token,
            "token_hash": token_hash,
            "chat_id": item.chat_id,
            "chat_name": item.chat_name,
            "chat_type": item.chat_type,
            "bot_id": str(item.bot_id) if item.bot_id else None,
            "bot_username": item.bot_username,
            "source": payload.source,
            "status": "active" if item.chat_id else "pending",
            "meta": base_meta,
        }

        try:
            await _exec(db.table("discovered_credentials").insert(new_data))
            inserted += 1
        except Exception as e:
            # Usually unique constraint on token_hash or other transient errors
            logger.warning(f"Supabase insert failed for token_hash {token_hash[:12]}...: {e}")
            skipped += 1

    return ExtensionIngestResponse(inserted=inserted, updated=updated, skipped=skipped)


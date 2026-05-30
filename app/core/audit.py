"""
Audit logging for security-sensitive operations.
Tracks credential access, token decryption, and security events.
"""
import asyncio
import re
from typing import Optional, Dict, Any
from datetime import datetime
from app.core.logger import get_logger
from app.core.database import db

logger = get_logger(__name__)

# Regex that matches Telegram bot token shape (digits:alphanum 35+)
_TOKEN_RE = re.compile(r'\b\d{8,12}:[A-Za-z0-9_-]{35,}\b')


def _redact_details(details: dict) -> dict:
    """
    Return a copy of details with any credential-shaped values scrubbed.
    Replaces raw bot tokens with '<redacted>' before logging or persisting.
    Operates recursively on nested dicts/lists.
    """
    if not details:
        return {}
    out = {}
    for k, v in details.items():
        if isinstance(v, str):
            out[k] = _TOKEN_RE.sub('<redacted>', v)
        elif isinstance(v, dict):
            out[k] = _redact_details(v)
        elif isinstance(v, list):
            out[k] = [_TOKEN_RE.sub('<redacted>', i) if isinstance(i, str) else i for i in v]
        else:
            out[k] = v
    return out


class AuditEvent:
    """Security audit event types"""
    TOKEN_DECRYPTED = "token_decrypted"
    CREDENTIAL_ACCESSED = "credential_accessed"
    CREDENTIAL_CREATED = "credential_created"
    CREDENTIAL_UPDATED = "credential_updated"
    TOKEN_VALIDATED = "token_validated"
    TOKEN_REVOKED = "token_revoked"
    BROADCAST_SENT = "broadcast_sent"
    SCANNER_RUN = "scanner_run"


class AuditLogger:
    """
    Audit logger for security events.
    Logs to both application logs and optionally to database.

    All details dicts are redacted before logging or persisting — raw bot
    tokens must never appear in audit logs or the audit_logs table.
    """

    @staticmethod
    def log(
        event_type: str,
        credential_id: Optional[str] = None,
        user: str = "system",
        details: Optional[Dict[str, Any]] = None,
        success: bool = True
    ):
        """
        Log a security audit event.

        Args:
            event_type: Type of event (use AuditEvent constants)
            credential_id: Associated credential ID if applicable
            user: User/service performing the action
            details: Additional event details (credential-shaped values are redacted)
            success: Whether the operation succeeded
        """
        timestamp = datetime.utcnow().isoformat()
        safe_details = _redact_details(details or {})

        audit_entry = {
            "timestamp": timestamp,
            "event_type": event_type,
            "credential_id": credential_id,
            "user": user,
            "success": success,
            "details": safe_details,
        }

        # Log to application logger (safe_details already redacted)
        log_level = logger.info if success else logger.warning
        log_msg = f"Audit: {event_type}"
        if credential_id:
            log_msg += f" | cred_id={credential_id}"
        log_msg += f" | user={user} | success={success}"
        if safe_details:
            log_msg += f" | details={safe_details}"
        log_level(log_msg)

        # Optionally persist to DB for compliance
        if AuditLogger._should_persist(event_type):
            # _persist_to_db is sync. If called from an async context, schedule
            # it in a thread so we don't block the event loop. If called from a
            # sync Celery task, call directly.
            try:
                loop = asyncio.get_running_loop()
                # We're inside an async context — schedule without blocking
                loop.create_task(
                    asyncio.to_thread(AuditLogger._persist_to_db, audit_entry)
                )
            except RuntimeError:
                # No running loop — sync Celery task, call directly
                try:
                    AuditLogger._persist_to_db(audit_entry)
                except Exception as e:
                    logger.error(f"Failed to persist audit log: {e}")

    @staticmethod
    def _should_persist(event_type: str) -> bool:
        """
        Determine if event should be persisted to database.
        Only persist high-importance events to avoid bloat.
        """
        high_importance = [
            AuditEvent.TOKEN_DECRYPTED,
            AuditEvent.TOKEN_REVOKED,
            AuditEvent.CREDENTIAL_CREATED,
        ]
        return event_type in high_importance

    @staticmethod
    def _persist_to_db(audit_entry: dict):
        """
        Persist audit log to database (synchronous — run in a thread from async context).
        Writes to the audit_logs table for compliance tracking.
        Failures are logged but never raised — audit must not break the main flow.
        """
        try:
            db.table("audit_logs").insert({
                "event_type":    audit_entry["event_type"],
                "credential_id": audit_entry.get("credential_id"),
                "user_agent":    audit_entry.get("user", "system"),
                "success":       audit_entry.get("success", True),
                "details":       audit_entry.get("details", {}),
            }).execute()
        except Exception as e:
            logger.error(f"Audit DB persist failed for {audit_entry.get('event_type')}: {e}")


# ---------------------------------------------------------------------------
# Convenience wrappers for common audit events
# ---------------------------------------------------------------------------

def audit_token_decryption(credential_id: str, success: bool = True):
    """Audit token decryption event — does NOT log the token itself."""
    AuditLogger.log(
        AuditEvent.TOKEN_DECRYPTED,
        credential_id=credential_id,
        success=success
    )


def audit_credential_access(credential_id: str, operation: str):
    """Audit credential access event."""
    AuditLogger.log(
        AuditEvent.CREDENTIAL_ACCESSED,
        credential_id=credential_id,
        details={"operation": operation}
    )


def audit_scanner_run(scanner: str, results_count: int, success: bool = True):
    """Audit scanner execution."""
    AuditLogger.log(
        AuditEvent.SCANNER_RUN,
        user="celery_worker",
        details={"scanner": scanner, "results": results_count},
        success=success
    )


def audit_token_validation(token_hash: str, is_valid: bool):
    """Audit token validation — logs full hash, never the raw token."""
    AuditLogger.log(
        AuditEvent.TOKEN_VALIDATED,
        # Use the full hash — truncating to 16 chars was misleadingly weak
        details={"token_hash": token_hash, "is_valid": is_valid}
    )


def audit_broadcast(credential_id: str, message_count: int):
    """Audit message broadcast."""
    AuditLogger.log(
        AuditEvent.BROADCAST_SENT,
        credential_id=credential_id,
        details={"message_count": message_count}
    )

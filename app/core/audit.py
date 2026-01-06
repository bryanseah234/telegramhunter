"""
Audit logging for security-sensitive operations.
Tracks credential access, token decryption, and security events.
"""
from typing import Optional, Dict, Any
from datetime import datetime
from app.core.logger import get_logger
from app.core.database import db

logger = get_logger(__name__)


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
            details: Additional event details
            success: Whether the operation succeeded
        """
        timestamp = datetime.utcnow().isoformat()
        
        audit_entry = {
            "timestamp": timestamp,
            "event_type": event_type,
            "credential_id": credential_id,
            "user": user,
            "success": success,
            "details": details or {}
        }
        
        # Log to application logger
        log_level = logger.info if success else logger.warning
        log_msg = f"Audit: {event_type}"
        if credential_id:
            log_msg += f" | cred_id={credential_id}"
        log_msg += f" | user={user} | success={success}"
        if details:
            log_msg += f" | details={details}"
        
        log_level(log_msg)
        
        # Optionally log to database for compliance
        # This could be enabled in production for critical events
        if AuditLogger._should_persist(event_type):
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
            AuditEvent.CREDENTIAL_CREATED
        ]
        return event_type in high_importance
    
    @staticmethod
    def _persist_to_db(audit_entry: dict):
        """
        Persist audit log to database.
        Note: Requires an 'audit_logs' table to be created.
        """
        # This is a placeholder - would require creating audit_logs table
        # For now, we just log it
        logger.debug(f"Would persist to DB: {audit_entry}")
        # db.table("audit_logs").insert(audit_entry).execute()


# Convenience functions for common audit events

def audit_token_decryption(credential_id: str, success: bool = True):
    """Audit token decryption event"""
    AuditLogger.log(
        AuditEvent.TOKEN_DECRYPTED,
        credential_id=credential_id,
        success=success
    )


def audit_credential_access(credential_id: str, operation: str):
    """Audit credential access event"""
    AuditLogger.log(
        AuditEvent.CREDENTIAL_ACCESSED,
        credential_id=credential_id,
        details={"operation": operation}
    )


def audit_scanner_run(scanner: str, results_count: int, success: bool = True):
    """Audit scanner execution"""
    AuditLogger.log(
        AuditEvent.SCANNER_RUN,
        user="celery_worker",
        details={"scanner": scanner, "results": results_count},
        success=success
    )


def audit_token_validation(token_hash: str, is_valid: bool):
    """Audit token validation"""
    AuditLogger.log(
        AuditEvent.TOKEN_VALIDATED,
        details={"token_hash": token_hash[:16], "is_valid": is_valid}
    )


def audit_broadcast(credential_id: str, message_count: int):
    """Audit message broadcast"""
    AuditLogger.log(
        AuditEvent.BROADCAST_SENT,
        credential_id=credential_id,
        details={"message_count": message_count}
    )

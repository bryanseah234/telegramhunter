"""
Database retry wrapper for Supabase operations.
Handles transient connection errors with exponential backoff.
"""
from typing import TypeVar, Callable
import functools
from app.core.retry import retry
from app.core.logger import get_logger

logger = get_logger(__name__)

T = TypeVar('T')


def with_db_retry(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator for database operations with retry logic.
    Retries on connection errors, timeouts, and transient failures.
    
    Example:
        @with_db_retry
        def get_credentials():
            return db.table("discovered_credentials").select("*").execute()
    """
    @functools.wraps(func)
    @retry(
        max_attempts=3,
        base_delay=1.0,
        max_delay=5.0,
        exponential=True,
        exceptions=(ConnectionError, TimeoutError, Exception)  # Broad for Supabase errors
    )
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # Log specific error types
            error_msg = str(e).lower()
            if 'connection' in error_msg or 'timeout' in error_msg:
                logger.warning(f"Database connection issue in {func.__name__}: {e}")
            raise
    
    return wrapper


class DatabaseHealth:
    """Database health check utilities"""
    
    @staticmethod
    @with_db_retry
    def check_connection() -> bool:
        """
        Verify database connectivity.
        Returns True if successful, raises exception if fails after retries.
        """
        from app.core.database import db
        
        # Simple ping query
        result = db.table("discovered_credentials").select("id").limit(1).execute()
        
        logger.info("Database health check passed")
        return True
    
    @staticmethod
    def get_pool_stats() -> dict:
        """Get connection pool statistics (if available)"""
        # Supabase Python client doesn't expose pool stats directly
        # This is a placeholder for future implementation
        return {
            "status": "unknown",
            "note": "Pool stats not available in current Supabase client"
        }

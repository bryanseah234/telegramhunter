import time
import functools
from typing import Callable, TypeVar, Optional, Tuple, Type
import asyncio
from app.core.logger import get_logger

logger = get_logger(__name__)

T = TypeVar('T')


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    Retry decorator with exponential backoff.
    
    Args:
        max_attempts: Maximum number of retry attempts
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries
        exponential: Use exponential backoff if True, constant delay otherwise
        exceptions: Tuple of exception types to catch and retry
    
    Example:
        @retry(max_attempts=3, base_delay=2.0)
        def fetch_data():
            # ... code that might fail ...
            
        @retry(max_attempts=5, exceptions=(ConnectionError, TimeoutError))
        async def async_fetch():
            # ... async code ...
    """
    def decorator(func: Callable) -> Callable:
        is_async = asyncio.iscoroutinefunction(func)
        
        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                last_exception = None
                
                for attempt in range(1, max_attempts + 1):
                    try:
                        return await func(*args, **kwargs)
                    except exceptions as e:
                        last_exception = e
                        
                        if attempt == max_attempts:
                            logger.error(
                                f"Function {func.__name__} failed after {max_attempts} attempts",
                                exc_info=True
                            )
                            raise
                        
                        # Calculate delay
                        if exponential:
                            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                        else:
                            delay = base_delay
                        
                        logger.warning(
                            f"Function {func.__name__} failed (attempt {attempt}/{max_attempts}). "
                            f"Retrying in {delay:.1f}s. Error: {str(e)}"
                        )
                        
                        await asyncio.sleep(delay)
                
                # Should never reach here, but type checker needs this
                raise last_exception if last_exception else RuntimeError("Unexpected retry state")
            
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                last_exception = None
                
                for attempt in range(1, max_attempts + 1):
                    try:
                        return func(*args, **kwargs)
                    except exceptions as e:
                        last_exception = e
                        
                        if attempt == max_attempts:
                            logger.error(
                                f"Function {func.__name__} failed after {max_attempts} attempts",
                                exc_info=True
                            )
                            raise
                        
                        # Calculate delay
                        if exponential:
                            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                        else:
                            delay = base_delay
                        
                        logger.warning(
                            f"Function {func.__name__} failed (attempt {attempt}/{max_attempts}). "
                            f"Retrying in {delay:.1f}s. Error: {str(e)}"
                        )
                        
                        time.sleep(delay)
                
                # Should never reach here, but type checker needs this
                raise last_exception if last_exception else RuntimeError("Unexpected retry state")
            
            return sync_wrapper
    
    return decorator


def retry_on_telegram_error(max_attempts: int = 3):
    """
    Specialized retry decorator for Telegram API errors.
    Handles rate limits (429) with proper backoff.
    """
    from telegram.error import RetryAfter, TimedOut, NetworkError
    
    return retry(
        max_attempts=max_attempts,
        base_delay=2.0,
        max_delay=30.0,
        exponential=True,
        exceptions=(RetryAfter, TimedOut, NetworkError)
    )


def retry_on_connection_error(max_attempts: int = 3):
    """
    Retry decorator for connection-related errors.
    Useful for database and HTTP requests.
    """
    import requests
    from httpx import ConnectError, TimeoutException
    
    return retry(
        max_attempts=max_attempts,
        base_delay=1.0,
        max_delay=10.0,
        exponential=True,
        exceptions=(
            ConnectionError,
            TimeoutError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            ConnectError,
            TimeoutException
        )
    )

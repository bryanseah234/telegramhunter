"""
Account Scheduler — Time-window based connect/disconnect for Telegram user accounts.

Prevents MTProto conflicts when multiple projects share the same Telegram phone numbers
by ensuring only one project's user clients are connected at any given time.

Environment variables:
    ACCOUNT_SCHEDULE_ENABLED: "true" or "false" (default: "true")
    ACCOUNT_ACTIVE_START: HH:MM in UTC (default: "12:00")
    ACCOUNT_ACTIVE_END: HH:MM in UTC (default: "24:00")
"""

import asyncio
import os
import logging
from datetime import datetime, timezone, time as dt_time
from typing import Any, Callable, Awaitable, Optional

logger = logging.getLogger("account_scheduler")

# Type alias for async callbacks
AsyncCallback = Callable[[], Awaitable[None]]

# Check interval in seconds
_CHECK_INTERVAL = 30


def _parse_time(value: str) -> dt_time:
    """Parse HH:MM string into a datetime.time (UTC).

    Treats "24:00" as 23:59:59 (end-of-day sentinel).
    """
    value = value.strip()
    if value in ("24:00", "24:0", "2400"):
        return dt_time(23, 59, 59)
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format '{value}', expected HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if hour == 24 and minute == 0:
        return dt_time(23, 59, 59)
    return dt_time(hour, minute, 0)


def is_within_window(now_time: dt_time, start: dt_time, end: dt_time) -> bool:
    """Return True if *now_time* falls inside the [start, end] window.

    Handles both:
      - Normal windows   (start < end):    08:00–20:00
      - Overnight windows (start >= end):  22:00–06:00
    """
    if start <= end:
        # Normal window: active when start <= now <= end
        return start <= now_time <= end
    else:
        # Overnight window: active when now >= start OR now <= end
        return now_time >= start or now_time <= end


class AccountScheduler:
    """Background scheduler that activates/deactivates user accounts on a UTC time window."""

    def __init__(
        self,
        on_activate: AsyncCallback,
        on_deactivate: AsyncCallback,
        enabled: Optional[bool] = None,
        active_start: Optional[str] = None,
        active_end: Optional[str] = None,
    ):
        # Read from env with fallbacks
        if enabled is None:
            enabled = os.getenv("ACCOUNT_SCHEDULE_ENABLED", "true").lower() in ("true", "1", "yes")
        if active_start is None:
            active_start = os.getenv("ACCOUNT_ACTIVE_START", "12:00")
        if active_end is None:
            active_end = os.getenv("ACCOUNT_ACTIVE_END", "24:00")

        self.enabled: bool = enabled
        self.start_time: dt_time = _parse_time(active_start)
        self.end_time: dt_time = _parse_time(active_end)
        self._on_activate = on_activate
        self._on_deactivate = on_deactivate

        # Internal state
        self._is_active: bool = False  # whether accounts are currently connected
        self._task: Optional[asyncio.Task[Any]] = None
        self._running: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Whether user accounts are currently in the active (connected) state."""
        return self._is_active

    async def start(self) -> None:
        """Start the scheduler loop.  If disabled, immediately activates accounts (always-on)."""
        if not self.enabled:
            logger.info("[AccountScheduler] Scheduling DISABLED — accounts stay connected 24/7.")
            if not self._is_active:
                await self._activate()
            return

        logger.info(
            f"[AccountScheduler] Scheduling ENABLED — "
            f"active window {self.start_time.strftime('%H:%M')}–{self.end_time.strftime('%H:%M')} UTC"
        )

        # Evaluate current state immediately
        if self._should_be_active():
            logger.info("[AccountScheduler] Currently INSIDE active window — connecting accounts.")
            await self._activate()
        else:
            logger.info("[AccountScheduler] Currently OUTSIDE active window — accounts will stay disconnected.")
            # Ensure deactivation (accounts may have been briefly connected at import time)
            await self._deactivate()

        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Gracefully stop the scheduler loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # On shutdown, always deactivate cleanly
        if self._is_active:
            await self._deactivate()
        logger.info("[AccountScheduler] Stopped.")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _should_be_active(self) -> bool:
        """Check current UTC time against the configured window."""
        now = datetime.now(timezone.utc).time()
        return is_within_window(now, self.start_time, self.end_time)

    async def _activate(self) -> None:
        if self._is_active:
            return
        logger.info("[AccountScheduler] ▶️  Active window started — connecting user accounts.")
        try:
            await self._on_activate()
        except Exception:
            logger.exception("[AccountScheduler] Error during on_activate callback")
        self._is_active = True

    async def _deactivate(self) -> None:
        if not self._is_active:
            return
        logger.info("[AccountScheduler] ⏸️  Active window ended — disconnecting user accounts.")
        try:
            await self._on_deactivate()
        except Exception:
            logger.exception("[AccountScheduler] Error during on_deactivate callback")
        self._is_active = False

    async def _loop(self) -> None:
        """Background loop that checks every _CHECK_INTERVAL seconds."""
        try:
            while self._running:
                await asyncio.sleep(_CHECK_INTERVAL)
                should = self._should_be_active()
                if should and not self._is_active:
                    await self._activate()
                elif not should and self._is_active:
                    await self._deactivate()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("[AccountScheduler] Unexpected error in scheduler loop")

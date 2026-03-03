"""
Tests for AccountScheduler — time-window logic, transitions, and disabled mode.
"""

import asyncio
import pytest
from datetime import time as dt_time
from unittest.mock import AsyncMock, patch

from app.services.account_scheduler import (
    AccountScheduler,
    is_within_window,
    _parse_time,
)


# ====================================================================
# Unit tests for _parse_time
# ====================================================================

class TestParseTime:
    def test_normal_time(self):
        assert _parse_time("08:00") == dt_time(8, 0, 0)

    def test_midnight(self):
        assert _parse_time("00:00") == dt_time(0, 0, 0)

    def test_end_of_day_24_00(self):
        """24:00 is treated as 23:59:59 (end-of-day sentinel)."""
        assert _parse_time("24:00") == dt_time(23, 59, 59)

    def test_afternoon(self):
        assert _parse_time("12:00") == dt_time(12, 0, 0)

    def test_with_whitespace(self):
        assert _parse_time("  14:30  ") == dt_time(14, 30, 0)

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _parse_time("8am")

    def test_single_digit(self):
        assert _parse_time("9:05") == dt_time(9, 5, 0)


# ====================================================================
# Unit tests for is_within_window
# ====================================================================

class TestIsWithinWindow:
    """Test both normal and overnight windows."""

    # --- Normal window (start < end) ---

    def test_normal_inside(self):
        assert is_within_window(dt_time(10, 0), dt_time(8, 0), dt_time(20, 0)) is True

    def test_normal_at_start(self):
        assert is_within_window(dt_time(8, 0), dt_time(8, 0), dt_time(20, 0)) is True

    def test_normal_at_end(self):
        assert is_within_window(dt_time(20, 0), dt_time(8, 0), dt_time(20, 0)) is True

    def test_normal_before_start(self):
        assert is_within_window(dt_time(7, 59), dt_time(8, 0), dt_time(20, 0)) is False

    def test_normal_after_end(self):
        assert is_within_window(dt_time(20, 1), dt_time(8, 0), dt_time(20, 0)) is False

    # --- Overnight window (start > end) ---

    def test_overnight_before_midnight(self):
        """22:00–06:00: 23:00 should be active."""
        assert is_within_window(dt_time(23, 0), dt_time(22, 0), dt_time(6, 0)) is True

    def test_overnight_after_midnight(self):
        """22:00–06:00: 03:00 should be active."""
        assert is_within_window(dt_time(3, 0), dt_time(22, 0), dt_time(6, 0)) is True

    def test_overnight_at_start(self):
        assert is_within_window(dt_time(22, 0), dt_time(22, 0), dt_time(6, 0)) is True

    def test_overnight_at_end(self):
        assert is_within_window(dt_time(6, 0), dt_time(22, 0), dt_time(6, 0)) is True

    def test_overnight_outside_midday(self):
        """22:00–06:00: 12:00 should NOT be active."""
        assert is_within_window(dt_time(12, 0), dt_time(22, 0), dt_time(6, 0)) is False

    def test_overnight_outside_just_after_end(self):
        assert is_within_window(dt_time(6, 1), dt_time(22, 0), dt_time(6, 0)) is False

    # --- Full-day window (00:00–23:59:59) ---

    def test_full_day(self):
        assert is_within_window(dt_time(15, 0), dt_time(0, 0), dt_time(23, 59, 59)) is True

    # --- Complementary windows ---

    def test_complementary_window_a(self):
        """telegramcollector: 00:00–12:00"""
        start, end = dt_time(0, 0), dt_time(12, 0)
        assert is_within_window(dt_time(6, 0), start, end) is True
        assert is_within_window(dt_time(13, 0), start, end) is False

    def test_complementary_window_b(self):
        """telegramhunter: 12:00–23:59:59 (24:00)"""
        start, end = dt_time(12, 0), dt_time(23, 59, 59)
        assert is_within_window(dt_time(13, 0), start, end) is True
        assert is_within_window(dt_time(6, 0), start, end) is False


# ====================================================================
# AccountScheduler integration tests
# ====================================================================

class TestAccountSchedulerDisabled:
    """When ACCOUNT_SCHEDULE_ENABLED=false, accounts stay connected 24/7."""

    @pytest.mark.asyncio
    async def test_disabled_calls_activate_immediately(self):
        on_activate = AsyncMock()
        on_deactivate = AsyncMock()

        scheduler = AccountScheduler(
            on_activate=on_activate,
            on_deactivate=on_deactivate,
            enabled=False,
            active_start="12:00",
            active_end="24:00",
        )

        await scheduler.start()

        on_activate.assert_awaited_once()
        on_deactivate.assert_not_awaited()
        assert scheduler.is_active is True

        # stop should not crash
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_disabled_never_starts_background_loop(self):
        scheduler = AccountScheduler(
            on_activate=AsyncMock(),
            on_deactivate=AsyncMock(),
            enabled=False,
        )
        await scheduler.start()
        assert scheduler._task is None
        await scheduler.stop()


class TestAccountSchedulerTransitions:
    """Verify activate/deactivate callbacks fire on window transitions."""

    @pytest.mark.asyncio
    async def test_start_inside_window_activates(self):
        on_activate = AsyncMock()
        on_deactivate = AsyncMock()

        scheduler = AccountScheduler(
            on_activate=on_activate,
            on_deactivate=on_deactivate,
            enabled=True,
            active_start="00:00",
            active_end="24:00",  # always active
        )

        await scheduler.start()

        on_activate.assert_awaited_once()
        on_deactivate.assert_not_awaited()
        assert scheduler.is_active is True

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_start_outside_window_deactivates(self):
        """Use a window that is guaranteed to not contain the current time."""
        on_activate = AsyncMock()
        on_deactivate = AsyncMock()

        # Window: impossible (start == end is treated as normal with zero width)
        # Use a 1-minute window in the far past relative to now...
        # Actually, let's mock _should_be_active to return False.
        scheduler = AccountScheduler(
            on_activate=on_activate,
            on_deactivate=on_deactivate,
            enabled=True,
            active_start="00:00",
            active_end="00:01",
        )

        with patch.object(scheduler, "_should_be_active", return_value=False):
            await scheduler.start()

        on_activate.assert_not_awaited()
        # on_deactivate called but since _is_active is False at init, it's a no-op guard
        assert scheduler.is_active is False

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_transition_activate_then_deactivate(self):
        """Simulate time crossing boundary: outside -> inside -> outside."""
        on_activate = AsyncMock()
        on_deactivate = AsyncMock()

        scheduler = AccountScheduler(
            on_activate=on_activate,
            on_deactivate=on_deactivate,
            enabled=True,
            active_start="12:00",
            active_end="24:00",
        )

        # Start outside window
        with patch.object(scheduler, "_should_be_active", return_value=False):
            await scheduler.start()
        assert scheduler.is_active is False

        # Simulate entering window
        scheduler._running = True  # ensure loop logic works
        with patch.object(scheduler, "_should_be_active", return_value=True):
            # Manually trigger check (simulating loop iteration)
            should = scheduler._should_be_active()
            assert should is True
            await scheduler._activate()
        assert scheduler.is_active is True
        on_activate.assert_awaited_once()

        # Simulate leaving window
        with patch.object(scheduler, "_should_be_active", return_value=False):
            await scheduler._deactivate()
        assert scheduler.is_active is False
        on_deactivate.assert_awaited_once()

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_activate_is_idempotent(self):
        """Calling _activate twice should only fire callback once."""
        on_activate = AsyncMock()

        scheduler = AccountScheduler(
            on_activate=on_activate,
            on_deactivate=AsyncMock(),
            enabled=True,
            active_start="00:00",
            active_end="24:00",
        )

        await scheduler._activate()
        await scheduler._activate()

        assert on_activate.await_count == 1

    @pytest.mark.asyncio
    async def test_deactivate_is_idempotent(self):
        """Calling _deactivate twice should only fire callback once."""
        on_deactivate = AsyncMock()

        scheduler = AccountScheduler(
            on_activate=AsyncMock(),
            on_deactivate=on_deactivate,
            enabled=True,
            active_start="00:00",
            active_end="24:00",
        )

        # Must be active first for deactivate to fire
        scheduler._is_active = True
        await scheduler._deactivate()
        await scheduler._deactivate()

        assert on_deactivate.await_count == 1


class TestAccountSchedulerLoop:
    """Test that the background loop correctly drives transitions."""

    @pytest.mark.asyncio
    async def test_loop_calls_activate_on_transition(self):
        on_activate = AsyncMock()
        on_deactivate = AsyncMock()

        scheduler = AccountScheduler(
            on_activate=on_activate,
            on_deactivate=on_deactivate,
            enabled=True,
            active_start="00:00",
            active_end="24:00",
        )

        call_count = 0
        original_sleep = asyncio.sleep

        async def short_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                scheduler._running = False  # break out after 2 iterations
            await original_sleep(0)  # yield control without actually waiting

        # Start outside window, then flip to inside on second check
        side_effects = [False, True, True]
        call_idx = 0

        def mock_should_be_active():
            nonlocal call_idx
            result = side_effects[min(call_idx, len(side_effects) - 1)]
            call_idx += 1
            return result

        with patch.object(scheduler, "_should_be_active", side_effect=mock_should_be_active):
            with patch("app.services.account_scheduler.asyncio.sleep", side_effect=short_sleep):
                # Start triggers first check (False -> deactivate guard)
                await scheduler.start()
                # Wait for loop task to finish
                if scheduler._task:
                    await scheduler._task

        on_activate.assert_awaited_once()


class TestAccountSchedulerEnvParsing:
    """Verify env-var based construction."""

    def test_reads_from_env(self):
        with patch.dict("os.environ", {
            "ACCOUNT_SCHEDULE_ENABLED": "false",
            "ACCOUNT_ACTIVE_START": "08:00",
            "ACCOUNT_ACTIVE_END": "20:00",
        }):
            scheduler = AccountScheduler(
                on_activate=AsyncMock(),
                on_deactivate=AsyncMock(),
            )
            assert scheduler.enabled is False
            assert scheduler.start_time == dt_time(8, 0, 0)
            assert scheduler.end_time == dt_time(20, 0, 0)

    def test_defaults_when_env_missing(self):
        with patch.dict("os.environ", {}, clear=False):
            # Remove keys if present
            import os
            for key in ("ACCOUNT_SCHEDULE_ENABLED", "ACCOUNT_ACTIVE_START", "ACCOUNT_ACTIVE_END"):
                os.environ.pop(key, None)

            scheduler = AccountScheduler(
                on_activate=AsyncMock(),
                on_deactivate=AsyncMock(),
            )
            assert scheduler.enabled is True
            assert scheduler.start_time == dt_time(12, 0, 0)
            assert scheduler.end_time == dt_time(23, 59, 59)

    def test_explicit_params_override_env(self):
        with patch.dict("os.environ", {
            "ACCOUNT_SCHEDULE_ENABLED": "true",
            "ACCOUNT_ACTIVE_START": "08:00",
            "ACCOUNT_ACTIVE_END": "20:00",
        }):
            scheduler = AccountScheduler(
                on_activate=AsyncMock(),
                on_deactivate=AsyncMock(),
                enabled=False,
                active_start="01:00",
                active_end="02:00",
            )
            assert scheduler.enabled is False
            assert scheduler.start_time == dt_time(1, 0, 0)
            assert scheduler.end_time == dt_time(2, 0, 0)

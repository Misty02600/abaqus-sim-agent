import asyncio

import pytest

from abaqus_sim_agent.coding import active_wall
from abaqus_sim_agent.coding.active_wall import ActiveWallClock


@pytest.mark.asyncio
async def test_pause_excludes_wait_and_reschedules_active_timeout(monkeypatch) -> None:
    elapsed = 0.0
    monkeypatch.setattr(active_wall, "perf_counter", lambda: elapsed)
    clock = ActiveWallClock()
    clock.reset(2.0)

    # Use a controlled elapsed clock and inspect the actual timeout state. Tiny
    # wall-clock sleeps test scheduler load instead of pause accounting on Windows.
    async with asyncio.timeout(3600) as timeout:
        clock.attach_timeout(timeout)
        original_deadline = timeout.when()
        assert original_deadline is not None
        elapsed = 0.01
        async with clock.pause():
            assert timeout.when() is None
            elapsed += 600
            assert clock.elapsed_seconds == pytest.approx(2.01)
            await asyncio.sleep(0)
        resumed_deadline = timeout.when()
        assert resumed_deadline is not None
        assert resumed_deadline >= original_deadline
        elapsed += 0.01

    assert clock.elapsed_seconds == pytest.approx(2.02)


@pytest.mark.asyncio
async def test_active_timeout_still_expires_outside_pause() -> None:
    clock = ActiveWallClock()
    clock.reset()

    async def exceed_limit() -> None:
        async with asyncio.timeout(0.01) as timeout:
            clock.attach_timeout(timeout)
            await asyncio.sleep(0.03)

    with pytest.raises(TimeoutError):
        await exceed_limit()

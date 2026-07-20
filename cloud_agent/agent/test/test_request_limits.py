import asyncio
import sys
from pathlib import Path

from fastapi import HTTPException


APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from security.limits import RequestBudget


def test_request_budget_blocks_parallel_slot_and_enforces_rate_limit():
    async def concurrency_case():
        budget = RequestBudget(
            max_requests=2,
            window_seconds=60.0,
            max_concurrent=1,
            error_detail="rate_limit_exceeded",
        )
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()
        release = asyncio.Event()

        async def first():
            async with budget.slot("demo"):
                first_entered.set()
                await release.wait()

        async def second():
            async with budget.slot("demo"):
                second_entered.set()

        first_task = asyncio.create_task(first())
        await first_entered.wait()
        second_task = asyncio.create_task(second())
        await asyncio.sleep(0.05)
        assert not second_entered.is_set()
        release.set()
        await asyncio.gather(first_task, second_task)
        assert second_entered.is_set()

    async def rate_limit_case():
        budget = RequestBudget(
            max_requests=1,
            window_seconds=60.0,
            max_concurrent=2,
            error_detail="rate_limit_exceeded",
        )
        async with budget.slot("demo"):
            pass
        try:
            async with budget.slot("demo"):
                pass
        except HTTPException as exc:
            assert exc.status_code == 429
            assert exc.detail == "rate_limit_exceeded"
        else:
            raise AssertionError("second request should be rate limited")

    asyncio.run(concurrency_case())
    asyncio.run(rate_limit_case())

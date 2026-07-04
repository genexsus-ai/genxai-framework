"""Schedule-based trigger implementation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from genxai.triggers.base import BaseTrigger

logger = logging.getLogger(__name__)


class ScheduleTrigger(BaseTrigger):
    """Cron/interval trigger using APScheduler if available."""

    def __init__(
        self,
        trigger_id: str,
        cron: str | None = None,
        interval_seconds: int | None = None,
        payload: dict[str, Any] | None = None,
        name: str | None = None,
        timezone: str = "UTC",
    ) -> None:
        super().__init__(trigger_id=trigger_id, name=name)
        self.cron = cron
        self.interval_seconds = interval_seconds
        self.payload = payload or {}
        self.timezone = timezone
        self._scheduler = None
        self._job = None

        if not self.cron and not self.interval_seconds:
            raise ValueError("Either cron or interval_seconds must be provided")

    async def _start(self) -> None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
        except ImportError as exc:
            raise ImportError(
                "APScheduler is required for ScheduleTrigger. Install with: pip install apscheduler"
            ) from exc

        scheduler = AsyncIOScheduler(timezone=self.timezone)

        if self.cron:
            trigger = CronTrigger.from_crontab(self.cron, timezone=self.timezone)
        else:
            trigger = IntervalTrigger(seconds=self.interval_seconds)

        async def _emit_wrapper() -> None:
            await self.emit(payload={"scheduled_at": datetime.now(UTC).isoformat(), **self.payload})

        scheduler.add_job(_emit_wrapper, trigger=trigger)
        scheduler.start()
        self._scheduler = scheduler
        logger.info("ScheduleTrigger %s started", self.trigger_id)

    async def _stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        logger.info("ScheduleTrigger %s stopped", self.trigger_id)

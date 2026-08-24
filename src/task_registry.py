"""Ownership and bounded diagnostics for HunterX-created asyncio tasks."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Coroutine


@dataclass(frozen=True)
class TaskRecord:
    owner: str
    purpose: str
    created_at: float
    generation: int
    status: str = "running"
    finished_at: float = 0.0
    error_type: str = ""


class TaskRegistry:
    """Keep strong references only while tasks run and harvest every outcome."""

    def __init__(self, *, history_capacity: int = 128) -> None:
        self._active: dict[asyncio.Task[Any], TaskRecord] = {}
        self._history: deque[TaskRecord] = deque(maxlen=max(1, history_capacity))

    def create(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        owner: str,
        purpose: str,
        generation: int = 0,
        name: str | None = None,
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine, name=name)
        return self.track(
            task,
            owner=owner,
            purpose=purpose,
            generation=generation,
        )

    def track(
        self,
        task: asyncio.Task[Any],
        *,
        owner: str,
        purpose: str,
        generation: int = 0,
    ) -> asyncio.Task[Any]:
        if task in self._active:
            return task
        record = TaskRecord(
            owner=str(owner or "hunterx"),
            purpose=str(purpose or "task"),
            created_at=time.monotonic(),
            generation=max(0, int(generation or 0)),
        )
        self._active[task] = record
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        record = self._active.pop(task, None)
        if record is None:
            return
        status = "completed"
        error_type = ""
        try:
            error = task.exception()
        except asyncio.CancelledError:
            status = "cancelled"
        except BaseException as exc:
            status = "failed"
            error_type = type(exc).__name__
        else:
            if error is not None:
                status = "failed"
                error_type = type(error).__name__
        self._history.append(
            TaskRecord(
                owner=record.owner,
                purpose=record.purpose,
                created_at=record.created_at,
                generation=record.generation,
                status=status,
                finished_at=time.monotonic(),
                error_type=error_type,
            )
        )

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def history(self) -> tuple[TaskRecord, ...]:
        return tuple(self._history)

    def active_records(self) -> tuple[TaskRecord, ...]:
        return tuple(self._active.values())

    async def cancel_owner(self, owner: str) -> int:
        selected = [
            task
            for task, record in self._active.items()
            if record.owner == str(owner)
        ]
        for task in selected:
            task.cancel()
        if selected:
            await asyncio.gather(*selected, return_exceptions=True)
        return len(selected)


hunterx_tasks = TaskRegistry()

"""
In-memory хранилище задач (для MVP без PostgreSQL).
В продакшене заменяется на asyncpg + PostgreSQL таблицу tasks.
"""
from __future__ import annotations

import asyncio
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ..api.schemas import AnalysisResultOut, TaskStatus


@dataclass
class TaskRecord:
    task_id: UUID
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    result: AnalysisResultOut | None = None
    error: str | None = None


# Глобальный in-memory store (для MVP)
_tasks: dict[UUID, TaskRecord] = {}
_lock = asyncio.Lock()


async def create_task() -> UUID:
    task_id = uuid4()
    async with _lock:
        _tasks[task_id] = TaskRecord(task_id=task_id)
    return task_id


async def get_task(task_id: UUID) -> TaskRecord | None:
    async with _lock:
        return _tasks.get(task_id)


async def update_task(task_id: UUID, **kwargs) -> None:
    async with _lock:
        record = _tasks.get(task_id)
        if record:
            for key, value in kwargs.items():
                setattr(record, key, value)

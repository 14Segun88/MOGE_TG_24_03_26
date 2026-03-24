"""
API роутер — эндпоинты для загрузки и получения результатов.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File, status

from ..api.pipeline import process_zip
from ..api.schemas import AnalysisResultOut, HealthResponse, TaskStatus, UploadResponse
from ..api.task_store import create_task, get_task

router = APIRouter(prefix="/api/v1", tags=["documents"])

# Максимальный размер ZIP (50 МБ)
MAX_ZIP_SIZE_BYTES = 50 * 1024 * 1024


@router.get("/health", response_model=HealthResponse, summary="Статус сервиса")
async def health_check() -> HealthResponse:
    """Проверка работоспособности сервиса."""
    return HealthResponse()


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Загрузить ZIP-пакет проектной документации",
    description="""
    Принимает ZIP-архив с пакетом проектной документации.

    Пайплайн обработки:
    1. **FileClassifier** — определяет типы всех файлов (XML, PDF, скан, смета ...)
    2. **XmlParser** — валидирует XML ПЗ по XSD v01.05, извлекает ГИП+СНИЛС+НОПРИЗ
    3. **FormalCheckRunner** — проверяет комплектность, ЭЦП/ИУЛ, версию схемы

    Возвращает `task_id` для получения результатов через GET `/results/{task_id}`.
    """,
)
async def upload_package(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="ZIP-архив пакета документов"),
) -> UploadResponse:
    # Проверка типа файла
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Ожидается ZIP-файл (.zip)",
        )

    # Читаем содержимое
    zip_bytes = await file.read()

    if len(zip_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Файл пустой",
        )

    if len(zip_bytes) > MAX_ZIP_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл превышает максимальный размер {MAX_ZIP_SIZE_BYTES // (1024*1024)} МБ",
        )

    # Создаём задачу и запускаем фоновую обработку
    task_id = await create_task()
    background_tasks.add_task(
        _run_pipeline_sync, task_id, zip_bytes
    )

    return UploadResponse(
        task_id=task_id,
        status=TaskStatus.PENDING,
        message=f"Пакет '{file.filename}' принят. Обработка начата.",
        estimated_seconds=15,
    )


@router.get(
    "/results/{task_id}",
    response_model=AnalysisResultOut,
    summary="Получить результаты анализа пакета",
)
async def get_results(task_id: UUID) -> AnalysisResultOut:
    """
    Получить результаты анализа по task_id.

    - **pending / running** → обработка ещё идёт, повторите запрос через 5-10 секунд
    - **done** → результаты готовы
    - **failed** → ошибка обработки, см. поле `error`
    """
    record = await get_task(task_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Задача {task_id} не найдена",
        )

    if record.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        # Возвращаем промежуточный статус
        from ..api.schemas import AnalysisResultOut
        from datetime import datetime
        return AnalysisResultOut(
            task_id=task_id,
            status=record.status,
            created_at=record.created_at,
            verdict="",
            verdict_reason="Обработка ещё идёт...",
        )

    if record.status == TaskStatus.FAILED:
        from datetime import datetime
        return AnalysisResultOut(
            task_id=task_id,
            status=record.status,
            created_at=record.created_at,
            completed_at=record.completed_at,
            error=record.error,
            verdict="FAILED",
            verdict_reason=record.error or "Неизвестная ошибка",
        )

    return record.result


async def _run_pipeline_sync(task_id: UUID, zip_bytes: bytes) -> None:
    """Запуск синхронного пайплайна в event loop (process_zip — корутина)."""
    await process_zip(task_id, zip_bytes)

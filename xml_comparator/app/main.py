"""
Точка входа FastAPI-приложения.

Структура:
  app/main.py         — этот файл, создаёт FastAPI, подключает роутеры
  app/config.py       — настройки из env
  app/api/router.py   — эндпоинты
  app/engine/         — движок сравнения
  app/models/         — Pydantic-модели
  app/parsers/        — XML-парсер
  app/mapping/        — загрузчик Excel-маппинга
  app/normalizers/    — нормализаторы значений
  app/strategies/     — стратегии сравнения
  app/reports/        — построитель итогового отчёта
"""
from __future__ import annotations

import logging
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import router
from app.config import settings

# ──────────────────────────────────────────────────────────────────────────────
# Логирование
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Приложение
# ──────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="XML Comparator — ПЗ vs ЗнП",
    description=(
        "Сервис сравнения XML-документов по Excel-маппингу.\n\n"
        "Поддерживает сравнение ПЗ (ExplanatoryNote) и ЗнП (DesignAssignment), "
        "а также любых других пар XML-документов через кастомный маппинг."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS (при необходимости ограничьте origins в .env)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Роутер API
app.include_router(router, prefix="/api/v1", tags=["comparison"])


# ──────────────────────────────────────────────────────────────────────────────
# Глобальный обработчик непредвиденных ошибок
# ──────────────────────────────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Необработанное исключение для %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "Внутренняя ошибка сервера", "error_type": type(exc).__name__},
    )

"""
Конфигурация приложения через переменные окружения.
Использует pydantic-settings для валидации и типизации.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Путь к файлу маппинга по умолчанию (для /compare/preset)
    mapping_file_path: Optional[str] = None

    # Папка для сохранения JSON-отчётов (создаётся автоматически)
    # None — отчёты не сохраняются на диск, только возвращаются в ответе API
    reports_dir: Optional[str] = "reports"

    # Настройки сервера
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    log_level: str = "info"

    # Путь к XSL-файлу для визуализации ПЗ (None = встроенный app/reports/xsl/)
    xsl_pz_path: Optional[str] = None

    # Ограничения файлов (байты)
    max_xml_size_bytes: int = 50 * 1024 * 1024   # 50 МБ
    max_mapping_size_bytes: int = 10 * 1024 * 1024  # 10 МБ


settings = Settings()

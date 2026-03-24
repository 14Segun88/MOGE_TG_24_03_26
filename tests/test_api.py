"""
Тесты API Gateway (Спринт 2).
pytest + httpx AsyncClient (без реального сервера).
"""
from __future__ import annotations

import io
import zipfile
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from src.main import app


# ─────────────────────────────────────────────
#  Фикстуры
# ─────────────────────────────────────────────

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def minimal_zip() -> bytes:
    """ZIP с минимальным XML ПЗ — намеренно не валидным (для тест FC-001+)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "explanatory.xml",
            b'<?xml version="1.0"?><ExplanatoryNote SchemaVersion="01.05">'
            b"<ExplanatoryNoteNumber>TEST-001</ExplanatoryNoteNumber>"
            b"</ExplanatoryNote>",
        )
        zf.writestr("drawing.pdf", b"%PDF-1.4 fake content here")
    return buf.getvalue()


@pytest.fixture
def empty_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", b"no documents")
    return buf.getvalue()


# ─────────────────────────────────────────────
#  Хелпер для клиента
# ─────────────────────────────────────────────

@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ─────────────────────────────────────────────
#  Тесты
# ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_health(client):
    """GET /api/v1/health → 200 OK."""
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["xsd_version"] == "01.05"


@pytest.mark.anyio
async def test_upload_returns_202(client, minimal_zip):
    """POST /api/v1/upload → 202 Accepted с task_id."""
    resp = await client.post(
        "/api/v1/upload",
        files={"file": ("package.zip", minimal_zip, "application/zip")},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "task_id" in data
    assert data["status"] == "pending"
    # task_id должен быть валидным UUID
    UUID(data["task_id"])


@pytest.mark.anyio
async def test_upload_non_zip_rejected(client):
    """POST /api/v1/upload с PDF → 422."""
    resp = await client.post(
        "/api/v1/upload",
        files={"file": ("document.pdf", b"%PDF-fake", "application/pdf")},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_upload_empty_file_rejected(client):
    """POST /api/v1/upload с пустым файлом → 422."""
    resp = await client.post(
        "/api/v1/upload",
        files={"file": ("empty.zip", b"", "application/zip")},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_results_unknown_task_404(client):
    """GET /api/v1/results/{unknown_uuid} → 404."""
    resp = await client.get(f"/api/v1/results/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_full_pipeline_upload_and_poll(client, minimal_zip):
    """
    Полный цикл: загрузить ZIP → получить task_id → дождаться результата.
    Проверяем структуру ответа (не содержательность, а форму).
    """
    import asyncio

    # 1. Загрузка
    upload_resp = await client.post(
        "/api/v1/upload",
        files={"file": ("package.zip", minimal_zip, "application/zip")},
    )
    assert upload_resp.status_code == 202
    task_id = upload_resp.json()["task_id"]

    # 2. Ждём завершения (до 10 секунд)
    result_data = None
    for _ in range(20):
        await asyncio.sleep(0.5)
        result_resp = await client.get(f"/api/v1/results/{task_id}")
        assert result_resp.status_code == 200
        result_data = result_resp.json()
        if result_data["status"] in ("done", "failed"):
            break

    assert result_data is not None
    assert result_data["status"] == "done"

    # 3. Проверяем структуру ответа
    assert "files" in result_data
    assert "formal_check" in result_data
    assert "verdict" in result_data
    assert result_data["total_files"] >= 1

    # Формальный контроль должен вернуть замечания (XML невалиден)
    fc = result_data["formal_check"]
    assert isinstance(fc["issues"], list)
    assert isinstance(fc["is_compliant"], bool)


@pytest.mark.anyio
async def test_no_zip_xml_pz_file_detected(client, empty_zip):
    """
    ZIP без XML ПЗ → FC-001 critical → verdict=RETURNED.
    """
    import asyncio

    upload_resp = await client.post(
        "/api/v1/upload",
        files={"file": ("empty_package.zip", empty_zip, "application/zip")},
    )
    task_id = upload_resp.json()["task_id"]

    # Ждём результата
    for _ in range(20):
        await asyncio.sleep(0.5)
        resp = await client.get(f"/api/v1/results/{task_id}")
        data = resp.json()
        if data["status"] == "done":
            break

    assert data["verdict"] == "RETURNED"
    # FC-001 должен быть в issues
    issue_codes = [i["code"] for i in data["formal_check"]["issues"]]
    assert "FC-001" in issue_codes

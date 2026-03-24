"""
E2E тесты полного пайплайна. Фаза 6.

Тест-кейсы:
  TC-01: ZIP без XML → FC-001 critical, verdict RETURNED
  TC-02: XML v01.04 (устаревший) → FC-002 critical, verdict RETURNED
  TC-03: Расхождение ТЭП → PP963 is_compliant=False  (существующий)
  TC-04: СНИЛС не в реестре НОПРИЗ → found=False       (существующий)
  TC-05: PP154 математика — дефицит мощности             (новый, без LLM)
  TC-06: Идеальный документ — всё зелёное               (существующий)
  TC-07: Полный пайплайн с валидным ZIP → no crash      (новый)

Запуск: cd "Практика в машинном обучении" && pytest tests/test_e2e_pipeline.py -v
"""
from __future__ import annotations

import io
import textwrap
import zipfile
from pathlib import Path

import pytest

from src.agents.external_integration.nopriz_agent import ExternalIntegrationAgent
from src.agents.orchestrator.orchestrator import Orchestrator


# ─────────────────────────────────────────────
#  Фикстуры
# ─────────────────────────────────────────────

@pytest.fixture
def orchestrator():
    """Фикстура: Главный мозг системы."""
    return Orchestrator()


@pytest.fixture
def nopriz_agent():
    """Фикстура: Агент проверки по реестру НОПРИЗ."""
    return ExternalIntegrationAgent(headless=True)


# ─────────────────────────────────────────────
#  Вспомогательная функция: создать ZIP в памяти
# ─────────────────────────────────────────────

def _make_zip(files: dict[str, bytes]) -> bytes:
    """Создаёт ZIP-архив в памяти из словаря {имя: содержимое}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _minimal_xml(schema_version: str = "01.06",
                 cipher: str = "TEST-2025",
                 snils: str = "123-456-789 00") -> bytes:
    """Генерирует минимальный валидный XML (заглушка без XSD)."""
    xml = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <ExplanatoryNote SchemaVersion="{schema_version}" Cipher="{cipher}"
                         xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
            <ObjectInfo ObjectType="NonIndustrialObject">
                <ObjectName>Тестовый объект TC-07</ObjectName>
                <ConstructionType>Строительство</ConstructionType>
            </ObjectInfo>
            <Signers>
                <Signer Role="GIP">
                    <PersonInfo>
                        <SNILS>{snils}</SNILS>
                        <FIO>Иванов Иван Иванович</FIO>
                    </PersonInfo>
                    <NoprizID>ПИ-12345</NoprizID>
                </Signer>
            </Signers>
        </ExplanatoryNote>
    """)
    return xml.encode("utf-8")


# ─────────────────────────────────────────────
#  TC-01: ZIP без XML → RETURNED
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tc01_no_xml_in_zip():
    """
    TC-01: Пакет без XML Пояснительной записки.
    Ожидаемый результат: FC-001 critical, verdict = RETURNED.
    """
    from uuid import uuid4
    from src.api.pipeline import _run_pipeline

    zip_bytes = _make_zip({
        "section03/AR_plan.pdf": b"%PDF-1.4 test pdf content",
        "section05/IOS_scheme.pdf": b"%PDF-1.4 engineering systems",
    })

    result = await _run_pipeline(uuid4(), zip_bytes)

    # FC-001 должен быть critical
    critical_codes = [i.code for i in result.formal_check.issues if i.severity == "critical"]
    assert "FC-001" in critical_codes, f"FC-001 не найден среди критических: {critical_codes}"

    # Вердикт — возврат
    assert result.verdict == "RETURNED", f"Ожидался RETURNED, получен: {result.verdict}"


# ─────────────────────────────────────────────
#  TC-02: XML v01.04 → RETURNED
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tc02_old_xsd_version():
    """
    TC-02: XML с устаревшей версией схемы v01.04.
    Ожидаемый результат: FC-002 critical, verdict = RETURNED.
    """
    from uuid import uuid4
    from src.api.pipeline import _run_pipeline

    zip_bytes = _make_zip({
        "01/pz.xml": _minimal_xml(schema_version="01.04"),
    })

    result = await _run_pipeline(uuid4(), zip_bytes)

    assert result.formal_check is not None
    critical_codes = [i.code for i in result.formal_check.issues if i.severity == "critical"]
    assert "FC-002" in critical_codes, f"FC-002 не найден среди критических: {critical_codes}"
    assert result.verdict == "RETURNED", f"Ожидался RETURNED, получен: {result.verdict}"


# ─────────────────────────────────────────────
#  TC-03: Кросс-валидация ТЭП (существующий)
# ─────────────────────────────────────────────

def test_tc03_building_area_discrepancy(orchestrator):
    """
    TC-03: Кросс-валидация ТЭП (PP963 Agent).
    Ожидаемый результат: Выявление расхождения площадки и статус is_compliant = False.
    """
    document_id = "TEST-TC03-ERR"
    document_text = (
        "РАЗДЕЛ 1: ПЗ. Общая площадь: 1500 кв.м.\n"
        "РАЗДЕЛ 3: АР. Общая площадь: 1550 кв.м."
    )
    report = orchestrator.route_and_execute(document_id, document_text)
    assert "Несоответствие" in report or "False" in report or "false" in report.lower()
    assert "1500" in report or "1550" in report


# ─────────────────────────────────────────────
#  TC-04: ГИП не найден в НОПРИЗ (существующий)
# ─────────────────────────────────────────────

def test_tc04_gip_not_found(nopriz_agent):
    """
    TC-04: Проверка ГИП/ГАП по реестру НОПРИЗ (Спринт 4).
    Ожидаемый результат: СНИЛС, которого нет в базе, даёт found=False.
    """
    bad_snils = "000-000-000 00"
    fio = "Тестов Тест Тестович"
    result = nopriz_agent.verify_specialist(snils=bad_snils, fio=fio)
    assert result["found"] is False
    assert result["status"] in ("not_found", "manual_check_required")


# ─────────────────────────────────────────────
#  TC-05: PP154 — математика дефицита мощности (без LLM)
# ─────────────────────────────────────────────

def test_tc05_energy_balance_math():
    """
    TC-05: PP154 математическая проверка энергобаланса.
    Дефицит: источник 10 МВт < нагрузка 10.5 МВт + потери 0.8 МВт.
    Ожидаемый результат: is_compliant = False, math_done = True.
    """
    from src.agents.compliance.pp154_agent import PP154Agent

    agent = PP154Agent()

    # Дефицит (источник не покрывает нагрузку)
    result_deficit = agent._check_energy_balance_math(
        source_text="Котельная. Установленная тепловая мощность источника: 10 МВт.",
        consumer_text="Суммарная тепловая нагрузка: 10.5 МВт. Потери в тепловых сетях: 0.8 МВт.",
        document_id="TEST-TC05-DEFICIT"
    )
    assert result_deficit.math_done is True, "Математика должна была извлечь числа"
    assert result_deficit.is_compliant is False, "Дефицит должен давать is_compliant=False"
    assert result_deficit.source_mw == pytest.approx(10.0, rel=0.1)

    # Нормальный баланс (≤ 2% невязки)
    result_ok = agent._check_energy_balance_math(
        source_text="ТЭЦ. Тепловая мощность: 10 МВт.",
        consumer_text="Тепловые нагрузки потребителей: 9.5 МВт. Потери: 0.3 МВт.",
        document_id="TEST-TC05-OK"
    )
    assert result_ok.math_done is True
    assert result_ok.is_compliant is True, f"Баланс норма, ошибки: {result_ok.errors}"


# ─────────────────────────────────────────────
#  TC-06: Идеальный документ (существующий)
# ─────────────────────────────────────────────

def test_tc06_complete_valid_document(orchestrator, nopriz_agent):
    """
    TC-06: Идеальный документ. Проходит PP963, PP154 и НОПРИЗ.
    Ожидаемый результат: Все проверки зелёного цвета.
    """
    good_snils = "123-456-789 00"
    gip_result = nopriz_agent.verify_specialist(snils=good_snils)
    assert gip_result["found"] is True

    document_id = "TEST-TC06-OK"
    document_text = (
        "РАЗДЕЛ 1: ПЗ. Площадь: 1000 кв.м. Ист. тепла: 5 МВт\n"
        "РАЗДЕЛ 3: АР. Площадь: 1000 кв.м.\n"
        "РАЗДЕЛ 5: ТС. Нагрузка: 4 МВт, Потери: 0.5 МВт."
    )
    report = orchestrator.route_and_execute(document_id, document_text)
    assert "is_compliant: false" not in report.lower()
    assert "несоответствие" not in report.lower() or "несоответствие выявлено: нет" in report.lower()


# ─────────────────────────────────────────────
#  TC-07: Полный пайплайн с валидным ZIP → no crash
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tc07_full_pipeline_zip():
    """
    TC-07: Полный пайплайн. Минимально корректный ZIP с XML v01.06.
    Ожидаемый результат: пайплайн не падает, возвращает AnalysisResultOut.
    """
    from uuid import uuid4
    from src.api.pipeline import _run_pipeline
    from src.api.schemas import AnalysisResultOut, TaskStatus

    zip_bytes = _make_zip({
        "01/pz.xml": _minimal_xml(schema_version="01.06", cipher="TC07-2025"),
        "03/ar_plan.pdf": b"%PDF-1.4 architectural drawings",
        "04/kr_schema.pdf": b"%PDF-1.4 structural design",
    })

    result = await _run_pipeline(uuid4(), zip_bytes)

    # Не должно упасть
    assert result is not None, "Пайплайн вернул None"
    assert isinstance(result, AnalysisResultOut), f"Неверный тип: {type(result)}"
    assert result.status == TaskStatus.DONE, f"Статус не DONE: {result.status}"

    # XML должен был найтись
    assert result.xml_files_count >= 1, "XML не найден в ZIP"

    # Вердикт должен быть одним из допустимых
    assert result.verdict in ("APPROVED", "RETURNED", "PENDING_EXPERT"), \
        f"Неожиданный вердикт: {result.verdict}"


# ─────────────────────────────────────────────
#  TC-08 (бонус): PP154 горизонт планирования
# ─────────────────────────────────────────────

def test_tc08_planning_horizon():
    """
    TC-08 (бонус): Проверка горизонта планирования PP154.
    10 лет → not ok; 20 лет → ok.
    """
    from src.agents.compliance.pp154_agent import PP154Agent

    agent = PP154Agent()

    years_short, ok_short = agent._check_planning_horizon(
        "Инвестиционная программа рассчитана на 10 лет (2026-2036 год)."
    )
    assert ok_short is False, f"10 лет должен быть < 15, ok={ok_short}"

    years_long, ok_long = agent._check_planning_horizon(
        "Схема теплоснабжения разработана на 20 лет до 2046 года."
    )
    assert ok_long is True, f"20 лет должен быть >= 15, ok={ok_long}"

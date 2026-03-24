"""
ExternalIntegrationAgent — проверка ГИП/ГАП в реестре НОПРИЗ (nrs.nopriz.ru).
Использует Playwright для веб-скрейпинга с сохранённой сессией.

Стратегия деградации:
  1. Если storage_state.json есть → используем сохранённую сессию
  2. Если нет / сайт блокирует → пишем в HITL для ручной проверки
  3. Никогда не падаем — возвращаем status='manual_check_required'
"""
import os
import json
import logging

log = logging.getLogger("nopriz_agent")


class ExternalIntegrationAgent:
    """
    Агент для проверки данных из внешних реестров (НОПРИЗ).
    
    Задача: Убедиться, что ГИП/ГАП состоит в Национальном реестре специалистов.
    Использует: Playwright для веб-скрейпинга реестра (nrs.nopriz.ru).
    Поддерживает: storage_state.json для обхода капчи/Cloudflare.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.registry_url = "https://nrs.nopriz.ru/"
        self.state_file = os.path.join(os.path.dirname(__file__), "storage_state.json")

    def verify_specialist(self, snils: str = None, fio: str = None, inn: str = None) -> dict:
        """
        Проверяет наличие специалиста в реестре НОПРИЗ.
        
        Стратегия деградации:
          - При ошибке Playwright → status='manual_check_required' 
          - Логируем в HITL для ручной проверки
        """
        log.info(f"НОПРИЗ: проверка СНИЛС={snils}, ФИО={fio}")

        result = {
            "found": None,
            "status": "error",
            "message": "Не указаны параметры поиска",
            "specialist_data": {}
        }

        if not snils and not fio:
            return result

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            log.warning("Playwright не установлен. Используем мок-режим.")
            return self._mock_search(snils, fio, inn)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=self.headless,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                )

                _user_agent = (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )

                # Загрузка сохранённой сессии
                if os.path.exists(self.state_file):
                    context = browser.new_context(
                        storage_state=self.state_file,
                        user_agent=_user_agent,
                    )
                    log.info(f"НОПРИЗ: загружена сессия из {self.state_file}")
                else:
                    context = browser.new_context(user_agent=_user_agent)
                    log.warning("НОПРИЗ: storage_state.json не найден. Запусти nopriz_login.py для авторизации.")

                try:
                    page = context.new_page()
                    page.set_default_timeout(30000)  # 30 сек для всех операций

                    # wait_until="commit" — срабатывает при получении первого байта,
                    # не ждёт полной загрузки DOM (сайт может быть медленным)
                    page.goto(self.registry_url, timeout=35000, wait_until="commit")

                    # ── Ждём появления таблицы или кнопки фильтра ──────
                    # Сайт nrs.nopriz.ru использует таблицу с фильтрами,
                    # а не форму поиска. Структура (по скриншоту):
                    #   - Кнопки "Применить фильтр" / "Очистить фильтр"
                    #   - Таблица с инпутами в строке фильтров (2-й input = ФИО)
                    #   - tbody с результатами: столбцы [0]=РегНомер [1]=ФИО ...
                    try:
                        page.wait_for_selector(
                            "button, table, input, .registry, .nrs",
                            timeout=20000,  # 20 сек — сайт грузится медленно
                        )
                    except PWTimeout:
                        raise PWTimeout("Страница не загрузилась за 20 сек")

                    # ── Небольшая пауза для JS-рендеринга (SPA) ────────
                    page.wait_for_timeout(1500)

                    # ── Ищем все text-инпуты на странице ───────────────
                    all_inputs = page.query_selector_all("input[type='text'], input:not([type])")
                    log.info(f"НОПРИЗ: найдено text-инпутов: {len(all_inputs)}")

                    fio_input = None
                    # Стратегия 1: 2-й инпут — это фильтр ФИО (из скриншота)
                    if len(all_inputs) >= 2:
                        fio_input = all_inputs[1]
                    elif len(all_inputs) == 1:
                        fio_input = all_inputs[0]

                    search_value = ""
                    if fio and fio_input:
                        parts = fio.strip().split()
                        # Вводим только фамилию для максимального охвата
                        search_value = parts[0] if parts else fio
                        fio_input.click()
                        fio_input.fill(search_value)
                        log.info(f"НОПРИЗ: ввели ФИО фильтр: '{search_value}'")

                    # ── Нажимаем кнопку фильтра ────────────────────────
                    # Порядок попыток: текст кнопки → class → submit
                    filter_btn = (
                        page.query_selector("button:has-text('Применить')") or
                        page.query_selector("button.apply-filter, .btn-filter, .filter-btn") or
                        page.query_selector("button[type='submit'], input[type='submit']") or
                        page.query_selector("button")  # первая попавшаяся кнопка
                    )

                    if filter_btn:
                        filter_btn.click()
                        log.info("НОПРИЗ: нажали кнопку фильтра")
                        page.wait_for_timeout(2000)  # ждём обновления таблицы
                    else:
                        # Fallback: Enter в поле ввода
                        if fio_input:
                            fio_input.press("Enter")
                            page.wait_for_timeout(2000)

                    # ── Парсим результаты ──────────────────────────────
                    # Структура: table > tbody > tr > td
                    # [0] = Идентиф. номер (ПИ-034252)
                    # [1] = ФИО
                    # Последний = Статус (Действует / Не действует)
                    rows = page.query_selector_all("table tbody tr")
                    log.info(f"НОПРИЗ: строк в таблице: {len(rows)}")

                    # Фильтровая строка содержит <input> — пропускаем её
                    data_row = None
                    for row in rows:
                        if not row.query_selector("input"):
                            data_row = row
                            break

                    if data_row:
                        cells = data_row.query_selector_all("td")
                        if cells and len(cells) >= 2:
                            # Нормализуем: inner_text может содержать \n и лишние пробелы
                            def _clean(el):
                                return " ".join(el.inner_text().split()).strip()

                            texts = [_clean(c) for c in cells]
                            non_empty = [t for t in texts if t]
                            
                            reg_number = non_empty[0] if len(non_empty) > 0 else ""
                            found_fio  = non_empty[1] if len(non_empty) > 1 else ""
                            status_raw = non_empty[-1].lower() if non_empty else "неизвестно"
                            is_active  = "действует" in status_raw

                            result = {
                                "found": True,
                                "status": "active" if is_active else "inactive",
                                "message": (
                                    f"Специалист найден в реестре НОПРИЗ. "
                                    f"Рег. №: {reg_number}. Статус: {status_raw}"
                                ),
                                "specialist_data": {
                                    "fio": found_fio,
                                    "reg_number": reg_number,
                                    "snils": snils or "",
                                    "inn": inn or "",
                                },
                            }
                            log.info(f"НОПРИЗ: найден '{found_fio}', №{reg_number}, статус='{status_raw}', все тексты: {texts}")
                        else:
                            result = {
                                "found": False,
                                "status": "not_found",
                                "message": "Строка найдена, но данные не прочитаны.",
                                "specialist_data": {}
                            }
                    else:
                        result = {
                            "found": False,
                            "status": "not_found",
                            "message": f"Специалист «{search_value or fio}» не найден в реестре НОПРИЗ.",
                            "specialist_data": {}
                        }

                    # Обновляем сохранённую сессию (cookies / localStorage)
                    context.storage_state(path=self.state_file)
                    log.info("НОПРИЗ: сессия обновлена")

                except PWTimeout:
                    log.warning("НОПРИЗ: Timeout — сайт недоступен или капча")
                    result = self._degradation_result(snils, fio, "Timeout при загрузке страницы НОПРИЗ")

                except Exception as parse_err:
                    log.warning(f"НОПРИЗ: ошибка парсинга: {parse_err}")
                    result = self._degradation_result(snils, fio, f"Ошибка парсинга: {parse_err}")

                finally:
                    context.close()
                    browser.close()

        except Exception as e:
            log.error(f"НОПРИЗ: критическая ошибка Playwright: {e}")
            result = self._degradation_result(snils, fio, f"Playwright error: {e}")

        log.info(f"НОПРИЗ: результат — found={result['found']}, status={result['status']}")
        return result

    def _degradation_result(self, snils: str, fio: str, reason: str) -> dict:
        """
        Стратегия деградации: при любой ошибке → пишем в HITL,
        возвращаем status='manual_check_required'.
        """
        self._log_hitl_edge_case(snils or "unknown", fio or "unknown", reason)
        return {
            "found": None,
            "status": "manual_check_required",
            "message": f"Автоматическая проверка невозможна: {reason}. Требуется ручная проверка.",
            "specialist_data": {}
        }

    def _mock_search(self, snils: str, fio: str, inn: str) -> dict:
        """Мок для случаев, когда Playwright не установлен."""
        log.info(f"НОПРИЗ (мок): СНИЛС={snils}, ФИО={fio}")
        if snils and snils.replace("-", "").replace(" ", "").startswith("123"):
            return {
                "found": True,
                "status": "active",
                "message": "Специалист найден (мок-режим).",
                "specialist_data": {
                    "fio": fio or "Иванов Иван Иванович",
                    "snils": snils,
                    "inn": inn or "771234567890",
                    "reg_number": "ПИ-123456"
                }
            }
        return {
            "found": False,
            "status": "not_found",
            "message": "Специалист не найден (мок-режим).",
            "specialist_data": {}
        }

    def _log_hitl_edge_case(self, snils: str, fio: str, reason: str):
        """Логирует в HITL как edge_case для ручной проверки экспертом."""
        try:
            from src.db.database import SessionLocal
            from src.db.models import DisagreementLog

            db = SessionLocal()
            new_log = DisagreementLog(
                document_id=f"NOPRIZ_{snils[:10]}",
                agent_name="ExternalIntegrationAgent/is_edge_case",
                ai_decision=f"Не удалось проверить специалиста в НОПРИЗ.\\nСНИЛС: {snils}\\nФИО: {fio}\\nПричина: {reason}",
                confidence=0.0,
                is_reviewed=False,
            )
            db.add(new_log)
            db.commit()
            db.close()
            log.info(f"НОПРИЗ: записано в HITL (is_edge_case)")
        except Exception as db_err:
            log.error(f"НОПРИЗ: ошибка записи в HITL: {db_err}")


if __name__ == "__main__":
    print("--- Тестовый запуск External Integration Agent ---")
    agent = ExternalIntegrationAgent(headless=True)

    # 1. Успешный кейс (мок)
    print("\nПроверка тестового СНИЛС:")
    res_ok = agent.verify_specialist(snils="123-456-789 00", fio="Терапевт Автоматизатор")
    print(json.dumps(res_ok, ensure_ascii=False, indent=2))

    # 2. Неуспешный кейс (мок)
    print("\nПроверка ложного СНИЛС:")
    res_fail = agent.verify_specialist(snils="999-000-111 22", fio="Неизвестный Инженер")
    print(json.dumps(res_fail, ensure_ascii=False, indent=2))

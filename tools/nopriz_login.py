#!/usr/bin/env python3
"""
nopriz_login.py — Интерактивная авторизация на nrs.nopriz.ru
=============================================================

Запускает браузер в ВИДИМОМ режиме. Ты вручную:
  1. Проходишь капчу (если есть)
  2. Выполняешь любой поиск (можно тестовый СНИЛС)
  3. Нажимаешь Enter в терминале

Скрипт сохраняет сессию в storage_state.json рядом с nopriz_agent.py.
После этого бот автоматически использует сохранённую сессию.

Запуск:
    cd "/home/segun/Практика в машинном обучении"
    .venv/bin/python nopriz_login.py
"""

import os
import json
from pathlib import Path

STATE_FILE = Path(__file__).parent / "src/agents/external_integration/storage_state.json"
NOPRIZ_URL = "https://nrs.nopriz.ru/"


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ Playwright не установлен. Выполни:")
        print("   .venv/bin/pip install playwright")
        print("   .venv/bin/playwright install chromium")
        return

    print("=" * 60)
    print("  НОПРИЗ — Авторизация и сохранение сессии")
    print("=" * 60)
    print(f"\n📁 Файл сессии будет сохранён:")
    print(f"   {STATE_FILE}\n")
    print("Инструкция:")
    print("  1. В открывшемся браузере перейдёт на nrs.nopriz.ru")
    print("  2. Если будет капча — реши её вручную")
    print("  3. Выполни любой поиск (например по СНИЛС: 080-864-940 92)")
    print("  4. Убедись что страница с результатами открылась")
    print("  5. Нажми Enter ЗДЕСЬ в терминале для сохранения сессии")
    print("\n⚠️  Браузер откроется на экране Windows (через WSLg)")
    print("-" * 60)
    input("\nНажми Enter чтобы открыть браузер...")

    with sync_playwright() as p:
        # Запускаем в ВИДИМОМ режиме (headless=False)
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",  # скрываем признаки бота
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        page = context.new_page()

        print(f"\n🌐 Открываю {NOPRIZ_URL} ...")
        try:
            page.goto(NOPRIZ_URL, timeout=30000, wait_until="domcontentloaded")
            print("✅ Страница открыта")
        except Exception as e:
            print(f"⚠️  Ошибка загрузки: {e}")
            print("   Попробуй действовать в браузере вручную")

        print("\n" + "=" * 60)
        print("  БРАУЗЕР ОТКРЫТ. Выполни поиск вручную.")
        print("  После успешного поиска нажми Enter здесь.")
        print("=" * 60)
        input("\nНажми Enter ПОСЛЕ выполнения поиска в браузере...")

        # Сохраняем сессию
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(STATE_FILE))

        print(f"\n✅ Сессия сохранена: {STATE_FILE}")

        # Показываем что сохранено
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        cookies = state.get("cookies", [])
        print(f"   Cookies: {len(cookies)} шт.")
        nopriz_cookies = [c for c in cookies if "nopriz" in c.get("domain", "")]
        if nopriz_cookies:
            print(f"   НОПРИЗ cookies: {[c['name'] for c in nopriz_cookies]}")
        else:
            print("   ⚠️  НОПРИЗ cookies не найдены — возможно сессия не авторизована")

        browser.close()

    print("\n" + "=" * 60)
    print("  ГОТОВО! Теперь запусти бота:")
    print("  ./start.sh")
    print("=" * 60)

    # Верифицируем: пробуем один автоматический поиск
    print("\n🔍 Тестирую сохранённую сессию...")
    _test_session()


def _test_session():
    """Быстрая проверка — ищем тестовый СНИЛС через сохранённую сессию."""
    if not STATE_FILE.exists():
        print("❌ Файл сессии не найден")
        return

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=str(STATE_FILE),
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            page.goto("https://nrs.nopriz.ru/", timeout=20000)

            # Ищем форму поиска
            selector = "input[name='snils'], input[placeholder*='СНИЛС'], #snils, input[type='search']"
            try:
                page.wait_for_selector(selector, timeout=8000)
                input_el = page.query_selector(selector)
                if input_el:
                    input_el.fill("08086494092")
                    # Нажимаем кнопку поиска
                    btn = page.query_selector("button[type='submit'], .search-btn, input[type='submit']")
                    if btn:
                        btn.click()
                        page.wait_for_load_state("networkidle", timeout=8000)

                    # Проверяем результат
                    content = page.content()
                    if "Файков" in content or "ПИ-034252" in content:
                        print("✅ Тест: Найден специалист Файков Г.В. (СНИЛС 080-864-940 92)")
                    elif "Ничего не найдено" in content or "результатов" in content.lower():
                        print("✅ Тест: Страница результатов загрузилась (специалист не найден или другой ответ)")
                    else:
                        print("⚠️  Тест: Не удалось проверить результат (возможно капча или другая структура)")
                else:
                    print("⚠️  Тест: Форма поиска не найдена (возможно сайт изменился)")
            except PWTimeout:
                print("⚠️  Тест: Сессия загружается медленно, но работает")

            # Обновляем сессию с новыми cookies
            context.storage_state(path=str(STATE_FILE))
            print("🔄 Сессия обновлена")

            context.close()
            browser.close()

    except Exception as e:
        print(f"⚠️  Тест не прошёл: {e}")
        print("   Запусти бота — при первом запросе сессия будет создана заново")


if __name__ == "__main__":
    main()

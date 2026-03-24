"""
Telegram-бот для анализа пакетов проектной документации.
МособлГосЭкспертиза — DocumentAnalyzer Agent v2.0

Логика работы:
  • ZIP-файл → немедленная проверка всего архива
  • Обычные файлы (XML, PDF, ...) → попадают в "Корзину" (Session) 
      и ждут нажатия кнопки [🚀 Запустить проверку]
  • Каждый файл анализируется по-отдельности,
    в конце выдаётся общее итоговое сводное заключение.

Запуск: python3 bot.py
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import textwrap
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, Document, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Загружаем .env
load_dotenv()

BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
ADMIN_ID    = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
XSD_VERSION = os.getenv("XSD_VERSION", "01.06")

# Регламент: принимаем версии XSD 01.05 и выше (Приказ Минстроя №421/пр от 28.03.2025)
XSD_MINIMUM_VERSION = "01.05"

# ─────────────────────────────────────────────
#  Пользовательские сессии и режим разработчика
# ─────────────────────────────────────────────
import json
DEBUG_USERS_FILE = "debug_users.json"
_debug_users: set[int] = set()

if os.path.exists(DEBUG_USERS_FILE):
    try:
        with open(DEBUG_USERS_FILE, "r") as f:
            _debug_users = set(json.load(f))
    except Exception as e:
        pass

def _save_debug_users():
    try:
        with open(DEBUG_USERS_FILE, "w") as f:
            json.dump(list(_debug_users), f)
    except Exception:
        pass

# ─────────────────────────────────────────────
#  RAG: гибридный поиск по нормативным документам
# ─────────────────────────────────────────────
_rag_search = None  # Ленивая инициализация при первом /search

def _get_rag_search():
    global _rag_search
    if _rag_search is None:
        try:
            from rag_search import NormSearch
            _rag_search = NormSearch()
            log.info("✅ RAG NormSearch инициализирован (Weaviate OK)")
        except Exception as exc:
            log.warning(f"⚠️ RAG недоступен: {exc}")
    return _rag_search


# ─────────────────────────────────────────────
#  Корзина: хранение файлов между сообщениями
#  { user_id: { filename: bytes } }
# ─────────────────────────────────────────────
user_sessions: dict[int, dict[str, bytes]] = defaultdict(dict)

# Режим сравнения с экспертом
compare_sessions: dict[int, bool] = defaultdict(bool)

# ─────────────────────────────────────────────
#  Логирование
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("moexp_bot")


# ─────────────────────────────────────────────
#  Telegram-логгер (пишет логи прямо в чат)
# ─────────────────────────────────────────────
async def tg_log(app: Application, msg: str, level: str = "ℹ️") -> None:
    """Отправить системный лог в Telegram ADMIN_ID."""
    try:
        timestamp = datetime.now().strftime("%H:%M:%S")
        await app.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"`[{timestamp}]` {level} {msg}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as exc:
        log.warning(f"Не удалось отправить лог в TG: {exc}")


# ─────────────────────────────────────────────
#  Формирование кнопок для Корзины
# ─────────────────────────────────────────────
def _basket_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Запустить проверку пакета", callback_data="run_analysis")],
        [InlineKeyboardButton("🗑 Очистить корзину", callback_data="clear_basket")],
    ])


# ─────────────────────────────────────────────
#  Команды
# ─────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    log.info(f"/start от user_id={user.id} ({user.full_name})")
    await update.message.reply_text(
        textwrap.dedent(f"""\
        👋 <b>Добро пожаловать!</b>

        🏛 <b>МособлГосЭкспертиза — Анализ проектной документации</b>
        Регламент: XML-схема ≥ v<code>{XSD_MINIMUM_VERSION}</code> (Приказ Минстроя №421/пр от 28.03.2025)

        📦 <b>Как пользоваться:</b>
        <b>Способ 1 — ZIP-архив (предпочтительный):</b>
        Упакуйте весь пакет ПД в один ZIP и отправьте.

        <b>Способ 2 — Отдельные файлы (Корзина):</b>
        Отправляйте файлы по одному или группой.
        Когда все готово — нажмите <b>[🚀 Запустить проверку]</b>.

        🔎 <b>Поиск по нормативной базе:</b>
        /search — найти нормы (СП, ФЗ, ГОСТ) по запросу
        Пример: <code>/search ширина пути эвакуации</code>

        🔍 <b>Что проверяю:</b>
        • FC-001 — наличие XML Пояснительной записки
        • FC-002 — версия XSD-схемы ≥ {XSD_MINIMUM_VERSION}
        • FC-003 — наличие ИУЛ
        • FC-004 — признак ЭЦП/XMLDsig
        • FC-005 — комплектность разделов ПД (пп. 72, 84)
        • FC-006 — имена файлов (Приказ №783/пр)

        /basket — посмотреть что в корзине
        /help — справка
        /status — статус системы
        """),
        parse_mode=ParseMode.HTML,
    )
    await tg_log(ctx.application, f"Новый пользователь: {user.full_name} (ID: {user.id})", "👤")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        textwrap.dedent(f"""\
        📖 *Справка*

        *Нормативное требование к версии XML ПЗ:*
        — Минимально допустимая версия XSD: `{XSD_MINIMUM_VERSION}` (с 28.03.2025)
        — Версия `01.04` и ниже: ❌ ВОЗВРАТ (устарела)
        — Версия `01.05`: ✅ принята (действующий стандарт)
        — Версия `01.06`: ✅ принята (новая версия)

        *Вердикты:*
        ✅ `APPROVED` — формальный контроль пройден
        🔄 `PENDING\\_EXPERT` — требуется проверка эксперта
        ❌ `RETURNED` — пакет возвращён на доработку

        *Нормативная база:*
        — ПП РФ №963 (от 01.09.2022): комплектность ПД
        — Приказ Минстроя №783/пр: именование файлов
        — Приказ Минстроя №421/пр: XSD v01.05 (с 28.03.2025)
        """),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        textwrap.dedent(f"""\
        ⚙️ <b>Статус системы</b>

        🟢 Бот: работает
        📐 Мин. версия XSD: <code>{XSD_MINIMUM_VERSION}</code> (по Приказу №421/пр)
        📐 Целевая версия XSD: <code>{XSD_VERSION}</code>
        🕐 Время: <code>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</code>
        📚 RAG: {'🟢 доступен' if _get_rag_search() else '🔴 Weaviate не запущен'}
        """),
        parse_mode=ParseMode.HTML,
    )


async def cmd_basket(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать содержимое корзины пользователя."""
    user = update.effective_user
    files = user_sessions.get(user.id, {})
    if not files:
        await update.message.reply_text(
            "🧺 *Ваша корзина пуста.*\n"
            "Отправьте файлы (XML, PDF и т.д.) чтобы добавить их в пакет.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    file_list = "\n".join(
        f"  {i+1}. <code>{_h(name)}</code> ({len(data) // 1024} КБ)"
        for i, (name, data) in enumerate(files.items())
    )
    await update.message.reply_text(
        f"🧺 <b>Ваш пакет документов ({len(files)} файлов):</b>\n\n{file_list}\n",
        parse_mode=ParseMode.HTML,
        reply_markup=_basket_keyboard(),
    )


# ─────────────────────────────────────────────
#  Сравнение с эталонным заключением (Фаза 9)
# ─────────────────────────────────────────────
async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Активирует режим ожидания PDF Заключения Эксперта"""
    msg = update.message
    if not msg or not msg.from_user: return
    user_id = msg.from_user.id
    
    bot_out_path = Path(f"/tmp/bot_out_{user_id}.json")
    if not bot_out_path.exists():
        await msg.reply_text("⚠️ <b>Ошибка:</b> Сначала нужно загрузить ZIP-архив с проектом, чтобы было с чем сравнивать.", parse_mode=ParseMode.HTML)
        return
        
    compare_sessions[user_id] = True
    await msg.reply_text(
        "⚖️ <b>Режим Экзамена запущен.</b>\n\n"
        "Отправьте мне <b>PDF-файл</b> с оригинальным заключением экспертизы (например, `Заключение...pdf`). Я проанализирую его и сравню со своим ответом.",
        parse_mode=ParseMode.HTML
    )

async def _run_expert_comparison(update: Update, ctx: ContextTypes.DEFAULT_TYPE, doc: Document) -> None:
    user_id = update.effective_user.id
    compare_sessions[user_id] = False  # Сбрасываем флаг ожидания
    
    await update.message.reply_text("⏳ Читаю заключение эксперта и сравниваю с ответами бота. Это займет пару минут...", parse_mode=ParseMode.HTML)
    
    try:
        # 1. Скачиваем PDF эксперта
        import tempfile
        import json
        from tools.parse_conclusion import parse_conclusion
        from tools.compare_with_expert import compare, format_report, load_bot_report
        
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            pdf_path = Path(tmp.name)
            
        tg_file = await ctx.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(pdf_path)
        
        # 2. Вытаскиваем замечания из PDF в JSON (Скрипт 1)
        expert_json = await asyncio.to_thread(parse_conclusion, pdf_path, pdf_path.parent)
        
        # 3. Загружаем вывод бота из кэша
        bot_out_path = Path(f"/tmp/bot_out_{user_id}.json")
        bot_report = load_bot_report(bot_out_path)
        
        # 4. Запускаем сравнение (Скрипт 2)
        cmp_result = compare(expert_json, bot_report, "С2") 
        report_text = format_report(cmp_result)
        
        # 5. Выводим пользователю
        await _send_long(update.message, "<b>Результат Экзамена:</b>\n" + report_text, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        log.error(f"Ошибка при сравнении с экспертом: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка сравнения:\n`{str(e)[:300]}`", parse_mode=ParseMode.MARKDOWN)
    finally:
        import os
        if 'pdf_path' in locals() and pdf_path.exists():
            os.remove(pdf_path)

# ─────────────────────────────────────────────
#  Загрузка локального ZIP архива (для админа)
# ─────────────────────────────────────────────
async def cmd_local_zip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Загрузить локальный ZIP-архив с жесткого диска сервера (до 2ГБ+)."""
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Команда доступна только администратору.")
        return

    args = ctx.args
    if not args:
        await update.message.reply_text(
            "🔎 Укажите абсолютный путь к ZIP-архиву.\n"
            "Пример: <code>/local_zip /home/user/BigArchive_300MB.zip</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    zip_path = " ".join(args).strip()
    path_obj = Path(zip_path)

    if not path_obj.exists() or not path_obj.is_file():
        await update.message.reply_text(f"❌ Файл не найден: <code>{_h(zip_path)}</code>", parse_mode=ParseMode.HTML)
        return
    if path_obj.suffix.lower() != ".zip":
        await update.message.reply_text("❌ Указанный файл не является ZIP-архивом.")
        return

    status_msg = await update.message.reply_text(
        f"📦 Читаю локальный архив <b>{_h(path_obj.name)}</b>...\n"
        f"📂 Путь: <code>{_h(zip_path)}</code>\n"
        f"⏳ Извлекаю файлы в память...",
        parse_mode=ParseMode.HTML,
    )

    try:
        import zipfile
        import tempfile
        import shutil
        import os

        added_count = 0
        total_size = 0
        
        with zipfile.ZipFile(zip_path, "r") as zf:
            extract_dir = tempfile.mkdtemp()
            try:
                for zinfo in zf.infolist():
                    if zinfo.is_dir() or "__MACOSX" in zinfo.filename or "Zone.Identifier" in zinfo.filename:
                        continue
                        
                    # Фикс кодировки CP437 -> CP866 (кириллица Windows)
                    try:
                        raw_bytes = zinfo.filename.encode("cp437")
                        try:
                            name = raw_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            name = raw_bytes.decode("cp866")
                    except Exception:
                        name = zinfo.filename

                    fname = name.replace("\\", "/")  # Сохраняем структуру папок, но нормализуем слеши
                    if not Path(name).name:
                        continue

                    # Извлекаем во временный файл с коротким безопасным именем во избежание Errno 36
                    safe_extracted_path = os.path.join(extract_dir, f"tmp_{added_count}.bin")
                    with zf.open(zinfo) as src, open(safe_extracted_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                        
                    with open(safe_extracted_path, "rb") as f:
                        file_data = f.read()
                    os.remove(safe_extracted_path)

                    user_sessions[user.id][fname] = file_data
                    added_count += 1
                    total_size += len(file_data)
            finally:
                shutil.rmtree(extract_dir, ignore_errors=True)

        size_mb = total_size / (1024 * 1024)
        await status_msg.edit_text(
            f"✅ <b>Архив успешно прочитан!</b>\n\n"
            f"📥 Файл: <code>{_h(path_obj.name)}</code>\n"
            f"📄 Добавлено файлов: <b>{added_count}</b>\n"
            f"💾 Общий объём: <b>{size_mb:.1f} МБ</b>\n\n"
            f"Нажмите <b>✅ Проверить</b>, чтобы запустить анализ всего пакета.",
            parse_mode=ParseMode.HTML,
            reply_markup=_basket_keyboard(),
        )
        await tg_log(ctx.application, f"Админ локально загрузил архив {path_obj.name} ({size_mb:.1f} МБ, {added_count} файлов)", "📥")

    except zipfile.BadZipFile:
        await status_msg.edit_text(f"❌ Это повреждённый или многотомный ZIP-архив.")
    except Exception as e:
        log.error(f"Ошибка при чтении {zip_path}: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Ошибка: <code>{_h(str(e))}</code>", parse_mode=ParseMode.HTML)


# ─────────────────────────────────────────────
#  Поиск по нормативной базе (/search)
# ─────────────────────────────────────────────
async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Гибридный поиск по нормативным документам (Weaviate BM25 + Semantic)."""
    query = " ".join(ctx.args).strip() if ctx.args else ""
    if not query:
        await update.message.reply_text(
            "🔎 Укажите запрос после команды.\n"
            "Примеры:\n"
            "  <code>/search ширина пути эвакуации школа</code>\n"
            "  <code>/search СП 42 таблица расстояния от застройки</code>\n"
            "  <code>/search состав проектной документации раздел 5</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    s = _get_rag_search()
    if s is None:
        await update.message.reply_text(
            "⚠️ База нормативных документов (Weaviate) недоступна.\n"
            "Убедитесь, что Docker-контейнер <code>moexp_weaviate</code> запущен.",
            parse_mode=ParseMode.HTML,
        )
        return

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    log.info(f"RAG /search: '{query}' от {update.effective_user.full_name}")

    try:
        results = s.hybrid(query, top_k=3, alpha=0.5)
    except Exception as exc:
        log.error(f"RAG поиск упал: {exc}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка поиска: {_h(str(exc)[:200])}",
                                        parse_mode=ParseMode.HTML)
        return

    if not results:
        await update.message.reply_text(
            f"🔍 По запросу <i>{_h(query)}</i> ничего не найдено.\n"
            "Попробуйте переформулировать или уточнить запрос.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [f"🔎 <b>Поиск по нормативной базе:</b> <i>{_h(query)}</i>\n"]
    for i, r in enumerate(results, 1):
        table_badge = " 📊" if r.is_table else ""
        section_path = f"\n  <i>{_h(r.breadcrumb[:100])}</i>" if r.breadcrumb and r.breadcrumb != r.doc_title else ""
        preview = _h(r.raw_text[:350].replace("\n", " "))
        lines.append(
            f"{i}. <b>{_h(r.doc_title[:70])}</b>{table_badge}{section_path}\n"
            f"  {preview}{'...' if len(r.raw_text) > 350 else ''}\n"
            f"  🔗 <a href='{r.source_url}'>Открыть документ</a>\n"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ─────────────────────────────────────────────
#  /debug — переключатель режима разработчика
# ─────────────────────────────────────────────
async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Включить/выключить подробный отчёт по агентам после каждого анализа."""
    user_id = update.effective_user.id
    if user_id in _debug_users:
        _debug_users.discard(user_id)
        _save_debug_users()
        await update.message.reply_text(
            "🔴 <b>Debug-режим ВЫКЛЮЧЕН</b>\n"
            "После следующего анализа будет только стандартный вывод.",
            parse_mode=ParseMode.HTML,
        )
    else:
        _debug_users.add(user_id)
        _save_debug_users()
        await update.message.reply_text(
            "🟢 <b>Debug-режим ВКЛЮЧЁН</b>\n\n"
            "После каждого анализа ты увидишь подробный отчёт:\n"
            "  📁 [S1-S2] FileClassifier + XmlParser\n"
            "  📋 [S3] FormalCheckRunner — правила, коды, severity\n"
            "  🕵️ [S4-S5] PP963Agent — LLM, RAG чанки, conf по разделам\n"
            "  🏭 [S6] PP154Agent — Теплоснабжение\n"
            "  💰 [S7] EstimateChecker — Смета (ССР, ЛСР)\n"
            "  🌐 [S8] ExternalIntegration — НОПРИЗ статус\n\n"
            "<i>Выключить: /debug</i>",
            parse_mode=ParseMode.HTML,
        )


# ─────────────────────────────────────────────
#  HITL — просмотр записей для эксперта (/hitl)
# ─────────────────────────────────────────────
async def cmd_hitl(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать последние неотвеченные записи из Disagreement Log."""
    try:
        from src.db.database import SessionLocal
        from src.db.models import DisagreementLog

        db = SessionLocal()
        unreviewed = (
            db.query(DisagreementLog)
            .filter(DisagreementLog.is_reviewed == False)
            .order_by(DisagreementLog.created_at.desc())
            .limit(5)
            .all()
        )
        db.close()

        if not unreviewed:
            await update.message.reply_text(
                "✅ Нет неотвеченных записей в Disagreement Log.\n"
                "Все решения агентов подтверждены.",
                parse_mode=ParseMode.HTML,
            )
            return

        lines = [f"<b>⚠️ Записи HITL, ожидающие проверки ({len(unreviewed)}):</b>\n"]
        for i, record in enumerate(unreviewed, 1):
            trigger = record.agent_name.split("/")[-1] if "/" in record.agent_name else "confidence"
            trigger_icon = {
                "confidence": "🔵",
                "critical_error": "🔴",
                "agent_disagreement": "🟡",
                "is_edge_case": "🟠",
            }.get(trigger, "⚪")

            lines.append(
                f"{trigger_icon} <b>{i}. [{_h(record.agent_name)}]</b>\n"
                f"  📄 Документ: <code>{_h(record.document_id[:30])}</code>\n"
                f"  🎯 Уверенность: {record.confidence:.0%}\n"
                f"  💬 {_h(record.ai_decision[:200])}{'...' if len(record.ai_decision) > 200 else ''}\n"
                f"  🕐 {record.created_at.strftime('%d.%m.%Y %H:%M') if record.created_at else '—'}\n"
            )

        lines.append(
            "\n<i>Для ответа используйте:\n"
            "<code>/hitl_review [ID] [комментарий]</code></i>"
        )

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        log.error(f"HITL ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка загрузки HITL: {e}")
# ─────────────────────────────────────────────
async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    doc: Document = update.message.document
    user = update.effective_user
    log.info(f"Получен файл: {doc.file_name} от {user.full_name} (ID={user.id})")

    # Перехват для режима Сравнения с экспертом
    if compare_sessions.get(user.id) and doc.file_name.lower().endswith(".pdf"):
        await _run_expert_comparison(update, ctx, doc)
        return

    if doc.file_size > 50 * 1024 * 1024:
        size_mb = doc.file_size / (1024 * 1024)
        await update.message.reply_text(
            f"⚠️ Файл *{doc.file_name}* слишком большой ({size_mb:.1f} МБ).\n\n"
            f"Telegram Bot API не принимает файлы ›50 МБ.\n\n"
            f"*Что сделать:*\n"
            f"1. Создайте **несколько независимых** ZIP-архивов по 30-40 МБ\n"
            f"   (⚠️ **НЕ используйте** функцию 'Разделить на тома' в архиваторах! Каждый ZIP должен быть самостоятельным)\n"
            f"2. Отправьте каждый архив отдельно — бот сам соберет все файлы в один пакет\n"
            f"3. Как отправите все архивы — нажмите *✅ Проверить*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    # ── ZIP: распаковываем и добавляем файлы в корзину (НЕ запускаем анализ сразу!) ──
    if doc.file_name.lower().endswith(".zip"):
        status_msg = await update.message.reply_text(
            f"📦 Получаю *{doc.file_name}*...\n⏳ Распаковываю в пакет...",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp_path = tmp.name

            success = False
            last_err = None
            
            # Попытки скачивания (до 3 раз) из-за частых отвалов TG API на файлах > 15 МБ
            for attempt in range(1, 4):
                try:
                    tg_file = await ctx.bot.get_file(doc.file_id)
                    await tg_file.download_to_drive(tmp_path)
                    success = True
                    break
                except Exception as e:
                    last_err = e
                    log.warning(f"[download] Попытка {attempt}/3 не удалась для {doc.file_name}: {e}")
                    await asyncio.sleep(2)
            
            if not success:
                import os
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise Exception(f"Не удалось скачать файл после 3 попыток: {last_err}")

            added_count = 0
            skipped = []
            try:
                try:
                    with zipfile.ZipFile(tmp_path, "r") as zf:
                        import tempfile
                        import shutil
                        extract_dir = tempfile.mkdtemp()
                        try:
                            for zinfo in zf.infolist():
                                # Пропускаем папки и скрытые файлы
                                if zinfo.is_dir() or zinfo.filename.startswith("__"):
                                    continue
                                # Берём только имя файла (без вложенных папок)
                                fname = zinfo.filename.replace("/", "_").replace("\\", "_")
                                if not Path(zinfo.filename).name:
                                    continue
                                    
                                # Распаковываем файл физически (обходит баги .read() и seek)
                                extracted_path = zf.extract(zinfo, path=extract_dir)
                                with open(extracted_path, "rb") as f:
                                    file_data = f.read()
                                os.remove(extracted_path)
                                
                                user_sessions[user.id][fname] = file_data
                                added_count += 1
                        finally:
                            shutil.rmtree(extract_dir, ignore_errors=True)
                except (zipfile.BadZipFile, OSError) as e:
                    await status_msg.edit_text(
                        f"❌ *{doc.file_name}* — повреждённый или многотомный ZIP-архив.\n"
                        f"Бот умеет читать только **независимые** архивы. Файлы разбитые через (part1, part2 и т.д.) не поддерживаются.\n"
                        f"Пожалуйста, перепакуйте исходные файлы в несколько обычных архивов.",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    return
            finally:
                import os
                if 'tmp_path' in locals() and os.path.exists(tmp_path):
                    os.remove(tmp_path)

            n_total = len(user_sessions[user.id])
            await status_msg.edit_text(
                f"✅ *{doc.file_name}* распакован → добавлено **{added_count}** файлов в пакет\n\n"
                f"📦 *Всего в пакете: {n_total} файлов*\n"
                f"Отправьте ещё части или нажмите *✅ Проверить* для анализа.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_basket_keyboard(),
            )
            await tg_log(ctx.application,
                         f"📦 ZIP: `{doc.file_name}` → {added_count} файлов добавлено "
                         f"(user: {user.full_name}, всего в пакете: {n_total})", "📦")

        except Exception as exc:
            err_text = str(exc)
            if "file is too big" in err_text.lower() or "file_is_too_big" in err_text.lower():
                await status_msg.edit_text(
                    f"⚠️ Telegram не даёт скачать *{doc.file_name}* — файл слишком большой для API.\n\n"
                    f"Разбейте ZIP на части по 30-40 МБ и отправьте каждую отдельно.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                log.error(f"Ошибка получения ZIP: {exc}", exc_info=True)
                await status_msg.edit_text(
                    f"❌ Ошибка при получении *{doc.file_name}*:\n`{err_text[:200]}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
        return

    # ── Обычный файл (не ZIP) — добавляем в корзину ──
    is_xml = doc.file_name.lower().endswith(".xml")
    is_large = doc.file_size > 2 * 1024 * 1024  # > 2 МБ

    if is_large and not is_xml:
        # Большие PDF/файлы НЕ скачиваем в память — только регистрируем имя.
        size_kb = doc.file_size // 1024
        stub = (f"%PDF-1.4 % stub:{size_kb}KB {doc.file_name}").encode()
        user_sessions[user.id][doc.file_name] = stub
        log.info(f"Большой файл {doc.file_name} ({size_kb} КБ) — сохранён стаб")
    else:
        try:
            tg_file = await ctx.bot.get_file(doc.file_id)
            buf = io.BytesIO()
            await tg_file.download_to_memory(buf)
            user_sessions[user.id][doc.file_name] = buf.getvalue()
        except Exception as exc:
            log.error(f"Ошибка скачивания файла: {exc}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка загрузки файла:\n`{exc}`",
                                            parse_mode=ParseMode.MARKDOWN)
            return

    n = len(user_sessions[user.id])
    names_short = list(user_sessions[user.id].keys())[-5:]  # последние 5
    names_str = "\n".join(f"  • `{nm}`" for nm in names_short)
    if n > 5:
        names_str = f"  _...и ещё {n-5} файлов_\n" + names_str
    await update.message.reply_text(
        f"✅ Добавлен: *{doc.file_name}*\n\n"
        f"📦 *Пакет: {n} файлов*\n{names_str}\n\n"
        "Добавьте ещё файлы или запустите проверку.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_basket_keyboard(),
    )
    await tg_log(ctx.application,
                 f"📥 Файл добавлен: `{doc.file_name}` (user: {user.full_name}, всего: {n})", "📁")


# ─────────────────────────────────────────────
#  Обработчик нажатий Inline-кнопок
# ─────────────────────────────────────────────
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    # answer() сразу — убираем "часики" в Telegram
    # Если callback устарел (бот перезапустился) — просто игнорируем
    try:
        await query.answer()
    except BadRequest as e:
        if "too old" in str(e).lower() or "query id" in str(e).lower():
            log.warning(f"Устаревший callback от {user.full_name}: {e}")
            return  # тихо игнорируем — пользователь должен нажать кнопку снова
        raise

    if query.data == "clear_basket":
        user_sessions.pop(user.id, None)
        try:
            await query.edit_message_text("🗑 Корзина очищена. Можете добавлять файлы заново.")
        except BadRequest:
            await ctx.bot.send_message(user.id, "🗑 Корзина очищена.")
        return

    if query.data == "run_analysis":
        files = user_sessions.get(user.id, {})
        if not files:
            await query.answer("⚠️ Корзина пуста!", show_alert=True)
            return
        try:
            await query.edit_message_text("⏳ Собираю пакет и запускаю анализ...")
        except BadRequest:
            pass

        # Упаковываем все файлы из Корзины в один виртуальный ZIP
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "a", zipfile.ZIP_DEFLATED, False) as zf:
            for fname, fdata in files.items():
                zf.writestr(fname, fdata)
        zip_bytes = zip_buf.getvalue()

        # Очищаем корзину сразу (чтобы пользователь мог загружать следующий пакет)
        user_sessions.pop(user.id, None)

        # Запускаем пайплайн
        await _run_analysis_and_report(update, ctx, zip_bytes, f"Пакет ({len(files)} файлов)")
        await tg_log(ctx.application,
                     f"🚀 Запуск проверки корзины: {len(files)} файлов (user: {user.full_name})", "📦")


# ─────────────────────────────────────────────
#  Запуск проверки готового ZIP-архива
# ─────────────────────────────────────────────
async def _run_zip_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE, doc: Document) -> None:
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    status_msg = await update.message.reply_text(
        f"📦 ZIP-архив получен: *{doc.file_name}*\n⏳ Запускаю проверку...",
        parse_mode=ParseMode.MARKDOWN,
    )
    await tg_log(ctx.application,
                 f"📦 ZIP: `{doc.file_name}` (user: {update.effective_user.full_name})", "📦")
    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        zip_bytes = buf.getvalue()
        try:
            await status_msg.delete()
        except BadRequest:
            pass
        await _run_analysis_and_report(update, ctx, zip_bytes, doc.file_name)
    except Exception as exc:
        log.error(f"Ошибка обработки ZIP: {exc}", exc_info=True)
        try:
            await status_msg.edit_text(f"❌ Ошибка при обработке:\n`{str(exc)[:300]}`",
                                        parse_mode=ParseMode.MARKDOWN)
        except BadRequest:
            await update.message.reply_text(f"❌ Ошибка при обработке:\n`{str(exc)[:300]}`",
                                             parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────
#  Единый метод запуска пайплайна и форматирования
# ─────────────────────────────────────────────
async def _run_analysis_and_report(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    zip_bytes: bytes,
    package_name: str,
) -> None:
    from src.api.pipeline import _run_pipeline
    from uuid import uuid4
    start = datetime.now()
    try:
        result = await _run_pipeline(uuid4(), zip_bytes)
    except Exception as exc:
        log.error(f"Пайплайн упал с ошибкой: {exc}", exc_info=True)
        await update.effective_message.reply_text(
            f"❌ Ошибка при анализе пакета:\n`{str(exc)[:300]}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    elapsed = (datetime.now() - start).total_seconds()
    
    # Сохраняем результат для последующего /compare
    try:
        tmp_bot_out = Path(f"/tmp/bot_out_{update.effective_user.id}.json")
        with open(tmp_bot_out, "w", encoding="utf-8") as f:
            f.write(result.model_dump_json())
    except Exception as e:
        log.warning(f"Не удалось сохранить bot_out: {e}")

    try:
        # ── Часть 1: анализ каждого файла ─────────
        per_file_msg = _format_per_file(result)
        if per_file_msg:
            await _send_long(update.effective_message, per_file_msg, ParseMode.HTML)

        # ── Часть 2: итоговое сводное заключение ───
        summary_msg = _format_summary(result, package_name, elapsed)
        await _send_long(update.effective_message, summary_msg, ParseMode.HTML)

        # ── Часть 2б: PDF-заключение по ГОСТ ───────
        if getattr(result, "pdf_report", None):
            try:
                # Транслит для безопасного имени файла (Telegram API не любит кириллицу)
                cipher = (result.xml_summary.cipher if result.xml_summary else "") or "report"
                
                # Простая таблица транслитерации
                tr_map = {
                    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
                    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
                    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
                    'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
                    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
                    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'E',
                    'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M',
                    'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
                    'Ф': 'F', 'Х': 'H', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Sch',
                    'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya',
                    '/': '-', ' ': '_'
                }
                safe_cipher = "".join(tr_map.get(c, c) for c in cipher)
                # Оставляем только ASCII
                ascii_filename = "".join(c for c in safe_cipher if ord(c) < 128)
                pdf_filename = f"Expertise_Report_{ascii_filename}.pdf"
                
                # Отправляем байты напрямую
                await update.effective_message.reply_document(
                    document=result.pdf_report,
                    filename=pdf_filename,
                    caption=(
                        "📄 <b>Официальное заключение по ГОСТ Р 7.0.97-2016</b>\n"
                        "МособлГосЭкспертиза — DocumentAnalyzer v2.0"
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception as pdf_err:
                log.warning(f"Не удалось отправить PDF: {pdf_err}")

        verdict = getattr(result, "verdict", "?")
        await tg_log(ctx.application,
                     f"✅ Готово: `{package_name}` | Вердикт: `{verdict}` | Время: {elapsed:.1f}с", "✅")
    except Exception as send_err:
        log.error(f"Ошибка при отправке результатов или PDF: {send_err}", exc_info=True)
        try:
            await update.effective_message.reply_text("⚠️ Произошла ошибка при отправке результатов (репорт слишком длинный). Проверьте логи.")
        except:
            pass

    # ── Часть 3: Отчёт разработчика (если включён /debug) ─────────────
    user_id = update.effective_user.id if update.effective_user else 0
    if user_id in _debug_users:
        debug_msg = _format_debug_report(result, elapsed)
        # Разбиваем debug по секциям агентов чтобы каждый чанк читался отдельно
        # Telegram лимит: 4096 символов. _send_long нарезает автоматически.
        await update.effective_message.reply_text(
            "<b>🔬 DEBUG MODE — начало отчёта по агентам</b>",
            parse_mode=ParseMode.HTML
        )
        await _send_long(update.effective_message, debug_msg, ParseMode.HTML)



# ─────────────────────────────────────────────
#  Нарезка длинных сообщений (лимит Telegram = 4096 симв)
# ─────────────────────────────────────────────
TG_MAX = 4000   # немного меньше лимита для запаса


async def _send_long(message, text: str, parse_mode: str) -> None:
    """Send text, splitting into TG_MAX-character chunks if needed."""
    if not text:
        return
    # Увеличим таймауты, так как длинные сообщения и баги сети могут вызывать ReadError
    t_kwargs = {"read_timeout": 30, "write_timeout": 30, "connect_timeout": 15}
    if len(text) <= TG_MAX:
        await message.reply_text(text, parse_mode=parse_mode, **t_kwargs)
        return
    try:
        # Иначе — нарезаем по целым строкам, не поря теги
        lines = text.split("\n")
        chunk: list[str] = []
        size = 0
        for line in lines:
            line_len = len(line) + 1  # +1 для \n
            if size + line_len > TG_MAX and chunk:
                await message.reply_text("\n".join(chunk), parse_mode=parse_mode, **t_kwargs)
                chunk = []
                size = 0
            chunk.append(line)
            size += line_len
        if chunk:
            await message.reply_text("\n".join(chunk), parse_mode=parse_mode, **t_kwargs)
    except Exception as exc:
        log.error(f"[_send_long] Ошибка при отправке куска сообщения: {exc}", exc_info=True)
        raise


# ─────────────────────────────────────────────
#  HTML-экранирование спецсимволов
# ─────────────────────────────────────────────
def _h(text: str) -> str:
    """Экранирование спецсимволов HTML."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─────────────────────────────────────────────
#  Debug-отчёт (режим разработчика)
# ─────────────────────────────────────────────
def _format_debug_report(result, elapsed: float) -> str:
    """Подробный отчёт по каждому агенту для режима разработчика (с ВХОД/ВЫХОД)."""
    from src.agents.groq_client import MODEL_USAGE_COUNTERS
    
    lines = ["<b>🔬 DEBUG — Архитектура Агентов (Маршрутизация)</b>", ""]

    # ── АГЕНТ 1: Orchestrator ──
    lines.append("🧠 <b>[АГЕНТ 1] Orchestrator (Маршрутизатор)</b>")
    lines.append("  <i>[ВХОД]</i> Имена и размеры файлов пакета, а также извлечённый текст из PDF (до 50000 символов со всех PDF)")
    # Восстанавливаем логику оркестратора из result: запускался PP154 или нет
    run_154 = "Да" if getattr(result, "pp154_report", None) else "Нет (не профильный объект)"
    lines.append(f"  <i>[ВЫХОД]</i> План выполнения: PP963=Да, PP154={run_154}")
    lines.append("")

    # ── АГЕНТ 2: Document Analyzer ──
    lines.append("<b>📁 [АГЕНТ 2] Document Analyzer (FileClassifier + XmlParser + FormalCheck)</b>")
    lines.append("  <i>[ВХОД]</i> Сырой ZIP-архив с ПД")
    lines.append(f"  <i>[ВЫХОД]</i> Файлов классифицировано: {result.total_files} (XML: {result.xml_files_count}, PDF: {result.pdf_files_count}, Сканы: {result.scan_files_count})")
    
    if getattr(result, "files", None):
        lines.append("  <i>[ВЫХОД] Список принятых документов:</i>")
        display_files = result.files[:30]
        for f in display_files:
            lines.append(f"    • <code>{_h(f.name)}</code>")
        if len(result.files) > 30:
            lines.append(f"    <i>...и ещё {len(result.files) - 30} файлов скрыто</i>")

    
    if result.xml_summary:
        xs = result.xml_summary
        lines.append(f"  <i>[ВЫХОД] Метаданные:</i> Шифр={xs.cipher}, XSD=v{xs.schema_version}")
    
    if result.formal_check:
        fc = result.formal_check
        lines.append(f"  <i>[ВЫХОД] Формальные проверки:</i> {getattr(fc, 'rules_checked', '?')} правил. Ошибок: {fc.critical_count} шт.")
    lines.append("")

    # ── АГЕНТ 3: PP87/963 Compliance + АГЕНТ 5: RAG ──
    lines.append("<b>🕵️ [АГЕНТ 3 + 5] PP963 Compliance & Knowledge Base (RAG)</b>")
    lines.append("  <i>[ВХОД]</i> ТЭП из XML, текст PDF-файлов")
    if result.pp963_report:
        pp = result.pp963_report
        lines.append(f"  <i>[ВЫХОД] PP963:</i> ТЭП консистентны? {'✅ Да' if pp.tep_compliant else '❌ Нет'}")
        rag_queried = sum(1 for s in pp.sections if s.norm_refs) if pp.sections else 0
        lines.append(f"  <i>[ВЫХОД] База Знаний:</i> Сделано {rag_queried}/{pp.sections_checked} RAG-запросов к Weaviate.")
        lines.append(f"  <i>[ВЫХОД] База Знаний:</i> Найдено норм: {pp.rag_chunks_used} шт.")
        # Секции
        if pp.sections:
            passed = sum(1 for s in pp.sections if s.passed)
            lines.append(f"  <i>[ВЫХОД] Итог по разделам:</i> {passed}/{len(pp.sections)} прошли проверку")
    else:
        lines.append("  ⚠️ Не запускался или упал")
    lines.append("")

    # ── АГЕНТ 4: PP154Agent (Теплоснабжение) ──
    lines.append("<b>🏭 [АГЕНТ 4] PP154 Compliance (Теплоснабжение)</b>")
    lines.append("  <i>[ВХОД]</i> Текст ПД, распознанный OCR (pdf/doc)")
    if getattr(result, "pp154_report", None):
        p154 = result.pp154_report
        
        eb_str = "Нет данных"
        if getattr(p154, "energy_balance", None):
            eb = p154.energy_balance
            eb_icon = "✅" if eb.is_compliant else ("⚠" if not eb.math_done else "❌")
            eb_str = f"Ист: {eb.source_mw}МВт -> Потребитель: {eb.load_mw}МВт + Потери {eb.loss_mw}МВт (Невязка: {eb.imbalance_pct:.1f}%) {eb_icon}"
            
        lines.append(f"  <i>[ВЫХОД] Энергобаланс (математика):</i> {eb_str}")
        lines.append(f"  <i>[ВЫХОД] Горизонт:</i> {p154.horizon_years} лет {'✅' if p154.horizon_ok else '❌'}")
        lines.append(f"  <i>[ВЫХОД] Разделы:</i> {len(p154.sections_found)}/13 найдено")
    else:
        lines.append("  ⏭️ <i>[ВЫХОД]</i> Пропущен (Оркестратор решил, что это не промышленный объект/теплосеть)")
    lines.append("")

    # ── АГЕНТ 4.5: Смета (Раздел 12) ──
    lines.append("")
    lines.append("💰 <b>[АГЕНТ 4.5] Estimate Checker (Сметная документация)</b>")
    lines.append("  <i>[ВХОД]</i> Имена и размеры файлов пакета, извлечённый текст из PDF (для поиска по ключевым словам и подписям)")
    if getattr(result, "estimate_report", None):
        est = result.estimate_report
        lines.append(f"  <i>[ВЫХОД] Раздел найден:</i> {'✅ Да' if est.found else '❌ Нет'}")
        if est.found:
            lines.append(f"  <i>[ВЫХОД] Найдено файлов:</i> {len(est.estimate_files)} шт.")
            if est.ssr_approved is True:
                lines.append("  <i>[ВЫХОД] Утверждение ССР:</i> ✅ Подтверждено")
            elif est.ssr_approved is False:
                lines.append("  <i>[ВЫХОД] Утверждение ССР:</i> ❌ ССР НЕ утвержден застройщиком")
            else:
                lines.append("  <i>[ВЫХОД] Утверждение ССР:</i> ⚠️ ССР не найден или нет текста")
            
            if est.issues:
                lines.append(f"  <i>[ВЫХОД] Замечаний:</i> {len(est.issues)} шт.")
    else:
        lines.append("  ⏭️ <i>[ВЫХОД]</i> Не запускался или упал")
    lines.append("")


    # ── АГЕНТ 7: Human-in-The-Loop (NODPRIZ) ─
    lines.append("<b>🌐 [АГЕНТ 7] Human-in-the-Loop & Внешние интеграции (НОПРИЗ)</b>")
    if result.xml_summary and result.xml_summary.chief_engineer:
        lines.append(f"  <i>[ВХОД]</i> СНИЛС ГИПа: {result.xml_summary.chief_engineer.snils}")
    else:
        lines.append("  <i>[ВХОД]</i> СНИЛС ГИПа не извлечен")
        
    if result.nopriz_check:
        nc = result.nopriz_check
        if nc.status == "skipped":
            lines.append("  <i>[ВЫХОД]</i> ⚠️ Данные ГИП отсутствуют в XML — проверка НОПРИЗ пропущена")
        elif nc.found == True:
            found_icon = "✅"
            lines.append(f"  <i>[ВЫХОД]</i> {found_icon} ГИП найден в реестре НОПРИЗ ({_h(nc.reg_number or 'Без номера')})")
            lines.append(f"  ФИО: {_h(nc.fio)} | Статус: {_h(nc.status)}")
        elif nc.found == False:
            lines.append(f"  <i>[ВЫХОД]</i> ❌ ГИП НЕ найден (ФИО: {_h(nc.fio)}, Рег.№: {_h(nc.reg_number)})")
        else:
            lines.append(f"  <i>[ВЫХОД]</i> ⚠️ {_h(nc.message or nc.status)}")
    else:
        lines.append("  <i>[ВЫХОД]</i> ⚠️ Ошибка проверки или данные не найдены")
    lines.append("")

    
    # ── АГЕНТ 6: Report Generator ──
    lines.append("<b>📄 [АГЕНТ 6] Report Generator</b>")
    lines.append("  <i>[ВХОД]</i> Выборки ВСЕХ предыдущих агентов")
    has_pdf = "✅ Сгенерирован (PDF)" if result.pdf_report else "❌ Ошибка"
    lines.append(f"  <i>[ВЫХОД] Отчёт:</i> {has_pdf}")
    lines.append("")

    # ── ИТОГОВЫЙ ВЕРДИКТ ──
    verdict = getattr(result, "verdict", "Н/Д")
    reason = getattr(result, "verdict_reason", "Причина не указана")
    verdict_emoji = {
        "POSITIVE": "✅",
        "NEGATIVE": "❌",
        "PENDING_EXPERT": "🚴",
        "RETURNED_FOR_REVISION": "⚠️"
    }.get(verdict, "❓")
    
    lines.append("<b>⚖️ [РЕЗУЛЬТАТ] Итоговое заключение</b>")
    lines.append(f"  <i>Статус:</i> {verdict_emoji} <b>{verdict}</b>")
    lines.append(f"  <i>Причина:</i> <code>{_h(reason)}</code>")
    lines.append("")


    # ── Мониторинг Моделей ─────────────────────
    lines.append("<b>📊 [МОНИТОРИНГ] Использование LLM</b>")
    
    # Так как пользователь мог поменять имена моделей в GROQ_MODELS, соберем все ключи
    for model_id, count in MODEL_USAGE_COUNTERS.items():
        if count > 0:
            lines.append(f"  <i>{model_id}:</i> {count} вызовов")
            
    if not any(c > 0 for c in MODEL_USAGE_COUNTERS.values()):
        lines.append("  <i>Счетчики по нулям</i>")
        
    lines.append("")

    # ── Тайминг ────────────────────────────────
    lines.append(f"<b>⏱ Общее время:</b> {elapsed:.1f}с")
    lines.append(f"<b>📦 Файлов в пакете:</b> {result.total_files}")
    lines.append("")
    lines.append("<i>Выключить: /debug</i>")

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  Форматирование: каждый файл отдельно
# ─────────────────────────────────────────────
def _format_per_file(result) -> str:
    """Анализ каждого файла из пакета + детали XML ПЗ (в формате HTML)."""
    lines = [f"<b>📂 Анализ состава пакета ({result.total_files} файла/файлов):</b>", ""]

    # Показываем карточку по каждому файлу из пакета
    file_type_icons = {
        "xml_pz":    "📄 XML ПЗ",
        "xml_other": "📄 XML (ТЗ/Иное)",
        "pdf_text":  "📃 PDF (текст)",
        "pdf_scan":  "🖼️ PDF (скан)",
        "estimate":  "💰 Смета",
        "drawing":   "📐 Чертёж",
        "archive":   "📦 Архив",
        "sig":       "🔐 ЭЦП (.sig)",
        "unknown":   "❓ Неизвестный",
    }

    display_files = result.files[:30]
    for i, f in enumerate(display_files, 1):
        ft = f.file_type.lower() if f.file_type else "unknown"
        icon = file_type_icons.get(ft, "❓")
        size = f.size_kb
        scan_note = " (скан)" if f.is_scan else ""
        section = f" → Раздел: {_h(f.suspected_section)}" if f.suspected_section else ""
        lines.append(
            f"<b>{i}. {icon}</b> <code>{_h(f.name)}</code>"
            f"  [{size:.0f} КБ{scan_note}]{section}"
        )

    if len(result.files) > 30:
        lines.append(f"    <i>...и ещё {len(result.files) - 30} файлов скрыто</i>")

    lines.append("")

    # Детальная карточка XML ПЗ
    if result.xml_summary:
        x = result.xml_summary
        version_ok = _version_gte(x.schema_version, XSD_MINIMUM_VERSION)
        version_icon = "✅" if version_ok else "❌"
        validity_icon = "✅" if x.is_valid else "⚠️"
        validity_text = "пройдена" if x.is_valid else f"ошибок: {len(x.validation_errors)}"

        lines += [
            "<b>📄 Пояснительная записка (XML):</b>",
            f"  Шифр: <code>{_h(x.cipher or '—')}</code>",
            f"  Год: <code>{_h(x.year or '—')}</code>",
            f"  {version_icon} Версия XSD: <code>v{_h(x.schema_version)}</code>"
            + ("" if version_ok else f" — ❗ требуется ≥ v{XSD_MINIMUM_VERSION}"),
            f"  {validity_icon} XSD-валидация: {validity_text}",
            f"  🏗 Объект: {_h((x.object_name or '—')[:120])}",
        ]

        if not x.is_valid and x.validation_errors:
            lines.append("  Первые ошибки валидации:")
            for err in x.validation_errors[:3]:
                lines.append(f"    — <i>{_h(str(err)[:120])}</i>")

        ce = x.chief_engineer
        if ce and (ce.full_name or ce.snils or ce.nopriz_id):
            snils_txt = ("✅ " + _h(ce.snils)) if ce.snils_present else "❌ не найден"
            nopriz_txt = ("✅ " + _h(ce.nopriz_id)) if ce.nopriz_id_present else "❌ не найден"
            lines += [
                "",
                "<b>👷 ГИП (Главный Инженер Проекта):</b>",
                f"  ФИО: {_h(ce.full_name or '—')}",
                f"  СНИЛС: {snils_txt}",
                f"  НОПРИЗ: {nopriz_txt}",
            ]

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  Форматирование: итоговое сводное заключение
# ─────────────────────────────────────────────
def _format_summary(result, package_name: str, elapsed: float) -> str:
    """Итоговое сводное заключение по пакету (HTML)."""
    verdict_emoji = {
        "APPROVED":       "✅",
        "RETURNED":       "❌",
        "PENDING_EXPERT": "🚴",
        "FAILED":         "🔴",
    }.get(result.verdict, "❓")

    # Русские названия статусов
    verdict_ru = {
        "APPROVED":       "Одобрено — замечаний не выявлено",
        "RETURNED":       "Возвращено на доработку",
        "PENDING_EXPERT": "На экспертной проверке",
        "FAILED":         "Ошибка обработки",
    }.get(result.verdict, result.verdict)

    lines = [
        "═══════════════════════",
        "<b>📋 ИТОГОВОЕ ЗАКЛЮЧЕНИЕ</b>",
        f"📁 <code>{_h(package_name)}</code>",
        f"⏱ Время обработки: {elapsed:.1f} сек.",
        "",
        f"{verdict_emoji} <b>Статус: {_h(verdict_ru)}</b>",
        f"<i>{_h(result.verdict_reason)}</i>",
        "",
        "─────────────────────",
        "<b>🔍 Формальные проверки (FC):</b>",
    ]

    if result.formal_check:
        fc = result.formal_check
        checks = [
            ("FC-001", fc.xml_found,                    "XML Пояснительной записки"),
            ("FC-002", fc.xml_version_ok,               f"Версия XSD ≥ {XSD_MINIMUM_VERSION}"),
            ("FC-003", fc.iul_present,                  "ИУЛ в документах"),
            ("FC-005", len(fc.missing_sections) == 0,   "Комплектность разделов ПД"),
        ]
        for code, ok, label in checks:
            icon = "✅" if ok else ("⚠️" if code == "FC-003" else "❌")
            lines.append(f"  {icon} {code}: {label}")

        # FC-UIN и FC-CRC — извлекаем из issues
        for issue in fc.issues:
            if issue.code == "FC-UIN":
                icon = "✅" if issue.severity == "info" else "⚠️"
                lines.append(f"  {icon} FC-UIN: {_h(issue.message[:120])}")
            elif issue.code == "FC-CRC":
                icon = "✅" if issue.severity == "info" else "⚠️"
                lines.append(f"  {icon} FC-CRC: {_h(issue.message[:120])}")

        lines.append("")
        if fc.critical_count > 0:
            lines.append(f"<b>🔴 Критических замечаний: {fc.critical_count}</b>")
            for issue in fc.issues:
                if issue.severity == "critical":
                    lines.append(f"  • [{issue.code}] {_h(issue.message)}")

        if fc.warning_count > 0:
            lines.append(f"<b>🟡 Предупреждений: {fc.warning_count}</b>")
            for issue in fc.issues:
                if issue.severity == "warning":
                    lines.append(f"  • [{issue.code}] {_h(issue.message)}")

        if fc.missing_sections:
            section_names = {
                "01.01": "Пояснительная записка",
                "02.01": "Схема планировочной организации (СПОЗУ)",
                "03.01": "Архитектурные решения (АР)",
                "04.01": "Конструктивные решения (КР)",
                "05.01": "Инженерное оборудование (ИС)",
                "10.01": "Пожарная безопасность (ПБ)",
                "11.01": "Смета на строительство (ССР)",
            }
            lines += ["", "<b>📂 Отсутствующие обязательные разделы:</b>"]
            for sec in fc.missing_sections[:7]:
                name = section_names.get(sec, "")
                lines.append(f"  — <code>{sec}</code> {name}")

    # ── Пропущенные по DPI файлы ───────────────────────
    low_dpi_files = [f for f in result.files if f.is_scan and getattr(f, "min_dpi", None) is not None and f.min_dpi < 300]
    if low_dpi_files:
        lines += ["", "<b>⚠️ Пропущено (качество скана менее 300 DPI):</b>"]
        for f in low_dpi_files[:3]:
            lines.append(f"  — <code>{_h(f.name)}</code> ({f.min_dpi} DPI)")
        if len(low_dpi_files) > 3:
            lines.append(f"  <i>...и ещё {len(low_dpi_files) - 3} файлов</i>")

    # ── PP963: Кросс-валидация ТЭП ───────────────────────
    if result.pp963_report:
        pp = result.pp963_report
        
        # Проверяем, не упала ли LLM с 403/500
        has_api_error = False
        if pp.tep_discrepancies:
            for d in pp.tep_discrepancies:
                if "Error" in d or "403" in d or "Forbidden" in d:
                    has_api_error = True
                    break

        if has_api_error:
            tep_icon = "⚠️"
            status_text = "ошибка LLM API!"
        else:
            tep_icon = "✅" if pp.tep_compliant else "❌"
            status_text = "совпадает" if pp.tep_compliant else "расхождения!"

        lines += ["", "─────────────────────",
                  "<b>🕵️ Проверка ПП №963 (ТЭП):</b>",
                  f"  {tep_icon} Кросс-валидация ТЭП: {status_text}"]
                  
        if pp.tep_discrepancies:
            # Разделяем ТЭП, ГПЗУ и ТУ расхождения
            tep_only = [d for d in pp.tep_discrepancies if not d.startswith("[ГПЗУ") and not d.startswith("[ТУ")]
            gpzu_items = [d for d in pp.tep_discrepancies if d.startswith("[ГПЗУ")]
            tu_items = [d for d in pp.tep_discrepancies if d.startswith("[ТУ")]
            
            for d in tep_only[:3]:
                lines.append(f"  ⚠️ {_h(d[:150])}")
            
            if gpzu_items:
                lines.append("")
                lines.append("  <b>📐 Кросс-проверка ГПЗУ↔ПЗ:</b>")
                for d in gpzu_items[:3]:
                    lines.append(f"    ⚠️ {_h(d[:150])}")
            
            if tu_items:
                lines.append("")
                lines.append("  <b>🔧 Кросс-проверка ТУ↔ИОС:</b>")
                for d in tu_items[:3]:
                    lines.append(f"    ⚠️ {_h(d[:150])}")
        
        if pp.sections_checked > 0:
            lines.append(f"  📋 Разделов проверено: {pp.sections_passed}/{pp.sections_checked}")
        
        # Детализация по разделам (какие прошли / не прошли)
        if pp.sections:
            for sec in pp.sections:
                s_icon = "✅" if sec.passed else "❌"
                conf_str = f" ({sec.confidence:.0%})" if sec.confidence > 0 else ""
                lines.append(f"    {s_icon} {sec.section_code}. {_h(sec.section_name[:60])}{conf_str}")
                # Замечания и нормы для непрошедших разделов
                if not sec.passed and sec.remarks:
                    for r in sec.remarks[:2]:
                        lines.append(f"      💬 {_h(r[:120])}")
                if sec.norm_refs:
                    norms_str = ", ".join(sec.norm_refs[:3])
                    if len(sec.norm_refs) > 3:
                        norms_str += f" (+{len(sec.norm_refs)-3})"
                    lines.append(f"      📎 {_h(norms_str[:120])}")
        
        if pp.llm_model:
            lines.append(f"  🤖 Модель: <code>{_h(pp.llm_model)}</code>")

    # ── Сверка ТЗ/ПЗ (Таблица Владимира) ────────────────────────────
    if getattr(result, "sverka_check", None):
        sv = result.sverka_check
        if sv.error:
            lines += ["", "─────────────────────",
                      "<b>📊 Сверка ТЗ/ПЗ (Таблица Владимира):</b>",
                      f"  ⚠️ Сверка не выполнена: {_h(sv.error[:100])}"]
        else:
            sv_icon = "✅" if sv.is_compliant else ("⚠️" if sv.compliance_rate >= 0.5 else "❌")
            lines += ["", "─────────────────────",
                      "<b>📊 Сверка ТЗ/ПЗ (Таблица Владимира):</b>",
                      f"  {sv_icon} Соответствует: <b>{sv.compliant_count}/{sv.total_items}</b> требований ({sv.compliance_rate:.0%})",
                      f"  ❌ Нарушений: {sv.non_compliant_count}  ⚠️ Пропущено: {sv.skipped_count}"]
            # Отфильтруем пустые/неинформативные требования (типо "-", "   ", "Нет")
            def is_valid_req(req: str) -> bool:
                r = req.strip()
                return len(r) > 3 and r != "-" and r.lower() != "нет"

            violations = [i for i in sv.items if i.compliant is False and is_valid_req(i.requirement)]
            skipped = [i for i in sv.items if i.compliant is None and is_valid_req(i.requirement)]
            
            if violations:
                lines.append("  <i>Список нарушений:</i>")
                for v in violations:
                    lines.append(f"  ❌ {_h(v.requirement[:150].strip())}")
                    
            if skipped:
                lines.append("  <i>Пропущено (нет данных):</i>")
                for s in skipped:
                    lines.append(f"  ⚠️ {_h(s.requirement[:150].strip())}")

    # ── Смета (EstimateChecker) ────────────────────────────
    if getattr(result, "estimate_report", None):
        est = result.estimate_report
        lines += ["", "─────────────────────",
                  "<b>💰 Сметная документация (Раздел 11/12):</b>"]
        if not est.found:
            lines.append("  ❌ Сметная документация не обнаружена (ССР, ЛСР)")
        else:
            files_short = ", ".join(est.estimate_files[:3])
            if len(est.estimate_files) > 3:
                files_short += f" (+{len(est.estimate_files)-3})"
            lines.append(f"  ✅ Найдено файлов: {len(est.estimate_files)} — <code>{_h(files_short)}</code>")
            if est.ssr_approved is True:
                lines.append("  ✅ ССР утверждён застройщиком")
            elif est.ssr_approved is False:
                lines.append("  ❌ ССР <b>НЕ утверждён</b> застройщиком (нет грифа «Утверждаю»)")
            else:
                lines.append("  ⚠️ ССР не найден или текст недоступен")
            for issue in est.issues[:3]:
                lines.append(f"  ⚠️ {_h(issue)}")

    # ── НОПРИЗ: Проверка ГИП ────────────────────────────

    if result.nopriz_check:
        nr = result.nopriz_check
        if nr.found is True:
            is_active = nr.status == "active"
            nr_icon = "✅" if is_active else "⚠️"
            status_str = "Действует" if is_active else "Не действует"
            nr_text = f"ГИП найден в реестре НОПРИЗ ({_h(nr.reg_number)}) — <b>{status_str}</b>"
        elif nr.found is False:
            nr_icon = "❌"
            nr_text = "ГИП НЕ найден в реестре НОПРИЗ"
        else:
            nr_icon = "⚠️"
            nr_text = "Проверка НОПРИЗ не завершена (требуется ручная проверка)"
        lines += ["", "─────────────────────",
                  "<b>🌐 Проверка НОПРИЗ (пп. 66-67):</b>",
                  f"  {nr_icon} {nr_text}"]
        if nr.fio:
            lines.append(f"  👷 {_h(nr.fio)}")

    lines += ["", "─────────────────────",
              "<i>МособлГосЭкспертиза | DocumentAnalyzer v2.0</i>"]

    return "\n".join(lines)


def _version_gte(version: str, minimum: str) -> bool:
    """Сравнение версий вида '01.05' ≥ '01.05'."""
    try:
        v = [int(x) for x in version.split(".")]
        m = [int(x) for x in minimum.split(".")]
        return v >= m
    except (ValueError, AttributeError):
        return False


# ─────────────────────────────────────────────
#  Глобальный обработчик ошибок
# ─────────────────────────────────────────────
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Перехватываем все необработанные ошибки бота."""
    from telegram.error import NetworkError, TimedOut, RetryAfter, BadRequest

    err = ctx.error

    # ── Временные сетевые ошибки — логируем тихо, бот сам восстановится ──
    if isinstance(err, (NetworkError, TimedOut)):
        log.warning(f"[network] Временный сбой сети (бот продолжает): {err}")
        return

    if isinstance(err, RetryAfter):
        log.warning(f"[rate-limit] Telegram просит подождать {err.retry_after}s")
        return

    # ── BadRequest: проверяем что именно пошло не так ──
    if isinstance(err, BadRequest):
        err_lower = str(err).lower()
        if "message is too long" in err_lower:
            # Это не сетевая ошибка — логическая в коде
            log.error("[msg-too-long] Сообщение превысило 4096 симв — используйте _send_long()")
        elif "query is too old" in err_lower or "invalid query id" in err_lower:
            log.warning(f"[callback] Устаревший callback: {err}")
            return
        else:
            log.error(f"[bad-request] {err}")

        # Уведомляем пользователя если возможно
        if update and hasattr(update, "effective_message") and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    f"❌ Ошибка Telegram API: {str(err)[:200]}"
                )
            except Exception:
                pass
        return

    # ── Реальные ошибки — логируем полностью ──
    log.error("Необработанная ошибка:", exc_info=err)

    # Если ошибка произошла внутри обработчика — уведомляем пользователя
    if update and hasattr(update, "effective_message") and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Внутренняя ошибка при обработке запроса.\n"
                "Попробуйте ещё раз или напишите /start\n\n"
                f"`{type(err).__name__}: {str(err)[:120]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass


# ─────────────────────────────────────────────
def main() -> None:
    if not BOT_TOKEN:
        log.error("BOT_TOKEN не задан в .env!")
        sys.exit(1)

    log.info("🚀 Запуск DocumentAnalyzer Bot v2.0...")
    log.info(f"   Admin ID:      {ADMIN_ID}")
    log.info(f"   XSD целевая:   {XSD_VERSION}")
    log.info(f"   XSD минимум:   {XSD_MINIMUM_VERSION}")

    from telegram.request import HTTPXRequest
    _request = HTTPXRequest(
        read_timeout=180,      # 180 сек на скачивание больших файлов (ZIP 30-50 МБ)
        connect_timeout=30,    # 30 сек на установку соединения
        write_timeout=30,      # 30 сек на отправку
        media_write_timeout=180,  # для send_document с PDF-отчётом
    )
    app = Application.builder().token(BOT_TOKEN).request(_request).build()

    # Команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("basket", cmd_basket))
    app.add_handler(CommandHandler("compare", cmd_compare)) # Сравнение с экспертом
    app.add_handler(CommandHandler("search", cmd_search))  # RAG поиск
    app.add_handler(CommandHandler("hitl", cmd_hitl))      # HITL записи
    app.add_handler(CommandHandler("debug", cmd_debug))    # Режим разработчика
    app.add_handler(CommandHandler("local_zip", cmd_local_zip)) # Загрузка локальных ZIP-архивов

    # Inline-кнопки
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Все файлы
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Глобальный error handler — бот не падает от необработанных исключений
    app.add_error_handler(error_handler)

    log.info("✅ Бот запущен. Жду файлы...")

    async def post_init(app: Application) -> None:
        await tg_log(app,
                     f"🚀 DocumentAnalyzer v2.0 запущен. "
                     f"XSD ≥ v{XSD_MINIMUM_VERSION}. Жду пакеты ПД.", "🟢")

    app.post_init = post_init
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

# 🏗 МособлГосЭкспертиза — DocumentAnalyzer v2.0

Автоматизированная система приёмки и проверки проектной документации (ПД) для государственной строительной экспертизы.

**Telegram-бот** принимает ZIP-архивы с проектной документацией, проводит формальные и содержательные проверки на соответствие ПП РФ №963, №154 и другим нормативам, и генерирует готовое заключение эксперта.

---

## 🚀 Быстрый старт

```bash
# 1. Клонировать
git clone git@github.com:14Segun88/MOGE_TG_24_03_26.git
cd MOGE_TG_24_03_26

# 2. Установить (создаст venv, установит зависимости, поднимет Weaviate)
chmod +x install.sh && ./install.sh

# 3. Заполнить ключи
nano .env   # BOT_TOKEN, GROQ_API_KEY, ADMIN_TELEGRAM_ID

# 4. Запустить бота
./start.sh
```

### Требования
- **ОС:** Ubuntu / WSL2
- **Python:** 3.10+
- **Docker:** для Weaviate (векторная БД)
- **LM Studio** (Windows): модель `nomic-embed-text` для RAG-эмбеддингов
- **API-ключи:** Groq (бесплатно на console.groq.com), Telegram Bot Token

---

## 📁 Структура проекта

### Ядро системы

| Файл | Назначение |
|------|-----------|
| `bot.py` | **Telegram-бот** — точка входа. Принимает ZIP, запускает пайплайн, отправляет отчёт |
| `start.sh` | Скрипт запуска бота (проверяет Weaviate, LM Studio) |
| `install.sh` | Автоустановка проекта на новый ПК |
| `docker-compose.yml` | Docker-контейнер Weaviate (векторная БД) |
| `.env.example` | Шаблон конфигурации (ключи, порты) |
| `requirements.txt` | Python-зависимости |
| `MEMORY.md` | Память проекта (баги, решения, контекст) |

### Агенты (src/agents/)

| Файл | Агент | Что делает |
|------|-------|-----------|
| `src/agents/groq_client.py` | LLM-клиент | Round-Robin балансировка ключей Groq, вызов llama-3.3-70b |
| `src/agents/orchestrator/orchestrator.py` | Оркестратор | Маршрутизация: какой агент обрабатывает какой файл |
| `src/agents/document_analyzer/file_classifier.py` | Классификатор | Разбор ZIP, определение типа файлов (PDF/XML/SIG) |
| `src/agents/document_analyzer/xml_parser.py` | XML-парсер | Извлечение ТЭП, метаданных из XML Пояснительной записки |
| `src/agents/document_analyzer/formal_check_runner.py` | Формальные проверки | FC-001..FC-007: XSD, ИУЛ, комплектность, DPI сканов |
| `src/agents/document_analyzer/estimate_checker.py` | Сметная проверка | Проверка ССР (утверждение застройщиком, наличие ЛСР/ОСР) |
| `src/agents/compliance/pp963_agent.py` | ПП РФ №963 | Кросс-валидация ТЭП, проверка разделов ПД |
| `src/agents/compliance/pp154_agent.py` | ПП РФ №154 | Проверка теплоснабжения (промышленные объекты) |
| `src/agents/compliance/sverka_checker.py` | Сверка ТЗ/ПЗ | Сравнение техзадания с ПЗ по таблице критериев |
| `src/agents/knowledge_base/agent.py` | База знаний (RAG) | Поиск нормативов в Weaviate по тексту документов |
| `src/agents/knowledge_base/server.py` | RAG-сервер | FastAPI-сервер для Knowledge Base |
| `src/agents/external_integration/nopriz_agent.py` | НОПРИЗ | Проверка ГИП в реестре НОПРИЗ (Playwright) |
| `src/agents/reporting/report_agent.py` | Генератор отчёта | PDF-заключение по ГОСТ Р 7.0.97-2016 |

### RAG / Векторный поиск

| Файл | Назначение |
|------|-----------|
| `rag_crawler.py` | Скачивание нормативных документов с cntd.ru |
| `rag_indexer.py` | Нарезка на чанки + загрузка в Weaviate |
| `rag_search.py` | Гибридный поиск (BM25 + Vector) по нормативке |
| `rag_ask.py` | CLI-инструмент: задай вопрос → получи ответ с RAG |
| `project_search.py` | Поиск + ответ 70B Groq по проектной базе |

### API / Пайплайн

| Файл | Назначение |
|------|-----------|
| `src/api/pipeline.py` | Оркестрация полного цикла проверки |
| `src/api/router.py` | FastAPI-роутер (REST API) |
| `src/api/schemas.py` | Pydantic-модели запросов/ответов |
| `src/api/task_store.py` | Хранилище задач в памяти |
| `src/main.py` | Точка входа FastAPI (uvicorn) |

### База данных

| Файл | Назначение |
|------|-----------|
| `src/db/models.py` | SQLAlchemy-модели (DisagreementLog — HITL) |
| `src/db/database.py` | Подключение к SQLite |
| `src/db/inject_precedent.py` | Загрузка решений HITL в Weaviate |
| `src/db/init_db.py` | Инициализация таблиц |

### XSD-схемы

| Файл | Назначение |
|------|-----------|
| `xsd/explanatorynote-01-05.xsd` | XSD-схема ПЗ v01.05 |
| `xsd/explanatorynote-01-06.xsd` | XSD-схема ПЗ v01.06 (текущая) |
| `xsd/*.xsl` | XSLT-трансформации для визуализации |

### XML-компаратор (xml_comparator/)

| Файл | Назначение |
|------|-----------|
| `xml_comparator/app/` | Микросервис сравнения XML-документов ПЗ |
| `xml_comparator/mapping_PZ_ZnP.json` | Маппинг полей ПЗ → замечания эксперта |
| `xml_comparator/Dockerfile` | Docker-образ микросервиса |

### Тестирование

| Файл | Назначение |
|------|-----------|
| `test_first_page.py` | Сверка титульных листов PDF (детерминированный алгоритм) |
| `test_monitor.sh` | 4-шаговый тестовый стенд (raw → expert → fixed → final) |
| `tests/test_api.py` | Unit-тесты API |
| `tests/test_document_analyzer.py` | Тесты классификатора документов |
| `tests/test_e2e_pipeline.py` | E2E-тесты пайплайна |

### Fine-tuning

| Файл | Назначение |
|------|-----------|
| `train/generate_dataset.py` | Генерация Q&A датасета из Weaviate-чанков |
| `train/finetune.py` | Скрипт fine-tuning через Unsloth (QLoRA) |
| `train/dataset.jsonl` | Сгенерированный датасет |

### Вспомогательные утилиты

| Файл | Назначение |
|------|-----------|
| `tools/compare_with_expert.py` | Сравнение бот-отчёта с заключением эксперта |
| `tools/parse_conclusion.py` | Парсинг экспертного PDF-заключения |
| `split_zip.py` | Разрезание ZIP по частям |
| `read_docx.py` | Извлечение текста из DOCX |
| `nopriz_login.py` | Авторизация в НОПРИЗ (Playwright) |
| `_generate_drawio.py` | Генерация блок-схем (Draw.io) |
| `mark_drawio.py` | Разметка блок-схем статусами ✅/🟡/❌ |

### Документация (в корне)

| Файл | Назначение |
|------|-----------|
| `plan_proposal.md` / `plan_proposal2.md` / `plan_proposal3.md` | Архитектурные предложения |
| `Структура работы приемки.drawio` | Блок-схема процесса приемки |
| `Пайплайн работы с замечаниями.drawio` | Блок-схема работы с замечаниями |
| `Чек-листы (2) (1).pdf` | Чек-листы экспертизы |
| `АВТОМАТИЗИРОВАННАЯ СИСТЕМА ЭКСПЕРТИЗЫ ДОКУМЕНТАЦИИ.docx` | ТЗ на систему |

---

## ❌ Что НЕ попало в репозиторий (.gitignore)

| Файл/папка | Причина |
|---|---|
| `.env` | **Секреты** (API-ключи Groq, Telegram-токен) |
| `.venv/` | Виртуальное окружение Python (~500 МБ) |
| `Test/` | Тестовые ZIP-архивы (~600 МБ) |
| `real_docs/` | Реальные документы заказчика |
| `hitl_database.db` | Локальная SQLite-база прецедентов |
| `*.log` | Логи работы бота |
| `__pycache__/` | Кэш Python |

### 📂 `ResyltatTesta/` — Результаты 4-шагового тестирования

Папка, в которую `test_monitor.sh` складывает MD-отчёты по каждому шагу тестирования бота.
Внутри — файлы вида `step1_raw.md`, `step2_expert.md`, `step3_fixed.md`, `step4_final.md`.
Каждый отчёт — это полный ответ LLM (llama-3.3-70b), сгенерированный на конкретном шаге проверки документации.
Эти файлы **не нужны в репозитории**, потому что:
- Они генерируются автоматически при каждом запуске тестов
- Имеют размер 50–200 КБ каждый и уникальны для каждого прогона
- Содержат конкретные данные из проверяемых документов (коммерческая тайна)

### 📄 `_parsed_docs.txt` — Дамп распарсенных документов

Текстовый файл (~1.5 МБ), в который скрипт парсинга XML/PDF сбрасывает полный извлечённый текст
из всех документов ZIP-архива. Используется как промежуточный кэш при отладке:
- При загрузке ZIP бот извлекает текст из каждого PDF (через PyMuPDF + Tesseract OCR) и XML
- Весь текст записывается в `_parsed_docs.txt` для ручной проверки разработчиком
- Это **одноразовый debug-файл**, он перезаписывается при каждом запуске и не несёт ценности вне конкретного сеанса

### 🏷 `*:Zone.Identifier` — Метаданные Windows Security

Скрытые файлы, которые Windows автоматически создаёт рядом с каждым файлом, скачанным из интернета.
Например, если скачать `project_search.py` через браузер, Windows создаст рядом файл
`project_search.py:Zone.Identifier` с содержимым:
```ini
[ZoneTransfer]
ZoneId=3
ReferringUrl=https://...
HostUrl=https://...
```
`ZoneId=3` означает «скачано из интернета» — Windows использует это для предупреждений безопасности.
В WSL эти файлы видны как обычные файлы и **засоряют `git status`**.
Они полностью бесполезны для проекта и безопасно игнорируются через `.gitignore`.

---

## ⚙️ Архитектура

```
ZIP-архив → Telegram Bot → Orchestrator
                              ├─→ FileClassifier (разбор архива)
                              ├─→ XmlParser (ТЭП из XML)
                              ├─→ FormalCheckRunner (FC-001..FC-007)
                              ├─→ PP963Agent (кросс-валидация ТЭП)
                              ├─→ SverkaChecker (сверка ТЗ/ПЗ)
                              ├─→ EstimateChecker (сметы)
                              ├─→ NoprizAgent (реестр НОПРИЗ)
                              ├─→ KnowledgeBase (RAG → Weaviate)
                              └─→ ReportAgent → PDF-заключение → Telegram
```

**LLM:** Llama-3.3-70b-versatile (Groq Cloud)
**Векторная БД:** Weaviate (Docker, гибридный BM25+Vector)
**Эмбеддинги:** nomic-embed-text (LM Studio, локально)

---

## 📜 Лицензия

Проприетарное ПО. Все права защищены.

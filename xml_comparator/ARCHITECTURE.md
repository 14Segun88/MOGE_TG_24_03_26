# XML Comparator — Архитектура и документация

## 1. Архитектурное описание

Сервис реализован как **многослойное Python-приложение** с чётким разделением ответственности.
При каждом запросе параллельно выполняются два независимых процесса:

```
HTTP запрос (multipart/form-data)
    │
    ▼
[FastAPI Layer]              app/api/router.py
    │  — распаковка файлов
    │  — создание общей папки для всех файлов запроса
    │
    ├──────────────────────────────────────────────┐
    │                                              │
    ▼                                              ▼
[XSD Validator]              [Mapping Loader]      app/mapping/loader.py
app/reports/xsd_validator.py   — парсинг JSON-маппинга
    │  — загрузка XSD-схем      — построение списка MappingRule
    │    (xmlschema, XSD 1.1)  │
    │  — XML парсинг (lxml)     ├──► [Rule Expander]   app/engine/rule_expander.py
    │  — iter_errors()          │       — раскрытие шаблонных правил
    │  — ValidationResult       │       — сопоставление списков документов
    │                           │
    │                           ├──► [XML Parser]      app/parsers/xml_parser.py
    │                           │       — lxml-парсинг XML
    │                           │       — вычисление XPath-выражений
    │                           │       — подстановка {ТипОбъекта}
    │                           │
    │                           ▼
    │                    [Comparison Engine]        app/engine/comparator.py
    │                        — итерация по правилам
    │                        — выбор стратегии через StrategyRegistry
    │                        — формирование CheckResult
    │                        │
    │                        ├──► [Strategy]       app/strategies/
    │                        │       StrictScalarStrategy  — риск Низкий
    │                        │       MediumScalarStrategy  — риск Средний
    │                        │
    │                        ├──► [Normalizers]    app/normalizers/
    │                        │       strict_normalizer()   — trim+unicode+lowercase
    │                        │       medium_normalizer()   — +кавычки+тире+ОПФ
    │                        │
    │                        ▼
    │                    [Report Builder]           app/reports/builder.py
    │                        — вычисление ComparisonSummary
    │                        — сборка ComparisonReport
    │
    └──────────────────────┬───────────────────────┘
                           │
                           ▼
                    [Storage]                       app/reports/storage.py
                        — сохранение всех файлов в одну папку:
                          report.json / report.html
                          validation.json / validation.html
                          pz.xml / znp.xml
                           │
                           ▼
                    JSON Response (CompareResponse)
                        — report: ComparisonReport
                        — xsd_validation: XsdValidationSummary
```

## 2. Структура проекта

```
xml_comparator/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, middleware, обработчики ошибок
│   ├── config.py            # Настройки из .env (pydantic-settings)
│   │
│   ├── models/
│   │   ├── mapping.py       # MappingRule, RiskLevel, CompareMode
│   │   └── comparison.py    # CheckResult, ComparisonReport, CheckStatus
│   │
│   ├── mapping/
│   │   └── loader.py        # Загрузчик JSON-маппинга
│   │
│   ├── parsers/
│   │   └── xml_parser.py    # XmlDocument, load_xml_document(), XPath
│   │
│   ├── normalizers/
│   │   ├── base.py          # BaseNormalizer, NormalizerPipeline
│   │   └── standard.py      # Конкретные нормализаторы + фабрики
│   │
│   ├── strategies/
│   │   ├── base.py          # BaseCompareStrategy, StrategyResult
│   │   ├── scalar.py        # StrictScalarStrategy, MediumScalarStrategy
│   │   └── registry.py      # StrategyRegistry, default_registry
│   │
│   ├── engine/
│   │   ├── comparator.py    # ComparisonEngine.run()
│   │   └── rule_expander.py # Раскрытие шаблонных правил, сопоставление списков
│   │
│   ├── reports/
│   │   ├── builder.py       # build_report(), _compute_summary()
│   │   ├── html_report.py   # Генерация HTML-отчёта сравнения (self-contained)
│   │   ├── storage.py       # make_report_dir(), save_report() — сохранение на диск
│   │   ├── xsd_validator.py # XSD 1.1-валидация через xmlschema + lxml
│   │   ├── xlsx_report.py   # Excel-экспорт (используется только в tests/)
│   │   └── xsl/
│   │       └── explanatorynote-01-06.xsl  # XSLT для отображения ПЗ в HTML
│   │
│   └── api/
│       ├── router.py        # FastAPI endpoints
│       ├── dependencies.py  # FastAPI Depends
│       └── schemas.py       # API-схемы: CompareResponse, XsdValidationSummary, …
│
├── xsd and xsl/
│   ├── XSD Пояснительная записка версия 01.06/
│   │   ├── explanatorynote-01-06.xsd   # XSD-схема ПЗ (XSD 1.1)
│   │   └── explanatorynote-01-06.xsl
│   └── XSD Задание на проектирование_01-00/
│       ├── DesignAssignment-01-00.xsd  # XSD-схема ЗнП (XSD 1.1)
│       └── DesignAssignment-01-00.xsl
│
├── tests/
│   └── run_test.py          # Локальный тест без HTTP (генерирует report_output_*)
│
├── reports/                 # Папка с сохранёнными отчётами (создаётся автоматически)
│   └── <YYYYMMDD>/
│       └── <uuid>/
│           ├── report.json
│           ├── report.html
│           ├── validation.json
│           ├── validation.html
│           ├── pz.xml
│           └── znp.xml
│
├── mapping_PZ_ZnP.json      # Основной маппинг ПЗ ↔ ЗнП
├── mapping_PZ_xsd_xsl.json  # Вспомогательный маппинг для XSD/XSL
├── Dockerfile
├── docker-compose.yml
├── run.py                   # Запуск uvicorn
├── requirements.txt
├── requirements-test.txt
├── .env.example
└── ARCHITECTURE.md          # Этот файл
```

## 3. Эндпоинты API

### `POST /api/v1/compare`
Принимает три файла (`multipart/form-data`):
- `file_pz` — XML ПЗ (ExplanatoryNote)
- `file_znp` — XML ЗнП (DesignAssignment)
- `file_mapping` — JSON-маппинг (*.json)

### `POST /api/v1/compare/preset`
Принимает два XML-файла; маппинг берётся с сервера (из `MAPPING_FILE_PATH` в `.env`).

Оба эндпоинта возвращают `CompareResponse`:
```json
{
  "report": { ... },
  "xsd_validation": {
    "pz_valid": true,
    "znp_valid": false,
    "pz":  { "file": "pz.xml",  "is_valid": true,  "error_count": 0, "errors": [] },
    "znp": { "file": "znp.xml", "is_valid": false, "error_count": 3, "errors": [...] }
  }
}
```

Валидация XSD и сравнение по маппингу **всегда выполняются оба** независимо друг от друга. Результаты объединяются в одном ответе.

### `GET /api/v1/health`
Проверка работоспособности.

### `GET /api/v1/mapping/rules?mapping_path=...&section=...&risk=...`
Список правил из файла маппинга с фильтрацией.

## 4. XSD-валидация

Реализована в `app/reports/xsd_validator.py`.

### Используемые библиотеки
| Библиотека | Роль |
|-----------|------|
| `xmlschema` (XSD 1.1) | Загрузка схемы и поиск ошибок (`iter_errors`) |
| `lxml` | Парсинг XML-байт с сохранением номеров строк (`.sourceline`) |

### Почему не lxml для валидации
Оба XSD-файла содержат элементы XSD 1.1 (`xs:assert`) и дублирующиеся `xml:id` в
`xs:documentation`, которые lxml (XSD 1.0) не поддерживает и аварийно завершает загрузку.
`xmlschema.XMLSchema11(..., validation='lax')` загружает такие схемы корректно.

### Кэш схем
XSD-схемы парсятся один раз при первом запросе и хранятся в `_SCHEMA_CACHE: dict[Path, XMLSchema11]`.
При рестарте процесса кэш сбрасывается.

### Файлы отчёта валидации
- `validation.html` — читаемый HTML с таблицей ошибок
- `validation.json` — машиночитаемый JSON с теми же данными

## 5. Правила сравнения

| Риск     | Стратегия           | Нормализация                                 |
|----------|---------------------|----------------------------------------------|
| Низкий   | StrictScalar        | trim + unicode NFC + lowercase               |
| Средний  | MediumScalar        | + кавычки + тире + ОПФ + пробелы + lowercase |
| Высокий  | —                   | Не сравнивается (status: skipped)            |

## 6. Статусы проверок

| Статус         | Отображение в отчёте          | Описание                                        |
|----------------|-------------------------------|-------------------------------------------------|
| `success`      | Совпадает                     | Значения совпадают после нормализации           |
| `failed`       | Расхождение                   | Расхождение после нормализации                  |
| `skipped`      | Пропущено                     | Правило пропущено (риск Высокий или нет XPath)  |
| `missing_pz`   | Отсутствует в ПЗ              | XPath ПЗ не нашёл значение                     |
| `missing_znp`  | Отсутствует в ЗнП             | XPath ЗнП не нашёл значение                    |
| `missing_both` | Отсутствует в обоих           | Оба XPath вернули пустой результат              |
| `only_pz`      | Только в ПЗ                   | Правило только для ПЗ (нет XPath ЗнП)          |
| `only_znp`     | Только в ЗнП                  | Правило только для ЗнП (нет XPath ПЗ)          |
| `error`        | Ошибка                        | Ошибка обработки правила                        |

## 7. Алгоритм сопоставления списков документов («2 из 3»)

Реализован в `app/engine/rule_expander.py`.

### Шаблонные правила

Правило считается шаблонным, если заданы `list_xpath_pz` и `list_xpath_znp`. В маппинге для каждого документа в списке (Документы-основания, ИРД) существует группа из 7–8 шаблонных правил, описывающих разные поля одного элемента.

### Критерии идентификации документа

| Критерий | Поля маппинга | Нормализация |
|----------|--------------|--------------|
| Тип (обязательный) | `list_key_pz` / `list_key_znp` | Точное совпадение, strip |
| Имя файла | `match_filename_pz` / `match_filename_znp` | `medium_normalizer` |
| Контрольная сумма | `match_checksum_pz` / `match_checksum_znp` | `hex_normalizer` (uppercase) |

### Логика сопоставления

1. Из обоих деревьев извлекаются все элементы по `list_xpath` с их типом, именем файла и контрольной суммой.
2. Элементы без значения типа **пропускаются**.
3. Жадное сопоставление: для каждого PZ-документа ищется лучший ZNP-документ (максимальный score).
4. Если `score >= 2` → пара сопоставлена. Если `score < 2` → документ несопоставлен.
5. Несопоставленные ZNP-документы добавляются в конец как `(None, znp_doc)`.

### Построение конкретных правил

Используются **позиционные предикаты** `[N]` (1-based):

```
ПЗ:  /ExplanatoryNote/ProjectDecisionDocuments/Document[2]/DocName
ЗнП: /Document/Content/DecisionDocuments/DocumentInfo[1]/Name
```

- `rule_id`: `{base_id}_P{pz_pos}Z{znp_pos}` (напр. `R064_P2Z1`)
- `section`: `{section} [код: {type}, ПЗ[{pz_pos}]->ЗнП[{znp_pos}]]`

## 8. Сохранение отчётов

Одна проверка = одна папка. Создаётся через `make_report_dir()`:

```
<REPORTS_DIR>/<YYYYMMDD>/<uuid>/
    report.json       — результаты сравнения (JSON)
    report.html       — результаты сравнения (HTML, self-contained)
    validation.json   — результаты XSD-валидации (JSON)
    validation.html   — результаты XSD-валидации (HTML)
    pz.xml            — исходный файл ПЗ
    znp.xml           — исходный файл ЗнП
```

Если `REPORTS_DIR` не задан или пуст — сохранение пропускается. Ошибка записи не прерывает запрос, только логируется.

## 9. HTML-отчёт сравнения

Реализован в `app/reports/html_report.py`. Ключевые особенности:

- **Self-contained**: весь CSS и JS встроены в файл, не требует внешних ресурсов
- **Фильтрация по статусу**: все статусы показываются при выборе "Проверенные"
- **Секции и вкладки**: группировка по `section` с счётчиками расхождений
- **XSLT-просмотр ПЗ**: опциональная вкладка с трансформацией XML через XSL
- **Очистка значений**: `_clean_single()` убирает технические имена XML-элементов из отображения, но не трогает римские цифры и коды (I, II, IIB и т.д.)

## 10. Инженерные допущения

1. **Один маппинг-файл на пару типов документов**. Для других пар создаётся новый JSON-маппинг с теми же полями.
2. **XPath без namespace**. XML-документы ПЗ и ЗнП не используют namespace, поэтому XPath работает напрямую.
3. **Автоопределение типа объекта ПЗ** по наличию дочерних элементов `NonIndustrialObject` / `IndustrialObject` / `LinearObject`. Если ни один не найден — `{ТипОбъекта}` не подставляется.
4. **Несколько значений по XPath** — обрабатываются как множество; сравниваются по равенству множеств нормализованных значений.
5. **Кэш маппинга** на уровне процесса (lru_cache). При обновлении файла нужен рестарт.
6. **Кэш XSD-схем** на уровне процесса (`_SCHEMA_CACHE`). Схемы парсятся один раз. При рестарте кэш сбрасывается.
7. **Только текстовые значения** извлекаются из XML-узлов. Для атрибутов XPath вида `/@атрибут` lxml возвращает строку напрямую.
8. **Нормализация ОПФ** (ООО, АО и т.д.) применяется только в MediumScalar.

## 11. Запуск

### Локально

```bash
cd xml_comparator
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install ...  # Linux/Mac

cp .env.example .env
# Отредактируйте .env: укажите MAPPING_FILE_PATH, REPORTS_DIR, XSD_DIR

python run.py
# Откройте http://localhost:8000/docs
```

### Docker

```bash
# Сборка и запуск
docker compose up --build

# Только сборка образа
docker build -t xml-comparator .

# Запуск с переопределением переменных
docker run -p 8000:8000 \
  -e REPORTS_DIR=/data/reports \
  -v /host/reports:/data/reports \
  xml-comparator
```

Dockerfile использует **multi-stage build** (`python:3.12-slim`):
- Этап 1 `builder`: установка Python-зависимостей
- Этап 2 финальный: копирование пакетов + системные зависимости lxml (`libxml2`, `libxslt1.1`)

В образ включены: `app/`, `run.py`, маппинги, XSD-схемы.

## 12. Пример curl-запроса

```bash
curl -X POST http://localhost:8000/api/v1/compare \
  -F "file_pz=@/path/to/pz.xml" \
  -F "file_znp=@/path/to/znp.xml" \
  -F "file_mapping=@/path/to/mapping_PZ_ZnP.json"
```

## 13. Направления расширения

1. **ListCompareStrategy** — специализированная стратегия для сравнения списковых значений с учётом/без учёта порядка.
2. **ComplexCompareStrategy** — сравнение структурированных блоков (ТЭП: сопоставление по ключу `Name` с толерантным сравнением `Value`).
3. **CodeMappingStrategy** — стратегия с таблицей соответствия кодов (`06.01` ↔ `1`).
4. **Плагины нормализаторов** — загрузка через `entry_points` Python.
5. **Новые пары документов** — добавить маппинг-файл и вызвать `/compare/preset?mapping_path=...`.
6. **Асинхронная обработка** — для больших XML (> 10 МБ) перенести в `asyncio.run_in_executor` или Celery.
7. **Кэш результатов** — Redis-кэш по хешу пары файлов.
8. **Webhooks / события** — отправка результатов во внешние системы при обнаружении расхождений.

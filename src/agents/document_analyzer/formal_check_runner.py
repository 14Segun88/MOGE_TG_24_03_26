"""
FormalCheckRunner — формальная проверка пакета документов.
Проверяет комплектность (пп. 72, 81, 84), ЭЦП/ИУЛ (п. 83), имена файлов (Приказ №783/пр).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .file_classifier import ClassifiedFile, FileType

# ------------------------------------------------------------------ #
#  Типы                                                                #
# ------------------------------------------------------------------ #

Severity = Literal["critical", "warning", "info"]


@dataclass
class FormalIssue:
    """Одно замечание формального контроля."""
    code: str           # Код замечания: FC-001, FC-002 ...
    severity: Severity
    message: str
    file_name: str = ""
    norm_ref: str = ""  # Ссылка на пункт нормативного документа


@dataclass
class FormalCheckResult:
    """Итог формальной проверки пакета документов."""
    is_compliant: bool = True
    issues: list[FormalIssue] = field(default_factory=list)
    # Сводка
    xml_found: bool = False
    pdf_pz_found: bool = False   # ПЗ есть, но в PDF (не XML)
    xml_version_ok: bool = False
    iul_present: bool = False
    edcz_present: bool = False          # ЭЦП/УКЭП
    missing_sections: list[str] = field(default_factory=list)
    extra_files: list[str] = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")


# ------------------------------------------------------------------ #
#  Обязательные разделы ПД (ПП РФ №963, нелинейный объект)           #
# ------------------------------------------------------------------ #

# Коды документов, которые ДОЛЖНЫ присутствовать в составе ПД
REQUIRED_DOC_CODES_NON_LINEAR: set[str] = {
    "01.01",  # Пояснительная записка
    "02.01",  # Схема планировочной организации земельного участка
    "03.01",  # Архитектурные решения
    "04.01",  # Конструктивные решения
    "05.01",  # Сведения об инженерном оборудовании
    "10.01",  # Мероприятия по обеспечению пожарной безопасности
    "11.01",  # Смета на строительство (ССР)
}

# ─────────────────────────────────────────────────────────────────── #
# FIX FC-005: Маппинг «код раздела» → альтернативные форматы имён   #
# Реальные архивы используют «Раздел ПД №1 ПЗ» вместо кода «01.01» #
# ─────────────────────────────────────────────────────────────────── #
SECTION_CODE_ALIASES: dict[str, list[str]] = {
    # ─── 01.01 Пояснительная записка ─────────────────────────────────────
    "01.01": [
        # XML-маркеры
        "пз.xml", "пз_",
        # Классические синонимы
        "раздел пд №1", "раздел пд 1", "разделпд1", "раздел 01", "раздел1",
        "пояснительная", "пз-ул",
        # Нумерованные папки (001/, /001 , \\001)
        "/001/", "/001 ", "\\001", "/001\\",
        # Папки из реальных архивов МособлГосЭкспертизы
        "1 проектная документация/001", "001 пз", "001_пз",
    ],
    # ─── 02.01 СПОЗУ / Схема планировочной организации ──────────────────
    "02.01": [
        "раздел пд №2", "раздел пд 2", "разделпд2", "раздел 02",
        "пзу", "спозу", "планировочной", "пзу-ул",
        "/002/", "/002 ", "\\002", "/002\\",
        "002 спозу", "002_спозу", "002 пзу",
    ],
    # ─── 03.01 АР (Архитектурные решения) ────────────────────────────────
    "03.01": [
        "раздел пд №3", "раздел пд 3", "разделпд3", "раздел 03",
        "архитектурные", "ар-ул",
        "/003/", "/003 ", "\\003", "/003\\",
        "003 ар", "003_ар", "003 архит",
    ],
    # ─── 04.01 КР (Конструктивные решения) ───────────────────────────────
    "04.01": [
        "раздел пд №4", "раздел пд 4", "разделпд4", "раздел 04",
        "конструктивные", "кр-ул",
        "/004/", "/004 ", "\\004", "/004\\",
        "004 кр", "004_кр", "004 констр",
    ],
    # ─── 05.01 ИОС / Инженерные системы ─────────────────────────────────
    "05.01": [
        "раздел пд №5", "раздел пд 5", "разделпд5", "раздел 05",
        "инженерное", "иос1", "иос2", "иос3", "иос4", "иос5",
        "/005/", "/005 ", "/005.1/", "/005.2/", "\\005",
        "005 иос", "005_иос", "005 инженер",
        # Подразделы ИОС (005.1-005.8)
        "/иос", "_иос", "-иос",
    ],
    # ─── 10.01 ПБ (Пожарная безопасность) ───────────────────────────────
    "10.01": [
        "раздел пд №9", "раздел пд 9", "разделпд9",   # В ЕГРЗ ПБ = раздел 9!
        "раздел пд №10", "раздел пд 10", "раздел 09", "раздел 10",
        "пб", "пожарная", "пб-ул", "тбэ", "безопасной эксплуатации",
        "/009/", "/010/", "/009 ", "/010 ", "\\009", "\\010",
        "009 пб", "010 пб", "009_пб", "010_пб",
    ],
    # ─── 11.01 ОДИ / Смета ───────────────────────────────────────────────
    "11.01": [
        "раздел пд №11", "раздел пд 11", "разделпд11", "оди",
        "раздел 11", "доступ инвалидов", "инвалидов",
        "раздел пд №12", "смета", "сср", "сметн",
        "/011/", "/012/", "/011 ", "/012 ", "\\011", "\\012",
        "011 оди", "012 смета", "011_оди", "012_см",
    ],
}

# Папки, которые однозначно указывают на принадлежность к ПД
_PD_FOLDER_KEYWORDS = (
    "проектная документация", "проектн", "1 проектная",
    "раздел пд", "pd", "project doc",
)
# Папки изысканий (для FC-003)
_IZY_FOLDER_KEYWORDS = (
    "изыскани", "результаты инженерных", "инженерно-геодезические",
    "обследование", "инженерные изыскания", "igdi", "igi",
)

# Паттерны имён файлов по Приказу №783/пр
_FILENAME_PATTERN_783 = re.compile(
    r"^[A-Za-zА-ЯЁа-яё0-9\-_]+\.(xml|pdf|dwg|dxf|xls[x]?)$",
    re.IGNORECASE,
)

# ----------------------------------------------------------------------- #
#  Эвристика: ключевые слова в именах PDF → коды разделов ПД            #
# ----------------------------------------------------------------------- #

# Порядок важен: более специфичные ключа должны быть раньше
PDF_SECTION_KEYWORDS: list[tuple[str, str]] = [
    # (подстрока в нижнем регистре имени файла, код раздела)
    ("-пз",        "01.01"),  # Пояснительная записка (шифр-ПЗ)
    ("_пз",        "01.01"),
    ("пз_",        "01.01"),
    ("пояснит",  "01.01"),  # пояснительная записка
    ("спозу",     "02.01"),  # СПОЗУ
    ("-ар",        "03.01"),  # Архитектурные решения
    ("_ар",        "03.01"),
    ("-кр",        "04.01"),  # Конструктивные
    ("_кр",        "04.01"),
    ("иос",       "05.01"),  # Инженерные сети
    ("-тх",        "05.01"),  # Технологические решения
    ("_тх",        "05.01"),
    ("пос",       "06.01"),  # Проект организации строительства
    ("-од",        "07.01"),  # Охрана окружающей среды
    ("_од",        "07.01"),
    ("-оди",       "08.01"),  # Доступность для инвалидов
    ("-пб",        "10.01"),  # Пожарная безопасность
    ("_пб",        "10.01"),
    ("пожар",     "10.01"),
    ("сср",       "11.01"),  # Смета
    ("-см",        "11.01"),  # смета (обозначение в шифре)
    ("_см",        "11.01"),
]


def _detect_sections_from_filenames(files: list) -> set[str]:
    """Определяет коды разделов ПД по ключевым словам в ПОЛНОМ ПУТИ файла.

    FIX: Теперь ищем по полному пути (str(f.path)), а не только по имени файла.
    Это поднимает распознаваемость вложенных папок вида:
      /4 ДОКУМЕНТАЦИЯ ПРЕДСТАВЛЕННАЯ/1 Проектная документация/001 ПЗ/файл.pdf
    """
    found: set[str] = set()
    for f in files:
        # Полный путь в нижнем регистре (нормализуем слэши)
        full_path_lower = str(f.path).lower().replace("\\", "/")
        # Просто имя файла
        name_lower = f.path.name.lower()

        for keyword, code in PDF_SECTION_KEYWORDS:
            # Ищем в имени файла
            if keyword in name_lower:
                found.add(code)
                break
            # Ищем в полном пути (папки!)
            if keyword in full_path_lower:
                found.add(code)
                break

        # Дополнительно: папки вида «001», «002» ... «012» в пути
        _folder_section = _detect_section_from_folder(full_path_lower)
        if _folder_section:
            found.add(_folder_section)

    return found


# Маппинг нумерованных папок → коды разделов
_FOLDER_NUMBER_MAP: dict[str, str] = {
    "/001": "01.01",  # Пояснительная записка
    "/002": "02.01",  # СПОЗУ
    "/003": "03.01",  # АР
    "/004": "04.01",  # КР
    "/005": "05.01",  # ИОС
    "/009": "10.01",  # ПБ (раздел 9 в ЕГРЗ)
    "/010": "10.01",  # ПБ
    "/011": "11.01",  # ОДИ
    "/012": "11.01",  # Смета
}


def _detect_section_from_folder(full_path_lower: str) -> str:
    """Определяет код раздела по нумерованной папке в пути.

    Пример: .../1 проектная документация/001 пз/файл.pdf → 01.01
    Пример: .../Проектная документация/002/спозу.pdf → 02.01
    """
    for folder_prefix, code in _FOLDER_NUMBER_MAP.items():
        # Ищем «/001» или «/001 » или «/001/» или «/001_» в пути
        if (
            folder_prefix + "/" in full_path_lower
            or folder_prefix + " " in full_path_lower
            or folder_prefix + "_" in full_path_lower
            or full_path_lower.endswith(folder_prefix)
        ):
            return code
    return ""


class FormalCheckRunner:
    """
    Выполняет формальный (не содержательный) контроль пакета проектной документации.

    Проверки:
    - FC-001  Наличие XML пояснительной записки (п. 1 чек-листа)
    - FC-002  Версия XML-схемы ≥ 01.05 (п. 3 чек-листа)
    - FC-003  Наличие ИУЛ хотя бы у одного документа (п. 83)
    - FC-004  Признак ЭЦП/УКЭП в XML (п. 83)
    - FC-005  Комплектность: присутствие обязательных разделов ПД (пп. 72, 84)
    - FC-006  Имена файлов соответствуют ИТ-требованиям портала экспертизы (без кириллицы)
    - FC-007  Наличие «Not developed» — разделов, которые не разрабатывались
    """

    def __init__(self, required_doc_codes: set[str] | None = None) -> None:
        self.required_doc_codes = required_doc_codes or REQUIRED_DOC_CODES_NON_LINEAR

    def run(
        self,
        classified_files: list[ClassifiedFile],
        parsed_xml=None,   # ParsedExplanatoryNote | None — импортируем динамически
    ) -> FormalCheckResult:
        """
        Запустить все формальные проверки.

        Args:
            classified_files: Список классифицированных файлов пакета.
            parsed_xml: Результат XmlParser.parse() (необязательный).

        Returns:
            FormalCheckResult — итоговый результат.
        """
        result = FormalCheckResult()

        self._check_xml_presence(classified_files, result)
        self._check_xml_version(parsed_xml, result)
        self._check_iul(parsed_xml, result)
        self._check_edcz(parsed_xml, result)
        self._check_completeness(parsed_xml, result, classified_files)
        self._check_filenames(classified_files, result)
        self._check_dpi(classified_files, result)
        self._check_crc_hashes(classified_files, parsed_xml, result)  # FC-CRC: хэши файлов
        self._check_uin(classified_files, parsed_xml, result)          # FC-UIN: поиск заявления
        self._check_bim_models(classified_files, result)               # FC-BIM: Информационная модель

        result.is_compliant = result.critical_count == 0
        return result

    # ------------------------------------------------------------------ #
    #  Отдельные проверки                                                 #
    # ------------------------------------------------------------------ #

    def _check_dpi(self, files: list[ClassifiedFile], result: FormalCheckResult) -> None:
        """FC-DPI: Проверка разрешения скан-копий."""
        for f in files:
            if getattr(f, "is_scan", False) and getattr(f, "min_dpi", None) is not None and f.min_dpi < 300:
                result.issues.append(
                    FormalIssue(
                        code="FC-DPI",
                        severity="warning",
                        message=f"Файл {f.path.name}: разрешение скана {f.min_dpi} DPI. Ожидается ≥ 300 DPI по Приказу №783/пр. Текст не извлекался.",
                        file_name=f.path.name,
                        norm_ref="Приказ Минстроя №783/пр",
                    )
                )

    def _check_xml_presence(
        self, files: list[ClassifiedFile], result: FormalCheckResult
    ) -> None:
        """FC-001: Наличие XML ПЗ в пакете.

        FIX: Ограничиваем поиск XML только файлами из папок проектной документации.
        Это исключает попадание XML из изысканий, метаданных, старых версий.
        """
        # Все XML-файлы типа XML_PZ
        all_xml = [f for f in files if f.file_type == FileType.XML_PZ]

        _pz_xml_keywords = ("пз", "pz", "раздел пд №1", "раздел_пд", "section_01")
        
        # Исключаем XML-файлы из папок изысканий
        filtered_all_xml = [
            f for f in all_xml
            if not any(kw in str(f.path).lower() for kw in _IZY_FOLDER_KEYWORDS)
        ]
        
        pd_xml = [
            f for f in filtered_all_xml
            if any(kw in f.path.name.lower() for kw in _pz_xml_keywords)
            or any(kw in str(f.path).lower() for kw in _PD_FOLDER_KEYWORDS)
        ]
        # Если фильтр дал результат — используем отфильтрованные, иначе все не-изыскательские
        xml_files = pd_xml if pd_xml else filtered_all_xml
        pdf_files = [f for f in files if f.file_type in (FileType.PDF_TEXT, FileType.PDF_SCAN)]
        result.xml_found = len(xml_files) > 0

        # Быстрая эвристика: если в PDF есть файл с "пз" / "pz" / "поясн" / "explanatory"
        pz_keywords = ["пз", "pz", "поясн", "explanatory", "note"]
        pdf_pz = [
            f for f in pdf_files
            if any(kw in f.path.name.lower() for kw in pz_keywords)
        ]
        result.pdf_pz_found = len(pdf_pz) > 0

        if not xml_files:
            if result.pdf_pz_found:
                # ПЗ есть, но в PDF — предупреждение, не критика
                result.issues.append(
                    FormalIssue(
                        code="FC-001",
                        severity="warning",
                        message=(
                            f"ПЗ представлена в PDF ({pdf_pz[0].path.name}), "
                            "а не в XML-формате. "
                            "Для подачи в федеральную экспертизу требуется XML по XSD ≥ 01.05"
                        ),
                        norm_ref="ПП РФ №963, Приложение 1, п. 1; Приказ Минстроя №421/пр",
                    )
                )
            elif pdf_files:
                # Есть PDF, но ни один не похож на ПЗ
                result.issues.append(
                    FormalIssue(
                        code="FC-001",
                        severity="warning",
                        message=(
                            "XML ПЗ не найдена. В пакете есть PDF-файлы, "
                            "но ни один не идентифицирован как пояснительная записка. "
                            "Для экспертизы требуется XML по XSD ≥ 01.05"
                        ),
                        norm_ref="ПП РФ №963, Приложение 1, п. 1",
                    )
                )
            else:
                # Нет ни XML ни PDF — критика
                result.issues.append(
                    FormalIssue(
                        code="FC-001",
                        severity="critical",
                        message="XML пояснительная записка не найдена в пакете документов",
                        norm_ref="ПП РФ №963, Приложение 1, п. 1",
                    )
                )
        elif len(xml_files) > 1:
            result.issues.append(
                FormalIssue(
                    code="FC-001",
                    severity="warning",
                    message=f"Найдено {len(xml_files)} XML-файлов ПЗ — ожидается один",
                    norm_ref="ПП РФ №963, Приложение 1, п. 1",
                )
            )

    def _check_xml_version(self, parsed_xml, result: FormalCheckResult) -> None:
        """FC-002: Версия XML-схемы должна быть ≥ 01.05."""
        if parsed_xml is None:
            return

        # Если XML отсутствует (пакет в PDF) — FC-002 не применим
        if not result.xml_found:
            return

        version = getattr(parsed_xml, "schema_version", "")
        result.xml_version_ok = self._version_gte(version, "01.05")

        if not result.xml_version_ok:
            result.issues.append(
                FormalIssue(
                    code="FC-002",
                    severity="critical",
                    message=(
                        f"Версия XML-схемы '{version}' устарела. "
                        "Требуется 01.05 или выше (действует с 28.03.2025)"
                    ),
                    norm_ref="Приказ Минстроя №421/пр, Письмо Минстроя №414372",
                )
            )

    def _check_iul(self, parsed_xml, result: FormalCheckResult) -> None:
        """FC-003: Наличие ИУЛ (Информационного удостоверительного листа).

        FIX: ИУЛ обязателен для результатов инженерных изысканий (ИГДИ, ОС),
        но НЕ обязателен для всех разделов ПД.
        Проверяем: хотя бы у одного документа изысканий есть ИУЛ.
        """
        if parsed_xml is None:
            return

        all_docs = getattr(parsed_xml, "documents", [])

        # Разделяем: документы изысканий vs проектная документация
        izy_docs = [
            d for d in all_docs
            if any(kw in getattr(d, "name", "").lower()
                   for kw in ("изыскан", "игди", "геодез", "обследован", "тзк", "грунт"))
        ]
        # Если XML описывает только ПД (изысканий нет) — проверяем все документы
        docs_to_check = izy_docs if izy_docs else all_docs

        docs_with_iul = [d for d in docs_to_check if getattr(d, "has_iul", False)]
        result.iul_present = len(docs_with_iul) > 0

        if not result.iul_present and docs_to_check:
            scope = "результатов инженерных изысканий" if izy_docs else "документов пакета"
            result.issues.append(
                FormalIssue(
                    code="FC-003",
                    severity="warning",
                    message=(
                        f"ИУЛ (информационный удостоверительный лист) не обнаружен "
                        f"ни у одного из {scope}"
                    ),
                    norm_ref="ПП РФ №963, п. 83; XSD v1.04+ элемент IULFile",
                )
            )

    def _check_edcz(self, parsed_xml, result: FormalCheckResult) -> None:
        """FC-004: Признак ЭЦП/УКЭП. Проверяем атрибут 'id' (UUID) в корне XML —
        его наличие означает, что XML подготовлен для подписания XMLDsig."""
        if parsed_xml is None:
            return

        # В схеме v1.05 атрибут id="UUID" добавляется для возможности XMLDsig
        # Считаем валидацию по схеме достаточным признаком подготовки к подписи
        result.edcz_present = getattr(parsed_xml, "is_valid", False)

        if not result.edcz_present:
            result.issues.append(
                FormalIssue(
                    code="FC-004",
                    severity="warning",
                    message=(
                        "Не удалось подтвердить наличие ЭЦП/УКЭП. "
                        "XML не прошёл валидацию по схеме или атрибут XMLDsig Id отсутствует"
                    ),
                    norm_ref="ФЗ №63 «Об электронной подписи», ПП РФ №963, п. 83",
                )
            )

    def _check_completeness(self, parsed_xml, result: FormalCheckResult,
                            classified_files: list[ClassifiedFile] | None = None) -> None:
        """FC-005: Обязательные разделы ПД присутствуют в составе проекта.

        FIX: Многоуровневый поиск разделов:
        1) Коды из XML (doc_number: 01.01, 02.01...)
        2) Синонимы кодов через SECTION_CODE_ALIASES (Раздел ПД №1 = 01.01)
        3) Имена всех файлов пакета через PDF_SECTION_KEYWORDS
        Раздел считается найденным если хотя бы один из методов даёт совпадение.
        """
        all_files = classified_files or []

        # ── Метод 1: коды из XML ───────────────────────────────────────────
        xml_doc_numbers: set[str] = set()
        if parsed_xml is not None:
            xml_doc_numbers = {
                d.doc_number
                for d in getattr(parsed_xml, "documents", [])
                if getattr(d, "doc_number", None)
            }

        # ── Метод 2: синонимы через SECTION_CODE_ALIASES ──────────────────
        # Собираем все имена файлов и пути в нижнем регистре
        all_names_lower = [
            str(f.path).lower().replace("\\", "/") for f in all_files
        ]
        found_by_alias: set[str] = set()
        for code, aliases in SECTION_CODE_ALIASES.items():
            for name_l in all_names_lower:
                if any(alias in name_l for alias in aliases):
                    found_by_alias.add(code)
                    break

        # ── Метод 3: PDF-кейворды ────────────────────────────────────────
        found_by_filename = _detect_sections_from_filenames(all_files)

        # ── Объединяем все источники ──────────────────────────────────────
        found_all = xml_doc_numbers | found_by_alias | found_by_filename
        missing = self.required_doc_codes - found_all
        result.missing_sections = sorted(missing)

        if not missing:
            # Все разделы найдены
            result.issues.append(
                FormalIssue(
                    code="FC-005",
                    severity="info",
                    message=(
                        f"Все обязательные разделы ПД найдены "
                        f"({len(found_all & self.required_doc_codes)}/{len(self.required_doc_codes)}). "
                        f"XML-кодов: {len(xml_doc_numbers & self.required_doc_codes)}, "
                        f"по синонимам: {len(found_by_alias & self.required_doc_codes)}, "
                        f"по именам файлов: {len(found_by_filename & self.required_doc_codes)}."
                    ),
                    norm_ref="ПП РФ №963, Приложение 1, пп. 72, 84",
                )
            )
        else:
            # Есть отсутствующие — предупреждение, не critical
            # (critical только если нет вообще ни XML, ни файлов ни одного раздела)
            sev = "critical" if len(found_all) == 0 else "warning"
            for code in sorted(missing):
                result.issues.append(
                    FormalIssue(
                        code="FC-005",
                        severity=sev,
                        message=(
                            f"Не найден обязательный раздел ПД с кодом {code}. "
                            f"Проверено: XML-коды={bool(xml_doc_numbers)}, "
                            f"синонимы={bool(found_by_alias)}, "
                            f"имена файлов={bool(found_by_filename)}."
                        ),
                        norm_ref="ПП РФ №963, Приложение 1, п. 72, 84",
                    )
                )

    def _check_filenames(
        self, files: list[ClassifiedFile], result: FormalCheckResult
    ) -> None:
        """FC-006: Проверка соответствия имён файлов Приказу №783/пр.

        Группируем нарушения: кириллические имена (распространённая практика)
        показываем одной сводной записью info, остальные — warning.
        """
        cyrillic_count = 0
        cyrillic_examples: list[str] = []
        other_bad: list[str] = []

        _has_cyrillic = re.compile(r'[а-яёА-ЯЁ]')

        for cf in files:
            fname = cf.path.name
            if _FILENAME_PATTERN_783.match(fname):
                continue  # соответствует — OK
            if _has_cyrillic.search(fname):
                cyrillic_count += 1
                if len(cyrillic_examples) < 3:
                    cyrillic_examples.append(fname)
            else:
                other_bad.append(fname)

        # Кириллические имена — одна сводная запись info (не warning, т.к. распространённая практика)
        if cyrillic_count:
            examples_str = ", ".join(f"'{e}'" for e in cyrillic_examples)
            if cyrillic_count > 3:
                examples_str += f" и ещё {cyrillic_count - 3}"
            result.issues.append(
                FormalIssue(
                    code="FC-006",
                    severity="info",
                    message=(
                        f"Имена {cyrillic_count} файлов содержат кириллицу "
                        f"(не соответствуют ИТ-требованиям порталов экспертизы): {examples_str}"
                    ),
                    norm_ref="ИТ-требования ЕГРЗ / Рекомендации к загрузке",
                )
            )

        # Прочие нарушения именования — warning
        for fname in other_bad:
            result.issues.append(
                FormalIssue(
                    code="FC-006",
                    severity="warning",
                    message=f"Имя файла не соответствует требованиям: '{fname}'",
                    file_name=fname,
                    norm_ref="Приказ Минстроя №783/пр, требования к именованию файлов",
                )
            )

    def _check_crc_hashes(
        self, files: list[ClassifiedFile], parsed_xml, result: FormalCheckResult
    ) -> None:
        """FC-CRC: Проверка CRC32/SHA256 контрольных сумм файлов из XML/ИУЛ."""
        import hashlib, binascii

        # Извлекаем эталонные хэши/размеры из XML, если доступны
        ref_hashes: dict[str, dict] = {}
        documents = getattr(parsed_xml, "documents", []) if parsed_xml else []
        for doc in documents:
            fname = getattr(doc, "file_name", "") or ""
            crc = getattr(doc, "crc32", "") or ""
            sha = getattr(doc, "sha256", "") or ""
            size = getattr(doc, "file_size", 0) or 0
            if fname:
                ref_hashes[fname.lower()] = {"crc32": crc, "sha256": sha, "size": size}

        if not ref_hashes:
            # Нет эталонных хэшей в XML — проверку пропускаем (не ошибка)
            return

        mismatches = []
        for cf in files:
            fname_lower = cf.path.name.lower()
            if fname_lower not in ref_hashes:
                continue
            ref = ref_hashes[fname_lower]
            try:
                file_bytes = cf.path.read_bytes()
                actual_crc = format(binascii.crc32(file_bytes) & 0xFFFFFFFF, '08x')
                actual_sha = hashlib.sha256(file_bytes).hexdigest()

                # Сверяем CRC32
                if ref["crc32"] and actual_crc.lower() != ref["crc32"].lower():
                    mismatches.append(
                        f"{cf.path.name}: CRC32 не совпадает (ожидалось {ref['crc32']}, получено {actual_crc})"
                    )
                # Сверяем SHA256
                elif ref["sha256"] and actual_sha.lower() != ref["sha256"].lower():
                    mismatches.append(
                        f"{cf.path.name}: SHA256 не совпадает — файл мог быть изменён после подписания"
                    )
            except Exception:
                pass

        if mismatches:
            for msg in mismatches[:5]:  # Показываем не более 5 нарушений
                result.issues.append(FormalIssue(
                    code="FC-CRC",
                    severity="critical",
                    message=f"Нарушение целостности: {msg}",
                    norm_ref="ФЗ №63 «Об электронной подписи», ПП РФ №963, п. 83",
                ))

    def _check_uin(
        self, files: list[ClassifiedFile], parsed_xml, result: FormalCheckResult
    ) -> None:
        """FC-UIN: Попытка извлечь УИН из заявления (если есть в пакете)."""
        # Ищем файл заявления
        zayavlenie_files = [
            f for f in files
            if any(kw in f.path.name.lower() for kw in ["заявл", "zayavl", "application"])
        ]

        if not zayavlenie_files:
            # Заявление не найдено — предупреждение
            result.issues.append(FormalIssue(
                code="FC-UIN",
                severity="info",
                message="Файл заявления не найден в пакете. УИН не извлечён. Наличие заявления требуется при электронной подаче (ПП РФ №154).",
                norm_ref="ПП РФ №154, п. 5; ПП РФ №963",
            ))
            return

        # Пробуем найти УИН в названии или содержимом файла
        uin_found = False
        uin_value = ""
        for zf in zayavlenie_files:
            # УИН в имени файла
            uin_match = re.search(r"УИН[_\-]?(\d{5,20})", zf.path.name, re.IGNORECASE)
            if uin_match:
                uin_value = uin_match.group(1)
                uin_found = True
                break
            # Пробуем прочитать текст PDF/TXT
            try:
                if zf.path.suffix.lower() == ".pdf":
                    import fitz as _fitz
                    with _fitz.open(str(zf.path)) as doc:
                        text = doc[0].get_text()[:2000]
                elif zf.path.suffix.lower() == ".txt":
                    text = zf.path.read_text(encoding="utf-8", errors="ignore")[:2000]
                else:
                    text = ""
                m = re.search(r"УИН[:\s]+(\d{5,20})", text, re.IGNORECASE)
                if m:
                    uin_value = m.group(1)
                    uin_found = True
                    break
            except Exception:
                pass

        if uin_found:
            result.issues.append(FormalIssue(
                code="FC-UIN",
                severity="info",
                message=f"УИН заявления обнаружен: {uin_value}",
            ))
        else:
            result.issues.append(FormalIssue(
                code="FC-UIN",
                severity="warning",
                message=f"Заявление найдено ({zayavlenie_files[0].path.name}), но УИН не извлечён. Проверьте вручную.",
                norm_ref="ПП РФ №154, п. 5",
            ))

    def _check_bim_models(
        self,
        files: list[ClassifiedFile],
        result: FormalCheckResult
    ) -> None:
        """
        FC-BIM: Проверка наличия информационной модели (ТИМ/BIM).
        Основание: Приказ Минстроя России от 24.12.2020 № 328/пр или 1046/пр (Требования к ИМ).
        Ищем файлы с расширением .ifc или специфичными расширениями САПР (пока ищем только .ifc).
        """
        has_ifc = any(str(f.path).lower().endswith(".ifc") for f in files)
        
        if not has_ifc:
            result.issues.append(FormalIssue(
                code="FC-BIM",
                severity="warning",
                message="Информационная модель (ТИМ/BIM) в формате IFC не найдена. Если объект финансируется из бюджета, предоставление ЦИМ обязательно.",
                norm_ref="Приказ Минстроя РФ №1046/пр"
            ))
        else:
            result.issues.append(FormalIssue(
                code="FC-BIM",
                severity="info",
                message="В пакете присутствуют файлы информационной модели (IFC). Требуется специализированная проверка ТИМ-координатором.",
                norm_ref="Приказ Минстроя РФ №1046/пр"
            ))

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _version_gte(version: str, minimum: str) -> bool:
        """Сравнение версий вида '01.05' ≥ '01.05'."""
        try:
            v_parts = [int(x) for x in version.split(".")]
            m_parts = [int(x) for x in minimum.split(".")]
            return v_parts >= m_parts
        except (ValueError, AttributeError):
            return False


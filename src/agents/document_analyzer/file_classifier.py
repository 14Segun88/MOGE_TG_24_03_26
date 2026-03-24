"""
FileClassifier — классификатор файлов в пакете проектной документации.
Определяет тип каждого файла: XML, PDF-текст, скан-PDF, смета, чертёж, архив.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class FileType(str, Enum):
    XML_PZ = "xml_pz"           # XML пояснительная записка (Раздел 1)
    XML_OTHER = "xml_other"     # Другой XML-документ
    PDF_TEXT = "pdf_text"       # PDF с текстовым слоем
    PDF_SCAN = "pdf_scan"       # Скан-PDF (без текста)
    ESTIMATE = "estimate"       # Сметный документ (xlsx, xml-смета, csv)
    DRAWING = "drawing"         # Чертёж (dwg, dxf, pdf-чертёж)
    ARCHIVE = "archive"         # ZIP/RAR-архив
    SIG = "sig"                 # ЭЦП-подпись (.sig, .sgn)
    UNKNOWN = "unknown"         # Неизвестный тип


@dataclass
class ClassifiedFile:
    path: Path
    file_type: FileType
    size_bytes: int
    is_scan: bool = False       # True если PDF без текстового слоя
    min_dpi: int | None = None  # В случае скана - минимальный из DPI основных картинок страниц
    suspected_section: str = "" # Предполагаемый раздел ПД (из имени файла)
    notes: str = ""


class FileClassifier:
    """
    Классифицирует файлы в директории или ZIP-пакете проектной документации.

    Использует:
    - Расширение файла
    - Магические байты (file signature)
    - Имя файла (паттерны разделов ПД)
    - Для PDF: проверка наличия текстового слоя через PyMuPDF
    """

    # Паттерны имён для определения раздела ПД (ПП963)
    # Формат: (regex, метка/код)
    # ВАЖНО: первый совпавший паттерн выигрывает — специфичные идут раньше!
    _SECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
        # Раздел 11 — ССР / Смета (раньше ПЗ! т.к. "ПЗ к СМ" это смета)
        (re.compile(r"(раздел\s*[пП][дД]\s*[№]?\s*12\b|[-_]пз\s+к\s+см|сср\b|[-_]см\b|смет|estimate|лcц|осц|ссц)", re.I), "11"),
        # Раздел 1 — Пояснительная записка (ПЗ)
        (re.compile(r"(раздел\s*1\b|пояснительная|pz[-_]|[-_]pz\b|[-_]пз\b|пз[-_]|пз\s+\d)", re.I), "01"),
        # Раздел 2 — СПОЗУ / Схема планировочной организации
        (re.compile(r"(раздел\s*[пП][дД]\s*[№]?\s*2\b|спозу|gpzu|схема\s*пзу|spz)", re.I), "02"),
        # Раздел 3 — АР (Архитектурные решения)
        (re.compile(r"(раздел\s*[пП][дД]\s*[№]?\s*3\b|[-_]ар\b|[-_]ar\b|архитектурн)", re.I), "03"),
        # Раздел 4 — КР (Конструктивные решения)
        (re.compile(r"(раздел\s*[пП][дД]\s*[№]?\s*4\b|[-_]кр\b|[-_]kr\b|конструктив)", re.I), "04"),
        # Раздел 5 — ИОС / ИС (Инженерные сети и системы)
        (re.compile(r"(раздел\s*[пП][дД]\s*[№]?\s*5\b|иос\d*|[-_]ис\b|[-_]тх\b|инженер.*систем|ics)", re.I), "05"),
        # Раздел 6 — ПОС (Проект организации строительства)
        (re.compile(r"(раздел\s*[пП][дД]\s*[№]?\s*7\b|[-_]пос\b|[-_]pos\b|организаци.*строит)", re.I), "06"),
        # Раздел 7 — ОД (Охрана окружающей среды)
        (re.compile(r"(раздел\s*[пП][дД]\s*[№]?\s*8\b|[-_]ос\b|охрана.*среды|экологи)", re.I), "07"),
        # Раздел 8 — ОДИ (Доступность для инвалидов)
        (re.compile(r"(оди\b|доступн.*инвалид)", re.I), "08"),
        # Раздел 10 — ПБ (Пожарная безопасность)
        (re.compile(r"(раздел\s*[пП][дД]\s*[№]?\s*9\b|[-_]пб\b|[-_]pb\b|пожарн|fire)", re.I), "10"),
        # Чертёж
        (re.compile(r"(чертёж|чертеж|drawing|dwg|dxf|plan)", re.I), "чертёж"),
    ]

    # XML-теги, характерные для XML ПЗ
    _XML_PZ_MARKERS = (b"ExplanatoryNote", b"explanatorynote")

    def classify_directory(self, directory: str | Path) -> list[ClassifiedFile]:
        """Классифицировать все файлы в директории."""
        directory = Path(directory)
        results: list[ClassifiedFile] = []
        for file_path in sorted(directory.rglob("*")):
            if file_path.is_file():
                results.append(self._classify_file(file_path))
        return results

    def classify_zip(self, zip_path: str | Path) -> list[ClassifiedFile]:
        """Распаковать ZIP и классифицировать все файлы внутри."""
        zip_path = Path(zip_path)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for info in zf.infolist():
                    if "__MACOSX" in info.filename or "Zone.Identifier" in info.filename:
                        continue
                    try:
                        raw_bytes = info.filename.encode("cp437")
                        try:
                            name = raw_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            name = raw_bytes.decode("cp866")
                    except Exception:
                        name = info.filename
                    
                    safe_name = "/".join([p[:100] for p in name.split("/")])
                    target_path = Path(tmp_dir) / safe_name
                    
                    if info.is_dir() or not target_path.name:
                        target_path.mkdir(parents=True, exist_ok=True)
                        continue
                    
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(target_path, "wb") as dst:
                        import shutil
                        shutil.copyfileobj(src, dst)
            return self.classify_directory(tmp_dir)

    def classify_file(self, file_path: str | Path) -> ClassifiedFile:
        """Классифицировать один файл."""
        return self._classify_file(Path(file_path))

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _classify_file(self, path: Path) -> ClassifiedFile:
        ext = path.suffix.lower()
        size = path.stat().st_size

        result = ClassifiedFile(
            path=path,
            file_type=FileType.UNKNOWN,
            size_bytes=size,
            suspected_section=self._detect_section(path.name),
        )

        if ext == ".xml":
            result.file_type = self._classify_xml(path)
        elif ext == ".pdf":
            result.file_type, result.is_scan, result.min_dpi = self._classify_pdf(path)
        elif ext in (".xlsx", ".xls", ".ods"):
            result.file_type = FileType.ESTIMATE
        elif ext in (".dwg", ".dxf"):
            result.file_type = FileType.DRAWING
        elif ext in (".zip", ".rar", ".7z"):
            result.file_type = FileType.ARCHIVE
        elif ext in (".sig", ".sgn"):
            result.file_type = FileType.SIG
        else:
            result.file_type = FileType.UNKNOWN

        return result

    def _classify_xml(self, path: Path) -> FileType:
        """Проверить, является ли XML пояснительной запиской."""
        try:
            with open(path, "rb") as f:
                header = f.read(2048)
            if any(marker in header for marker in self._XML_PZ_MARKERS):
                return FileType.XML_PZ
        except OSError:
            pass
        return FileType.XML_OTHER

    def _classify_pdf(self, path: Path) -> tuple[FileType, bool, int | None]:
        """
        Определить тип PDF: текстовый или скан.
        Возвращает (FileType, is_scan, min_dpi).
        """
        try:
            with open(path, "rb") as f:
                header = f.read(64)
            if b"%PDF-1.4 % stub:" in header:
                return FileType.PDF_TEXT, False, None
        except OSError:
            return FileType.PDF_SCAN, True, None

        min_dpi = None
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(path))
            total_chars = 0
            for i in range(min(3, len(doc))):
                total_chars += len(doc[i].get_text("text").strip())
            
            is_scan = (total_chars < 50)
            
            if is_scan:
                dpis = []
                for i in range(min(3, len(doc))):
                    page = doc[i]
                    images = page.get_image_info()
                    if not images: continue
                    main_img = max(images, key=lambda img: img['width'] * img['height'])
                    bw = main_img['bbox'][2] - main_img['bbox'][0]
                    if bw > 0:
                        dpi = int((main_img['width'] / bw) * 72)
                        dpis.append(dpi)
                if dpis:
                    min_dpi = min(dpis)
            
            doc.close()

            if is_scan:
                return FileType.PDF_SCAN, True, min_dpi

            if self._is_drawing_name(path.name):
                return FileType.DRAWING, False, None
                
            return FileType.PDF_TEXT, False, None

        except ImportError:
            return FileType.PDF_TEXT, False, None
        except Exception:
            return FileType.PDF_SCAN, True, None
    def _detect_section(self, filename: str) -> str:
        for pattern, label in self._SECTION_PATTERNS:
            if pattern.search(filename):
                return label
        return ""

    @staticmethod
    def _is_drawing_name(name: str) -> bool:
        drawing_keywords = re.compile(
            r"(чертёж|чертеж|plan|schema|схема|лист|sheet|drawing)", re.I
        )
        return bool(drawing_keywords.search(name))

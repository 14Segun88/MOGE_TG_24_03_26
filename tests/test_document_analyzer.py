"""
Unit-тесты для DocumentAnalyzerAgent: XmlParser, FileClassifier, FormalCheckRunner.
Тест-кейсы TC-01 (п. 1 чек-листа), TC-02 (п. 3), TC-05 (п. 83).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

# Пути к XSD и тестовым файлам
PROJECT_ROOT = Path(__file__).parents[2]
XSD_DIR = PROJECT_ROOT / "xsd"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ============================================================
#  Вспомогательные функции
# ============================================================

def make_minimal_xml(
    schema_version: str = "01.05",
    cipher: str = "TEST-2025",
    year: str = "2025",
    obj_type: str = "NonIndustrialObject",
    snils: str = "123-456-789 00",
    nopriz_id: str = "NRS-12345",
    with_iul: bool = False,
    include_required_docs: bool = True,
) -> str:
    """Генерация минимального валидного XML (заглушка для тестов)."""
    iul_block = '<IULFile FileName="iul.pdf"/>' if with_iul else ""
    docs_block = ""
    if include_required_docs:
        codes = ["01.01", "02.01", "03.01", "04.01", "05.01", "10.01", "11.01"]
        docs_block = "\n".join(
            f'<Document><DocNumber>{c}</DocNumber>'
            f'<DocName>Раздел {c}</DocName>{iul_block}</Document>'
            for c in codes
        )

    return textwrap.dedent(f"""\
    <?xml version="1.0" encoding="UTF-8"?>
    <ExplanatoryNote SchemaVersion="{schema_version}">
      <ExplanatoryNoteNumber>{cipher}</ExplanatoryNoteNumber>
      <ExplanatoryNoteYear>{year}</ExplanatoryNoteYear>
      <IssueAuthor>
        <OrgName>ООО Проектировщик</OrgName>
        <OrgINN>7700000001</OrgINN>
      </IssueAuthor>
      <Signers>
        <ChiefEngineer>
          <FullName>Иванов Иван Иванович</FullName>
          <SNILS>{snils}</SNILS>
          <NRSId>{nopriz_id}</NRSId>
        </ChiefEngineer>
      </Signers>
      <Developer OrgType="legal">
        <OrgName>ООО Застройщик</OrgName>
        <OrgINN>7700000002</OrgINN>
      </Developer>
      <UsedNorms>
        <Norm><NormName>ГОСТ Р 7.0.97-2016</NormName></Norm>
      </UsedNorms>
      <ProjectDecisionDocuments>
        <Document>
          <DocNumber>15.01</DocNumber>
          <DocName>Решение о разработке</DocName>
        </Document>
      </ProjectDecisionDocuments>
      <ProjectInitialDocuments>
        <Document>
          <DocNumber>16.01</DocNumber>
          <DocName>Технические условия</DocName>
        </Document>
      </ProjectInitialDocuments>
      <{obj_type} ObjectID="OBJ-001" Placement="local">
        <Name>Жилой дом №1</Name>
        <ConstructionType>new_construction</ConstructionType>
        <Address><AddressText>г. Москва, ул. Тестовая, 1</AddressText></Address>
        <Functions><FunctionalPurpose code="1.2">Жильё</FunctionalPurpose></Functions>
        <FunctionsFeatures><Text>Нет особенностей</Text></FunctionsFeatures>
        <PowerIndicator><Name>Площадь</Name><Value>5000</Value><UnitName>кв.м</UnitName></PowerIndicator>
        <EnergyEfficiency><EfficiencyClass>B</EfficiencyClass></EnergyEfficiency>
        <FireDangerCategory>Д</FireDangerCategory>
        <PeoplePermanentStay><Text>Есть</Text></PeoplePermanentStay>
        <ResponsibilityLevel>II</ResponsibilityLevel>
        <Resources><Electricity><Value>100</Value><UnitName>кВт</UnitName></Electricity></Resources>
        <LandCategory>1</LandCategory>
        <ProjectDocumentation>
          {docs_block}
        </ProjectDocumentation>
      </{obj_type}>
      <DesignerAssurance><Text>Проектная документация разработана в соответствии с требованиями.</Text></DesignerAssurance>
    </ExplanatoryNote>
    """)


def write_xml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ============================================================
#  Тесты FileClassifier
# ============================================================

class TestFileClassifier:
    """TC-01: Классификация типов файлов."""

    def test_classify_xml_pz(self, tmp_path: Path):
        from src.agents.document_analyzer import FileClassifier
        xml_file = tmp_path / "explanatory.xml"
        xml_file.write_bytes(b'<?xml version="1.0"?><ExplanatoryNote SchemaVersion="01.05"/>')
        fc = FileClassifier()
        result = fc.classify_file(xml_file)
        from src.agents.document_analyzer.file_classifier import FileType
        assert result.file_type == FileType.XML_PZ

    def test_classify_xml_other(self, tmp_path: Path):
        from src.agents.document_analyzer import FileClassifier
        from src.agents.document_analyzer.file_classifier import FileType
        xml_file = tmp_path / "other.xml"
        xml_file.write_bytes(b'<?xml version="1.0"?><Root><Data/></Root>')
        fc = FileClassifier()
        result = fc.classify_file(xml_file)
        assert result.file_type == FileType.XML_OTHER

    def test_classify_archive(self, tmp_path: Path):
        import zipfile
        from src.agents.document_analyzer import FileClassifier
        from src.agents.document_analyzer.file_classifier import FileType
        zip_file = tmp_path / "package.zip"
        with zipfile.ZipFile(zip_file, "w") as zf:
            zf.writestr("test.xml", "<Root/>")
        fc = FileClassifier()
        result = fc.classify_file(zip_file)
        assert result.file_type == FileType.ARCHIVE

    def test_classify_estimate_xlsx(self, tmp_path: Path):
        from src.agents.document_analyzer import FileClassifier
        from src.agents.document_analyzer.file_classifier import FileType
        f = tmp_path / "smeta.xlsx"
        f.write_bytes(b"PK\x03\x04")  # ZIP magic (xlsx = zip)
        fc = FileClassifier()
        result = fc.classify_file(f)
        assert result.file_type == FileType.ESTIMATE

    def test_detect_section_from_filename(self, tmp_path: Path):
        from src.agents.document_analyzer import FileClassifier
        f = tmp_path / "Пояснительная записка_01.xml"
        f.write_bytes(b'<ExplanatoryNote/>')
        fc = FileClassifier()
        result = fc.classify_file(f)
        assert "Раздел 1" in result.suspected_section


# ============================================================
#  Тесты XmlParser
# ============================================================

class TestXmlParser:
    """TC-02: Разбор XML и извлечение полей."""

    def test_init_valid_version(self):
        from src.agents.document_analyzer import XmlParser
        parser = XmlParser(schema_version="01.05")
        assert parser.schema_version == "01.05"

    def test_init_invalid_version(self):
        from src.agents.document_analyzer import XmlParser
        with pytest.raises(ValueError, match="Неподдерживаемая версия"):
            XmlParser(schema_version="99.99")

    def test_file_not_found(self, tmp_path: Path):
        from src.agents.document_analyzer import XmlParser
        parser = XmlParser(schema_version="01.05", strict=False)
        with pytest.raises(FileNotFoundError):
            parser.parse(tmp_path / "nonexistent.xml")

    def test_parse_schema_version_extracted(self, tmp_path: Path):
        """Schema version должна быть извлечена из атрибута корневого элемента."""
        from src.agents.document_analyzer import XmlParser
        xml_content = '<?xml version="1.0"?><ExplanatoryNote SchemaVersion="01.05"><ExplanatoryNoteNumber>T-01</ExplanatoryNoteNumber></ExplanatoryNote>'
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(xml_content, encoding="utf-8")
        parser = XmlParser(schema_version="01.05", strict=False)
        result = parser.parse(xml_file)
        assert result.schema_version == "01.05"
        assert result.cipher == "T-01"

    def test_parse_chief_engineer_snils(self, tmp_path: Path):
        """СНИЛС ГИПа должен быть извлечён из блока Signers."""
        from src.agents.document_analyzer import XmlParser
        xml_content = """<?xml version="1.0"?>
        <ExplanatoryNote SchemaVersion="01.05">
          <Signers>
            <ChiefEngineer>
              <FullName>Петров Петр Петрович</FullName>
              <SNILS>234-567-890 12</SNILS>
              <NRSId>NRS-99999</NRSId>
            </ChiefEngineer>
          </Signers>
        </ExplanatoryNote>"""
        xml_file = tmp_path / "test_snils.xml"
        xml_file.write_text(xml_content, encoding="utf-8")
        parser = XmlParser(schema_version="01.05", strict=False)
        result = parser.parse(xml_file)
        assert result.chief_engineer.snils == "234-567-890 12"
        assert result.chief_engineer.nopriz_id == "NRS-99999"

    def test_validate_only_returns_list(self, tmp_path: Path):
        from src.agents.document_analyzer import XmlParser
        xml_file = tmp_path / "bad.xml"
        xml_file.write_text("<Root/>", encoding="utf-8")
        parser = XmlParser(schema_version="01.05", strict=False)
        errors = parser.validate_only(xml_file)
        assert isinstance(errors, list)
        # <Root/> не соответствует схеме → должны быть ошибки
        assert len(errors) > 0


# ============================================================
#  Тесты FormalCheckRunner
# ============================================================

class TestFormalCheckRunner:
    """TC-05: Формальные проверки пакета документов."""

    def _make_classified_xml(self, tmp_path: Path, filename: str = "pz.xml"):
        """Создать фиктивный ClassifiedFile с типом XML_PZ."""
        from src.agents.document_analyzer.file_classifier import ClassifiedFile, FileType
        f = tmp_path / filename
        f.write_bytes(b'<ExplanatoryNote/>')
        return ClassifiedFile(path=f, file_type=FileType.XML_PZ, size_bytes=f.stat().st_size)

    def test_no_xml_gives_critical(self, tmp_path: Path):
        from src.agents.document_analyzer import FormalCheckRunner
        from src.agents.document_analyzer.file_classifier import ClassifiedFile, FileType
        runner = FormalCheckRunner()
        other_file = tmp_path / "doc.pdf"
        other_file.write_bytes(b"%PDF")
        cf = ClassifiedFile(path=other_file, file_type=FileType.PDF_TEXT, size_bytes=4)
        result = runner.run([cf])
        assert not result.is_compliant
        codes = [i.code for i in result.issues]
        assert "FC-001" in codes

    def test_xml_found_no_critical_001(self, tmp_path: Path):
        from src.agents.document_analyzer import FormalCheckRunner
        runner = FormalCheckRunner()
        cf = self._make_classified_xml(tmp_path)
        result = runner.run([cf])
        fc001_critical = [i for i in result.issues if i.code == "FC-001" and i.severity == "critical"]
        assert len(fc001_critical) == 0

    def test_old_version_gives_critical_002(self, tmp_path: Path):
        from src.agents.document_analyzer import FormalCheckRunner
        from src.agents.document_analyzer.xml_parser import ParsedExplanatoryNote
        runner = FormalCheckRunner()
        cf = self._make_classified_xml(tmp_path)
        parsed = ParsedExplanatoryNote(schema_version="01.03", is_valid=False)
        result = runner.run([cf], parsed_xml=parsed)
        codes = [i.code for i in result.issues]
        assert "FC-002" in codes

    def test_version_105_ok(self, tmp_path: Path):
        from src.agents.document_analyzer import FormalCheckRunner
        from src.agents.document_analyzer.xml_parser import ParsedExplanatoryNote, DocumentRef
        runner = FormalCheckRunner()
        cf = self._make_classified_xml(tmp_path)
        docs = [
            DocumentRef(doc_number=c)
            for c in ["01.01", "02.01", "03.01", "04.01", "05.01", "10.01", "11.01"]
        ]
        parsed = ParsedExplanatoryNote(
            schema_version="01.05", is_valid=True, documents=docs
        )
        result = runner.run([cf], parsed_xml=parsed)
        # Не должно быть FC-002 и FC-005
        critical_codes = {i.code for i in result.issues if i.severity == "critical"}
        assert "FC-002" not in critical_codes
        assert "FC-005" not in critical_codes

    def test_missing_sections_detected(self, tmp_path: Path):
        from src.agents.document_analyzer import FormalCheckRunner
        from src.agents.document_analyzer.xml_parser import ParsedExplanatoryNote, DocumentRef
        runner = FormalCheckRunner()
        cf = self._make_classified_xml(tmp_path)
        # Только документ 01.01 — остальные отсутствуют
        parsed = ParsedExplanatoryNote(
            schema_version="01.05",
            is_valid=True,
            documents=[DocumentRef(doc_number="01.01")],
        )
        result = runner.run([cf], parsed_xml=parsed)
        codes = [i.code for i in result.issues]
        assert "FC-005" in codes
        assert len(result.missing_sections) > 0

    def test_iul_detected(self, tmp_path: Path):
        from src.agents.document_analyzer import FormalCheckRunner
        from src.agents.document_analyzer.xml_parser import ParsedExplanatoryNote, DocumentRef
        runner = FormalCheckRunner()
        cf = self._make_classified_xml(tmp_path)
        docs = [DocumentRef(doc_number="01.01", has_iul=True)]
        parsed = ParsedExplanatoryNote(schema_version="01.05", is_valid=True, documents=docs)
        result = runner.run([cf], parsed_xml=parsed)
        assert result.iul_present

    def test_version_comparison(self):
        from src.agents.document_analyzer.formal_check_runner import FormalCheckRunner
        assert FormalCheckRunner._version_gte("01.05", "01.05")
        assert FormalCheckRunner._version_gte("01.06", "01.05")
        assert not FormalCheckRunner._version_gte("01.04", "01.05")
        assert not FormalCheckRunner._version_gte("", "01.05")

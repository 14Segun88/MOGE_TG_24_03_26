import sys
import json
from pathlib import Path
from src.agents.document_analyzer.xml_parser import XmlParser
from src.agents.document_analyzer.file_classifier import FileClassifier

def run_tests():
    print("Loading XML parser with schema version: 01.05")
    try:
        parser = XmlParser(schema_version="01.05", strict=False)
    except Exception as e:
        print(f"Failed to initialize XmlParser: {e}")
        return

    classifier = FileClassifier()
    real_docs_dir = Path("/home/segun/Практика в машинном обучении/real_docs")
    
    # 1. Тестирование FileClassifier
    print("\n--- Testing FileClassifier ---")
    for file_path in real_docs_dir.glob("*"):
        if file_path.name.endswith(".Identifier") or file_path.name.endswith(".py"):
            continue
        try:
            file_category = classifier.classify_file(str(file_path))
            print(f"[{file_category.file_type.value}] {file_path.name}")
            print(f"   Size: {file_category.size_bytes} bytes")
            print(f"   Is Scan: {file_category.is_scan}")
            print(f"   Suspected Section: {file_category.suspected_section}")
        except Exception as e:
            print(f"[ERROR] {file_path.name}: {e}")

    # 2. Тестирование XmlParser на боевых XML
    print("\n--- Testing XmlParser ---")
    xml_files = list(real_docs_dir.glob("*.xml"))
    
    for xml_file in xml_files:
        if xml_file.name.endswith(".Identifier"):
            continue
            
        print(f"\nAnalyzing: {xml_file.name}")
        try:
            # Отключаем strict чтобы увидеть все ошибки парсинга, но получить распарсенные данные
            result = parser.parse(str(xml_file))
            print(f"Validation Status: {'PASS (100% Valid)' if result.is_valid else 'FAIL (Has XSD Errors)'}")
            
            if not result.is_valid:
                print(f"Schema Version inside XML: {result.schema_version}")
                print(f"Validation Errors ({len(result.validation_errors)}):")
                for err in result.validation_errors[:5]:
                    print(f" - {err}")
                if len(result.validation_errors) > 5:
                    print(f" ... and {len(result.validation_errors)-5} more errors.")
            
            # Показать извлеченные метаданные
            print("\nExtracted Data:")
            print(f" - Cipher: {result.cipher}")
            print(f" - Object Name: {result.object_name}")
            print(f" - Object Type: {result.object_type}")
            print(f" - Address: {result.address}")
            if result.chief_engineer.full_name:
                print(f" - Chief Engineer: {result.chief_engineer.full_name} (SNILS: {result.chief_engineer.snils}, NOPRIZ: {result.chief_engineer.nopriz_id})")
            print(f" - Documents Count: {len(result.documents)}")
            print(f" - TEI Indicators Count: {len(result.tei)}")
            
        except Exception as e:
            print(f"Parsing Failed with exception: {e}")

if __name__ == "__main__":
    run_tests()

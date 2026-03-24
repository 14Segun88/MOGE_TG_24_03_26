import sys
from pathlib import Path
from src.agents.document_analyzer.xml_parser import XmlParser
from src.agents.document_analyzer.file_classifier import FileClassifier

fc = FileClassifier()

files = [
    "/home/segun/Практика в машинном обучении/real_docs/ПЗ_ГК.261-062_25022026 (7).xml",
    "/home/segun/Практика в машинном обучении/real_docs/2026_02_02-ЗП16094_Версия 1 (13.02.26).xml"
]

for f in files:
    path = Path(f)
    if not path.exists():
        print(f"File not found: {path.name}")
        continue
        
    print(f"\n--- Testing {path.name} ---")
    
    # Classification
    file_type = fc.classify_file(path)
    print(f"Classification: {file_type.file_type.value}")
    
    # Parsing (try 01.05 and 01.06)
    for version in ["01.05", "01.06"]:
        print(f"  Attempting parse with XSD v{version}...")
        try:
            parser = XmlParser(schema_version=version, strict=False)
            result = parser.parse(path)
            print(f"    SUCCESS! Schema Version found: {result.schema_version}")
            print(f"    Object Name: {result.object_name}")
            print(f"    Is Valid (strict=False): {result.is_valid}")
            
            if not result.is_valid:
                print(f"    Errors (first 2): {result.validation_errors[:2]}")
                
            # Важный момент — читает ли он ГИПа?
            if result.chief_engineer:
                print(f"    GIP FIO: {result.chief_engineer.full_name}")
                print(f"    GIP SNILS: {result.chief_engineer.snils}")
                print(f"    GIP NOPRIZ: {result.chief_engineer.nopriz_id}")
            else:
                print("    NO GIP FOUND")
                
        except Exception as e:
            print(f"    FAIL: {e}")


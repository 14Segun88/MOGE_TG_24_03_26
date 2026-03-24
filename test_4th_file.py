import sys
from pathlib import Path
from src.agents.document_analyzer.file_classifier import FileClassifier

classifier = FileClassifier()
file_path = "/home/segun/Практика в машинном обучении/real_docs/Технический отч.ГК261-ИГДИ.2.pdf"

try:
    file_category = classifier.classify_file(file_path)
    print(f"File: {Path(file_path).name}")
    print(f"Type: {file_category.file_type.value}")
    print(f"Size: {file_category.size_bytes} bytes")
    print(f"Is Scan: {file_category.is_scan}")
except Exception as e:
    print(f"Error: {e}")

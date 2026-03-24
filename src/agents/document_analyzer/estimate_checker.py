"""
EstimateChecker — проверка наличия и оформления сметной документации (Раздел 12/11).
Извлекает информацию о наличии ССР (Сводный сметный расчет), ЛСР, и проверяет 
утверждение ССР заказчиком (поиск "Утверждаю" в начале документов).
"""
import re
from typing import Optional
from dataclasses import dataclass, field
from ..document_analyzer.file_classifier import ClassifiedFile, FileType
from ...api.schemas import XmlSummaryOut

@dataclass
class EstimateResult:
    found: bool = False
    ssr_approved: Optional[bool] = None
    estimate_files: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

class EstimateChecker:
    @staticmethod
    def check(files: list[ClassifiedFile], xml_summary: Optional[XmlSummaryOut], texts: dict[str, str]) -> EstimateResult:
        result = EstimateResult()
        
        # Находим сметные файлы (по типу или паттернам имени)
        smet_files = [f for f in files if f.file_type == FileType.ESTIMATE or 
                      f.suspected_section in ("11", "12") or 
                      "смет" in f.path.name.lower() or 
                      "сср" in f.path.name.lower() or
                      re.search(r"\bсм\b|[-_]см\b", f.path.name.lower())]
                      
        if not smet_files:
            return result
            
        result.found = True
        result.estimate_files = [f.path.name for f in smet_files]
        
        # Ищем Сводный сметный расчет (ССР) среди сметных файлов
        ssr_files = [f for f in smet_files if "сср" in f.path.name.lower() or "сводн" in f.path.name.lower()]
        
        if not ssr_files:
            result.issues.append("Не найден Сводный сметный расчет (ССР) среди сметной документации")
        
        # Проверяем факт утверждения ССР (гриф "Утверждаю")
        ssr_approved = False
        approval_keywords = re.compile(r"утверждаю|утвержден|утвердить|\bутв\b", re.IGNORECASE)
        
        # Проверяем тексты ССР-файлов на ключевые слова
        for f in sum([ssr_files, smet_files], []):
            path_str = str(f.path)
            if path_str in texts:
                text = texts[path_str][:2000] # Ищем в первых 2000 символах
                if approval_keywords.search(text):
                    ssr_approved = True
                    break
        
        if ssr_files:
            result.ssr_approved = ssr_approved
            if not ssr_approved:
                result.issues.append("Сводный сметный расчет стоимости строительства не утвержден застройщиком (отсутствует гриф 'Утверждаю')")
                
        # Наличие локальных (ЛСР) или объектных (ОСР) смет
        lsr_files = [f for f in smet_files if "лср" in f.path.name.lower() or "локал" in f.path.name.lower() or "оср" in f.path.name.lower() or "объектн" in f.path.name.lower()]
        
        if not lsr_files and len(smet_files) <= len(ssr_files):
            result.issues.append("Отсутствуют локальные (ЛСР) или объектные (ОСР) сметные расчеты")
            
        return result

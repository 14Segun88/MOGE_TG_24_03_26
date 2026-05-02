import re

def update_file(filename, mapping):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for k, v in mapping.items():
        old_val = f'value="{k}"'
        new_val = f'value="{v}"'
        if old_val in content:
            content = content.replace(old_val, new_val)
        else:
            print(f"Warning: could not find {repr(old_val)} in {filename}")
        
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

mapping1_patch = {
    "ТУ": "✅ ТУ",
    "Обработка исключений": "🟡 Обработка исключений",
    "Сканированные документы": "✅ Сканированные документы",
    "Схемы, чертежи, планы": "✅ Схемы, чертежи, планы",
    "Языковая модель (LLM)": "✅ Языковая модель (LLM)",
    "Прямые запросы пользователей": "❌ Прямые запросы пользователей",
    "Промпт-шаблоны": "✅ Промпт-шаблоны",
    "Выгрузка документов из системы документооборота": "🟡 Выгрузка документов из системы документооборота"
}

update_file("Структура работы приемки.drawio", mapping1_patch)
print("Done marking extra nodes.")

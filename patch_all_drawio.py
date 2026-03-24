import re
import xml.etree.ElementTree as ET

def update_file(filename, mapping):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for k, v in mapping.items():
        # find value="k" and replace with value="v"
        old_val = f'value="{k}"'
        new_val = f'value="{v}"'
        if old_val in content:
            content = content.replace(old_val, new_val)
        else:
            # Maybe the text has HTML entities or something
            print(f"Warning: could not find {repr(old_val)} in {filename}")
        
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

mapping_struct = {
    "Отдел приемки": "🟡 Отдел приемки"
}

mapping_pipeline = {
    "1": "✅ 1",
    "3": "🟡 3",
    "8": "✅ 8",
    "16-20": "✅ 16-20",
    "24": "❌ 24",
    "25": "🟡 25",
    "26": "❌ 26",
    "28": "✅ 28",
    "31": "❌ 31",
    "58": "🟡 58",
    "60": "❌ 60",
    "64": "❌ 64",
    "66": "✅ 66",
    "67": "🟡 67",
    "68": "🟡 68",
    "70": "❌ 70",
    "72": "✅ 72",
    "74": "🟡 74",
    "81": "❌ 81",
    "83": "✅ 83",
    "84": "✅ 84",
    "102": "❌ 102",
    "103": "❌ 103",
    "106": "❌ 106"
}

update_file("/home/segun/Практика в машинном обучении/Структура работы приемки.drawio", mapping_struct)
update_file("/home/segun/Практика в машинном обучении/Пайплайн работы с замечаниями.drawio", mapping_pipeline)

print("Patching completed.")

import xml.etree.ElementTree as ET
from pathlib import Path

path = "/home/segun/Практика в машинном обучении/real_docs/ПЗ_ГК.261-062_25022026 (7).xml"
tree = ET.parse(path)
root = tree.getroot()

def _get_namespace(el):
    tag = el.tag
    if tag.startswith("{"):
        return "{" + tag.split("}")[0][1:] + "}"
    return ""

ns = _get_namespace(root)
print(f"Namespace: {ns}")

# Посмотрим, как выглядит блок Signers в 01.06
signers = root.find(f"{ns}Signers")
if signers is not None:
    print("Found Signers block!")
    for child in signers:
        print(f"  Signer child: {child.tag}")
        for grand in child:
            text = grand.text.strip() if grand.text else ''
            print(f"    {grand.tag}: {text}")
else:
    print("Signers block NOT found at root level.")
    
# Поищем любые теги со словом "Sign", "Chief", "Person"
for el in root.iter():
    if any(k in el.tag for k in ["Sign", "Chief", "Person", "Engineer", "SNILS", "Nopriz"]):
        print(f"Found related tag: {el.tag}")
        if list(el):
            for child in el:
                text = child.text.strip() if child.text else ''
                print(f"  {child.tag}: {text}")
        else:
            print(f"  Text: {el.text}")

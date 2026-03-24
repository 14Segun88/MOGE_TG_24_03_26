import xml.etree.ElementTree as ET
import xml.dom.minidom

def create_base():
    mxfile = ET.Element("mxfile", host="Electron", modified="2024-03-01T00:00:00.000Z", version="24.4.0", type="device")
    diagram = ET.SubElement(mxfile, "diagram", id="diagram-1", name="Page-1")
    mxGraphModel = ET.SubElement(diagram, "mxGraphModel", dx="1000", dy="1000", grid="1", gridSize="10", guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1", pageWidth="1600", pageHeight="1200", math="0", shadow="0")
    root = ET.SubElement(mxGraphModel, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")
    return mxfile, root

def add_node(root, id, val, x, y, w, h, style):
    node = ET.SubElement(root, "mxCell", id=id, value=val, style=style, vertex="1", parent="1")
    ET.SubElement(node, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), **{"as": "geometry"})

def add_edge(root, id, source, target, style, waypoints=None):
    edge = ET.SubElement(root, "mxCell", id=id, style=style, edge="1", parent="1", source=source, target=target)
    geom = ET.SubElement(edge, "mxGeometry", relative="1", **{"as": "geometry"})
    if waypoints:
        arr = ET.SubElement(geom, "Array", **{"as": "points"})
        for wp in waypoints:
            ET.SubElement(arr, "mxPoint", x=str(wp[0]), y=str(wp[1]))

def save_xml(mxfile, filename):
    xml_str = ET.tostring(mxfile, encoding='utf-8')
    parsed_xml = xml.dom.minidom.parseString(xml_str)
    pretty_xml = parsed_xml.toprettyxml(indent="  ")
    with open(filename, "w", encoding="utf-8") as f:
        f.write(pretty_xml)

# --- Пайплайн работы с замечаниями ---
mxfile2, root2 = create_base()

# Background black for pipeline
BG_COLOR="#000000"
TEXT_COLOR="#FFFFFF"
mxGraphModel = mxfile2.find(".//mxGraphModel")
mxGraphModel.set("background", BG_COLOR)

def add_rect(root, id, val, x, y, w, h, fill, font="#000000"):
    style = f"rounded=0;whiteSpace=wrap;html=1;fillColor={fill};strokeColor=#000000;fontColor={font};fontFamily=Arial;fontSize=10;align=left;spacingLeft=5;"
    add_node(root, id, val, x, y, w, h, style)

add_node(root2, "p_vhod", "Входящие\nдокументы", 20, 480, 100, 60, "rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#000000;fontColor=#000000;fontFamily=Arial;fontSize=12;")

# Left Table
add_node(root2, "t1_h", "Проверка наличия\nдокументов", 320, 200, 150, 40, "rounded=0;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontColor=#000000;")

y_offset = 250
left_items = [
    ("1", "Представлен раздел «Пояснительная записка» в формате XML (версия 1.05).", "#dae8fc", 60),
    ("3", "Задание на проектирование оформлено в формате XML (в случае если реквизиты задания после 09.07.2025).", "#ffe6cc", 60),
    ("24", "Представлен акт, утверждённый застройщиком или техническим заказчиком, содержащий перечень дефектов...", "#ffffff", 100),
    ("31", "Требуется разработка проекта планировки территории в соответствии с Постановлением Правительства РФ...", "#ffffff", 80),
    ("72", "Представлен документ, подтверждающий полномочия заявителя.", "#ffffff", 60),
]

l_nodes = {}
for num, text, col, h in left_items:
    id_n = f"l_{num}_n"
    id_t = f"l_{num}_t"
    add_rect(root2, id_n, num, 200, y_offset, 30, h, col)
    add_rect(root2, id_t, text, 230, y_offset, 340, h, col)
    l_nodes[num] = (200, y_offset, 370, h)
    y_offset += h

add_rect(root2, "hdr_komp_n", "", 200, y_offset, 30, 40, "#ffffff")
add_rect(root2, "hdr_komp", "Проверка комплектности томов ПД", 230, y_offset, 340, 40, "#ffffff", "#000000")
ET.SubElement(root2.find(".//*[@id='hdr_komp']"), "mxGeometry").set("align", "center")
y_offset += 40

left_items_2 = [
    ("81", "Представлен документ, подтверждающий передачу документации техническому заказчику...", "#ffffff", 60),
    ("83", "Документация подписана электронными подписями лиц, участвовавшими в ее разработке...", "#ffffff", 80),
    ("84", "Электронные документы оформлены в соответствии с требованиями Требования, утвержденные приказом...", "#ffffff", 80),
]
for num, text, col, h in left_items_2:
    id_n = f"l_{num}_n"
    id_t = f"l_{num}_t"
    add_rect(root2, id_n, num, 200, y_offset, 30, h, col)
    add_rect(root2, id_t, text, 230, y_offset, 340, h, col)
    l_nodes[num] = (200, y_offset, 370, h)
    y_offset += h

add_rect(root2, "hdr_zayv_n", "", 200, y_offset, 30, 40, "#ffffff")
add_rect(root2, "hdr_zayv", "Заявление на проведение экспертизы", 230, y_offset, 340, 40, "#ffffff", "#000000")
ET.SubElement(root2.find(".//*[@id='hdr_zayv']"), "mxGeometry").set("align", "center")


# Right Table
add_node(root2, "t2_h", "Проверка содержимого\nдокументов", 900, 20, 150, 40, "rounded=0;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontColor=#000000;")

y_offset2 = 70
right_items = [
    ("8", "В заявлении указан УИН.", "#ffffff", 40),
    ("16-20", "В разделе «Пояснительная записка» указаны реквизиты отчетной документации по результатам инженерных изысканий.", "#dae8fc", 60),
    ("25", "В разделе 1 «Пояснительная записка» указаны реквизиты отчетной документации по результатам изысканий предусмотренных заданием.", "#dae8fc", 60),
    ("26", "В разделе 1 «Пояснительная записка» указаны реквизиты акта (решения) собственника о проведении реконструкции...", "#dae8fc", 60),
    ("28", "В разделе 1 «Пояснительная записка» указаны реквизиты ГПЗУ.", "#dae8fc", 40),
    ("58", "В разделе 1 «Пояснительная записка» указаны реквизиты документа о согласовании отступлений от положений технических условий.", "#dae8fc", 60),
    ("60", "В разделе 1 «Пояснительная записка» указаны реквизиты согласия владельца автомобильной дороги на примыкание.", "#dae8fc", 60),
    ("64", "В разделе 1 «Пояснительная записка» указан уровень ответственности объекта.", "#dae8fc", 40),
    ("66", "ГИП включен в Национальный реестр специалистов (НОПРИЗ) на подготовку проектной документации.", "#ffffff", 60),
    ("67", "ГИП включен в Национальный реестр специалистов (НОПРИЗ) на выполнение инженерных изысканий.", "#ffffff", 60),
    ("68", "В разделе «Пояснительная записка» указаны сведения о застройщике.", "#ffffff", 40),
    ("70", "В разделе «Пояснительная записка» указаны сведения о техническом заказчике.", "#ffffff", 40),
    ("74", "В разделе 1 «Пояснительная записка» указан состав проектной документации.", "#dae8fc", 40),
    ("102", "В разделе 1 \"Пояснительная записка\" в подразделе \"Сведения о принадлежности к опасным производственным объектам\"...", "#dae8fc", 60),
    ("103", "В разделе 1 \"Пояснительная записка\" приведены сведения о разделах и пунктах проектной документации...", "#dae8fc", 60),
    ("106", "Задание на проектирование содержит требование о разработке декларации промышленной безопасности...", "#ffe6cc", 80),
]

r_nodes = {}
for num, text, col, h in right_items:
    id_n = f"r_{num}_n"
    id_t = f"r_{num}_t"
    add_rect(root2, id_n, num, 760, y_offset2, 40, h, col)
    add_rect(root2, id_t, text, 800, y_offset2, 300, h, col)
    r_nodes[num] = (760, y_offset2, 340, h)
    y_offset2 += h

# Black boxes
add_node(root2, "b_srv1", "Данные от\nстороннего сервиса", 620, 520, 100, 40, "rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;fontColor=#333333;fontFamily=Arial;fontSize=10;")
add_node(root2, "b_srv2", "Данные от\nстороннего сервиса", 620, 580, 100, 40, "rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;fontColor=#333333;fontFamily=Arial;fontSize=10;")

# Edges
s_edge_w = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=classic;endFill=1;strokeColor=#ffffff;"
s_edge_b = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=classic;endFill=1;strokeColor=#000000;"

add_edge(root2, "e_v_l", "p_vhod", "l_1_t", s_edge_w, [[150, 510], [150, 280]])

# from left 1 to right Blues
add_edge(root2, "eb1", "l_1_t", "r_16-20_n", s_edge_w, [[600, 280], [600, 140]])

# from left 3 to right 106
add_edge(root2, "eb2", "l_3_t", "r_106_n", s_edge_w, [[600, 340], [600, 890]])

# 72 to black boxes
add_edge(root2, "eb3", "l_72_t", "b_srv1", s_edge_w, [[590, 600], [590, 540]])
add_edge(root2, "eb4", "l_72_t", "b_srv2", s_edge_w, [[590, 600], [590, 600]])
add_edge(root2, "eb5", "b_srv1", "r_66_n", s_edge_w, [[740, 540], [740, 500]])
add_edge(root2, "eb6", "b_srv2", "r_67_n", s_edge_w, [[740, 600], [740, 560]])

save_xml(mxfile2, "Пайплайн работы с замечаниями.drawio")
print("Fixed layout generated successfully.")

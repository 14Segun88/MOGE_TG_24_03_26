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

# --- Структура работы приемки ---
mxfile1, root1 = create_base()

# Styles
s_white = "rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#000000;fontColor=#000000;fontFamily=Arial;fontSize=12;"
s_white_sq = "rounded=0;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#000000;fontColor=#000000;fontFamily=Arial;fontSize=12;"
s_blue = "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontColor=#000000;"
s_green = "rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;fontColor=#000000;"
s_red = "rounded=1;whiteSpace=wrap;html=1;fillColor=#f8cecc;strokeColor=#b85450;fontColor=#000000;"
s_orange_sq = "rounded=0;whiteSpace=wrap;html=1;fillColor=#ffe6cc;strokeColor=#d79b00;fontColor=#000000;"
s_db = "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;fillColor=#ffffff;strokeColor=#000000;fontColor=#000000;"
s_text = "text;html=1;align=center;verticalAlign=middle;resizable=0;points=[];autosize=1;strokeColor=none;fillColor=none;fontColor=#000000;"
s_dash_cont = "rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#000000;dashed=1;"

# Nodes
add_node(root1, "cont1", "", 960, 200, 360, 520, s_dash_cont)
add_node(root1, "title_ot", "Отдел приемки", 1200, 200, 100, 30, s_orange_sq)

add_node(root1, "n_vhod", "Входящие\nдокументы", 1000, 100, 120, 60, s_white)
add_node(root1, "n_nal_doc", "Проверка наличия\nдокументов", 980, 260, 120, 60, s_white)
add_node(root1, "n_tu", "ТУ", 1140, 260, 120, 60, s_white)
add_node(root1, "n_nal_info", "Проверка наличия\nинформации в\nдокументах", 980, 420, 120, 60, s_blue)
add_node(root1, "n_soot", "Проверка\nсоответствия\nинформации в\nдокументах", 980, 580, 120, 60, s_green)
add_node(root1, "n_zam", "Подготовка\nзамечаний к\nвходящей\nдокументации", 1360, 580, 120, 60, s_red)
add_node(root1, "n_tech", "Инженерно-\nтехнические\nотделы", 1180, 770, 100, 60, s_orange_sq)
add_node(root1, "t_exp", "Передача документации\nэкспертам для проверки", 1000, 770, 150, 40, s_text)

add_node(root1, "n_form", "Проверка по\nформальному\nпризнаку\n(комплектность,\nназвания файлов,\nразрешения, ЭЦП)", 540, 20, 120, 100, s_white_sq)
add_node(root1, "n_model", "Модель\nпроверки типов\nфайлов и их\nсодержания", 540, 180, 120, 70, s_white_sq)
add_node(root1, "n_exc", "Обработка\nисключений", 880, 240, 60, 300, s_white)

add_node(root1, "t_text_d", "текстовые данные", 250, 310, 110, 50, s_white)
add_node(root1, "t_graf_d", "графическая\nинформация", 380, 310, 110, 50, s_white)
add_node(root1, "t_smet_d", "файлы смет и\nрасчетов", 510, 310, 110, 50, s_white)
add_node(root1, "t_info_d", "информационные\nмодели", 640, 310, 110, 50, s_white)
add_node(root1, "t_xml_d", "машиночитаемые\nфайлы\n(XML, JSON)", 770, 310, 110, 50, s_white)

add_node(root1, "n_scan", "Сканированные\nдокументы", 340, 420, 110, 50, s_white)
add_node(root1, "n_cher", "Схемы, чертежи,\nпланы", 480, 420, 110, 50, s_white)

add_node(root1, "n_t_mod1", "Модель разделения\nосновной информации и\nслужебной информации,\nформирование ТЭП и\nконтрольных данных из\nкаждого документа,\nприоритета достоверности\nданных", 10, 420, 200, 100, s_white_sq)
add_node(root1, "n_t_mod2", "Модель разделения\nосновной информации и\nслужебной информации,\nраспознавание текста", 240, 500, 200, 70, s_white_sq)

add_node(root1, "n_db", "Формирование записи в\nвекторной БД с\nинформацией о проекте и\nфайлах", 40, 700, 160, 180, s_db)
add_node(root1, "n_llm", "Языковая\nмодель (LLM)", 800, 750, 100, 150, s_white)
add_node(root1, "t_user", "Прямые запросы\nпользователей", 950, 850, 120, 40, s_text)
add_node(root1, "t_prompt", "Промпт-шаблоны", 950, 750, 120, 30, s_text)

add_node(root1, "t_vyg", "Выгрузка документов\nиз системы документооборота", 800, 100, 180, 40, s_text)
add_node(root1, "t_tu1", "Проверка\nданных\nиз ТУ", 820, 210, 80, 50, s_text)
add_node(root1, "t_tu2", "Проверка\nданных\nиз ТУ", 250, 180, 80, 50, s_text)
add_node(root1, "t_dog", "Догрузка документов\nзаявителем", 1360, 320, 140, 40, s_text)

# Edges for structure
s_edge_arr = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=classic;endFill=1;strokeColor=#000000;"
s_edge_line = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=none;endFill=0;strokeColor=#000000;"
s_edge_red = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=classic;endFill=1;strokeColor=#B85450;"
s_edge_blue_dash = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;dashed=1;endArrow=classic;endFill=1;strokeColor=#0000FF;"

add_edge(root1, "e1", "n_vhod", "n_nal_doc", s_edge_arr)
add_edge(root1, "e2", "n_nal_doc", "n_tu", s_edge_arr)
add_edge(root1, "e3", "n_nal_doc", "n_nal_info", s_edge_arr)
add_edge(root1, "e4", "n_tu", "n_nal_info", s_edge_arr)
add_edge(root1, "e5", "n_tu", "n_soot", s_edge_arr)
add_edge(root1, "e6", "n_nal_info", "n_soot", s_edge_arr)
add_edge(root1, "e7", "n_soot", "n_zam", s_edge_arr)
add_edge(root1, "e8", "n_zam", "n_vhod", "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=classic;endFill=1;strokeColor=#000000;", [[1400, 130]])

add_edge(root1, "e9", "n_nal_doc", "n_form", s_edge_red, [[800, 80]])
add_edge(root1, "e10", "n_form", "n_exc", s_edge_blue_dash)
add_edge(root1, "e11", "n_form", "n_model", s_edge_arr)
add_edge(root1, "e12", "n_model", "n_exc", s_edge_blue_dash)
add_edge(root1, "e13", "n_tu", "n_model", s_edge_red)
add_edge(root1, "e14", "n_tu", "n_t_mod1", s_edge_red, [[1200, 10], [50, 10]]) 

add_edge(root1, "e15", "n_model", "t_text_d", s_edge_arr)
add_edge(root1, "e16", "n_model", "t_graf_d", s_edge_arr)
add_edge(root1, "e17", "n_model", "t_smet_d", s_edge_arr)
add_edge(root1, "e18", "n_model", "t_info_d", s_edge_arr)
add_edge(root1, "e19", "n_model", "t_xml_d", s_edge_arr)
add_edge(root1, "e20", "t_xml_d", "n_exc", s_edge_blue_dash)

add_edge(root1, "e21", "t_graf_d", "n_scan", s_edge_red)
add_edge(root1, "e22", "t_graf_d", "n_cher", s_edge_arr)
add_edge(root1, "e23", "t_text_d", "n_t_mod1", s_edge_arr)
add_edge(root1, "e24", "t_text_d", "n_t_mod2", s_edge_red)
add_edge(root1, "e25", "n_scan", "n_t_mod2", s_edge_arr)

add_edge(root1, "e26", "n_t_mod1", "n_db", s_edge_arr)
add_edge(root1, "e27", "n_t_mod2", "n_db", s_edge_arr)
add_edge(root1, "e28", "n_llm", "n_db", "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=classic;endFill=1;startArrow=classic;startFill=1;strokeColor=#000000;")
add_edge(root1, "e29", "n_soot", "n_llm", s_edge_arr)

add_edge(root1, "e30", "n_soot", "n_tech", s_edge_arr)

save_xml(mxfile1, "Структура работы приемки.drawio")

# --- Пайплайн работы с замечаниями ---
mxfile2, root2 = create_base()

# Background black for pipeline
BG_COLOR="#000000"
TEXT_COLOR="#FFFFFF"
ET.SubElement(mxfile2.find(".//diagram"), "mxGraphModel").set("background", BG_COLOR)

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

add_edge(root2, "e_v_l", "p_vhod", "l_1_t", s_edge_w)

# from left 1 to right Blues
add_edge(root2, "eb1", "l_1_t", "r_16-20_n", s_edge_w, [[600, 280], [600, y_offset2/2]])

# from left 3 to right 106
add_edge(root2, "eb2", "l_3_t", "r_106_n", s_edge_w, [[600, 340], [600, 1040]])

# 72 to black boxes
add_edge(root2, "eb3", "l_72_t", "b_srv1", s_edge_w)
add_edge(root2, "eb4", "l_72_t", "b_srv2", s_edge_w)
add_edge(root2, "eb5", "b_srv1", "r_66_n", s_edge_w)
add_edge(root2, "eb6", "b_srv2", "r_67_n", s_edge_w)

save_xml(mxfile2, "Пайплайн работы с замечаниями.drawio")
print("Exact drawio diagrams generated successfully.")

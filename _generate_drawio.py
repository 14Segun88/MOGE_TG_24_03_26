import xml.etree.ElementTree as ET
import xml.dom.minidom

def create_drawio(filename, nodes):
    # Base structure
    mxfile = ET.Element("mxfile", host="Electron", modified="2024-03-01T00:00:00.000Z", version="24.4.0", type="device")
    diagram = ET.SubElement(mxfile, "diagram", id="diagram-1", name="Page-1")
    mxGraphModel = ET.SubElement(diagram, "mxGraphModel", dx="1000", dy="1000", grid="1", gridSize="10", guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1", pageWidth="827", pageHeight="1169", math="0", shadow="0")
    root = ET.SubElement(mxGraphModel, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    y_offset = 40
    prev_id = None
    
    # Standard dimensions
    width = 240
    height = 60
    x_offset = 290

    for i, title in enumerate(nodes):
        node_id = f"node_{i}"
        
        # Create node
        node = ET.SubElement(root, "mxCell", id=node_id, value=title, style="rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontColor=#000000;fontFamily=Helvetica;fontSize=12;fontStyle=1", vertex="1", parent="1")
        ET.SubElement(node, "mxGeometry", x=str(x_offset), y=str(y_offset), width=str(width), height=str(height), **{"as": "geometry"})
        
        # Create edge if not first element
        if prev_id:
            edge_id = f"edge_{i}"
            edge = ET.SubElement(root, "mxCell", id=edge_id, style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;", edge="1", parent="1", source=prev_id, target=node_id)
            geom_edge = ET.SubElement(edge, "mxGeometry", relative="1", **{"as": "geometry"})
            
        prev_id = node_id
        y_offset += 100

    # formatting the xml output nicely
    xml_str = ET.tostring(mxfile, encoding='utf-8')
    parsed_xml = xml.dom.minidom.parseString(xml_str)
    pretty_xml_as_string = parsed_xml.toprettyxml(indent="  ")
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(pretty_xml_as_string)

structure_nodes = [
    "1. Загрузка пакета документов (ПД)\n(включая XML и PDF/DOCX)",
    "2. Document Analyzer:\nВалидация форматов (XML 1.05), OCR (LayoutLMv3/Camelot),\nGround Truth (сверка УИН и КС)",
    "3. Orchestrator:\nКлассификация и динамическая декомпозиция (urgency: high)",
    "4. Knowledge Base (RAG):\nПоиск по нормативной базе\n(500 000+ чанков, Pinecone, Cohere)",
    "5. Маршрутизация проверок:\nАгенты PP87 / PP154\n(Аудит разделов, ТЭП и энергобалансов)",
    "6. Кросс-секционные проверки:\nСверка ТЭП между Разделом 1 (ПЗ) и 3 (АР)",
    "7. Human-in-the-loop:\nВерификация (<70% уверенности) и\nразрешение спорных ситуаций",
    "8. Report Generator:\nСборка экспертного заключения \n(ГОСТ Р 7.0.97-2016, форматы GGE/PDF)",
    "9. Выдача результатов Заявителю"
]

remarks_nodes = [
    "1. Получение пакета замечаний / ответов\n(от Заказчика или Эксперта)",
    "2. Orchestrator:\nПарсинг замечаний, определение критичности",
    "3. Knowledge Base (RAG):\nПодбор аргументации на базе\nСП и ГОСТ для снятия замечания",
    "4. Document Analyzer:\nАнализ исправленных документов / чертежей",
    "5. Human-in-the-Loop:\nВалидация предложенного ответа\n(или исправления)",
    "6. Disagreement Log:\nЛогирование расхождений мнений (AI vs Человек)\nдля fine-tuning моделей",
    "7. Report Generator:\nФормирование обновленной версии ПД\nили пакета ответов на замечания",
    "8. Отправка отработанных замечаний\n(Завершение пайплайна)"
]

create_drawio("Структура работы приемки.drawio", structure_nodes)
create_drawio("Пайплайн работы с замечаниями.drawio", remarks_nodes)

print("Draw.io files generated successfully.")

from bot import _format_per_file
from types import SimpleNamespace

files = []
for i in range(208):
    files.append(SimpleNamespace(
        file_type="pdf_text",
        name=f"Document_number_{i}.pdf",
        size_kb=150.0,
        is_scan=False,
        suspected_section="Смета"
    ))

result = SimpleNamespace(
    total_files=208,
    files=files,
    xml_summary=None
)

msg = _format_per_file(result)
print("Length:", len(msg))

lines = msg.split("\n")
chunk = []
size = 0
chunks_count = 0
for line in lines:
    line_len = len(line) + 1
    if size + line_len > 4000 and chunk:
        print(f"Chunk {chunks_count+1}: {size} chars")
        chunks_count += 1
        chunk = []
        size = 0
    chunk.append(line)
    size += line_len
if chunk:
    print(f"Chunk {chunks_count+1}: {size} chars")
    chunks_count += 1

print("Total chunks:", chunks_count)

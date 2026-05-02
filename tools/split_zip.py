import os
import zipfile
import sys
from pathlib import Path

def split_zip(input_zip_path: str, max_size_mb: int = 40):
    input_zip = Path(input_zip_path)
    if not input_zip.exists():
        print(f"❌ Файл {input_zip_path} не найден.")
        return

    max_size_bytes = max_size_mb * 1024 * 1024
    out_dir = input_zip.parent
    base_name = input_zip.stem
    
    print(f"📦 Распаковка и переупаковка {input_zip.name} в части по ~{max_size_mb} MB")

    current_part = 1
    current_size = 0
    current_files = []
    
    with zipfile.ZipFile(input_zip, 'r') as zf_in:
        file_list = zf_in.infolist()
        total_files = len(file_list)
        
        print(f"Всего файлов в архиве: {total_files}")
        
        for i, zinfo in enumerate(file_list, 1):
            if zinfo.is_dir():
                continue
                
            # Читаем файл в память
            file_data = zf_in.read(zinfo.filename)
            file_size = len(file_data)
            
            # Если файл сам по себе больше лимита - предупреждаем, но пакуем отдельно
            if file_size > max_size_bytes and not current_files:
                pass # будет запакован один в архив
            elif current_size + file_size > max_size_bytes and current_files:
                # Текущая корзина полна, сохраняем её
                _write_part(current_files, out_dir / f"{base_name}_part{current_part:02d}.zip")
                current_part += 1
                current_files = []
                current_size = 0
                
            current_files.append((zinfo.filename, file_data))
            current_size += file_size
            
            if i % 100 == 0:
                print(f"Обработано {i}/{total_files} файлов...")
                
        # Сохраняем остатки
        if current_files:
            _write_part(current_files, out_dir / f"{base_name}_part{current_part:02d}.zip")
            
    print(f"✅ Готово! Создано {current_part} независимых ZIP архивов файлов.")
    print("Теперь вы можете по одному отправить их боту в Telegram.")

def _write_part(files_data: list, out_path: Path):
    print(f"  -> Создаю {out_path.name} ({len(files_data)} файлов)")
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf_out:
        for fname, fdata in files_data:
            zf_out.writestr(fname, fdata)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python split_zip.py <путь_к_большому_zip_файлу>")
        sys.exit(1)
        
    split_zip(sys.argv[1])

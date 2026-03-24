"""
train/finetune.py — QLoRA Fine-tuning qwen2.5 на датасете строительных норм
МособлГосЭкспертиза | Fine-tuning Pipeline

Требования:
  pip install unsloth[colab-new] xformers trl peft accelerate bitsandbytes

Запуск:
  python3 train/finetune.py                  # полное обучение
  python3 train/finetune.py --test-only      # проверка VRAM без обучения
  python3 train/finetune.py --epochs 1       # быстрый тест (1 эпоха)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DATASET_PATH = Path(__file__).parent / "dataset.jsonl"
OUTPUT_DIR   = Path(__file__).parent.parent / "models" / "qwen_legal_rag"
GGUF_PATH    = Path(__file__).parent.parent / "models" / "qwen_legal_rag.gguf"

# ─────────────────────────────────────────────
#  Параметры обучения (оптимизированы под GTX 1650 4GB)
# ─────────────────────────────────────────────
MODEL_NAME     = "Qwen/Qwen2.5-3B-Instruct"   # базовая модель с HuggingFace
MAX_SEQ_LEN    = 2048
LORA_RANK      = 16
LORA_ALPHA     = 32
LORA_DROPOUT   = 0.0
BATCH_SIZE     = 1                             # micro-batch (мало VRAM)
GRAD_ACCUM     = 8                             # эффективный batch = 8
LR             = 2e-4
NUM_EPOCHS     = 3
WARMUP_RATIO   = 0.1
SAVE_STEPS     = 50
LORA_TARGETS   = ["q_proj","k_proj","v_proj","o_proj",
                   "gate_proj","up_proj","down_proj"]


def check_vram():
    """Проверяет доступную VRAM."""
    import torch
    if not torch.cuda.is_available():
        print("❌ CUDA недоступна! Проверьте драйвер NVIDIA и CUDA toolkit.")
        return False
    
    gpu = torch.cuda.get_device_properties(0)
    total_gb  = gpu.total_memory / 1e9
    print(f"🖥  GPU: {gpu.name}")
    print(f"   VRAM: {total_gb:.1f} GB")
    
    if total_gb < 3.5:
        print(f"❌ Недостаточно VRAM! Нужно минимум 3.5 GB, доступно {total_gb:.1f} GB")
        return False
    
    print(f"   ✅ Достаточно VRAM для QLoRA 4-bit обучения")
    return True


def load_dataset(path: Path) -> list[dict]:
    """Загружает датасет из JSONL файла."""
    if not path.exists():
        raise FileNotFoundError(
            f"❌ Датасет не найден: {path}\n"
            f"   Сначала запустите: python3 train/generate_dataset.py"
        )
    
    examples = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    
    print(f"📚 Загружено примеров: {len(examples)}")
    return examples


def format_for_unsloth(examples: list[dict]) -> list[dict]:
    """
    Форматирует примеры в формат для Unsloth trainer.
    Используем apply_chat_template с ChatML форматом qwen.
    """
    return examples  # Unsloth принимает messages напрямую


def main():
    parser = argparse.ArgumentParser(description="QLoRA Fine-tuning qwen2.5 на строительных нормах")
    parser.add_argument("--test-only", action="store_true",
                        help="Только проверить VRAM и датасет без обучения")
    parser.add_argument("--epochs",   type=int,   default=NUM_EPOCHS)
    parser.add_argument("--lr",       type=float, default=LR)
    parser.add_argument("--rank",     type=int,   default=LORA_RANK)
    args = parser.parse_args()

    print("🚀 QLoRA Fine-tuning: qwen2.5-3b → qwen_legal_rag")
    print(f"   Датасет:   {DATASET_PATH}")
    print(f"   Вывод:     {OUTPUT_DIR}")
    print(f"   Эпохи:     {args.epochs} | LR: {args.lr} | LoRA rank: {args.rank}\n")

    # 1. Проверка GPU
    if not check_vram():
        return
    print()

    # 2. Загрузка датасета
    dataset = load_dataset(DATASET_PATH)

    if args.test_only:
        print("\n✅ Тест пройден! Можно запускать полное обучение:")
        print(f"   python3 train/finetune.py --epochs {args.epochs}")
        return

    # 3. Загрузка Unsloth
    print("\n🤖 Загрузка модели через Unsloth (4-bit)...")
    try:
        from unsloth import FastLanguageModel
        import torch
        from trl import SFTTrainer
        from transformers import TrainingArguments
        from datasets import Dataset
    except ImportError as e:
        print(f"❌ Не установлены зависимости: {e}")
        print("   Установите: pip install unsloth[colab-new] trl")
        return

    # 4. Загрузка базовой модели с QLoRA
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_NAME,
        max_seq_length=MAX_SEQ_LEN,
        dtype=None,          # Автоопределение (float16 для GTX 1650)
        load_in_4bit=True,   # QLoRA 4-bit — главная экономия VRAM
        cache_dir="/mnt/d/models_cache",  # кэш на диск D чтобы не засорять WSL
    )
    print("   ✅ Базовая модель загружена")

    # 5. Добавление LoRA адаптеров
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.rank,
        target_modules=LORA_TARGETS,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        bias="none",
        use_gradient_checkpointing="unsloth",  # экономит ещё ~30% VRAM
        random_state=42,
    )
    print(f"   ✅ LoRA адаптеры добавлены (rank={args.rank})")

    # 6. Подготовка датасета
    def format_messages(example):
        """Применяет chat_template для qwen2.5."""
        text = tokenizer.apply_chat_template(
            example["messages"],
            tokenize=False,
            add_generation_prompt=False,
        )
        return {"text": text}

    hf_dataset = Dataset.from_list(dataset)
    hf_dataset = hf_dataset.map(format_messages)
    
    # Разбиваем на train/eval 90/10
    split = hf_dataset.train_test_split(test_size=0.1, seed=42)
    train_ds = split["train"]
    eval_ds  = split["test"]
    print(f"   Train: {len(train_ds)} | Eval: {len(eval_ds)}")

    # 7. Настройка тренера
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(OUTPUT_DIR),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        warmup_ratio=WARMUP_RATIO,
        learning_rate=args.lr,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=10,
        save_steps=SAVE_STEPS,
        evaluation_strategy="steps",
        eval_steps=SAVE_STEPS,
        load_best_model_at_end=True,
        optim="adamw_8bit",          # 8-bit optimizer (экономит VRAM)
        report_to="none",
        run_name="qwen_legal_rag",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        dataset_text_field="text",
        max_seq_length=MAX_SEQ_LEN,
        args=training_args,
    )

    # 8. ОБУЧЕНИЕ
    print(f"\n🏋 Начало обучения ({args.epochs} эпохи, {len(train_ds)} примеров)...")
    print("   Ожидаемое время: ~2-4 часа на GTX 1650")
    print("   Совет: запустите в tmux чтобы не прерывалось\n")

    trainer_stats = trainer.train()
    
    print(f"\n✅ Обучение завершено!")
    print(f"   Время: {trainer_stats.metrics['train_runtime']:.0f} сек.")
    print(f"   Loss:  {trainer_stats.metrics['train_loss']:.4f}")

    # 9. Экспорт в GGUF (Q4_K_M — оптимально для LM Studio)
    print(f"\n📦 Экспорт в GGUF ({GGUF_PATH})...")
    model.save_pretrained_gguf(
        str(GGUF_PATH.with_suffix("")),
        tokenizer,
        quantization_method="q4_k_m",
    )
    
    print(f"\n{'═' * 55}")
    print(f"✅ Fine-tuning завершён!")
    print(f"   Адаптер:  {OUTPUT_DIR / 'best_model'}")
    print(f"   GGUF:     {GGUF_PATH}")
    print(f"\n📋 Следующий шаг:")
    print(f"   1. Скопируйте {GGUF_PATH.name} в папку моделей LM Studio")
    print(f"   2. Загрузите 'qwen_legal_rag' в LM Studio")
    print(f"   3. Обновите LM_STUDIO_MODEL в .env")
    print(f"{'═' * 55}")


if __name__ == "__main__":
    main()

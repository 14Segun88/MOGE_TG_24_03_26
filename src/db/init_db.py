from src.db.database import engine, Base
# Импортируем все модели, чтобы Base.metadata их зарегистрировал
from src.db.models import DisagreementLog

def init_db():
    """
    Создает все таблицы в базе данных (если не существуют).
    """
    # Base.metadata.drop_all(bind=engine) # Раскомментируй, если нужно пересоздать
    Base.metadata.create_all(bind=engine)
    print("✅ База данных успешно инициализирована (SQLite)")

if __name__ == "__main__":
    init_db()

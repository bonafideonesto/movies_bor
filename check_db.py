import os
import sys

print("🔧 Проверка окружения и БД...")

# Проверяем переменные окружения
print("\n📝 Переменные окружения:")
print(f"TELEGRAM_TOKEN: {'✅' if os.getenv('TELEGRAM_TOKEN') else '❌'}")
print(f"DATABASE_URL: {'✅' if os.getenv('DATABASE_URL') else '❌'}")
if os.getenv('DATABASE_URL'):
    print(f"   (первые 30 символов): {os.getenv('DATABASE_URL')[:30]}...")

# Проверяем импорты
print("\n📦 Проверка пакетов:")
try:
    import telebot
    print("✅ telebot установлен")
except ImportError as e:
    print(f"❌ telebot: {e}")

try:
    import psycopg2
    print("✅ psycopg2 установлен")
except ImportError as e:
    print(f"❌ psycopg2: {e}")

try:
    import sqlite3
    print("✅ sqlite3 установлен (встроенный)")
except ImportError as e:
    print(f"❌ sqlite3: {e}")

# Пробуем подключиться к БД
print("\n🔗 Проверка подключения к БД...")
try:
    from bot import get_connection, init_db
    
    conn = get_connection()
    if conn:
        print("✅ Подключение к БД успешно")
        
        # Проверяем тип соединения
        if isinstance(conn, sqlite3.Connection):
            print("   Тип: SQLite (локальная база)")
        else:
            print("   Тип: PostgreSQL (Supabase)")
        
        # Инициализируем БД
        init_db()
        print("✅ Таблицы инициализированы")
        
        conn.close()
    else:
        print("❌ Не удалось подключиться к БД")
        
except Exception as e:
    print(f"❌ Ошибка при проверке БД: {e}")
    import traceback
    traceback.print_exc()

print("\n✅ Проверка завершена")

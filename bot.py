import telebot
from telebot import types
import psycopg2  # ← ВМЕСТО sqlite3
from urllib.parse import urlparse  # ← ДОБАВЬТЕ
import os
import requests
import re
import time
from deep_translator import GoogleTranslator

# ========== КОНФИГУРАЦИЯ ==========
# Используем переменные окружения для безопасности
TOKEN = os.getenv('TELEGRAM_TOKEN', "8572008688:AAFxlCebMUSKOhzsspjJXtr1vLoP3JUsvDU")
OMDB_API_KEY = os.getenv('OMDB_API_KEY', "7717512b")
KINOPOISK_API_KEY = os.getenv('KINOPOISK_API_KEY', "ZS97X1F-7M144TE-Q24BJS9-BAWFJDE")
DATABASE_URL = os.getenv('DATABASE_URL')  # ← ДОБАВЬТЕ ЭТО

bot = telebot.TeleBot(TOKEN, skip_pending=True)

# ========== БАЗА ДАННЫХ POSTGRESQL (SUPABASE) ==========
def get_connection():
    """Создает подключение к Supabase PostgreSQL"""
    if not DATABASE_URL:
        print("⚠️ DATABASE_URL не указан. Используем SQLite для совместимости.")
        import sqlite3
        return sqlite3.connect('movies.db')
    
    try:
        # Парсим URL подключения
        result = urlparse(DATABASE_URL)
        
        # Подключаемся к Supabase
        conn = psycopg2.connect(
            host=result.hostname,
            port=result.port,
            database=result.path[1:],  # Убираем первый слэш
            user=result.username,
            password=result.password,
            sslmode='require'  # Supabase требует SSL
        )
        return conn
    except Exception as e:
        print(f"❌ Ошибка подключения к Supabase: {e}")
        # Fallback на SQLite
        import sqlite3
        return sqlite3.connect('movies.db')

def init_db():
    """Создает таблицы если их нет"""
    conn = get_connection()
    if not conn:
        print("❌ Не удалось подключиться к БД")
        return
    
    cur = conn.cursor()
    
    try:
        # Проверяем тип подключения
        if isinstance(conn, psycopg2.extensions.connection):
            # PostgreSQL для Supabase
            cur.execute('''
                CREATE TABLE IF NOT EXISTS items (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(20) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    original_title VARCHAR(255),
                    year VARCHAR(10),
                    kp_rating REAL,
                    imdb_rating REAL,
                    kp_url TEXT,
                    imdb_url TEXT,
                    watched INTEGER DEFAULT 0,
                    comment TEXT,
                    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT unique_type_title UNIQUE(type, title)
                )
            ''')
        else:
            # SQLite (для локальной разработки)
            cur.execute('''CREATE TABLE IF NOT EXISTS items
                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 type TEXT NOT NULL,
                 title TEXT NOT NULL,
                 original_title TEXT,
                 year TEXT,
                 kp_rating REAL,
                 imdb_rating REAL,
                 kp_url TEXT,
                 imdb_url TEXT,
                 watched INTEGER DEFAULT 0,
                 comment TEXT,
                 added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 UNIQUE(type, title))''')
        
        conn.commit()
        print("✅ База данных инициализирована (Supabase)")
    except Exception as e:
        print(f"❌ Ошибка при создании таблиц: {e}")
    finally:
        conn.close()

def add_item(item_type, title, original_title, year, kp_rating=None, imdb_rating=None, kp_url=None, imdb_url=None):
    """Добавляет фильм/сериал в БД"""
    conn = get_connection()
    if not conn:
        return None
    
    cur = conn.cursor()
    try:
        # Проверяем тип подключения
        if isinstance(conn, psycopg2.extensions.connection):
            # PostgreSQL (Supabase)
            cur.execute('''
                INSERT INTO items (type, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT unique_type_title DO NOTHING
                RETURNING id
            ''', (item_type, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url))
            
            result = cur.fetchone()
            item_id = result[0] if result else None
        else:
            # SQLite
            cur.execute('''
                INSERT OR IGNORE INTO items (type, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (item_type, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url))
            
            item_id = cur.lastrowid
        
        conn.commit()
        return item_id
    except Exception as e:
        print(f"❌ Ошибка при добавлении: {e}")
        return None
    finally:
        conn.close()

def get_items(item_type):
    """Получает все фильмы/сериалы указанного типа"""
    conn = get_connection()
    if not conn:
        return []
    
    cur = conn.cursor()
    try:
        if isinstance(conn, psycopg2.extensions.connection):
            cur.execute('''
                SELECT id, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                FROM items WHERE type = %s ORDER BY title
            ''', (item_type,))
        else:
            cur.execute('''
                SELECT id, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                FROM items WHERE type = ? ORDER BY title
            ''', (item_type,))
        
        items = cur.fetchall()
        return items
    except Exception as e:
        print(f"❌ Ошибка при получении данных: {e}")
        return []
    finally:
        conn.close()

def get_item_by_id(item_id):
    """Получает фильм/сериал по ID"""
    conn = get_connection()
    if not conn:
        return None
    
    cur = conn.cursor()
    try:
        if isinstance(conn, psycopg2.extensions.connection):
            cur.execute('''
                SELECT id, type, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                FROM items WHERE id = %s
            ''', (item_id,))
        else:
            cur.execute('''
                SELECT id, type, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                FROM items WHERE id = ?
            ''', (item_id,))
        
        item = cur.fetchone()
        return item
    except Exception as e:
        print(f"❌ Ошибка при получении элемента: {e}")
        return None
    finally:
        conn.close()

def update_item(item_id, **kwargs):
    """Обновляет данные фильма/сериала"""
    conn = get_connection()
    if not conn or not kwargs:
        return False
    
    cur = conn.cursor()
    try:
        if isinstance(conn, psycopg2.extensions.connection):
            # PostgreSQL
            set_clause = ", ".join([f"{key} = %s" for key in kwargs.keys()])
            values = list(kwargs.values())
            values.append(item_id)
            
            cur.execute(f"UPDATE items SET {set_clause} WHERE id = %s", values)
        else:
            # SQLite
            set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values())
            values.append(item_id)
            
            cur.execute(f"UPDATE items SET {set_clause} WHERE id = ?", values)
        
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        print(f"❌ Ошибка при обновлении: {e}")
        return False
    finally:
        conn.close()

def delete_item(item_id):
    """Удаляет фильм/сериал"""
    conn = get_connection()
    if not conn:
        return False
    
    cur = conn.cursor()
    try:
        if isinstance(conn, psycopg2.extensions.connection):
            cur.execute("DELETE FROM items WHERE id = %s", (item_id,))
        else:
            cur.execute("DELETE FROM items WHERE id = ?", (item_id,))
        
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        print(f"❌ Ошибка при удалении: {e}")
        return False
    finally:
        conn.close()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
# (ВСЕ ФУНКЦИИ ОСТАЮТСЯ БЕЗ ИЗМЕНЕНИЙ:
# is_russian_text, translate_russian_to_english, 
# search_kinopoisk, search_omdb, search_film,
# клавиатуры, форматирование текста, обработчики)
# ...

# ========== ЗАПУСК БОТА ==========
if __name__ == '__main__':
    print("=" * 50)
    print("🎬 КиноБот запущен!")
    print("=" * 50)
    
    # Проверяем подключение к БД
    if DATABASE_URL:
        print(f"📊 База данных: Supabase PostgreSQL")
    else:
        print(f"📊 База данных: SQLite (локально)")
        print("⚠️ Для постоянного хранения укажите DATABASE_URL")
    
    init_db()
    
    # Бесконечный цикл с перезапуском при ошибках
    while True:
        try:
            print("🟢 Бот запускается...")
            bot.polling(none_stop=True, timeout=60, skip_pending=True)
        except Exception as e:
            print(f"🔴 Ошибка: {e}")
            print("🔄 Перезапуск через 5 секунд...")
            time.sleep(5)
            continue

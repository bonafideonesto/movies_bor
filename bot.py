import telebot
from telebot import types
import os
import requests
import re
import time
import threading
from deep_translator import GoogleTranslator
from flask import Flask, request
import sqlite3
import logging

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== HTTP СЕРВЕР ДЛЯ RENDER ==========
app = Flask(__name__)

# ========== КОНФИГУРАЦИЯ ==========
TOKEN = os.getenv('TELEGRAM_TOKEN')
OMDB_API_KEY = os.getenv('OMDB_API_KEY', "7717512b")
KINOPOISK_API_KEY = os.getenv('KINOPOISK_API_KEY', "ZS97X1F-7M144TE-Q24BJS9-BAWFJDE")
DATABASE_URL = os.getenv('DATABASE_URL')

# Автоматически генерируем WEBHOOK_URL для Render
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL')
if RENDER_EXTERNAL_URL:
    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/webhook"
    logger.info(f"🌐 WEBHOOK_URL сгенерирован: {WEBHOOK_URL}")
else:
    WEBHOOK_URL = None
    logger.warning("⚠️ RENDER_EXTERNAL_URL не установлен, вебхук не настроен")

# Глобальная переменная для SQLite соединения
sqlite_conn = None

# Проверка токена
if not TOKEN:
    logger.error("❌❌❌ ВНИМАНИЕ: TELEGRAM_TOKEN не установлен!")
    logger.error("❌❌❌ Установите переменную окружения TELEGRAM_TOKEN на Render")
    exit(1)

bot = telebot.TeleBot(TOKEN)
logger.info(f"🤖 Бот инициализирован с токеном: {TOKEN[:10]}...")

# ========== ВЕБХУК РУТЫ ==========
@app.route('/')
def home():
    logger.info("📄 Главная страница запрошена")
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>🎬 КиноБот</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
            h1 { color: #333; }
            p { color: #666; }
            .status { color: green; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>🎬 КиноБот работает!</h1>
        <p class="status">✅ Бот активен и готов к работе</p>
        <p>Добавляйте фильмы и сериалы через Telegram</p>
        <p><a href="/health">Проверить статус</a></p>
        <p><a href="/set_webhook">Установить вебхук</a></p>
        <p><a href="/check_webhook">Проверить вебхук</a></p>
    </body>
    </html>
    """

@app.route('/health')
def health_check():
    logger.info("🏥 Health check запрошен")
    return "OK", 200

@app.route('/ping')
def ping():
    return "pong", 200

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    if not WEBHOOK_URL:
        logger.error("❌ WEBHOOK_URL не установлен")
        return "❌ WEBHOOK_URL не установлен", 500
    
    try:
        logger.info(f"🔄 Устанавливаю вебхук на {WEBHOOK_URL}")
        bot.remove_webhook()
        time.sleep(0.5)
        
        # Устанавливаем вебхук
        success = bot.set_webhook(url=WEBHOOK_URL)
        if success:
            logger.info(f"✅ Вебхук установлен: {WEBHOOK_URL}")
            return f"✅ Вебхук установлен: {WEBHOOK_URL}", 200
        else:
            logger.error("❌ Не удалось установить вебхук")
            return "❌ Не удалось установить вебхук", 500
    except Exception as e:
        logger.error(f"❌ Ошибка установки вебхука: {e}")
        return f"❌ Ошибка установки вебхука: {e}", 500

@app.route('/check_webhook', methods=['GET'])
def check_webhook():
    try:
        info = bot.get_webhook_info()
        return f"""
        <h1>Webhook Info</h1>
        <p>URL: {info.url}</p>
        <p>Has custom certificate: {info.has_custom_certificate}</p>
        <p>Pending update count: {info.pending_update_count}</p>
        <p>Last error date: {info.last_error_date}</p>
        <p>Last error message: {info.last_error_message}</p>
        <p>Max connections: {info.max_connections}</p>
        """
    except Exception as e:
        return f"Error: {e}", 500

@app.route('/webhook', methods=['POST'])
def webhook():
    logger.info(f"📨 Получен вебхук в {time.strftime('%H:%M:%S')}")
    
    if request.headers.get('content-type') != 'application/json':
        logger.error(f"❌ Неверный content-type: {request.headers.get('content-type')}")
        return 'Invalid content type', 403
    
    try:
        json_string = request.get_data().decode('utf-8')
        logger.info(f"📝 Длина данных: {len(json_string)} символов")
        
        update = types.Update.de_json(json_string)
        
        # Логируем что получили
        if update.message:
            logger.info(f"📩 Сообщение от {update.message.chat.id}: '{update.message.text}'")
        elif update.callback_query:
            logger.info(f"🔘 Callback от {update.callback_query.from_user.id}: {update.callback_query.data}")
        else:
            logger.info(f"📦 Update другого типа: {update}")
        
        # Обрабатываем update
        bot.process_new_updates([update])
        logger.info(f"✅ Вебхук обработан успешно")
        
        return ''
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}")
        import traceback
        traceback.print_exc()
        return 'Error', 500

# ========== БАЗА ДАННЫХ ==========
def get_connection():
    """Создает подключение к БД"""
    global sqlite_conn
    
    # Всегда используем PostgreSQL/Supabase
    if not DATABASE_URL or DATABASE_URL == '':
        logger.warning("⚠️ DATABASE_URL не установлен, используем SQLite in-memory")
        
        # Используем одно соединение для SQLite
        if sqlite_conn is None:
            sqlite_conn = sqlite3.connect(':memory:', check_same_thread=False)
            logger.info("✅ Создано новое SQLite соединение")
        
        return sqlite_conn
    
    logger.info(f"🔗 Подключаемся к PostgreSQL...")
    
    try:
        import psycopg2
        from urllib.parse import urlparse
        
        result = urlparse(DATABASE_URL)
        
        conn_params = {
            'host': result.hostname,
            'port': result.port,
            'database': result.path[1:],
            'user': result.username,
            'password': result.password,
            'sslmode': 'require'
        }
        
        conn = psycopg2.connect(**conn_params)
        logger.info("✅ Успешное подключение к PostgreSQL")
        return conn
        
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к PostgreSQL: {e}")
        # В режиме разработки используем SQLite в памяти
        logger.warning("⚠️ Используем in-memory SQLite")
        
        if sqlite_conn is None:
            sqlite_conn = sqlite3.connect(':memory:', check_same_thread=False)
            logger.info("✅ Создано новое SQLite соединение")
        
        return sqlite_conn

def init_db():
    """Создает таблицы"""
    logger.info("🔄 Инициализация базы данных...")
    conn = get_connection()
    if not conn:
        logger.error("❌ Не удалось подключиться к БД")
        return False
    
    cur = conn.cursor()
    
    try:
        is_sqlite = isinstance(conn, sqlite3.Connection)
        
        if is_sqlite:
            # SQLite версия с полем жанра
            cur.execute('''
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type VARCHAR(20) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    original_title VARCHAR(255),
                    year VARCHAR(10),
                    genre VARCHAR(255),
                    kp_rating REAL,
                    imdb_rating REAL,
                    kp_url TEXT,
                    imdb_url TEXT,
                    watched INTEGER DEFAULT 0,
                    comment TEXT,
                    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            logger.info("✅ Таблица items создана (SQLite)")
        else:
            # PostgreSQL версия с полем жанра
            cur.execute('''
                CREATE TABLE IF NOT EXISTS items (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(20) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    original_title VARCHAR(255),
                    year VARCHAR(10),
                    genre VARCHAR(255),
                    kp_rating REAL,
                    imdb_rating REAL,
                    kp_url TEXT,
                    imdb_url TEXT,
                    watched INTEGER DEFAULT 0,
                    comment TEXT,
                    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            logger.info("✅ Таблица items создана (PostgreSQL)")
        
        conn.commit()
        logger.info("✅ База данных инициализирована")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # НЕ закрываем соединение для SQLite!
        if not isinstance(conn, sqlite3.Connection):
            conn.close()

def add_item(item_type, title, original_title, year, genre=None, kp_rating=None, imdb_rating=None, kp_url=None, imdb_url=None):
    """Добавляет фильм/сериал"""
    logger.info(f"➕ Добавление: {title} (тип: {item_type}, год: {year}, жанр: {genre})")
    
    conn = get_connection()
    if not conn:
        logger.error("❌ Нет подключения к БД")
        return None
    
    cur = conn.cursor()
    try:
        is_sqlite = isinstance(conn, sqlite3.Connection)
        
        if is_sqlite:
            cur.execute('''
                INSERT INTO items (type, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (item_type, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url))
            
            conn.commit()
            cur.execute('SELECT last_insert_rowid()')
            result = cur.fetchone()
        else:
            cur.execute('''
                INSERT INTO items (type, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            ''', (item_type, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url))
            
            conn.commit()
            result = cur.fetchone()
        
        if result:
            item_id = result[0]
            logger.info(f"✅ Успешно добавлено с ID: {item_id}")
            return item_id
        else:
            logger.warning("⚠️ Элемент не добавлен")
            return None
            
    except Exception as e:
        logger.error(f"❌ Ошибка при добавлении в БД: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        # Закрываем только для PostgreSQL
        if not isinstance(conn, sqlite3.Connection):
            conn.close()

def get_items(item_type):
    """Получает все фильмы/сериалы"""
    conn = get_connection()
    if not conn:
        logger.error("❌ Нет подключения к БД")
        return []
    
    cur = conn.cursor()
    try:
        is_sqlite = isinstance(conn, sqlite3.Connection)
        
        if is_sqlite:
            cur.execute('''
                SELECT id, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                FROM items WHERE type = ? ORDER BY title
            ''', (item_type,))
        else:
            cur.execute('''
                SELECT id, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                FROM items WHERE type = %s ORDER BY title
            ''', (item_type,))
        
        return cur.fetchall()
    except Exception as e:
        logger.error(f"❌ Ошибка при получении данных: {e}")
        return []
    finally:
        # Закрываем только для PostgreSQL
        if not isinstance(conn, sqlite3.Connection):
            conn.close()

def search_items(search_term, search_type=None, limit=50):
    """Ищет фильмы/сериалы по названию"""
    conn = get_connection()
    if not conn:
        logger.error("❌ Нет подключения к БД")
        return []
    
    cur = conn.cursor()
    try:
        is_sqlite = isinstance(conn, sqlite3.Connection)
        search_term = f"%{search_term.lower()}%"
        
        if search_type:
            # Поиск в конкретном типе
            if is_sqlite:
                query = '''
                    SELECT id, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                    FROM items 
                    WHERE type = ? AND (LOWER(title) LIKE ? OR LOWER(original_title) LIKE ?)
                    ORDER BY title
                    LIMIT ?
                '''
                cur.execute(query, (search_type, search_term, search_term, limit))
            else:
                query = '''
                    SELECT id, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                    FROM items 
                    WHERE type = %s AND (LOWER(title) LIKE %s OR LOWER(original_title) LIKE %s)
                    ORDER BY title
                    LIMIT %s
                '''
                cur.execute(query, (search_type, search_term, search_term, limit))
        else:
            # Поиск по всем типам
            if is_sqlite:
                query = '''
                    SELECT id, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                    FROM items 
                    WHERE LOWER(title) LIKE ? OR LOWER(original_title) LIKE ?
                    ORDER BY type, title
                    LIMIT ?
                '''
                cur.execute(query, (search_term, search_term, limit))
            else:
                query = '''
                    SELECT id, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                    FROM items 
                    WHERE LOWER(title) LIKE %s OR LOWER(original_title) LIKE %s
                    ORDER BY type, title
                    LIMIT %s
                '''
                cur.execute(query, (search_term, search_term, limit))
        
        results = cur.fetchall()
        logger.info(f"🔍 Найдено результатов: {len(results)}")
        return results
        
    except Exception as e:
        logger.error(f"❌ Ошибка при поиске: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        # Закрываем только для PostgreSQL
        if not isinstance(conn, sqlite3.Connection):
            conn.close()

def get_item_by_id(item_id):
    """Получает по ID"""
    conn = get_connection()
    if not conn:
        logger.error("❌ Нет подключения к БД")
        return None
    
    cur = conn.cursor()
    try:
        is_sqlite = isinstance(conn, sqlite3.Connection)
        
        if is_sqlite:
            cur.execute('''
                SELECT id, type, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                FROM items WHERE id = ?
            ''', (item_id,))
        else:
            cur.execute('''
                SELECT id, type, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                FROM items WHERE id = %s
            ''', (item_id,))
        return cur.fetchone()
    except Exception as e:
        logger.error(f"❌ Ошибка при получении элемента: {e}")
        return None
    finally:
        # Закрываем только для PostgreSQL
        if not isinstance(conn, sqlite3.Connection):
            conn.close()

def update_item(item_id, **kwargs):
    """Обновляет данные"""
    conn = get_connection()
    if not conn or not kwargs:
        return False
    
    cur = conn.cursor()
    try:
        is_sqlite = isinstance(conn, sqlite3.Connection)
        
        if is_sqlite:
            set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values())
            values.append(item_id)
            
            cur.execute(f"UPDATE items SET {set_clause} WHERE id = ?", values)
        else:
            set_clause = ", ".join([f"{key} = %s" for key in kwargs.keys()])
            values = list(kwargs.values())
            values.append(item_id)
            
            cur.execute(f"UPDATE items SET {set_clause} WHERE id = %s", values)
        
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.error(f"❌ Ошибка при обновлении: {e}")
        return False
    finally:
        # Закрываем только для PostgreSQL
        if not isinstance(conn, sqlite3.Connection):
            conn.close()

def delete_item(item_id):
    """Удаляет запись"""
    conn = get_connection()
    if not conn:
        logger.error("❌ Нет подключения к БД")
        return False
    
    cur = conn.cursor()
    try:
        is_sqlite = isinstance(conn, sqlite3.Connection)
        
        if is_sqlite:
            cur.execute("DELETE FROM items WHERE id = ?", (item_id,))
        else:
            cur.execute("DELETE FROM items WHERE id = %s", (item_id,))
        
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении: {e}")
        return False
    finally:
        # Закрываем только для PostgreSQL
        if not isinstance(conn, sqlite3.Connection):
            conn.close()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_russian_text(text):
    return bool(re.search('[а-яА-Я]', text))

def translate_russian_to_english(text):
    try:
        translator = GoogleTranslator(source='ru', target='en')
        return translator.translate(text)
    except:
        return text

def search_kinopoisk(title):
    if not KINOPOISK_API_KEY:
        return None
    
    headers = {'X-API-KEY': KINOPOISK_API_KEY}
    url = f"https://api.kinopoisk.dev/v1.4/movie/search?page=1&limit=3&query={requests.utils.quote(title)}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('docs') and data['docs']:
                film = data['docs'][0]
                
                # Получаем жанры
                genres = []
                for genre in film.get('genres', []):
                    if genre.get('name'):
                        genres.append(genre['name'])
                genre_str = ', '.join(genres[:3]) if genres else None
                
                return {
                    'title': film.get('name', 'Неизвестно'),
                    'original_title': film.get('alternativeName', film.get('name', 'Неизвестно')),
                    'year': film.get('year', 'Неизвестно'),
                    'genre': genre_str,
                    'kp_rating': round(film.get('rating', {}).get('kp', 0), 1) if film.get('rating', {}).get('kp') else None,
                    'imdb_rating': round(film.get('rating', {}).get('imdb', 0), 1) if film.get('rating', {}).get('imdb') else None,
                    'type': film.get('type', 'movie'),
                    'kp_url': f"https://www.kinopoisk.ru/film/{film.get('id', '')}" if film.get('id') else None
                }
    except Exception as e:
        logger.error(f"❌ Ошибка поиска на Кинопоиске: {e}")
    return None

def search_omdb(title):
    if not OMDB_API_KEY:
        return None
    
    search_titles = [translate_russian_to_english(title), title] if is_russian_text(title) else [title]
    
    for search_title in search_titles:
        url = f"http://www.omdbapi.com/?t={requests.utils.quote(search_title)}&apikey={OMDB_API_KEY}"
        try:
            response = requests.get(url, timeout=5)
            data = response.json()
            if data.get('Response') == 'True':
                imdb_rating = None
                for rating_item in data.get('Ratings', []):
                    if rating_item['Source'] == 'Internet Movie Database':
                        try:
                            imdb_rating = float(rating_item['Value'].split('/')[0])
                            break
                        except:
                            pass
                
                # Получаем жанр из OMDB
                genre_str = data.get('Genre', None)
                if genre_str and ',' in genre_str:
                    genre_str = genre_str.split(',')[0]  # Берем первый жанр
                
                return {
                    'title': data.get('Title', search_title),
                    'original_title': data.get('Title', search_title),
                    'year': data.get('Year', 'Неизвестно'),
                    'genre': genre_str,
                    'imdb_rating': round(imdb_rating, 1) if imdb_rating else None,
                    'kp_rating': None,
                    'type': 'movie' if data.get('Type') == 'movie' else 'series',
                    'imdb_url': f"https://www.imdb.com/title/{data.get('imdbID', '')}" if data.get('imdbID') else None
                }
        except Exception as e:
            logger.error(f"❌ Ошибка поиска на OMDB: {e}")
            continue
    return None

def search_film(title, item_type=None):
    results = {}
    
    kp_result = search_kinopoisk(title)
    if kp_result:
        results.update(kp_result)
        eng_title = kp_result.get('original_title') if is_russian_text(title) else title
    else:
        eng_title = title
    
    omdb_result = search_omdb(eng_title)
    if omdb_result:
        if not results:
            results = omdb_result
        else:
            if not results.get('imdb_rating') and omdb_result.get('imdb_rating'):
                results['imdb_rating'] = omdb_result['imdb_rating']
            if not results.get('imdb_url') and omdb_result.get('imdb_url'):
                results['imdb_url'] = omdb_result['imdb_url']
            if not results.get('genre') and omdb_result.get('genre'):
                results['genre'] = omdb_result['genre']
    
    if not results:
        results = {
            'title': title,
            'original_title': title,
            'year': 'Неизвестно',
            'genre': None,
            'kp_rating': None,
            'imdb_rating': None,
            'type': item_type or 'movie',
            'kp_url': None,
            'imdb_url': None
        }
    
    return results

# ========== КЛАВИАТУРЫ ==========
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1 = types.KeyboardButton('🎬 Список сериалов')
    btn2 = types.KeyboardButton('🎥 Список фильмов')
    btn3 = types.KeyboardButton('➕ Добавить фильм или сериал')
    btn4 = types.KeyboardButton('🔍 Поиск в списке')
    btn5 = types.KeyboardButton('📊 Статистика')
    markup.add(btn1, btn2, btn3, btn4, btn5)
    return markup

def type_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1 = types.KeyboardButton('Фильм')
    btn2 = types.KeyboardButton('Сериал')
    btn3 = types.KeyboardButton('Назад')
    markup.add(btn1, btn2, btn3)
    return markup

def skip_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1 = types.KeyboardButton('➡️ Пропустить комментарий')
    markup.add(btn1)
    return markup

def search_type_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1 = types.KeyboardButton('🎬 Поиск сериалов')
    btn2 = types.KeyboardButton('🎥 Поиск фильмов')
    btn3 = types.KeyboardButton('🔍 Поиск везде')
    btn4 = types.KeyboardButton('↩️ Назад')
    markup.add(btn1, btn2, btn3, btn4)
    return markup

def list_keyboard(items, prefix="item"):
    markup = types.InlineKeyboardMarkup(row_width=2)
    for item in items:
        item_id, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment = item
        watched_icon = "✅" if watched else "👁"
        btn_text = f"{watched_icon} {title}"
        if year and year != 'Неизвестно':
            btn_text += f" ({year})"
        if len(btn_text) > 40:
            btn_text = btn_text[:37] + "..."
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"{prefix}_{item_id}"))
    return markup

def search_results_keyboard(search_results):
    markup = types.InlineKeyboardMarkup(row_width=2)
    for item in search_results:
        item_id, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment = item
        # Определяем тип элемента
        item_details = get_item_by_id(item_id)
        type_icon = "🎬" if item_details and item_details[1] == 'series' else "🎥"
        watched_icon = "✅" if watched else "👁"
        btn_text = f"{type_icon}{watched_icon} {title}"
        if year and year != 'Неизвестно':
            btn_text += f" ({year})"
        if len(btn_text) > 40:
            btn_text = btn_text[:37] + "..."
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"item_{item_id}"))
    markup.add(types.InlineKeyboardButton("🔄 Новый поиск", callback_data="new_search"))
    markup.add(types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_main"))
    return markup

def item_keyboard(item_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('✅ Просмотрено', callback_data=f'watch_{item_id}'),
        types.InlineKeyboardButton('👁 Хочу посмотреть', callback_data=f'unwatch_{item_id}'),
        types.InlineKeyboardButton('💬 Комментарий', callback_data=f'comment_{item_id}'),
        types.InlineKeyboardButton('🗑 Удалить', callback_data=f'delete_{item_id}'),
        types.InlineKeyboardButton('↩️ Назад к списку', callback_data='back_to_list')
    )
    return markup

# ========== ФОРМАТИРОВАНИЕ ТЕКСТА ==========
def format_item_details(item):
    item_id, item_type, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment = item
    
    type_ru = "сериал" if item_type == 'series' else "фильм"
    watched_text = "✅ Просмотрено" if watched else "👁 Хочу посмотреть"
    
    text = f"🎬 *{type_ru.upper()} #{item_id}*\n\n"
    text += f"📌 *{title}*\n"
    
    if original_title and original_title != title:
        text += f"🌐 *Оригинальное название:* {original_title}\n"
    
    text += f"📅 *Год:* {year}\n"
    
    if genre:
        text += f"🎭 *Жанр:* {genre}\n"
    
    text += f"📊 *Статус:* {watched_text}\n"
    
    ratings = []
    if kp_rating:
        ratings.append(f"КП: ⭐{kp_rating}")
    if imdb_rating:
        ratings.append(f"IMDb: ⭐{imdb_rating}")
    if ratings:
        text += f"⭐ *Рейтинги:* {' | '.join(ratings)}\n"
    
    links = []
    if kp_url:
        links.append(f"[Кинопоиск]({kp_url})")
    if imdb_url:
        links.append(f"[IMDb]({imdb_url})")
    if links:
        text += f"🔗 *Ссылки:* {' | '.join(links)}\n"
    
    if comment:
        text += f"\n💭 *Комментарий:*\n{comment}\n"
    else:
        text += f"\n💭 *Комментарий:* не добавлен\n"
    
    return text

def format_search_results(search_results, search_term, search_type=None):
    movies_count = 0
    series_count = 0
    
    for item in search_results:
        item_details = get_item_by_id(item[0])
        if item_details and item_details[1] == 'movie':
            movies_count += 1
        else:
            series_count += 1
    
    if search_type == 'movie':
        type_text = "фильмов"
    elif search_type == 'series':
        type_text = "сериалов"
    else:
        type_text = "результатов"
    
    text = f"🔍 *Результаты поиска по запросу: '{search_term}'*\n\n"
    text += f"📊 *Найдено {type_text}:* {len(search_results)}\n"
    
    if not search_type:
        text += f"🎥 Фильмы: {movies_count}\n"
        text += f"🎬 Сериалы: {series_count}\n"
    
    if len(search_results) > 10:
        text += f"\n⚠️ Показаны первые 10 из {len(search_results)} результатов\n"
    
    return text

def format_stats():
    all_movies = get_items('movie')
    all_series = get_items('series')
    
    watched_movies = sum(1 for m in all_movies if m[9])
    watched_series = sum(1 for s in all_series if s[9])
    
    text = "📊 *Ваша статистика:*\n\n"
    text += f"🎥 *Фильмы:* {len(all_movies)} (просмотрено: {watched_movies})\n"
    text += f"🎬 *Сериалы:* {len(all_series)} (просмотрено: {watched_series})\n"
    text += f"📋 *Всего:* {len(all_movies) + len(all_series)} (просмотрено: {watched_movies + watched_series})"
    
    return text

# ========== ОБРАБОТЧИКИ СООБЩЕНИЙ ==========
user_states = {}

@bot.message_handler(commands=['start', 'help'])
def start(message):
    logger.info(f"🚀 Команда /start от {message.chat.id}")
    try:
        bot.send_message(
            message.chat.id, 
            "🎬 *КиноБот - ваш персональный список фильмов и сериалов*\n\n"
            "Я помогу вам:\n"
            "• 📝 Вести список просмотренных фильмов и сериалов\n"
            "• 🎭 Добавлять информацию о жанрах\n"
            "• ✅ Отмечать 'Просмотрено' или 'Хочу посмотреть'\n"
            "• 💬 Добавлять комментарии к фильмам\n"
            "• 🗑 Удалять записи из списка\n"
            "• 🔍 Искать по вашему списку\n"
            "• ⭐ Автоматически находить рейтинги и жанры\n\n"
            "Выберите действие ниже:",
            parse_mode='Markdown',
            reply_markup=main_keyboard()
        )
        logger.info(f"✅ Ответ на /start отправлен {message.chat.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке /start: {e}")

@bot.message_handler(func=lambda message: message.text == '🎬 Список сериалов')
def show_series(message):
    logger.info(f"📺 Запрос списка сериалов от {message.chat.id}")
    try:
        items = get_items('series')
        if not items:
            text = "📭 Список сериалов пуст.\n\nДобавьте первый сериал через меню '➕ Добавить фильм или сериал'"
            bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=main_keyboard())
        else:
            bot.send_message(
                message.chat.id,
                "📺 *Ваш список сериалов:*\n\nВыберите сериал для детального просмотра:",
                parse_mode='Markdown',
                reply_markup=list_keyboard(items, "series")
            )
        logger.info(f"✅ Список сериалов отправлен {message.chat.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при показе сериалов: {e}")

@bot.message_handler(func=lambda message: message.text == '🎥 Список фильмов')
def show_movies(message):
    logger.info(f"🎥 Запрос списка фильмов от {message.chat.id}")
    try:
        items = get_items('movie')
        if not items:
            text = "📭 Список фильмов пуст.\n\nДобавьте первый фильм через меню '➕ Добавить фильм или сериал'"
            bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=main_keyboard())
        else:
            bot.send_message(
                message.chat.id,
                "🎞 *Ваш список фильмов:*\n\nВыберите фильм для детального просмотра:",
                parse_mode='Markdown',
                reply_markup=list_keyboard(items, "movie")
            )
        logger.info(f"✅ Список фильмов отправлен {message.chat.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при показе фильмов: {e}")

@bot.message_handler(func=lambda message: message.text == '🔍 Поиск в списке')
def start_search(message):
    logger.info(f"🔍 Запрос поиска от {message.chat.id}")
    try:
        bot.send_message(
            message.chat.id,
            "🔍 *Поиск в вашем списке*\n\n"
            "Вы можете искать фильмы и сериалы по названию.\n"
            "Поиск работает по русским и английским названиям.\n\n"
            "Выберите область поиска:",
            parse_mode='Markdown',
            reply_markup=search_type_keyboard()
        )
        user_states[message.chat.id] = {'state': 'choosing_search_type'}
        logger.info(f"✅ Меню поиска отправлено {message.chat.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при начале поиска: {e}")

@bot.message_handler(func=lambda message: message.text in ['🎬 Поиск сериалов', '🎥 Поиск фильмов', '🔍 Поиск везде'])
def choose_search_type(message):
    chat_id = message.chat.id
    logger.info(f"🔍 Выбор типа поиска от {chat_id}: {message.text}")
    
    try:
        if message.text == '🎬 Поиск сериалов':
            search_type = 'series'
            type_text = "сериалов"
        elif message.text == '🎥 Поиск фильмов':
            search_type = 'movie'
            type_text = "фильмов"
        else:
            search_type = None
            type_text = "везде"
        
        user_states[chat_id] = {
            'state': 'entering_search_term',
            'search_type': search_type
        }
        
        if search_type:
            bot.send_message(
                chat_id,
                f"🔍 *Поиск {type_text}*\n\nВведите название или часть названия для поиска:",
                parse_mode='Markdown',
                reply_markup=types.ReplyKeyboardRemove()
            )
        else:
            bot.send_message(
                chat_id,
                f"🔍 *Поиск во всех записях*\n\nВведите название или часть названия для поиска:",
                parse_mode='Markdown',
                reply_markup=types.ReplyKeyboardRemove()
            )
        logger.info(f"✅ Запрос поискового запроса отправлен {chat_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при выборе типа поиска: {e}")

@bot.message_handler(func=lambda message: message.chat.id in user_states and user_states[message.chat.id]['state'] == 'entering_search_term')
def perform_search(message):
    chat_id = message.chat.id
    search_term = message.text.strip()
    search_type = user_states[chat_id].get('search_type')
    
    logger.info(f"🔍 Выполнение поиска от {chat_id}: '{search_term}', тип: {search_type}")
    
    if not search_term:
        bot.send_message(chat_id, "❌ Поисковый запрос не может быть пустым.", 
                       reply_markup=search_type_keyboard())
        user_states[chat_id]['state'] = 'choosing_search_type'
        return
    
    try:
        bot.send_message(chat_id, f"🔍 *Ищу '{search_term}'...*", parse_mode='Markdown')
        
        search_results = search_items(search_term, search_type, limit=50)
        
        if not search_results:
            if search_type == 'movie':
                text = f"🎥 *Фильмы не найдены*\n\nПо запросу '{search_term}' не найдено фильмов в вашем списке."
            elif search_type == 'series':
                text = f"🎬 *Сериалы не найдены*\n\nПо запросу '{search_term}' не найдено сериалов в вашем списке."
            else:
                text = f"📭 *Ничего не найдено*\n\nПо запросу '{search_term}' ничего не найдено в вашем списке."
            
            bot.send_message(
                chat_id,
                text,
                parse_mode='Markdown',
                reply_markup=main_keyboard()
            )
            del user_states[chat_id]
            logger.info(f"🔍 Поиск не дал результатов для {chat_id}")
            return
        
        # Сохраняем результаты поиска в состоянии пользователя
        user_states[chat_id]['search_results'] = search_results
        user_states[chat_id]['search_term'] = search_term
        user_states[chat_id]['state'] = 'showing_search_results'
        
        # Показываем первые 10 результатов
        results_to_show = search_results[:10]
        
        bot.send_message(
            chat_id,
            format_search_results(results_to_show, search_term, search_type),
            parse_mode='Markdown',
            reply_markup=search_results_keyboard(results_to_show)
        )
        logger.info(f"✅ Результаты поиска отправлены {chat_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при выполнении поиска: {e}")
        bot.send_message(chat_id, "❌ Произошла ошибка при поиске.", reply_markup=main_keyboard())
        if chat_id in user_states:
            del user_states[chat_id]

@bot.message_handler(func=lambda message: message.text == '📊 Статистика')
def show_stats(message):
    logger.info(f"📊 Запрос статистики от {message.chat.id}")
    try:
        bot.send_message(message.chat.id, format_stats(), parse_mode='Markdown')
        logger.info(f"✅ Статистика отправлена {message.chat.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при показе статистики: {e}")

@bot.message_handler(func=lambda message: message.text == '➕ Добавить фильм или сериал')
def add_item_start(message):
    logger.info(f"➕ Запрос добавления от {message.chat.id}")
    try:
        bot.send_message(message.chat.id, "🎬 *Что вы хотите добавить?*\n\nВы можете ввести название на русском или английском языке.", 
                         parse_mode='Markdown', reply_markup=type_keyboard())
        user_states[message.chat.id] = {'state': 'choosing_type'}
        logger.info(f"✅ Меню выбора типа отправлено {message.chat.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при начале добавления: {e}")

@bot.message_handler(func=lambda message: message.text in ['Фильм', 'Сериал'])
def choose_type(message):
    chat_id = message.chat.id
    logger.info(f"🎬 Выбор типа от {chat_id}: {message.text}")
    
    try:
        user_states[chat_id] = {
            'state': 'entering_title',
            'type': 'movie' if message.text == 'Фильм' else 'series'
        }
        type_ru = "фильм" if message.text == 'Фильм' else "сериал"
        bot.send_message(chat_id, 
                         f"🎥 *Введите название {type_ru}а:*\n\n"
                         f"• Можно ввести на русском или английском\n"
                         f"• Например: 'Интерстеллар' или 'Inception'\n"
                         f"• Я поищу рейтинги и жанры на Кинопоиске и IMDb",
                         parse_mode='Markdown',
                         reply_markup=types.ReplyKeyboardRemove())
        logger.info(f"✅ Запрос названия отправлен {chat_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при выборе типа: {e}")

@bot.message_handler(func=lambda message: message.text in ['Назад', '↩️ Назад'])
def back_to_main(message):
    logger.info(f"↩️ Возврат в главное меню от {message.chat.id}")
    try:
        bot.send_message(message.chat.id, "Главное меню:", reply_markup=main_keyboard())
        if message.chat.id in user_states:
            del user_states[message.chat.id]
        logger.info(f"✅ Возврат в главное меню выполнен {message.chat.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при возврате в главное меню: {e}")

@bot.message_handler(func=lambda message: message.chat.id in user_states and user_states[message.chat.id]['state'] == 'entering_title')
def enter_title(message):
    chat_id = message.chat.id
    title = message.text.strip()
    item_type = user_states[chat_id]['type']
    
    logger.info(f"🎬 Ввод названия от {chat_id}: '{title}', тип: {item_type}")
    
    if not title:
        bot.send_message(chat_id, "❌ Название не может быть пустым. Попробуйте еще раз:", 
                       reply_markup=type_keyboard())
        return
    
    try:
        # Проверяем, существует ли уже такой фильм
        existing_items = get_items(item_type)
        for item in existing_items:
            if item[1].lower() == title.lower():
                bot.send_message(chat_id, 
                               f"❌ *'{title}'* уже есть в вашем списке!\n\n"
                               f"Попробуйте добавить другой {item_type}.",
                               parse_mode='Markdown',
                               reply_markup=main_keyboard())
                del user_states[chat_id]
                return
        
        bot.send_message(chat_id, f"🔍 *Ищу информацию о '{title}'...*", parse_mode='Markdown')
        result = search_film(title, item_type)
        
        item_id = add_item(
            item_type=item_type,
            title=title,
            original_title=result.get('original_title', title),
            year=result['year'],
            genre=result.get('genre'),
            kp_rating=result.get('kp_rating'),
            imdb_rating=result.get('imdb_rating'),
            kp_url=result.get('kp_url'),
            imdb_url=result.get('imdb_url')
        )
        
        if item_id:
            type_ru = "фильм" if item_type == 'movie' else "сериал"
            
            found_kp = result.get('kp_rating') is not None
            found_imdb = result.get('imdb_rating') is not None
            found_genre = result.get('genre') is not None
            
            message_text = f"✅ *'{title}' добавлен успешно!*\n\n"
            
            if found_genre:
                message_text += f"🎭 *Жанр:* {result['genre']}\n"
            
            if found_kp:
                message_text += f"⭐ *Кинопоиск:* {result['kp_rating']}/10\n"
            if found_imdb:
                message_text += f"⭐ *IMDb:* {result['imdb_rating']}/10\n"
            
            message_text += f"📅 *Год:* {result['year']}\n"
            
            if not found_kp and not found_imdb:
                message_text += "⚠️ Рейтинги не найдены\n"
            if not found_genre:
                message_text += "⚠️ Жанр не найден\n"
            
            bot.send_message(chat_id, message_text, parse_mode='Markdown')
            
            user_states[chat_id] = {'state': 'adding_comment', 'item_id': item_id}
            bot.send_message(
                chat_id,
                "💭 *Хотите добавить комментарий?*\n\n"
                "Напишите ваш комментарий или нажмите кнопку '➡️ Пропустить комментарий'",
                parse_mode='Markdown',
                reply_markup=skip_keyboard()
            )
            logger.info(f"✅ Фильм добавлен с ID {item_id} для {chat_id}")
        else:
            bot.send_message(chat_id, "❌ Ошибка при сохранении.", reply_markup=main_keyboard())
            del user_states[chat_id]
            logger.error(f"❌ Ошибка добавления фильма для {chat_id}")
            
    except Exception as e:
        logger.error(f"❌ Ошибка при вводе названия: {e}")
        bot.send_message(chat_id, "❌ Произошла ошибка при добавлении.", reply_markup=main_keyboard())
        if chat_id in user_states:
            del user_states[chat_id]

@bot.message_handler(func=lambda message: message.chat.id in user_states and user_states[message.chat.id].get('state') == 'adding_comment')
def add_comment(message):
    chat_id = message.chat.id
    item_id = user_states[chat_id]['item_id']
    
    logger.info(f"💭 Добавление комментария от {chat_id} для item {item_id}")
    
    try:
        if message.text == '➡️ Пропустить комментарий':
            bot.send_message(chat_id, "➡️ Комментарий пропущен.", reply_markup=main_keyboard())
            logger.info(f"💭 Комментарий пропущен для {chat_id}")
        else:
            if update_item(item_id, comment=message.text):
                bot.send_message(chat_id, "💭 *Комментарий добавлен!*", parse_mode='Markdown', reply_markup=main_keyboard())
                logger.info(f"💭 Комментарий добавлен для {chat_id}")
            else:
                bot.send_message(chat_id, "❌ Ошибка при добавлении комментария.", reply_markup=main_keyboard())
                logger.error(f"❌ Ошибка добавления комментария для {chat_id}")
        
        item = get_item_by_id(item_id)
        if item:
            bot.send_message(
                chat_id,
                format_item_details(item),
                parse_mode='Markdown',
                disable_web_page_preview=True,
                reply_markup=item_keyboard(item_id)
            )
            logger.info(f"✅ Детали фильма отправлены {chat_id}")
        
        del user_states[chat_id]
        
    except Exception as e:
        logger.error(f"❌ Ошибка при добавлении комментария: {e}")
        bot.send_message(chat_id, "❌ Произошла ошибка.", reply_markup=main_keyboard())
        if chat_id in user_states:
            del user_states[chat_id]

@bot.message_handler(func=lambda message: message.chat.id in user_states and user_states[message.chat.id].get('state') == 'editing_comment')
def edit_comment(message):
    chat_id = message.chat.id
    item_id = user_states[chat_id]['item_id']
    
    logger.info(f"💭 Редактирование комментария от {chat_id} для item {item_id}")
    
    try:
        if update_item(item_id, comment=message.text):
            bot.send_message(chat_id, "💭 *Комментарий обновлен!*", parse_mode='Markdown')
            item = get_item_by_id(item_id)
            bot.send_message(
                chat_id,
                format_item_details(item),
                parse_mode='Markdown',
                disable_web_page_preview=True,
                reply_markup=item_keyboard(item_id)
            )
            logger.info(f"💭 Комментарий обновлен для {chat_id}")
        else:
            bot.send_message(chat_id, "❌ Ошибка при обновлении.")
            logger.error(f"❌ Ошибка обновления комментария для {chat_id}")
        
        del user_states[chat_id]
        
    except Exception as e:
        logger.error(f"❌ Ошибка при редактировании комментария: {e}")
        bot.send_message(chat_id, "❌ Произошла ошибка.", reply_markup=main_keyboard())
        if chat_id in user_states:
            del user_states[chat_id]

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Обрабатывает все сообщения для отладки"""
    logger.info(f"📩 ВСЕ СООБЩЕНИЯ: chat_id={message.chat.id}, text='{message.text}'")
    
    try:
        # Если сообщение не обработано другими обработчиками
        if message.text and not message.text.startswith('/'):
            bot.send_message(
                message.chat.id,
                f"🤖 Получил: '{message.text}'\n\nИспользуйте кнопки меню 👇",
                reply_markup=main_keyboard()
            )
            logger.info(f"✅ Тестовый ответ отправлен для {message.chat.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_all_messages: {e}")

# ========== ОБРАБОТЧИКИ CALLBACK ==========
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    logger.info(f"🔘 Callback от {chat_id}: {call.data}")
    
    try:
        if call.data.startswith('item_') or call.data.startswith('series_') or call.data.startswith('movie_'):
            item_id = int(call.data.split('_')[1])
            item = get_item_by_id(item_id)
            if item:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=format_item_details(item),
                    parse_mode='Markdown',
                    disable_web_page_preview=True,
                    reply_markup=item_keyboard(item_id)
                )
                logger.info(f"✅ Детали фильма {item_id} отправлены {chat_id}")
            else:
                bot.answer_callback_query(call.id, "❌ Запись не найдена")
                logger.error(f"❌ Запись не найдена: {item_id}")
        
        elif call.data.startswith('watch_'):
            item_id = int(call.data.split('_')[1])
            if update_item(item_id, watched=1):
                item = get_item_by_id(item_id)
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=format_item_details(item),
                    parse_mode='Markdown',
                    disable_web_page_preview=True,
                    reply_markup=item_keyboard(item_id)
                )
                bot.answer_callback_query(call.id, "✅ Отмечено как просмотренное")
                logger.info(f"✅ Фильм {item_id} отмечен как просмотренный для {chat_id}")
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка")
                logger.error(f"❌ Ошибка отметки просмотренного: {item_id}")
        
        elif call.data.startswith('unwatch_'):
            item_id = int(call.data.split('_')[1])
            if update_item(item_id, watched=0):
                item = get_item_by_id(item_id)
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=format_item_details(item),
                    parse_mode='Markdown',
                    disable_web_page_preview=True,
                    reply_markup=item_keyboard(item_id)
                )
                bot.answer_callback_query(call.id, "👁 Отмечено как 'хочу посмотреть'")
                logger.info(f"✅ Фильм {item_id} отмечен как 'хочу посмотреть' для {chat_id}")
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка")
                logger.error(f"❌ Ошибка отметки 'хочу посмотреть': {item_id}")
        
        elif call.data.startswith('comment_'):
            item_id = int(call.data.split('_')[1])
            user_states[chat_id] = {'state': 'editing_comment', 'item_id': item_id}
            
            item = get_item_by_id(item_id)
            current_comment = item[11] if item and item[11] else "нет комментария"
            
            bot.delete_message(chat_id, message_id)
            bot.send_message(
                chat_id,
                f"💭 *Редактирование комментария*\n\nТекущий комментарий: {current_comment}\n\nВведите новый комментарий:",
                parse_mode='Markdown',
                reply_markup=types.ForceReply(selective=True)
            )
            logger.info(f"💭 Запрос редактирования комментария для {item_id} от {chat_id}")
        
        elif call.data.startswith('delete_'):
            item_id = int(call.data.split('_')[1])
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton('✅ Да, удалить', callback_data=f'confirm_delete_{item_id}'),
                types.InlineKeyboardButton('❌ Нет, отмена', callback_data=f'show_{item_id}')
            )
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="🗑 *Вы уверены, что хотите удалить этот элемент?*\n\nЭто действие нельзя отменить.",
                parse_mode='Markdown',
                reply_markup=markup
            )
            logger.info(f"🗑 Запрос подтверждения удаления для {item_id} от {chat_id}")
        
        elif call.data.startswith('confirm_delete_'):
            item_id = int(call.data.split('_')[2])
            item = get_item_by_id(item_id)
            if item:
                title = item[2]
                if delete_item(item_id):
                    bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"🗑 *'{title}' удален из вашего списка.*",
                        parse_mode='Markdown'
                    )
                    bot.answer_callback_query(call.id, "✅ Удалено")
                    logger.info(f"🗑 Фильм {item_id} удален для {chat_id}")
                else:
                    bot.answer_callback_query(call.id, "❌ Ошибка при удалении")
                    logger.error(f"❌ Ошибка удаления фильма {item_id}")
            else:
                bot.answer_callback_query(call.id, "❌ Запись не найдена")
                logger.error(f"❌ Запись не найдена для удаления: {item_id}")
        
        elif call.data == 'back_to_list' or call.data == 'back_to_main':
            bot.delete_message(chat_id, message_id)
            bot.send_message(chat_id, "Главное меню:", reply_markup=main_keyboard())
            if chat_id in user_states:
                del user_states[chat_id]
            logger.info(f"↩️ Возврат в главное меню по callback от {chat_id}")
        
        elif call.data == 'new_search':
            bot.delete_message(chat_id, message_id)
            start_search(call.message)
            logger.info(f"🔍 Новый поиск по callback от {chat_id}")
        
        elif call.data.startswith('show_'):
            item_id = int(call.data.split('_')[1])
            item = get_item_by_id(item_id)
            if item:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=format_item_details(item),
                    parse_mode='Markdown',
                    disable_web_page_preview=True,
                    reply_markup=item_keyboard(item_id)
                )
                logger.info(f"✅ Показан фильм {item_id} для {chat_id}")
                
    except Exception as e:
        logger.error(f"❌ Ошибка обработки callback: {e}")
        import traceback
        traceback.print_exc()
        try:
            bot.answer_callback_query(call.id, "❌ Произошла ошибка")
        except:
            pass

# ========== ЗАПУСК ПРИЛОЖЕНИЯ ==========
if __name__ == '__main__':
    print("=" * 60)
    print(f"🚀 Запуск КиноБота в {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🔑 Токен: {'✅ установлен' if TOKEN else '❌ НЕТ'}")
    print(f"🌐 RENDER_EXTERNAL_URL: {RENDER_EXTERNAL_URL or 'не установлен'}")
    print(f"🌐 WEBHOOK_URL: {WEBHOOK_URL or 'не установлен'}")
    print("=" * 60)
    
    # Инициализируем БД
    if init_db():
        print("🗄️  База данных: ✅ инициализирована")
    else:
        print("🗄️  База данных: ⚠️ проблемы с инициализацией")
    
    # Настраиваем вебхук если есть URL
    if WEBHOOK_URL:
        print(f"🔧 Настраиваю вебхук на {WEBHOOK_URL}")
        try:
            bot.remove_webhook()
            time.sleep(1)
            success = bot.set_webhook(
                url=WEBHOOK_URL,
                max_connections=100,
                timeout=60
            )
            if success:
                print("✅ Вебхук установлен успешно")
            else:
                print("❌ Не удалось установить вебхук")
                
            # Проверяем вебхук
            webhook_info = bot.get_webhook_info()
            print(f"📊 Информация о вебхуке:")
            print(f"   URL: {webhook_info.url}")
            print(f"   Ошибок: {webhook_info.last_error_message or 'нет'}")
            
        except Exception as e:
            print(f"❌ Ошибка установки вебхука: {e}")
    else:
        print("⚠️ WEBHOOK_URL не установлен, вебхук не настроен")
        print("ℹ️ На Render это должно устанавливаться автоматически")
    
    # Запускаем сервер
    port = int(os.getenv('PORT', 10000))
    print(f"🌐 Запуск Flask на порту {port}")
    print(f"🌐 Главная страница: http://0.0.0.0:{port}/")
    if WEBHOOK_URL:
        print(f"🌐 Вебхук: {WEBHOOK_URL}")
    print("=" * 60)
    
    # Запускаем Flask
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)

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
import hashlib

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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

# ========== ХЕШИРОВАНИЕ ПИН-КОДОВ ==========
def hash_pin(pin):
    """Хеширует пин-код для безопасного хранения"""
    return hashlib.sha256(pin.encode()).hexdigest()

def verify_pin(pin, hashed_pin):
    """Проверяет пин-код"""
    return hash_pin(pin) == hashed_pin

# ========== ВЕБХУК РУТЫ ==========
@app.route('/')
def home():
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
        <p>Управляйте своими списками фильмов и сериалов через Telegram</p>
        <p><a href="/health">Проверить статус</a></p>
    </body>
    </html>
    """

@app.route('/health')
def health_check():
    return "OK", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') != 'application/json':
        return 'Invalid content type', 403
    
    try:
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}")
        return 'Error', 500

# ========== БАЗА ДАННЫХ ==========
def get_connection():
    """Создает подключение к БД"""
    global sqlite_conn
    
    if not DATABASE_URL or DATABASE_URL == '':
        logger.warning("⚠️ DATABASE_URL не установлен, используем SQLite")
        
        if sqlite_conn is None:
            sqlite_conn = sqlite3.connect('movies.db', check_same_thread=False)
            sqlite_conn.row_factory = sqlite3.Row
        
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
        logger.warning("⚠️ Используем SQLite")
        
        if sqlite_conn is None:
            sqlite_conn = sqlite3.connect('movies.db', check_same_thread=False)
            sqlite_conn.row_factory = sqlite3.Row
        
        return sqlite_conn

def init_db():
    """Создает таблицы"""
    logger.info("🔄 Инициализация базы данных...")
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        # Таблица списков
        cur.execute('''
            CREATE TABLE IF NOT EXISTS lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(255) NOT NULL,
                pin_hash VARCHAR(64) NOT NULL,
                owner_id INTEGER NOT NULL,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(owner_id, name)
            )
        ''')
        
        # Таблица элементов
        cur.execute('''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER NOT NULL,
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
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (list_id) REFERENCES lists (id) ON DELETE CASCADE
            )
        ''')
        
        conn.commit()
        logger.info("✅ База данных инициализирована")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        return False

# ========== ОПЕРАЦИИ СО СПИСКАМИ ==========
def create_list(owner_id, name, pin):
    """Создает новый список"""
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        # Проверяем, существует ли уже список с таким именем у пользователя
        cur.execute('SELECT id FROM lists WHERE owner_id = ? AND name = ?', (owner_id, name))
        if cur.fetchone():
            return None, "У вас уже есть список с таким названием"
        
        pin_hash = hash_pin(pin)
        cur.execute(
            'INSERT INTO lists (name, pin_hash, owner_id) VALUES (?, ?, ?)',
            (name, pin_hash, owner_id)
        )
        conn.commit()
        list_id = cur.lastrowid
        
        logger.info(f"📝 Создан список '{name}' (ID: {list_id}) для пользователя {owner_id}")
        return list_id, None
    except Exception as e:
        logger.error(f"❌ Ошибка создания списка: {e}")
        return None, f"Ошибка создания списка: {e}"

def get_user_lists(owner_id):
    """Получает все списки пользователя"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute('''
        SELECT id, name, created_date 
        FROM lists 
        WHERE owner_id = ? 
        ORDER BY created_date DESC
    ''', (owner_id,))
    
    return cur.fetchall()

def get_list_by_id(list_id):
    """Получает список по ID"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute('SELECT id, name, pin_hash, owner_id FROM lists WHERE id = ?', (list_id,))
    return cur.fetchone()

def verify_list_access(list_id, pin, user_id):
    """Проверяет доступ к списку"""
    list_data = get_list_by_id(list_id)
    if not list_data:
        return False, None
    
    # Владелец всегда имеет доступ
    if list_data['owner_id'] == user_id:
        return True, list_data
    
    # Проверяем пин-код для других пользователей
    if verify_pin(pin, list_data['pin_hash']):
        return True, list_data
    
    return False, None

def update_list_pin(list_id, new_pin):
    """Обновляет пин-код списка"""
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        pin_hash = hash_pin(new_pin)
        cur.execute('UPDATE lists SET pin_hash = ? WHERE id = ?', (pin_hash, list_id))
        conn.commit()
        
        logger.info(f"🔐 Обновлен пин-код списка {list_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка обновления пин-кода: {e}")
        return False

# ========== ОПЕРАЦИИ С ЭЛЕМЕНТАМИ ==========
def add_item(list_id, item_type, title, original_title, year, genre=None, kp_rating=None, imdb_rating=None, kp_url=None, imdb_url=None):
    """Добавляет элемент в список"""
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('''
            INSERT INTO items (list_id, type, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (list_id, item_type, title, original_title, year, genre, kp_rating, imdb_rating, kp_url, imdb_url))
        
        conn.commit()
        item_id = cur.lastrowid
        
        logger.info(f"✅ Добавлен элемент {item_id} в список {list_id}")
        return item_id
    except Exception as e:
        logger.error(f"❌ Ошибка добавления элемента: {e}")
        return None

def get_list_items(list_id, item_type=None):
    """Получает элементы списка"""
    conn = get_connection()
    cur = conn.cursor()
    
    if item_type:
        cur.execute('''
            SELECT * FROM items 
            WHERE list_id = ? AND type = ? 
            ORDER BY title
        ''', (list_id, item_type))
    else:
        cur.execute('''
            SELECT * FROM items 
            WHERE list_id = ? 
            ORDER BY type, title
        ''', (list_id,))
    
    return cur.fetchall()

def get_item_by_id(item_id):
    """Получает элемент по ID"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute('SELECT * FROM items WHERE id = ?', (item_id,))
    return cur.fetchone()

def delete_item_from_list(item_id):
    """Удаляет элемент из списка"""
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('DELETE FROM items WHERE id = ?', (item_id,))
        conn.commit()
        
        logger.info(f"🗑 Удален элемент {item_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка удаления элемента: {e}")
        return False

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_russian_text(text):
    return bool(re.search('[а-яА-Я]', text))

def translate_russian_to_english(text):
    try:
        translator = GoogleTranslator(source='ru', target='en')
        return translator.translate(text)
    except:
        return text

def search_film(title, item_type=None):
    """Упрощенная функция поиска"""
    return {
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

# ========== КЛАВИАТУРЫ ==========
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton('📋 Мои списки'),
        types.KeyboardButton('➕ Новый список'),
        types.KeyboardButton('🔑 Доступ к списку'),
        types.KeyboardButton('⚙️ Управление')
    )
    return markup

def lists_keyboard(lists_data):
    """Клавиатура со списками"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for list_data in lists_data:
        markup.add(types.InlineKeyboardButton(
            f"📋 {list_data['name']}",
            callback_data=f"open_list_{list_data['id']}"
        ))
    
    markup.add(types.InlineKeyboardButton("➕ Новый список", callback_data="create_list"))
    return markup

def list_menu_keyboard(list_id):
    """Меню для работы со списком"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('🎬 Сериалы', callback_data=f'list_series_{list_id}'),
        types.InlineKeyboardButton('🎥 Фильмы', callback_data=f'list_movies_{list_id}'),
        types.InlineKeyboardButton('🔍 Поиск', callback_data=f'list_search_{list_id}'),
        types.InlineKeyboardButton('➕ Добавить', callback_data=f'list_add_{list_id}'),
        types.InlineKeyboardButton('🔐 Сменить пин', callback_data=f'change_pin_{list_id}'),
        types.InlineKeyboardButton('↩️ Назад', callback_data='back_to_lists')
    )
    return markup

def item_keyboard(item_id, list_id):
    """Клавиатура для элемента"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('✅ Просмотрено', callback_data=f'watch_{item_id}_{list_id}'),
        types.InlineKeyboardButton('👁 Не просмотрено', callback_data=f'unwatch_{item_id}_{list_id}'),
        types.InlineKeyboardButton('🗑 Удалить', callback_data=f'delete_item_{item_id}_{list_id}'),
        types.InlineKeyboardButton('↩️ К списку', callback_data=f'back_to_list_{list_id}')
    )
    return markup

def type_keyboard(list_id):
    """Выбор типа для добавления"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton('Фильм'),
        types.KeyboardButton('Сериал'),
        types.KeyboardButton('↩️ Отмена')
    )
    return markup

# ========== ФОРМАТИРОВАНИЕ ТЕКСТА ==========
def format_list_info(list_data):
    """Форматирует информацию о списке"""
    return f"""
📋 *{list_data['name']}*

📅 Создан: {list_data['created_date']}

Используйте меню ниже для работы со списком:
"""

def format_item_details(item):
    """Форматирует информацию об элементе"""
    item_type = "сериал" if item['type'] == 'series' else "фильм"
    watched = "✅ Просмотрено" if item['watched'] else "👁 Хочу посмотреть"
    
    text = f"🎬 *{item_type.upper()} #{item['id']}*\n\n"
    text += f"📌 *{item['title']}*\n"
    
    if item['original_title'] and item['original_title'] != item['title']:
        text += f"🌐 *Оригинальное название:* {item['original_title']}\n"
    
    text += f"📅 *Год:* {item['year']}\n"
    
    if item['genre']:
        text += f"🎭 *Жанр:* {item['genre']}\n"
    
    text += f"📊 *Статус:* {watched}\n"
    
    if item['comment']:
        text += f"\n💭 *Комментарий:*\n{item['comment']}\n"
    
    return text

# ========== СОСТОЯНИЯ ПОЛЬЗОВАТЕЛЕЙ ==========
user_states = {}

# ========== ОСНОВНЫЕ КОМАНДЫ ==========
@bot.message_handler(commands=['start', 'help'])
def start(message):
    bot.send_message(
        message.chat.id,
        "🎬 *Добро пожаловать в КиноБот!*\n\n"
        "Я помогу вам создавать и управлять списками фильмов и сериалов.\n\n"
        "📋 *Возможности:*\n"
        "• Создавайте собственные списки с пин-кодами\n"
        "• Давайте доступ к спискам другим пользователям\n"
        "• Добавляйте фильмы и сериалы\n"
        "• Отмечайте просмотренное\n"
        "• Ищите по своим спискам\n\n"
        "Выберите действие в меню:",
        parse_mode='Markdown',
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda message: message.text == '📋 Мои списки')
def show_my_lists(message):
    lists_data = get_user_lists(message.chat.id)
    
    if not lists_data:
        bot.send_message(
            message.chat.id,
            "📭 У вас пока нет списков.\n\n"
            "Создайте первый список через меню '➕ Новый список'",
            reply_markup=main_keyboard()
        )
        return
    
    markup = lists_keyboard(lists_data)
    bot.send_message(
        message.chat.id,
        "📋 *Ваши списки:*\n\nВыберите список для работы:",
        parse_mode='Markdown',
        reply_markup=markup
    )

@bot.message_handler(func=lambda message: message.text == '➕ Новый список')
def new_list_start(message):
    user_states[message.chat.id] = {'state': 'awaiting_list_name'}
    bot.send_message(
        message.chat.id,
        "📝 *Создание нового списка*\n\n"
        "Введите название для вашего списка:",
        parse_mode='Markdown',
        reply_markup=types.ReplyKeyboardRemove()
    )

@bot.message_handler(func=lambda message: message.text == '🔑 Доступ к списку')
def access_list_start(message):
    user_states[message.chat.id] = {'state': 'awaiting_list_id'}
    bot.send_message(
        message.chat.id,
        "🔑 *Доступ к списку*\n\n"
        "Введите ID списка для доступа:",
        parse_mode='Markdown',
        reply_markup=types.ReplyKeyboardRemove()
    )

# ========== ОБРАБОТЧИКИ СОСТОЯНИЙ ==========
@bot.message_handler(func=lambda message: message.chat.id in user_states)
def handle_user_state(message):
    chat_id = message.chat.id
    state = user_states[chat_id]['state']
    
    if state == 'awaiting_list_name':
        handle_list_name(chat_id, message.text)
    
    elif state == 'awaiting_list_pin':
        handle_list_pin(chat_id, message.text)
    
    elif state == 'awaiting_list_id':
        handle_list_id(chat_id, message.text)
    
    elif state == 'awaiting_list_pin_access':
        handle_list_pin_access(chat_id, message.text)
    
    elif state == 'awaiting_new_pin':
        handle_new_pin(chat_id, message.text)
    
    elif state == 'awaiting_item_type':
        handle_item_type(chat_id, message.text)
    
    elif state == 'awaiting_item_title':
        handle_item_title(chat_id, message.text)

def handle_list_name(chat_id, list_name):
    if not list_name.strip():
        bot.send_message(chat_id, "❌ Название не может быть пустым.")
        return
    
    user_states[chat_id] = {
        'state': 'awaiting_list_pin',
        'list_name': list_name.strip()
    }
    
    bot.send_message(
        chat_id,
        "🔐 *Установите пин-код для списка*\n\n"
        "Введите пин-код (4-6 цифр):\n"
        "Этот пин нужен для доступа к списку.",
        parse_mode='Markdown'
    )

def handle_list_pin(chat_id, pin):
    if not pin.isdigit() or len(pin) < 4 or len(pin) > 6:
        bot.send_message(
            chat_id,
            "❌ Пин-код должен содержать 4-6 цифр.\n"
            "Попробуйте еще раз:"
        )
        return
    
    list_name = user_states[chat_id]['list_name']
    list_id, error = create_list(chat_id, list_name, pin)
    
    if error:
        bot.send_message(chat_id, f"❌ {error}", reply_markup=main_keyboard())
    else:
        bot.send_message(
            chat_id,
            f"✅ *Список создан успешно!*\n\n"
            f"📋 Название: {list_name}\n"
            f"🔑 Пин-код: {pin}\n"
            f"🆔 ID списка: {list_id}\n\n"
            f"Сохраните ID и пин-код для доступа к списку.",
            parse_mode='Markdown',
            reply_markup=main_keyboard()
        )
    
    del user_states[chat_id]

def handle_list_id(chat_id, list_id):
    if not list_id.isdigit():
        bot.send_message(chat_id, "❌ ID списка должен быть числом.")
        return
    
    list_data = get_list_by_id(int(list_id))
    if not list_data:
        bot.send_message(chat_id, "❌ Список не найден.")
        return
    
    user_states[chat_id] = {
        'state': 'awaiting_list_pin_access',
        'list_id': int(list_id)
    }
    
    bot.send_message(
        chat_id,
        f"🔐 *Доступ к списку '{list_data['name']}'*\n\n"
        f"Введите пин-код для доступа:",
        parse_mode='Markdown'
    )

def handle_list_pin_access(chat_id, pin):
    list_id = user_states[chat_id]['list_id']
    access, list_data = verify_list_access(list_id, pin, chat_id)
    
    if not access:
        bot.send_message(chat_id, "❌ Неверный пин-код.", reply_markup=main_keyboard())
        del user_states[chat_id]
        return
    
    # Открываем список
    open_list_for_user(chat_id, list_data)
    del user_states[chat_id]

def handle_new_pin(chat_id, pin):
    if not pin.isdigit() or len(pin) < 4 or len(pin) > 6:
        bot.send_message(
            chat_id,
            "❌ Пин-код должен содержать 4-6 цифр.\n"
            "Попробуйте еще раз:"
        )
        return
    
    list_id = user_states[chat_id]['list_id']
    
    if update_list_pin(list_id, pin):
        bot.send_message(
            chat_id,
            f"✅ Пин-код успешно изменен на: {pin}",
            reply_markup=main_keyboard()
        )
    else:
        bot.send_message(
            chat_id,
            "❌ Ошибка при изменении пин-кода.",
            reply_markup=main_keyboard()
        )
    
    del user_states[chat_id]

def handle_item_type(chat_id, item_type):
    if item_type not in ['Фильм', 'Сериал']:
        bot.send_message(chat_id, "❌ Выберите тип из предложенных.")
        return
    
    user_states[chat_id] = {
        'state': 'awaiting_item_title',
        'list_id': user_states[chat_id]['list_id'],
        'item_type': 'movie' if item_type == 'Фильм' else 'series'
    }
    
    bot.send_message(
        chat_id,
        f"🎬 *Добавление {item_type.lower()}а*\n\n"
        f"Введите название:",
        parse_mode='Markdown',
        reply_markup=types.ReplyKeyboardRemove()
    )

def handle_item_title(chat_id, title):
    if not title.strip():
        bot.send_message(chat_id, "❌ Название не может быть пустым.")
        return
    
    list_id = user_states[chat_id]['list_id']
    item_type = user_states[chat_id]['item_type']
    
    # Ищем информацию о фильме
    result = search_film(title.strip(), item_type)
    
    # Добавляем элемент
    item_id = add_item(
        list_id=list_id,
        item_type=item_type,
        title=title.strip(),
        original_title=result['original_title'],
        year=result['year'],
        genre=result['genre'],
        kp_rating=result['kp_rating'],
        imdb_rating=result['imdb_rating'],
        kp_url=result['kp_url'],
        imdb_url=result['imdb_url']
    )
    
    if item_id:
        item = get_item_by_id(item_id)
        bot.send_message(
            chat_id,
            f"✅ *{title.strip()} добавлен успешно!*\n\n"
            f"{format_item_details(item)}",
            parse_mode='Markdown',
            reply_markup=item_keyboard(item_id, list_id)
        )
    else:
        bot.send_message(
            chat_id,
            "❌ Ошибка при добавлении.",
            reply_markup=list_menu_keyboard(list_id)
        )
    
    del user_states[chat_id]

# ========== ОБРАБОТЧИКИ CALLBACK ==========
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    data = call.data
    
    try:
        if data.startswith('open_list_'):
            list_id = int(data.split('_')[2])
            open_user_list(chat_id, list_id)
        
        elif data == 'create_list':
            new_list_start(call.message)
        
        elif data.startswith('list_series_'):
            list_id = int(data.split('_')[2])
            show_list_items(chat_id, list_id, 'series')
        
        elif data.startswith('list_movies_'):
            list_id = int(data.split('_')[2])
            show_list_items(chat_id, list_id, 'movie')
        
        elif data.startswith('list_add_'):
            list_id = int(data.split('_')[2])
            start_add_item(chat_id, list_id)
        
        elif data.startswith('change_pin_'):
            list_id = int(data.split('_')[2])
            start_change_pin(chat_id, list_id)
        
        elif data.startswith('watch_'):
            item_id = int(data.split('_')[1])
            list_id = int(data.split('_')[2])
            toggle_watch_status(chat_id, item_id, list_id, True)
        
        elif data.startswith('unwatch_'):
            item_id = int(data.split('_')[1])
            list_id = int(data.split('_')[2])
            toggle_watch_status(chat_id, item_id, list_id, False)
        
        elif data.startswith('delete_item_'):
            item_id = int(data.split('_')[2])
            list_id = int(data.split('_')[3])
            delete_item(chat_id, item_id, list_id)
        
        elif data.startswith('back_to_list_'):
            list_id = int(data.split('_')[3])
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=call.message.message_id,
                text="📋 *Меню списка:*",
                parse_mode='Markdown',
                reply_markup=list_menu_keyboard(list_id)
            )
        
        elif data == 'back_to_lists':
            show_my_lists(call.message)
        
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки callback: {e}")
        bot.answer_callback_query(call.id, "❌ Произошла ошибка")

def open_user_list(chat_id, list_id):
    """Открывает список пользователя (без проверки пин-кода)"""
    list_data = get_list_by_id(list_id)
    if list_data and list_data['owner_id'] == chat_id:
        open_list_for_user(chat_id, list_data)
    else:
        bot.send_message(chat_id, "❌ У вас нет доступа к этому списку.")

def open_list_for_user(chat_id, list_data):
    """Открывает список для пользователя"""
    bot.send_message(
        chat_id,
        f"📋 *{list_data['name']}*\n\n"
        f"Выберите действие:",
        parse_mode='Markdown',
        reply_markup=list_menu_keyboard(list_data['id'])
    )

def show_list_items(chat_id, list_id, item_type):
    """Показывает элементы списка"""
    items = get_list_items(list_id, item_type)
    list_data = get_list_by_id(list_id)
    
    if not items:
        type_text = "сериалы" if item_type == 'series' else "фильмы"
        bot.send_message(
            chat_id,
            f"📭 В списке '{list_data['name']}' пока нет {type_text}.",
            reply_markup=list_menu_keyboard(list_id)
        )
        return
    
    # Создаем клавиатуру с элементами
    markup = types.InlineKeyboardMarkup(row_width=1)
    for item in items:
        watched_icon = "✅" if item['watched'] else "👁"
        item_text = f"{watched_icon} {item['title']}"
        if item['year'] and item['year'] != 'Неизвестно':
            item_text += f" ({item['year']})"
        
        if len(item_text) > 40:
            item_text = item_text[:37] + "..."
        
        markup.add(types.InlineKeyboardButton(
            item_text,
            callback_data=f"show_item_{item['id']}_{list_id}"
        ))
    
    markup.add(types.InlineKeyboardButton(
        "↩️ Назад",
        callback_data=f"back_to_list_{list_id}"
    ))
    
    type_text = "сериалы" if item_type == 'series' else "фильмы"
    bot.send_message(
        chat_id,
        f"🎬 *{type_text.capitalize()} в списке '{list_data['name']}':*\n\n"
        f"Всего: {len(items)}\n"
        f"Просмотрено: {sum(1 for i in items if i['watched'])}",
        parse_mode='Markdown',
        reply_markup=markup
    )

def start_add_item(chat_id, list_id):
    """Начинает процесс добавления элемента"""
    user_states[chat_id] = {
        'state': 'awaiting_item_type',
        'list_id': list_id
    }
    
    bot.send_message(
        chat_id,
        "🎬 *Что вы хотите добавить?*\n\nВыберите тип:",
        parse_mode='Markdown',
        reply_markup=type_keyboard(list_id)
    )

def start_change_pin(chat_id, list_id):
    """Начинает процесс изменения пин-кода"""
    user_states[chat_id] = {
        'state': 'awaiting_new_pin',
        'list_id': list_id
    }
    
    bot.send_message(
        chat_id,
        "🔐 *Изменение пин-кода*\n\n"
        "Введите новый пин-код (4-6 цифр):",
        parse_mode='Markdown',
        reply_markup=types.ReplyKeyboardRemove()
    )

def toggle_watch_status(chat_id, item_id, list_id, watched):
    """Изменяет статус просмотра"""
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        cur.execute(
            'UPDATE items SET watched = ? WHERE id = ?',
            (1 if watched else 0, item_id)
        )
        conn.commit()
        
        item = get_item_by_id(item_id)
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=format_item_details(item),
            parse_mode='Markdown',
            reply_markup=item_keyboard(item_id, list_id)
        )
        
        status = "просмотрено" if watched else "не просмотрено"
        bot.answer_callback_query(call.id, f"✅ Отмечено как {status}")
    except Exception as e:
        logger.error(f"❌ Ошибка изменения статуса: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка")

def delete_item(chat_id, item_id, list_id):
    """Удаляет элемент"""
    item = get_item_by_id(item_id)
    if not item:
        bot.answer_callback_query(call.id, "❌ Элемент не найден")
        return
    
    # Запрашиваем подтверждение
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('✅ Да, удалить', callback_data=f'confirm_delete_{item_id}_{list_id}'),
        types.InlineKeyboardButton('❌ Нет, отмена', callback_data=f'show_item_{item_id}_{list_id}')
    )
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"🗑 *Удалить '{item['title']}'?*\n\nЭто действие нельзя отменить.",
        parse_mode='Markdown',
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_delete_'))
def confirm_delete(call):
    chat_id = call.message.chat.id
    item_id = int(call.data.split('_')[2])
    list_id = int(call.data.split('_')[3])
    
    item = get_item_by_id(item_id)
    if delete_item_from_list(item_id):
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🗑 *'{item['title']}' удален из списка.*",
            parse_mode='Markdown'
        )
        bot.answer_callback_query(call.id, "✅ Удалено")
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка удаления")

@bot.callback_query_handler(func=lambda call: call.data.startswith('show_item_'))
def show_item_details(call):
    chat_id = call.message.chat.id
    item_id = int(call.data.split('_')[2])
    list_id = int(call.data.split('_')[3])
    
    item = get_item_by_id(item_id)
    if item:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=format_item_details(item),
            parse_mode='Markdown',
            reply_markup=item_keyboard(item_id, list_id)
        )
    else:
        bot.answer_callback_query(call.id, "❌ Элемент не найден")

# ========== ЗАПУСК ПРИЛОЖЕНИЯ ==========
if __name__ == '__main__':
    print("=" * 60)
    print(f"🚀 Запуск КиноБота в {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🔑 Токен: {'✅ установлен' if TOKEN else '❌ НЕТ'}")
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
        except Exception as e:
            print(f"❌ Ошибка установки вебхука: {e}")
    else:
        print("⚠️ WEBHOOK_URL не установлен, используем polling")
        # Удаляем вебхук для локальной разработки
        bot.remove_webhook()
    
    # Запускаем сервер
    port = int(os.getenv('PORT', 10000))
    print(f"🌐 Запуск Flask на порту {port}")
    print(f"🌐 Главная страница: http://0.0.0.0:{port}/")
    print("=" * 60)
    
    # Запускаем Flask
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)

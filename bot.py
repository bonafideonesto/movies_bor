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
class Database:
    """Класс для управления подключением к базе данных"""
    
    def __init__(self):
        self.conn = None
        self.use_postgres = False
        self.init_connection()
    
    def init_connection(self):
        """Инициализирует подключение к базе данных"""
        try:
            # Пытаемся подключиться к PostgreSQL если указан DATABASE_URL
            if DATABASE_URL and DATABASE_URL.startswith('postgres'):
                import psycopg2
                from urllib.parse import urlparse
                
                result = urlparse(DATABASE_URL)
                logger.info(f"🔗 Попытка подключения к PostgreSQL: {result.hostname}")
                
                # Подключение с таймаутом
                conn_params = {
                    'host': result.hostname,
                    'port': result.port or 5432,
                    'database': result.path[1:],
                    'user': result.username,
                    'password': result.password,
                    'connect_timeout': 5,
                    'sslmode': 'require' if 'supabase' in result.hostname else 'prefer'
                }
                
                # Пробуем IPv4 если IPv6 не работает
                try:
                    self.conn = psycopg2.connect(**conn_params)
                    self.use_postgres = True
                    logger.info("✅ Успешное подключение к PostgreSQL")
                except:
                    # Пробуем без SSL для локальной разработки
                    if 'supabase' not in result.hostname:
                        conn_params['sslmode'] = 'disable'
                        self.conn = psycopg2.connect(**conn_params)
                        self.use_postgres = True
                        logger.info("✅ Успешное подключение к PostgreSQL (без SSL)")
                    else:
                        raise
            
            # Если PostgreSQL не сработал, используем SQLite
            if not self.conn:
                db_path = os.getenv('SQLITE_PATH', 'movies.db')
                logger.info(f"📁 Используем SQLite: {db_path}")
                self.conn = sqlite3.connect(db_path, check_same_thread=False)
                self.conn.row_factory = sqlite3.Row
                self.use_postgres = False
                
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к PostgreSQL: {e}")
            logger.warning("⚠️ Используем SQLite")
            
            try:
                db_path = os.getenv('SQLITE_PATH', 'movies.db')
                self.conn = sqlite3.connect(db_path, check_same_thread=False)
                self.conn.row_factory = sqlite3.Row
                self.use_postgres = False
                logger.info(f"✅ Создано SQLite подключение к {db_path}")
            except Exception as e2:
                logger.error(f"❌ Ошибка создания SQLite подключения: {e2}")
                raise
    
    def get_connection(self):
        """Возвращает соединение с БД"""
        if self.conn is None:
            self.init_connection()
        return self.conn
    
    def close(self):
        """Закрывает соединение"""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def execute(self, query, params=None):
        """Выполняет запрос"""
        cursor = self.conn.cursor()
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            self.conn.commit()
            return cursor
        except Exception as e:
            self.conn.rollback()
            raise e
    
    def fetchone(self, query, params=None):
        """Выполняет запрос и возвращает одну строку"""
        cursor = self.execute(query, params)
        return cursor.fetchone()
    
    def fetchall(self, query, params=None):
        """Выполняет запрос и возвращает все строки"""
        cursor = self.execute(query, params)
        return cursor.fetchall()

# Создаем глобальный экземпляр базы данных
db = Database()

def init_db():
    """Создает таблицы"""
    logger.info("🔄 Инициализация базы данных...")
    
    try:
        # Таблица списков
        db.execute('''
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
        db.execute('''
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
        
        logger.info("✅ База данных инициализирована")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        return False

# ========== ОПЕРАЦИИ СО СПИСКАМИ ==========
def create_list(owner_id, name, pin):
    """Создает новый список"""
    try:
        # Проверяем, существует ли уже список с таким именем у пользователя
        existing = db.fetchone(
            'SELECT id FROM lists WHERE owner_id = %s AND name = %s',
            (owner_id, name)
        )
        if existing:
            return None, "У вас уже есть список с таким названием"
        
        pin_hash = hash_pin(pin)
        cursor = db.execute(
            'INSERT INTO lists (name, pin_hash, owner_id) VALUES (%s, %s, %s)',
            (name, pin_hash, owner_id)
        )
        
        list_id = cursor.lastrowid if hasattr(cursor, 'lastrowid') else None
        if not list_id and db.use_postgres:
            # Для PostgreSQL получаем ID другим способом
            result = db.fetchone('SELECT LASTVAL()')
            list_id = result[0] if result else None
        
        logger.info(f"📝 Создан список '{name}' (ID: {list_id}) для пользователя {owner_id}")
        return list_id, None
    except Exception as e:
        logger.error(f"❌ Ошибка создания списка: {e}")
        return None, f"Ошибка создания списка: {e}"

def get_user_lists(owner_id):
    """Получает все списки пользователя"""
    try:
        return db.fetchall('''
            SELECT id, name, created_date 
            FROM lists 
            WHERE owner_id = %s 
            ORDER BY created_date DESC
        ''', (owner_id,))
    except Exception as e:
        logger.error(f"❌ Ошибка получения списков: {e}")
        return []

def get_list_by_id(list_id):
    """Получает список по ID"""
    try:
        return db.fetchone(
            'SELECT id, name, pin_hash, owner_id FROM lists WHERE id = %s',
            (list_id,)
        )
    except Exception as e:
        logger.error(f"❌ Ошибка получения списка: {e}")
        return None

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
    try:
        pin_hash = hash_pin(new_pin)
        cursor = db.execute(
            'UPDATE lists SET pin_hash = %s WHERE id = %s',
            (pin_hash, list_id)
        )
        
        logger.info(f"🔐 Обновлен пин-код списка {list_id}")
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"❌ Ошибка обновления пин-кода: {e}")
        return False

# ========== ОПЕРАЦИИ С ЭЛЕМЕНТАМИ ==========
def add_item(list_id, item_type, title, original_title, year, **kwargs):
    """Добавляет элемент в список"""
    try:
        cursor = db.execute('''
            INSERT INTO items (list_id, type, title, original_title, year, genre, 
                              kp_rating, imdb_rating, kp_url, imdb_url) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            list_id, item_type, title, original_title, year,
            kwargs.get('genre'), kwargs.get('kp_rating'), 
            kwargs.get('imdb_rating'), kwargs.get('kp_url'), 
            kwargs.get('imdb_url')
        ))
        
        item_id = cursor.lastrowid if hasattr(cursor, 'lastrowid') else None
        if not item_id and db.use_postgres:
            result = db.fetchone('SELECT LASTVAL()')
            item_id = result[0] if result else None
        
        logger.info(f"✅ Добавлен элемент {item_id} в список {list_id}")
        return item_id
    except Exception as e:
        logger.error(f"❌ Ошибка добавления элемента: {e}")
        return None

def get_list_items(list_id, item_type=None):
    """Получает элементы списка"""
    try:
        if item_type:
            return db.fetchall(
                'SELECT * FROM items WHERE list_id = %s AND type = %s ORDER BY title',
                (list_id, item_type)
            )
        else:
            return db.fetchall(
                'SELECT * FROM items WHERE list_id = %s ORDER BY type, title',
                (list_id,)
            )
    except Exception as e:
        logger.error(f"❌ Ошибка получения элементов: {e}")
        return []

def get_item_by_id(item_id):
    """Получает элемент по ID"""
    try:
        return db.fetchone('SELECT * FROM items WHERE id = %s', (item_id,))
    except Exception as e:
        logger.error(f"❌ Ошибка получения элемента: {e}")
        return None

def update_item(item_id, **kwargs):
    """Обновляет элемент"""
    if not kwargs:
        return False
    
    try:
        set_clause = ", ".join([f"{key} = %s" for key in kwargs.keys()])
        values = list(kwargs.values())
        values.append(item_id)
        
        cursor = db.execute(
            f'UPDATE items SET {set_clause} WHERE id = %s',
            values
        )
        
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"❌ Ошибка обновления элемента: {e}")
        return False

def delete_item_from_list(item_id):
    """Удаляет элемент из списка"""
    try:
        cursor = db.execute('DELETE FROM items WHERE id = %s', (item_id,))
        
        logger.info(f"🗑 Удален элемент {item_id}")
        return cursor.rowcount > 0
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

def type_keyboard():
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
    user_text = message.text.strip()
    
    if not user_text:
        bot.send_message(chat_id, "❌ Ввод не может быть пустым.")
        return
    
    try:
        if state == 'awaiting_list_name':
            user_states[chat_id] = {
                'state': 'awaiting_list_pin',
                'list_name': user_text
            }
            bot.send_message(
                chat_id,
                "🔐 *Установите пин-код для списка*\n\n"
                "Введите пин-код (4-6 цифр):\n"
                "Этот пин нужен для доступа к списку.",
                parse_mode='Markdown'
            )
        
        elif state == 'awaiting_list_pin':
            if not user_text.isdigit() or len(user_text) < 4 or len(user_text) > 6:
                bot.send_message(
                    chat_id,
                    "❌ Пин-код должен содержать 4-6 цифр.\n"
                    "Попробуйте еще раз:"
                )
                return
            
            list_name = user_states[chat_id]['list_name']
            list_id, error = create_list(chat_id, list_name, user_text)
            
            if error:
                bot.send_message(chat_id, f"❌ {error}", reply_markup=main_keyboard())
            else:
                bot.send_message(
                    chat_id,
                    f"✅ *Список создан успешно!*\n\n"
                    f"📋 Название: {list_name}\n"
                    f"🔑 Пин-код: {user_text}\n"
                    f"🆔 ID списка: {list_id}\n\n"
                    f"Сохраните ID и пин-код для доступа к списку.",
                    parse_mode='Markdown',
                    reply_markup=main_keyboard()
                )
            
            del user_states[chat_id]
        
        elif state == 'awaiting_list_id':
            if not user_text.isdigit():
                bot.send_message(chat_id, "❌ ID списка должен быть числом.")
                return
            
            list_id = int(user_text)
            list_data = get_list_by_id(list_id)
            if not list_data:
                bot.send_message(chat_id, "❌ Список не найден.")
                return
            
            user_states[chat_id] = {
                'state': 'awaiting_list_pin_access',
                'list_id': list_id
            }
            
            bot.send_message(
                chat_id,
                f"🔐 *Доступ к списку '{list_data['name']}'*\n\n"
                f"Введите пин-код для доступа:",
                parse_mode='Markdown'
            )
        
        elif state == 'awaiting_list_pin_access':
            list_id = user_states[chat_id]['list_id']
            access, list_data = verify_list_access(list_id, user_text, chat_id)
            
            if not access:
                bot.send_message(chat_id, "❌ Неверный пин-код.", reply_markup=main_keyboard())
                del user_states[chat_id]
                return
            
            # Открываем список
            bot.send_message(
                chat_id,
                f"✅ *Доступ получен!*\n\n"
                f"📋 *{list_data['name']}*\n\n"
                f"Выберите действие:",
                parse_mode='Markdown',
                reply_markup=list_menu_keyboard(list_data['id'])
            )
            del user_states[chat_id]
        
        elif state == 'awaiting_new_pin':
            if not user_text.isdigit() or len(user_text) < 4 or len(user_text) > 6:
                bot.send_message(
                    chat_id,
                    "❌ Пин-код должен содержать 4-6 цифр.\n"
                    "Попробуйте еще раз:"
                )
                return
            
            list_id = user_states[chat_id]['list_id']
            
            if update_list_pin(list_id, user_text):
                bot.send_message(
                    chat_id,
                    f"✅ Пин-код успешно изменен на: {user_text}",
                    reply_markup=main_keyboard()
                )
            else:
                bot.send_message(
                    chat_id,
                    "❌ Ошибка при изменении пин-кода.",
                    reply_markup=main_keyboard()
                )
            
            del user_states[chat_id]
        
        elif state == 'awaiting_item_type':
            if user_text not in ['Фильм', 'Сериал']:
                bot.send_message(chat_id, "❌ Выберите тип из предложенных.")
                return
            
            user_states[chat_id] = {
                'state': 'awaiting_item_title',
                'list_id': user_states[chat_id]['list_id'],
                'item_type': 'movie' if user_text == 'Фильм' else 'series'
            }
            
            bot.send_message(
                chat_id,
                f"🎬 *Добавление {user_text.lower()}а*\n\n"
                f"Введите название:",
                parse_mode='Markdown',
                reply_markup=types.ReplyKeyboardRemove()
            )
        
        elif state == 'awaiting_item_title':
            list_id = user_states[chat_id]['list_id']
            item_type = user_states[chat_id]['item_type']
            
            # Ищем информацию о фильме
            result = search_film(user_text, item_type)
            
            # Добавляем элемент
            item_id = add_item(
                list_id=list_id,
                item_type=item_type,
                title=user_text,
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
                    f"✅ *{user_text} добавлен успешно!*\n\n"
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
        
        elif state == 'awaiting_comment':
            item_id = user_states[chat_id]['item_id']
            list_id = user_states[chat_id]['list_id']
            
            if update_item(item_id, comment=user_text):
                bot.send_message(chat_id, "💭 *Комментарий добавлен!*", parse_mode='Markdown')
                item = get_item_by_id(item_id)
                bot.send_message(
                    chat_id,
                    format_item_details(item),
                    parse_mode='Markdown',
                    reply_markup=item_keyboard(item_id, list_id)
                )
            else:
                bot.send_message(chat_id, "❌ Ошибка при добавлении комментария.")
            
            del user_states[chat_id]
    
    except Exception as e:
        logger.error(f"❌ Ошибка обработки состояния: {e}")
        bot.send_message(chat_id, "❌ Произошла ошибка. Попробуйте еще раз.", reply_markup=main_keyboard())
        if chat_id in user_states:
            del user_states[chat_id]

# ========== ОБРАБОТЧИКИ CALLBACK ==========
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    data = call.data
    
    try:
        if data.startswith('open_list_'):
            list_id = int(data.split('_')[2])
            list_data = get_list_by_id(list_id)
            if list_data and list_data['owner_id'] == chat_id:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    text=f"📋 *{list_data['name']}*\n\nВыберите действие:",
                    parse_mode='Markdown',
                    reply_markup=list_menu_keyboard(list_id)
                )
            else:
                bot.answer_callback_query(call.id, "❌ У вас нет доступа к этому списку.")
        
        elif data == 'create_list':
            bot.delete_message(chat_id, call.message.message_id)
            new_list_start(call.message)
        
        elif data.startswith('list_series_'):
            list_id = int(data.split('_')[2])
            show_list_items(chat_id, call.message.message_id, list_id, 'series')
        
        elif data.startswith('list_movies_'):
            list_id = int(data.split('_')[2])
            show_list_items(chat_id, call.message.message_id, list_id, 'movie')
        
        elif data.startswith('list_add_'):
            list_id = int(data.split('_')[2])
            user_states[chat_id] = {
                'state': 'awaiting_item_type',
                'list_id': list_id
            }
            
            bot.send_message(
                chat_id,
                "🎬 *Что вы хотите добавить?*\n\nВыберите тип:",
                parse_mode='Markdown',
                reply_markup=type_keyboard()
            )
        
        elif data.startswith('change_pin_'):
            list_id = int(data.split('_')[2])
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
        
        elif data.startswith('watch_'):
            item_id = int(data.split('_')[1])
            list_id = int(data.split('_')[2])
            if update_item(item_id, watched=1):
                item = get_item_by_id(item_id)
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    text=format_item_details(item),
                    parse_mode='Markdown',
                    reply_markup=item_keyboard(item_id, list_id)
                )
                bot.answer_callback_query(call.id, "✅ Отмечено как просмотренное")
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка")
        
        elif data.startswith('unwatch_'):
            item_id = int(data.split('_')[1])
            list_id = int(data.split('_')[2])
            if update_item(item_id, watched=0):
                item = get_item_by_id(item_id)
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    text=format_item_details(item),
                    parse_mode='Markdown',
                    reply_markup=item_keyboard(item_id, list_id)
                )
                bot.answer_callback_query(call.id, "👁 Отмечено как не просмотренное")
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка")
        
        elif data.startswith('delete_item_'):
            item_id = int(data.split('_')[2])
            list_id = int(data.split('_')[3])
            item = get_item_by_id(item_id)
            
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
        
        elif data.startswith('confirm_delete_'):
            item_id = int(data.split('_')[2])
            list_id = int(data.split('_')[3])
            
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
        
        elif data.startswith('show_item_'):
            item_id = int(data.split('_')[2])
            list_id = int(data.split('_')[3])
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
        
        elif data.startswith('back_to_list_'):
            list_id = int(data.split('_')[3])
            list_data = get_list_by_id(list_id)
            if list_data:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    text=f"📋 *{list_data['name']}*\n\nВыберите действие:",
                    parse_mode='Markdown',
                    reply_markup=list_menu_keyboard(list_id)
                )
        
        elif data == 'back_to_lists':
            lists_data = get_user_lists(chat_id)
            if lists_data:
                markup = lists_keyboard(lists_data)
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    text="📋 *Ваши списки:*\n\nВыберите список для работы:",
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            else:
                bot.delete_message(chat_id, call.message.message_id)
                bot.send_message(
                    chat_id,
                    "📭 У вас пока нет списков.",
                    reply_markup=main_keyboard()
                )
        
        elif data.startswith('add_comment_'):
            item_id = int(data.split('_')[2])
            list_id = int(data.split('_')[3])
            
            user_states[chat_id] = {
                'state': 'awaiting_comment',
                'item_id': item_id,
                'list_id': list_id
            }
            
            bot.send_message(
                chat_id,
                "💭 *Добавление комментария*\n\nВведите ваш комментарий:",
                parse_mode='Markdown',
                reply_markup=types.ForceReply(selective=True)
            )
        
        else:
            bot.answer_callback_query(call.id, "❌ Неизвестная команда")
    
    except Exception as e:
        logger.error(f"❌ Ошибка обработки callback: {e}")
        bot.answer_callback_query(call.id, "❌ Произошла ошибка")

def show_list_items(chat_id, message_id, list_id, item_type):
    """Показывает элементы списка"""
    items = get_list_items(list_id, item_type)
    list_data = get_list_by_id(list_id)
    
    if not items:
        type_text = "сериалы" if item_type == 'series' else "фильмы"
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"📭 В списке '{list_data['name']}' пока нет {type_text}.",
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
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=f"🎬 *{type_text.capitalize()} в списке '{list_data['name']}':*\n\n"
        f"Всего: {len(items)}\n"
        f"Просмотрено: {sum(1 for i in items if i['watched'])}",
        parse_mode='Markdown',
        reply_markup=markup
    )

# ========== ЗАПУСК ПРИЛОЖЕНИЯ ==========
if __name__ == '__main__':
    print("=" * 60)
    print(f"🚀 Запуск КиноБота в {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🔑 Токен: {'✅ установлен' if TOKEN else '❌ НЕТ'}")
    print(f"🌐 RENDER_EXTERNAL_URL: {RENDER_EXTERNAL_URL or 'не установлен'}")
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
        bot.remove_webhook()
    
    # Запускаем сервер
    port = int(os.getenv('PORT', 10000))
    print(f"🌐 Запуск Flask на порту {port}")
    print(f"🌐 Главная страница: http://0.0.0.0:{port}/")
    print("=" * 60)
    
    # Запускаем Flask
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)

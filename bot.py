import telebot
from telebot import types
import sqlite3
import requests
import re
from deep_translator import GoogleTranslator

# ========== КОНФИГУРАЦИЯ ==========
TOKEN = "8572008688:AAFxlCebMUSKOhzsspjJXtr1vLoP3JUsvDU"
OMDB_API_KEY = "7717512b"
KINOPOISK_API_KEY = "ZS97X1F-7M144TE-Q24BJS9-BAWFJDE"

bot = telebot.TeleBot(TOKEN)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('movies.db')
    cur = conn.cursor()
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
    conn.close()

def add_item(item_type, title, original_title, year, kp_rating=None, imdb_rating=None, kp_url=None, imdb_url=None):
    conn = sqlite3.connect('movies.db')
    cur = conn.cursor()
    try:
        cur.execute('''INSERT INTO items (type, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url) 
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                    (item_type, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

def get_items(item_type):
    conn = sqlite3.connect('movies.db')
    cur = conn.cursor()
    cur.execute('''SELECT id, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                   FROM items WHERE type = ? ORDER BY title''', (item_type,))
    items = cur.fetchall()
    conn.close()
    return items

def get_item_by_id(item_id):
    conn = sqlite3.connect('movies.db')
    cur = conn.cursor()
    cur.execute('''SELECT id, type, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment 
                   FROM items WHERE id = ?''', (item_id,))
    item = cur.fetchone()
    conn.close()
    return item

def update_item(item_id, **kwargs):
    conn = sqlite3.connect('movies.db')
    cur = conn.cursor()
    
    if not kwargs:
        return False
    
    set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
    values = list(kwargs.values())
    values.append(item_id)
    
    try:
        cur.execute(f"UPDATE items SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return cur.rowcount > 0
    except:
        return False
    finally:
        conn.close()

def delete_item(item_id):
    conn = sqlite3.connect('movies.db')
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
        return cur.rowcount > 0
    except:
        return False
    finally:
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
                return {
                    'title': film.get('name', 'Неизвестно'),
                    'original_title': film.get('alternativeName', film.get('name', 'Неизвестно')),
                    'year': film.get('year', 'Неизвестно'),
                    'kp_rating': round(film.get('rating', {}).get('kp', 0), 1) if film.get('rating', {}).get('kp') else None,
                    'imdb_rating': round(film.get('rating', {}).get('imdb', 0), 1) if film.get('rating', {}).get('imdb') else None,
                    'type': film.get('type', 'movie'),
                    'kp_url': f"https://www.kinopoisk.ru/film/{film.get('id', '')}" if film.get('id') else None
                }
    except:
        pass
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
                
                return {
                    'title': data.get('Title', search_title),
                    'original_title': data.get('Title', search_title),
                    'year': data.get('Year', 'Неизвестно'),
                    'imdb_rating': round(imdb_rating, 1) if imdb_rating else None,
                    'kp_rating': None,
                    'type': 'movie' if data.get('Type') == 'movie' else 'series',
                    'imdb_url': f"https://www.imdb.com/title/{data.get('imdbID', '')}" if data.get('imdbID') else None
                }
        except:
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
    
    if not results:
        results = {
            'title': title,
            'original_title': title,
            'year': 'Неизвестно',
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
    btn4 = types.KeyboardButton('📊 Статистика')
    markup.add(btn1, btn2, btn3, btn4)
    return markup

def type_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1 = types.KeyboardButton('Фильм')
    btn2 = types.KeyboardButton('Сериал')
    btn3 = types.KeyboardButton('Назад')
    markup.add(btn1, btn2, btn3)
    return markup

def skip_keyboard():
    """Клавиатура для пропуска комментария"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1 = types.KeyboardButton('➡️ Пропустить комментарий')
    markup.add(btn1)
    return markup

def list_keyboard(items, prefix="item"):
    markup = types.InlineKeyboardMarkup(row_width=2)
    for item in items:
        item_id, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment = item
        watched_icon = "✅" if watched else "👁"
        btn_text = f"{watched_icon} {title}"
        if year and year != 'Неизвестно':
            btn_text += f" ({year})"
        if len(btn_text) > 40:
            btn_text = btn_text[:37] + "..."
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"{prefix}_{item_id}"))
    return markup

def item_keyboard(item_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('✅ Просмотрено', callback_data=f'watch_{item_id}'),
        types.InlineKeyboardButton('👁 Хочу посмотреть', callback_data=f'unwatch_{item_id}'),
        types.InlineKeyboardButton('💬 Комментарий', callback_data=f'comment_{item_id}'),
        types.InlineKeyboardButton('🗑 Удалить', callback_data=f'delete_{item_id}'),
        types.InlineKeyboardButton('↩️ Назад', callback_data='back_to_list')
    )
    return markup

# ========== ФОРМАТИРОВАНИЕ ТЕКСТА ==========
def format_item_details(item):
    item_id, item_type, title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url, watched, comment = item
    
    type_ru = "сериал" if item_type == 'series' else "фильм"
    watched_text = "✅ Просмотрено" if watched else "👁 Хочу посмотреть"
    
    text = f"🎬 *{type_ru.upper()} #{item_id}*\n\n"
    text += f"📌 *{title}*\n"
    
    if original_title and original_title != title:
        text += f"🌐 *Оригинальное название:* {original_title}\n"
    
    text += f"📅 *Год:* {year}\n"
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

def format_stats():
    all_movies = get_items('movie')
    all_series = get_items('series')
    
    watched_movies = sum(1 for m in all_movies if m[8])
    watched_series = sum(1 for s in all_series if s[8])
    
    text = "📊 *Ваша статистика:*\n\n"
    text += f"🎥 *Фильмы:* {len(all_movies)} (просмотрено: {watched_movies})\n"
    text += f"🎬 *Сериалы:* {len(all_series)} (просмотрено: {watched_series})\n"
    text += f"📋 *Всего:* {len(all_movies) + len(all_series)} (просмотрено: {watched_movies + watched_series})"
    
    return text

# ========== ОБРАБОТЧИКИ СООБЩЕНИЙ ==========
user_states = {}

@bot.message_handler(commands=['start', 'help'])
def start(message):
    init_db()
    bot.send_message(message.chat.id, 
                     "🎬 *КиноБот - ваш персональный список фильмов и сериалов*\n\n"
                     "Я помогу вам:\n"
                     "• 📝 Вести список просмотренных фильмов и сериалов\n"
                     "• ✅ Отмечать 'Просмотрено' или 'Хочу посмотреть'\n"
                     "• 💬 Добавлять комментарии к фильмам\n"
                     "• 🗑 Удалять записи из списка\n"
                     "• ⭐ Автоматически находить рейтинги\n\n"
                     "Выберите действие ниже:",
                     parse_mode='Markdown',
                     reply_markup=main_keyboard())

@bot.message_handler(func=lambda message: message.text == '🎬 Список сериалов')
def show_series(message):
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

@bot.message_handler(func=lambda message: message.text == '🎥 Список фильмов')
def show_movies(message):
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

@bot.message_handler(func=lambda message: message.text == '📊 Статистика')
def show_stats(message):
    bot.send_message(message.chat.id, format_stats(), parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == '➕ Добавить фильм или сериал')
def add_item_start(message):
    bot.send_message(message.chat.id, "🎬 *Что вы хотите добавить?*\n\nВы можете ввести название на русском или английском языке.", 
                     parse_mode='Markdown', reply_markup=type_keyboard())
    user_states[message.chat.id] = {'state': 'choosing_type'}

@bot.message_handler(func=lambda message: message.text in ['Фильм', 'Сериал'])
def choose_type(message):
    chat_id = message.chat.id
    user_states[chat_id] = {
        'state': 'entering_title',
        'type': 'movie' if message.text == 'Фильм' else 'series'
    }
    type_ru = "фильм" if message.text == 'Фильм' else "сериал"
    bot.send_message(chat_id, 
                     f"🎥 *Введите название {type_ru}а:*\n\n"
                     f"• Можно ввести на русском или английском\n"
                     f"• Например: 'Интерстеллар' или 'Inception'\n"
                     f"• Я поищу рейтинги на Кинопоиске и IMDb",
                     parse_mode='Markdown',
                     reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda message: message.text == 'Назад')
def back_to_main(message):
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=main_keyboard())
    if message.chat.id in user_states:
        del user_states[message.chat.id]

@bot.message_handler(func=lambda message: message.chat.id in user_states and user_states[message.chat.id]['state'] == 'entering_title')
def enter_title(message):
    chat_id = message.chat.id
    title = message.text.strip()
    item_type = user_states[chat_id]['type']
    
    if not title:
        bot.send_message(chat_id, "❌ Название не может быть пустым. Попробуйте еще раз:", 
                       reply_markup=type_keyboard())
        return
    
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
        kp_rating=result.get('kp_rating'),
        imdb_rating=result.get('imdb_rating'),
        kp_url=result.get('kp_url'),
        imdb_url=result.get('imdb_url')
    )
    
    if item_id:
        # Показываем информацию о найденном фильме
        type_ru = "фильм" if item_type == 'movie' else "сериал"
        
        # Формируем сообщение о добавлении
        found_kp = result.get('kp_rating') is not None
        found_imdb = result.get('imdb_rating') is not None
        
        message_text = f"✅ *'{title}' добавлен успешно!*\n\n"
        
        if found_kp or found_imdb:
            if found_kp:
                message_text += f"⭐ *Кинопоиск:* {result['kp_rating']}/10\n"
            if found_imdb:
                message_text += f"⭐ *IMDb:* {result['imdb_rating']}/10\n"
            message_text += f"📅 *Год:* {result['year']}\n"
        else:
            message_text += f"📅 *Год:* {result['year']}\n"
            message_text += "⚠️ Рейтинги не найдены\n"
        
        bot.send_message(chat_id, message_text, parse_mode='Markdown')
        
        # Теперь предлагаем добавить комментарий с кнопкой "Пропустить"
        user_states[chat_id] = {'state': 'adding_comment', 'item_id': item_id}
        bot.send_message(
            chat_id,
            "💭 *Хотите добавить комментарий?*\n\n"
            "Напишите ваш комментарий или нажмите кнопку '➡️ Пропустить комментарий'",
            parse_mode='Markdown',
            reply_markup=skip_keyboard()
        )
    else:
        bot.send_message(chat_id, "❌ Ошибка при сохранении.", reply_markup=main_keyboard())
        del user_states[chat_id]

@bot.message_handler(func=lambda message: message.chat.id in user_states and user_states[message.chat.id].get('state') == 'adding_comment')
def add_comment(message):
    chat_id = message.chat.id
    item_id = user_states[chat_id]['item_id']
    
    if message.text == '➡️ Пропустить комментарий':
        # Пропускаем комментарий
        bot.send_message(chat_id, "➡️ Комментарий пропущен.", reply_markup=main_keyboard())
    else:
        # Сохраняем комментарий
        update_item(item_id, comment=message.text)
        bot.send_message(chat_id, "💭 *Комментарий добавлен!*", parse_mode='Markdown', reply_markup=main_keyboard())
    
    # Показываем детали добавленного фильма
    item = get_item_by_id(item_id)
    if item:
        bot.send_message(
            chat_id,
            format_item_details(item),
            parse_mode='Markdown',
            disable_web_page_preview=True,
            reply_markup=item_keyboard(item_id)
        )
    
    del user_states[chat_id]

@bot.message_handler(func=lambda message: message.chat.id in user_states and user_states[message.chat.id].get('state') == 'editing_comment')
def edit_comment(message):
    chat_id = message.chat.id
    item_id = user_states[chat_id]['item_id']
    
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
    else:
        bot.send_message(chat_id, "❌ Ошибка при обновлении.")
    
    del user_states[chat_id]

@bot.message_handler(func=lambda message: True)
def handle_other(message):
    if message.chat.id in user_states:
        state = user_states[message.chat.id].get('state')
        if state == 'adding_comment':
            bot.send_message(message.chat.id, 
                           "💭 *Хотите добавить комментарий?*\n\n"
                           "Напишите ваш комментарий или нажмите кнопку '➡️ Пропустить комментарий'",
                           parse_mode='Markdown',
                           reply_markup=skip_keyboard())
        else:
            bot.send_message(message.chat.id, "Пожалуйста, введите название фильма или сериала:", 
                           reply_markup=types.ReplyKeyboardRemove())
    else:
        bot.send_message(message.chat.id, "Используйте кнопки меню 👇", reply_markup=main_keyboard())

# ========== ОБРАБОТЧИКИ CALLBACK ==========
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
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
        else:
            bot.answer_callback_query(call.id, "❌ Запись не найдена")
    
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
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка")
    
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
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка")
    
    elif call.data.startswith('comment_'):
        item_id = int(call.data.split('_')[1])
        user_states[chat_id] = {'state': 'editing_comment', 'item_id': item_id}
        
        item = get_item_by_id(item_id)
        current_comment = item[10] if item and item[10] else "нет комментария"
        
        bot.delete_message(chat_id, message_id)
        bot.send_message(
            chat_id,
            f"💭 *Редактирование комментария*\n\nТекущий комментарий: {current_comment}\n\nВведите новый комментарий:",
            parse_mode='Markdown',
            reply_markup=types.ForceReply(selective=True)
        )
    
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
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка при удалении")
        else:
            bot.answer_callback_query(call.id, "❌ Запись не найдена")
    
    elif call.data == 'back_to_list':
        bot.delete_message(chat_id, message_id)
        bot.send_message(chat_id, "Главное меню:", reply_markup=main_keyboard())
    
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

# ========== ЗАПУСК БОТА ==========
if __name__ == '__main__':
    print("=" * 50)
    print("🎬 КиноБот запущен!")
    print("=" * 50)
    print("\nФункции бота:")
    print("• Добавление фильмов и сериалов")
    print("• Комментарии к записям")
    print("• Статусы 'Просмотрено'/'Хочу посмотреть'")
    print("• Удаление записей")
    print("• Автопоиск рейтингов")
    print("• Статистика")
    print("=" * 50)
    
    init_db()
    bot.polling(none_stop=True)

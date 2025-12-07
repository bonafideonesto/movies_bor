import telebot
from telebot import types
import sqlite3
import requests
import re
import os
import time  # ← ДОБАВЬТЕ ЭТУ СТРОЧКУ
from deep_translator import GoogleTranslator

# ========== КОНФИГУРАЦИЯ ==========
# ИСПОЛЬЗУЕМ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ДЛЯ БЕЗОПАСНОСТИ
TOKEN = os.getenv('TELEGRAM_TOKEN', "8572008688:AAFxlCebMUSKOhzsspjJXtr1vLoP3JUsvDU")
OMDB_API_KEY = os.getenv('OMDB_API_KEY', "7717512b")
KINOPOISK_API_KEY = os.getenv('KINOPOISK_API_KEY', "ZS97X1F-7M144TE-Q24BJS9-BAWFJDE")

bot = telebot.TeleBot(TOKEN)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('movies.db')
    cur = conn.cursor()
    # Убраны комментарии из SQL запроса
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
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_items(item_type):
    conn = sqlite3.connect('movies.db')
    cur = conn.cursor()
    cur.execute('''SELECT title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url 
                   FROM items WHERE type = ? ORDER BY title''', (item_type,))
    items = cur.fetchall()
    conn.close()
    return items

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def is_russian_text(text):
    """Проверяет, содержит ли текст русские буквы"""
    return bool(re.search('[а-яА-Я]', text))

def translate_russian_to_english(text):
    """Переводит русский текст на английский"""
    try:
        translator = GoogleTranslator(source='ru', target='en')
        translated = translator.translate(text)
        return translated
    except Exception as e:
        print(f"Ошибка перевода: {e}")
        return text  # Если перевод не удался, возвращаем оригинал

# ========== ПОИСК В KINOPOISK ==========
def search_kinopoisk(title):
    """Поиск в Kinopoisk API"""
    if not KINOPOISK_API_KEY or KINOPOISK_API_KEY == "ВАШ_KINOPOISK_API_КЛЮЧ":
        return None
    
    headers = {'X-API-KEY': KINOPOISK_API_KEY}
    
    # Если текст на русском, ищем как есть
    search_title = title
    
    url = f"https://api.kinopoisk.dev/v1.4/movie/search?page=1&limit=3&query={requests.utils.quote(search_title)}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get('docs') and len(data['docs']) > 0:
                # Ищем наиболее подходящий результат
                for film in data['docs']:
                    film_name = film.get('name', '').lower()
                    film_alternative = film.get('alternativeName', '').lower()
                    search_lower = search_title.lower()
                    
                    # Проверяем совпадение названий
                    if (search_lower in film_name or 
                        search_lower in film_alternative or
                        film_name in search_lower):
                        
                        # Получаем информацию
                        result = {
                            'title': film.get('name', 'Неизвестно'),
                            'original_title': film.get('alternativeName', film.get('name', 'Неизвестно')),
                            'year': film.get('year', 'Неизвестно'),
                            'kp_rating': round(film.get('rating', {}).get('kp', 0), 1) if film.get('rating', {}).get('kp') else None,
                            'imdb_rating': round(film.get('rating', {}).get('imdb', 0), 1) if film.get('rating', {}).get('imdb') else None,
                            'type': film.get('type', 'movie'),
                            'kp_url': f"https://www.kinopoisk.ru/film/{film.get('id', '')}" if film.get('id') else None
                        }
                        return result
                
                # Если точного совпадения нет, берем первый результат
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
    
    except Exception as e:
        print(f"Ошибка Kinopoisk API: {e}")
    
    return None

# ========== ПОИСК В OMDb ==========
def search_omdb(title):
    """Поиск в OMDb API"""
    if not OMDB_API_KEY or OMDB_API_KEY == "ВАШ_OMDB_КЛЮЧ":
        return None
    
    # Если текст на русском, переводим
    if is_russian_text(title):
        translated = translate_russian_to_english(title)
        search_titles = [translated, title]  # Пробуем оба варианта
    else:
        search_titles = [title]
    
    for search_title in search_titles:
        url = f"http://www.omdbapi.com/?t={requests.utils.quote(search_title)}&apikey={OMDB_API_KEY}"
        
        try:
            response = requests.get(url, timeout=5)
            data = response.json()
            
            if data.get('Response') == 'True':
                # Получаем рейтинг IMDb
                imdb_rating = None
                for rating_item in data.get('Ratings', []):
                    if rating_item['Source'] == 'Internet Movie Database':
                        try:
                            imdb_rating = float(rating_item['Value'].split('/')[0])
                            break
                        except:
                            pass
                
                result = {
                    'title': data.get('Title', search_title),
                    'original_title': data.get('Title', search_title),
                    'year': data.get('Year', 'Неизвестно'),
                    'imdb_rating': round(imdb_rating, 1) if imdb_rating else None,
                    'kp_rating': None,
                    'type': 'movie' if data.get('Type') == 'movie' else 'series',
                    'imdb_url': f"https://www.imdb.com/title/{data.get('imdbID', '')}" if data.get('imdbID') else None
                }
                return result
                
        except Exception as e:
            print(f"Ошибка OMDb API: {e}")
            continue
    
    return None

# ========== ОБЪЕДИНЕННЫЙ ПОИСК ==========
def search_film(title, item_type=None):
    """Ищет фильм/сериал в Kinopoisk и OMDb, возвращает объединенные данные"""
    results = {}
    
    # 1. Ищем в Kinopoisk (особенно хорошо для русских названий)
    kp_result = search_kinopoisk(title)
    if kp_result:
        results.update(kp_result)
        # Если нашли в Kinopoisk, пробуем найти английское название для поиска в OMDb
        if is_russian_text(title) and kp_result.get('original_title'):
            eng_title = kp_result['original_title']
        else:
            eng_title = title
    else:
        eng_title = title
    
    # 2. Ищем в OMDb (если не нашли в Kinopoisk или хотим дополнить IMDb рейтингом)
    omdb_result = search_omdb(eng_title)
    if omdb_result:
        # Объединяем результаты, отдавая приоритет Kinopoisk для названия
        if not results:
            results = omdb_result
        else:
            # Обновляем только IMDb рейтинг и ссылку, если их нет
            if not results.get('imdb_rating') and omdb_result.get('imdb_rating'):
                results['imdb_rating'] = omdb_result['imdb_rating']
            if not results.get('imdb_url') and omdb_result.get('imdb_url'):
                results['imdb_url'] = omdb_result['imdb_url']
    
    # 3. Если ничего не найдено, создаем базовую запись
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
    else:
        # Убедимся, что есть оригинальное название (как ввел пользователь)
        if 'title' not in results or results['title'] != title:
            results['user_title'] = title  # Сохраняем введенное пользователем название
    
    return results

# ========== КЛАВИАТУРЫ ==========
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1 = types.KeyboardButton('🎬 Список сериалов')
    btn2 = types.KeyboardButton('🎥 Список фильмов')
    btn3 = types.KeyboardButton('➕ Добавить фильм или сериал')
    markup.add(btn1, btn2, btn3)
    return markup

def type_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1 = types.KeyboardButton('Фильм')
    btn2 = types.KeyboardButton('Сериал')
    btn3 = types.KeyboardButton('Назад')
    markup.add(btn1, btn2, btn3)
    return markup

# ========== ОБРАБОТЧИКИ ==========
user_states = {}

@bot.message_handler(commands=['start', 'help'])
def start(message):
    init_db()
    bot.send_message(message.chat.id, 
                     "🎬 *КиноБот - ваш персональный список фильмов и сериалов*\n\n"
                     "Я помогу вам:\n"
                     "• 📝 Вести список просмотренных фильмов и сериалов\n"
                     "• ⭐ Автоматически находить рейтинги (Кинопоиск и IMDb)\n"
                     "• 🔍 Искать информацию по русским и английским названиям\n\n"
                     "Выберите действие ниже:",
                     parse_mode='Markdown',
                     reply_markup=main_keyboard())

@bot.message_handler(func=lambda message: message.text == '🎬 Список сериалов')
def show_series(message):
    items = get_items('series')
    if not items:
        text = "📭 Список сериалов пуст.\n\nДобавьте первый сериал через меню '➕ Добавить фильм или сериал'"
    else:
        text = "📺 *Ваш список сериалов:*\n\n"
        for title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url in items:
            text += f"▪️ *{title}*"
            if original_title and original_title != title:
                text += f" ({original_title})"
            text += f" ({year})\n"
            
            # Показываем рейтинги
            ratings = []
            if kp_rating:
                ratings.append(f"КП: ⭐{kp_rating}")
            if imdb_rating:
                ratings.append(f"IMDb: ⭐{imdb_rating}")
            
            if ratings:
                text += f"   {' | '.join(ratings)}\n"
            text += "\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=main_keyboard())

@bot.message_handler(func=lambda message: message.text == '🎥 Список фильмов')
def show_movies(message):
    items = get_items('movie')
    if not items:
        text = "📭 Список фильмов пуст.\n\nДобавьте первый фильм через меню '➕ Добавить фильм или сериал'"
    else:
        text = "🎞 *Ваш список фильмов:*\n\n"
        for title, original_title, year, kp_rating, imdb_rating, kp_url, imdb_url in items:
            text += f"▪️ *{title}*"
            if original_title and original_title != title:
                text += f" ({original_title})"
            text += f" ({year})\n"
            
            # Показываем рейтинги
            ratings = []
            if kp_rating:
                ratings.append(f"КП: ⭐{kp_rating}")
            if imdb_rating:
                ratings.append(f"IMDb: ⭐{imdb_rating}")
            
            if ratings:
                text += f"   {' | '.join(ratings)}\n"
            
            # Показываем ссылки
            links = []
            if kp_url:
                links.append(f"[Кинопоиск]({kp_url})")
            if imdb_url:
                links.append(f"[IMDb]({imdb_url})")
            
            if links:
                text += f"   {' | '.join(links)}\n"
            text += "\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown', disable_web_page_preview=True, reply_markup=main_keyboard())

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
    
    # Проверяем, не пустое ли название
    if not title:
        bot.send_message(chat_id, "❌ Название не может быть пустым. Попробуйте еще раз:", 
                       reply_markup=type_keyboard())
        return
    
    # Проверяем, есть ли уже в базе
    existing_items = get_items(item_type)
    for existing_title, _, _, _, _, _, _ in existing_items:
        if existing_title.lower() == title.lower():
            bot.send_message(chat_id, 
                           f"❌ *'{title}'* уже есть в вашем списке {item_type}ов!\n\n"
                           f"Попробуйте добавить другой {item_type}.",
                           parse_mode='Markdown',
                           reply_markup=main_keyboard())
            del user_states[chat_id]
            return
    
    # Ищем информацию
    bot.send_message(chat_id, f"🔍 *Ищу информацию о '{title}'...*\n\n"
                           f"Проверяю Кинопоиск и IMDb...", 
                     parse_mode='Markdown')
    
    result = search_film(title, item_type)
    
    # Формируем сообщение с результатами
    type_ru = "фильм" if item_type == 'movie' else "сериал"
    
    # Определяем, что нашли
    found_kp = result.get('kp_rating') is not None
    found_imdb = result.get('imdb_rating') is not None
    
    if not found_kp and not found_imdb:
        # Ничего не нашли
        success = add_item(
            item_type=item_type,
            title=title,
            original_title=result.get('original_title', title),
            year=result['year'],
            kp_rating=None,
            imdb_rating=None,
            kp_url=None,
            imdb_url=None
        )
        
        if success:
            bot.send_message(chat_id,
                           f"✅ *'{title}'* добавлен в список {type_ru}ов!\n\n"
                           f"⚠️ *Информация:* Рейтинги не найдены\n"
                           f"📅 Год: {result['year']}\n\n"
                           f"Вы можете добавить рейтинг вручную, отредактировав запись позже.",
                           parse_mode='Markdown',
                           reply_markup=main_keyboard())
        else:
            bot.send_message(chat_id, "❌ Произошла ошибка при сохранении.", 
                           reply_markup=main_keyboard())
    
    else:
        # Что-то нашли
        # Используем оригинальное название из результата, если оно есть и отличается
        display_title = result.get('title', title)
        original_title = result.get('original_title', display_title)
        
        success = add_item(
            item_type=item_type,
            title=title,  # Сохраняем как ввел пользователь
            original_title=original_title,
            year=result['year'],
            kp_rating=result.get('kp_rating'),
            imdb_rating=result.get('imdb_rating'),
            kp_url=result.get('kp_url'),
            imdb_url=result.get('imdb_url')
        )
        
        if success:
            # Формируем сообщение с рейтингами
            rating_text = ""
            if found_kp:
                rating_text += f"⭐ *Кинопоиск:* {result['kp_rating']}/10\n"
            if found_imdb:
                rating_text += f"⭐ *IMDb:* {result['imdb_rating']}/10\n"
            
            links_text = ""
            if result.get('kp_url'):
                links_text += f"[🔗 Кинопоиск]({result['kp_url']})"
            if result.get('imdb_url'):
                if links_text:
                    links_text += " | "
                links_text += f"[🔗 IMDb]({result['imdb_url']})"
            
            message_text = f"✅ *{type_ru.capitalize()} добавлен!*\n\n"
            message_text += f"🎬 *Название:* {title}\n"
            if original_title and original_title.lower() != title.lower():
                message_text += f"🌐 *Оригинальное название:* {original_title}\n"
            message_text += f"📅 *Год:* {result['year']}\n\n"
            message_text += rating_text
            
            if links_text:
                message_text += f"\n{links_text}"
            
            bot.send_message(chat_id, message_text, 
                           parse_mode='Markdown',
                           disable_web_page_preview=True,
                           reply_markup=main_keyboard())
        else:
            bot.send_message(chat_id, "❌ Произошла ошибка при сохранении.", 
                           reply_markup=main_keyboard())
    
    del user_states[chat_id]

@bot.message_handler(func=lambda message: True)
def handle_other(message):
    if message.chat.id in user_states:
        bot.send_message(message.chat.id, "Пожалуйста, введите название фильма или сериала:", 
                       reply_markup=types.ReplyKeyboardRemove())
    else:
        bot.send_message(message.chat.id, "Используйте кнопки меню 👇", reply_markup=main_keyboard())

# ========== ЗАПУСК БОТА ==========
if __name__ == '__main__':
    print("=" * 50)
    print("🎬 КиноБот запущен!")
    print("=" * 50)
    print("\nДля работы бота необходимо:")
    print("1. Токен Telegram бота (от @BotFather)")
    print("2. Ключ OMDb API (бесплатный: omdbapi.com/apikey.aspx)")
    print("3. Ключ Kinopoisk API (опционально: kinopoisk.dev)")
    print("\nБот будет работать даже без API ключей, но без рейтингов")
    print("=" * 50)
    
    init_db()
    
    # БЕСКОНЕЧНЫЙ ЦИКЛ С ПЕРЕЗАПУСКОМ ПРИ ОШИБКАХ
    while True:
        try:
            print("🟢 Бот запускается...")
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            print(f"🔴 Ошибка: {e}")
            print("🔄 Перезапуск через 5 секунд...")
            time.sleep(5)
            continue
import telebot
import requests
from bs4 import BeautifulSoup
import sqlite3
import logging
import os
import re
import hashlib
from datetime import datetime, timedelta
import schedule
import time
import threading
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Log sozlamalari
logging.basicConfig(filename='bot.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Bot tokeni va kanal ma'lumotlari
BOT_TOKEN = '6815962912:AAGd-zchFzok2FBT6qlq4E8fJL9lflB2wb8'
YOUR_CHANNEL = '@yangiliklarning_barchasi'
SOURCE_CHANNELS = ['@sinov_uchun_kanalcha', '@koraxabar', '@TezkorxabarUz_official']

# Fayllar va baza
DB_FILE = 'messages.db'
LAST_POST_FILE = 'last_posts.txt'

# Botni ishga tushirish
bot = telebot.TeleBot(BOT_TOKEN)

# Requests sessiyasi (qayta urinishlar uchun)
session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
session.mount('http://', HTTPAdapter(max_retries=retries))
session.mount('https://', HTTPAdapter(max_retries=retries))

# SQLite bazasini sozlash
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (hash TEXT PRIMARY KEY, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS last_posts
                 (channel TEXT PRIMARY KEY, message_id INTEGER, link TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()
    logging.info("SQLite baza ishga tushirildi")

# Oxirgi post ma'lumotlarini o'qish
def load_last_posts():
    last_posts = {}
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT channel, message_id FROM last_posts")
        rows = c.fetchall()
        for row in rows:
            last_posts[row[0]] = row[1]
        conn.close()
    except Exception as e:
        logging.error(f"Bazadan last_posts o'qishda xato: {e}")
    return last_posts

# Oxirgi post ma'lumotlarini saqlash
def save_last_post(channel, message_id, link):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        c.execute("INSERT OR REPLACE INTO last_posts (channel, message_id, link, timestamp) VALUES (?, ?, ?, ?)",
                  (channel, message_id, link, timestamp))
        conn.commit()
        conn.close()
        logging.info(f"Bazaga oxirgi post saqlandi: {channel} -> ID: {message_id}, Link: {link}")
    except Exception as e:
        logging.error(f"Bazaga last_posts saqlashda xato: {e}")
    
    try:
        last_posts = load_last_posts()
        last_posts[channel] = message_id
        with open(LAST_POST_FILE, 'w', encoding='utf-8') as f:
            for ch, pid in last_posts.items():
                f.write(f"{ch}:{pid}\n")
        logging.info(f"last_posts.txt ga saqlandi: {channel} -> {message_id}")
    except Exception as e:
        logging.error(f"last_posts.txt saqlashda xato: {e}")

# Xabar imzosini yaratish
def get_message_signature(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

# Xabar takrorlanadimi tekshirish
def is_duplicate_message(signature):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT hash FROM messages WHERE hash = ?", (signature,))
    result = c.fetchone()
    conn.close()
    return result is not None

# Xabar imzosini saqlash
def save_message_signature(signature):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        c.execute("INSERT OR REPLACE INTO messages (hash, timestamp) VALUES (?, ?)", (signature, timestamp))
        conn.commit()
        conn.close()
        logging.info(f"Xabar imzosini saqlandi: {signature}")
    except Exception as e:
        logging.error(f"Xabar imzosini saqlashda xato: {e}")

# 24 soatdan eski xabarlarni o'chirish
def clean_old_messages():
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        expiry_time = (datetime.now() - timedelta(hours=24)).isoformat()
        c.execute("DELETE FROM messages WHERE timestamp < ?", (expiry_time,))
        c.execute("DELETE FROM last_posts WHERE timestamp < ?", (expiry_time,))
        conn.commit()
        conn.close()
        logging.info("Eski xabarlar va last_posts tozalandi")
    except Exception as e:
        logging.error(f"Eski xabarlarni tozalashda xato: {e}")

# Matnni tahrirlash funksiyasi (giperhavola bilan)
def edit_message_text(text, source_channel, message_id):
    text = re.sub(r'https?://t\.me/[^\s]+', '', text)
    source_link = f"https://t.me/{source_channel[1:]}/{message_id}"
    # HTML formatida giperhavola
    edited_text = f"{text}\n\n{YOUR_CHANNEL}\n\n<a href='{source_link}'>Manba</a>"
    return edited_text, source_link

# Kanal xabarlarini olish (veb-scraping)
def get_channel_messages(channel):
    try:
        url = f"https://t.me/s/{channel[1:]}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        messages = []
        posts = soup.find_all('div', class_='tgme_widget_message')
        for post in posts[:5]:  # So'nggi 5 ta xabar
            text = post.find('div', class_='tgme_widget_message_text')
            text = text.get_text(strip=True) if text else ''
            link = post.find('a', class_='tgme_widget_message_date')
            link = link['href'] if link else ''
            message_id = int(link.split('/')[-1]) if link else 0
            if message_id:
                messages.append({'text': text, 'id': message_id, 'link': link})
        
        logging.info(f"{channel} kanalidan {len(messages)} ta xabar olindi")
        return messages
    except requests.exceptions.RequestException as e:
        logging.error(f"{channel} xabarlarini olishda tarmoq xatosi: {e}")
        return []
    except Exception as e:
        logging.error(f"{channel} xabarlarini olishda xato: {e}")
        return []

# Yangi xabarlarni tekshirish
def check_new_posts():
    last_posts = load_last_posts()
    clean_old_messages()
    
    for channel in SOURCE_CHANNELS:
        try:
            logging.info(f"{channel} kanalini tekshirish boshlandi")
            messages = get_channel_messages(channel)
            
            if not messages and channel not in last_posts:
                logging.info(f"{channel} uchun yangi kanal sifatida oxirgi xabar olinadi")
                messages = get_channel_messages(channel)
            
            latest_post_id = last_posts.get(channel, 0)
            
            for message in reversed(messages):
                message_id = message['id']
                link = message['link']
                logging.info(f"{channel} kanalidan xabar ID olindi: {message_id}, Link: {link}")
                
                if message_id > latest_post_id or (channel not in last_posts and message_id >= latest_post_id):
                    signature = get_message_signature(message['text'])
                    
                    if is_duplicate_message(signature):
                        logging.info(f"Takrorlangan xabar o'tkazib yuborildi: {channel}/{message_id}")
                        continue
                    
                    text = message['text']
                    edited_text, source_link = edit_message_text(text, channel, message_id)
                    
                    try:
                        bot.send_message(YOUR_CHANNEL, edited_text, parse_mode='HTML')
                        logging.info(f"Matn yuborildi: {channel}/{message_id}, Link: {source_link}")
                        save_message_signature(signature)
                        save_last_post(channel, message_id, source_link)
                    except Exception as e:
                        logging.error(f"{channel}/{message_id} xabarini yuborishda xato: {e}")
        
        except Exception as e:
            logging.error(f"{channel} kanalini tekshirishda xato: {e}")
    
    logging.info(f"Tekshiruv yakunlandi: {datetime.now()}")

# Bot polling
def run_bot_polling():
    while True:
        try:
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            logging.error(f"Polling xatosi: {e}")
            time.sleep(5)

# Asosiy funksiya
def main():
    logging.info("Bot ishga tushdi...")
    init_db()
    
    try:
        check_new_posts()
    except Exception as e:
        logging.error(f"Birinchi tekshiruvda xato: {e}")
    
    polling_thread = threading.Thread(target=run_bot_polling, daemon=True)
    polling_thread.start()
    
    while True:
        schedule.run_pending()
        time.sleep(1)

# Har 5 minutda tekshirish
schedule.every(5).minutes.do(check_new_posts)

if __name__ == "__main__":
    main()

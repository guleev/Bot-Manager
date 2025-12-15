import asyncio
import logging
import sqlite3
import json
import os
import sys
import subprocess
import threading
import time
import traceback
import signal
import csv
import datetime
import random
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import Dispatcher
from aiogram.utils import executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, InputFile
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import psutil
import math
import hashlib
import shutil

# ========== НАСТРОЙКА ЛОГИРОВАНИЯ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ========== НАСТРОЙКИ ==========
MANAGER_TOKEN = "8258712810:AAFPsRukN8UMS8S-dpl0sFrj3zNAq0T6Ytk"
ADMIN_IDS = [8545483002]
REQUIRED_CHANNEL = "@Gamma404"  # Канал для подписки

# Папки
IMAGES_FOLDER = "images"
BOTS_FOLDER = "bots"
TEMPLATES_FOLDER = "templates"

# Создаем папки если их нет
for folder in [IMAGES_FOLDER, BOTS_FOLDER, TEMPLATES_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Хранилище для запущенных процессов
running_processes = {}

# Состояния для создания бота
class BotCreationStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_owner_id = State()
    waiting_for_template = State()
    waiting_for_bot_name = State()

# Состояния для анкеты пользователя
class UserQuestionnaireStates(StatesGroup):
    waiting_for_q1 = State()
    waiting_for_q2 = State()
    waiting_for_q3 = State()

# Состояния для управления анкетами (админ)
class QuestionnaireAdminStates(StatesGroup):
    viewing_questionnaire = State()
    awaiting_action = State()

# ========== ФУНКЦИЯ ДЛЯ ОТПРАВКИ ФОТО С ПОДПИСЬЮ ==========
async def send_photo_with_caption(message: types.Message, photo_name: str, caption: str, reply_markup=None):
    """
    Отправляет фото с подписью. Ищет файлы с расширениями .jpg, .png, .jpeg, .gif, .webp
    """
    # Обрезаем подпись если слишком длинная
    max_caption_length = 1024  # Ограничение Telegram для подписей к фото
    if len(caption) > max_caption_length:
        caption = caption[:max_caption_length - 50] + "...\n\n🖥 Сообщение было обрезано"
    
    # Сначала ищем в папке images
    possible_files = [
        f"{IMAGES_FOLDER}/{photo_name}.jpg",
        f"{IMAGES_FOLDER}/{photo_name}.png",
        f"{IMAGES_FOLDER}/{photo_name}.jpeg",
        f"{IMAGES_FOLDER}/{photo_name}.gif",
        f"{IMAGES_FOLDER}/{photo_name}.webp",
        # Резервные пути
        f"{photo_name}.jpg",
        f"{photo_name}.png",
        f"{photo_name}.jpeg",
        f"{photo_name}.gif",
        f"{photo_name}.webp"
    ]
    
    photo_file = None
    
    for filename in possible_files:
        if os.path.exists(filename):
            photo_file = filename
            break
    
    if photo_file:
        try:
            with open(photo_file, 'rb') as photo:
                await message.answer_photo(
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки фото {photo_file}: {e}")
            # Пробуем отправить как текст если фото не отправляется
            await message.answer(f"<b>🖥 {caption.split(chr(10))[0] if caption.split(chr(10)) else 'Информация'}</b>\n\n{caption}", 
                               reply_markup=reply_markup, parse_mode="HTML")
            return False
    else:
        # Если фото не найдено, отправляем просто текст с красивым форматированием
        await message.answer(f"<b>🖥 {caption.split(chr(10))[0] if caption.split(chr(10)) else 'Информация'}</b>\n\n{caption}", 
                           reply_markup=reply_markup, parse_mode="HTML")
        return False

# ========== ФУНКЦИЯ ПРОВЕРКИ ПОДПИСКИ ==========
async def check_subscription(user_id: int, bot: Bot) -> bool:
    """Проверяет, подписан ли пользователь на канал"""
    try:
        # Для администраторов всегда разрешаем доступ
        if user_id in ADMIN_IDS:
            return True
            
        # Пробуем получить информацию о подписке
        try:
            member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
            # Проверяем статус участника
            if member.status in ['member', 'administrator', 'creator']:
                return True
        except Exception as e:
            logger.error(f"Ошибка проверки подписки для {user_id}: {e}")
            
        return False
    except Exception as e:
        logger.error(f"Ошибка в функции проверки подписки: {e}")
        return False

# ========== ФУНКЦИЯ ОТПРАВКИ ЗАПРОСА НА ПОДПИСКУ С ФОТКОЙ ==========
async def send_subscription_request(message: types.Message):
    """Отправляет запрос на подписку на канал с фоткой"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"),
        InlineKeyboardButton("✅ Я подписался", callback_data="check_subscription")
    )
    
    subscription_text = (
        f"<b>🚫 ДОСТУП ЗАКРЫТ</b>\n\n"
        f"Для использования бота необходимо подписаться на наш канал:\n"
        f"<b>{REQUIRED_CHANNEL}</b>\n\n"
        f"После подписки нажмите кнопку <b>'✅ Я подписался'</b> для проверки.\n\n"
        f"<i>Это необходимо для получения обновлений и важных уведомлений.</i>"
    )
    
    # Пробуем отправить с фоткой tgk.jpg
    await send_photo_with_caption(message, "tgk", subscription_text, keyboard)

# ========== ФУНКЦИИ ДЛЯ АНКЕТЫ ==========
def save_questionnaire_to_db(user_id: int, username: str, full_name: str, q1: str, q2: str, q3: str):
    """Сохраняет анкету пользователя в БД"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO user_questionnaires 
            (user_id, username, full_name, q1_answer, q2_answer, q3_answer, status, submitted_at) 
            VALUES (?, ?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
        """, (user_id, username, full_name, q1, q2, q3))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения анкеты: {e}")
        return False

def get_questionnaire_status(user_id: int) -> str:
    """Получает статус анкеты пользователя"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT status FROM user_questionnaires WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return result[0]
        return "not_submitted"
    except Exception as e:
        logger.error(f"Ошибка получения статуса анкеты: {e}")
        return "error"

def get_questionnaire_by_user_id(user_id: int):
    """Получает анкету пользователя по ID"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT user_id, username, full_name, q1_answer, q2_answer, q3_answer, 
                   status, submitted_at, reviewed_by, reviewed_at 
            FROM user_questionnaires 
            WHERE user_id = ?
        """, (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            columns = ['user_id', 'username', 'full_name', 'q1_answer', 'q2_answer', 
                      'q3_answer', 'status', 'submitted_at', 'reviewed_by', 'reviewed_at']
            return dict(zip(columns, result))
        return None
    except Exception as e:
        logger.error(f"Ошибка получения анкеты: {e}")
        return None

def get_questionnaires_by_status(status: str, limit: int = 50):
    """Получает анкеты по статусу"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT user_id, username, full_name, q1_answer, q2_answer, q3_answer, 
                   status, submitted_at, reviewed_by, reviewed_at 
            FROM user_questionnaires 
            WHERE status = ? 
            ORDER BY submitted_at DESC
            LIMIT {limit}
        """, (status,))
        
        questionnaires = cursor.fetchall()
        conn.close()
        
        results = []
        for row in questionnaires:
            columns = ['user_id', 'username', 'full_name', 'q1_answer', 'q2_answer', 
                      'q3_answer', 'status', 'submitted_at', 'reviewed_by', 'reviewed_at']
            results.append(dict(zip(columns, row)))
        
        return results
    except Exception as e:
        logger.error(f"Ошибка получения анкет по статусу {status}: {e}")
        return []

def get_all_questionnaires(limit: int = 100):
    """Получает все анкеты"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT user_id, username, full_name, q1_answer, q2_answer, q3_answer, 
                   status, submitted_at, reviewed_by, reviewed_at 
            FROM user_questionnaires 
            ORDER BY submitted_at DESC
            LIMIT {limit}
        """)
        
        questionnaires = cursor.fetchall()
        conn.close()
        
        results = []
        for row in questionnaires:
            columns = ['user_id', 'username', 'full_name', 'q1_answer', 'q2_answer', 
                      'q3_answer', 'status', 'submitted_at', 'reviewed_by', 'reviewed_at']
            results.append(dict(zip(columns, row)))
        
        return results
    except Exception as e:
        logger.error(f"Ошибка получения всех анкет: {e}")
        return []

def get_questionnaire_stats():
    """Получает статистику по анкетам"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM user_questionnaires")
        total = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM user_questionnaires WHERE status = 'pending'")
        pending = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM user_questionnaires WHERE status = 'approved'")
        approved = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM user_questionnaires WHERE status = 'rejected'")
        rejected = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM user_questionnaires")
        unique_users = cursor.fetchone()[0] or 0
        
        # Самые активные дни
        cursor.execute("""
            SELECT DATE(submitted_at) as date, COUNT(*) as count 
            FROM user_questionnaires 
            GROUP BY DATE(submitted_at) 
            ORDER BY count DESC 
            LIMIT 5
        """)
        top_days = cursor.fetchall()
        
        conn.close()
        
        return {
            'total': total,
            'pending': pending,
            'approved': approved,
            'rejected': rejected,
            'unique_users': unique_users,
            'top_days': top_days
        }
    except Exception as e:
        logger.error(f"Ошибка получения статистики анкет: {e}")
        return {}

def update_questionnaire_status(user_id: int, status: str, reviewed_by: int = None):
    """Обновляет статус анкеты"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE user_questionnaires 
            SET status = ?, reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP 
            WHERE user_id = ?
        """, (status, reviewed_by, user_id))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления статуса анкеты: {e}")
        return False

def delete_questionnaire(user_id: int):
    """Удаляет анкету пользователя"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM user_questionnaires WHERE user_id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления анкеты: {e}")
        return False

def export_questionnaires_to_csv(filename: str = "questionnaires_export.csv"):
    """Экспортирует все анкеты в CSV файл"""
    try:
        questionnaires = get_all_questionnaires(limit=1000)
        
        if not questionnaires:
            return False, "Нет анкет для экспорта"
        
        # Создаем папку exports если нет
        os.makedirs("exports", exist_ok=True)
        filepath = os.path.join("exports", filename)
        
        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['user_id', 'username', 'full_name', 'q1_answer', 'q2_answer', 
                         'q3_answer', 'status', 'submitted_at', 'reviewed_by', 'reviewed_at']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            writer.writeheader()
            for q in questionnaires:
                writer.writerow(q)
        
        return True, filepath
    except Exception as e:
        logger.error(f"Ошибка экспорта анкет: {e}")
        return False, str(e)

# ========== ФУНКЦИИ ДЛЯ ЗАПУСКА БОТОВ ==========
def start_bot_process(bot_id: int, owner_id: int, bot_filename: str) -> Tuple[bool, str]:
    """Запускает процесс бота в отдельном процессе"""
    try:
        logger.info(f"🚀 Запускаю бота #{bot_id}: {bot_filename}")
        
        # Проверяем существование файла
        if not os.path.exists(bot_filename):
            return False, f"Файл {bot_filename} не найден"
        
        # Создаем уникальное имя процесса
        process_name = f"Bot#{bot_id}_{int(time.time())}"
        
        # Запускаем бота как отдельный процесс
        process = subprocess.Popen(
            [sys.executable, bot_filename],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        running_processes[bot_id] = {
            'process': process,
            'filename': bot_filename,
            'started_at': datetime.now(),
            'owner_id': owner_id,
            'pid': process.pid,
            'name': process_name,
            'status': 'running'
        }
        
        # Запускаем мониторинг вывода процесса
        threading.Thread(target=log_process_output, args=(bot_id, process), daemon=True).start()
        
        logger.info(f"✅ Бот #{bot_id} запущен (PID: {process.pid}, Имя: {process_name})")
        return True, f"✅ Бот #{bot_id} успешно запущен!"
        
    except Exception as e:
        error_msg = f"Ошибка запуска бота #{bot_id}: {str(e)}"
        logger.error(f"❌ {error_msg}")
        return False, f"❌ {error_msg}"

def stop_bot_process(bot_id: int) -> Tuple[bool, str]:
    """Останавливает процесс бота"""
    try:
        if bot_id not in running_processes:
            return False, f"Бот #{bot_id} не запущен"
        
        process_info = running_processes[bot_id]
        process = process_info['process']
        
        # Пробуем завершить корректно
        process.terminate()
        
        # Ждем завершения
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Принудительно завершаем
            process.kill()
            process.wait()
        
        # Удаляем из списка запущенных процессов
        del running_processes[bot_id]
        
        logger.info(f"🛑 Бот #{bot_id} остановлен (PID: {process_info['pid']})")
        return True, f"✅ Бот #{bot_id} успешно остановлен!"
        
    except Exception as e:
        error_msg = f"Ошибка остановки бота #{bot_id}: {str(e)}"
        logger.error(f"❌ {error_msg}")
        return False, f"❌ {error_msg}"

def restart_bot_process(bot_id: int) -> Tuple[bool, str]:
    """Перезапускает процесс бота"""
    try:
        # Останавливаем если запущен
        if bot_id in running_processes:
            stop_success, stop_msg = stop_bot_process(bot_id)
            if not stop_success:
                return False, f"Не удалось остановить бота: {stop_msg}"
            time.sleep(2)  # Пауза между остановкой и запуском
        
        # Получаем информацию о боте из БД
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        cursor.execute("SELECT owner_id, bot_filename FROM bots WHERE id = ?", (bot_id,))
        bot_data = cursor.fetchone()
        conn.close()
        
        if not bot_data:
            return False, f"Бот #{bot_id} не найден в БД"
        
        owner_id, bot_filename = bot_data
        
        # Запускаем заново
        return start_bot_process(bot_id, owner_id, bot_filename)
        
    except Exception as e:
        error_msg = f"Ошибка перезапуска бота #{bot_id}: {str(e)}"
        logger.error(f"❌ {error_msg}")
        return False, f"❌ {error_msg}"

def log_process_output(bot_id: int, process):
    """Логирует вывод процесса"""
    try:
        for line in process.stdout:
            if line.strip():
                logger.info(f"🤖 Бот #{bot_id}: {line.strip()}")
    except Exception as e:
        logger.error(f"Ошибка логирования вывода бота #{bot_id}: {e}")

def get_bot_info(bot_id: int):
    """Получает информацию о боте"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, bot_name, owner_id, status, template_name, bot_filename, 
                   created_at, last_started 
            FROM bots WHERE id = ?
        """, (bot_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            columns = ['id', 'bot_name', 'owner_id', 'status', 'template_name', 
                      'bot_filename', 'created_at', 'last_started']
            return dict(zip(columns, result))
        return None
    except Exception as e:
        logger.error(f"Ошибка получения информации о боте #{bot_id}: {e}")
        return None

def get_user_bots(user_id: int):
    """Получает боты пользователя"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, bot_name, status, owner_id, template_name, created_at 
            FROM bots WHERE owner_id = ? 
            ORDER BY id DESC
        """, (user_id,))
        
        bots = cursor.fetchall()
        conn.close()
        
        results = []
        for row in bots:
            columns = ['id', 'bot_name', 'status', 'owner_id', 'template_name', 'created_at']
            results.append(dict(zip(columns, row)))
        
        return results
    except Exception as e:
        logger.error(f"Ошибка получения ботов пользователя {user_id}: {e}")
        return []

def update_bot_status(bot_id: int, status: str):
    """Обновляет статус бота в БД"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        if status == 'running':
            cursor.execute("""
                UPDATE bots 
                SET status = ?, last_started = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (status, bot_id))
        else:
            cursor.execute("""
                UPDATE bots SET status = ? WHERE id = ?
            """, (status, bot_id))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления статуса бота #{bot_id}: {e}")
        return False

def delete_bot(bot_id: int) -> Tuple[bool, str]:
    """Удаляет бота"""
    try:
        # Останавливаем если запущен
        if bot_id in running_processes:
            stop_bot_process(bot_id)
        
        # Получаем информацию о файле
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT bot_filename FROM bots WHERE id = ?", (bot_id,))
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            return False, f"Бот #{bot_id} не найден в БД"
        
        bot_filename = result[0]
        
        # Удаляем запись из БД
        cursor.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
        conn.commit()
        conn.close()
        
        # Пробуем удалить файл
        try:
            if os.path.exists(bot_filename):
                os.remove(bot_filename)
                return True, f"✅ Бот #{bot_id} удален (файл удален)"
            else:
                return True, f"✅ Бот #{bot_id} удален (файл не найден)"
        except Exception as file_error:
            return True, f"✅ Бот #{bot_id} удален (ошибка удаления файла: {file_error})"
            
    except Exception as e:
        logger.error(f"Ошибка удаления бота #{bot_id}: {e}")
        return False, f"❌ Ошибка удаления: {str(e)}"

def get_all_bots(limit: int = 100):
    """Получает всех ботов"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT id, bot_name, owner_id, status, template_name, created_at 
            FROM bots 
            ORDER BY id DESC
            LIMIT {limit}
        """)
        
        bots = cursor.fetchall()
        conn.close()
        
        results = []
        for row in bots:
            columns = ['id', 'bot_name', 'owner_id', 'status', 'template_name', 'created_at']
            results.append(dict(zip(columns, row)))
        
        return results
    except Exception as e:
        logger.error(f"Ошибка получения всех ботов: {e}")
        return []

def get_system_stats():
    """Получает статистику системы"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM bots")
        total_bots = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM bots WHERE status = 'running'")
        running_bots = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM user_questionnaires")
        total_quests = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM user_questionnaires WHERE status = 'pending'")
        pending_quests = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM bot_logs")
        total_logs = cursor.fetchone()[0] or 0
        
        conn.close()
        
        # Использование памяти
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        
        # Использование CPU
        cpu_percent = psutil.cpu_percent(interval=0.1)
        
        # Дисковое пространство
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        
        return {
            'total_bots': total_bots,
            'running_bots': running_bots,
            'total_quests': total_quests,
            'pending_quests': pending_quests,
            'total_logs': total_logs,
            'memory_percent': memory_percent,
            'cpu_percent': cpu_percent,
            'disk_percent': disk_percent,
            'running_processes': len(running_processes)
        }
    except Exception as e:
        logger.error(f"Ошибка получения статистики системы: {e}")
        return {}

# ========== ФУНКЦИИ ДЛЯ ШАБЛОНОВ БОТОВ ==========
def get_available_templates():
    """Получает список доступных шаблонов ботов"""
    templates = []
    
    # Сначала проверяем папку templates
    if os.path.exists(TEMPLATES_FOLDER):
        for file in os.listdir(TEMPLATES_FOLDER):
            if file.endswith('.py'):
                template_name = file.replace('.py', '')
                templates.append({
                    'name': template_name,
                    'path': os.path.join(TEMPLATES_FOLDER, file),
                    'type': 'template'
                })
    
    # Затем проверяем папку bots (готовые боты как шаблоны)
    if os.path.exists(BOTS_FOLDER):
        for file in os.listdir(BOTS_FOLDER):
            if file.endswith('.py'):
                template_name = file.replace('.py', '')
                templates.append({
                    'name': f"🤖 {template_name}",
                    'path': os.path.join(BOTS_FOLDER, file),
                    'type': 'bot'
                })
    
    # Если нет шаблонов, создаем стандартный
    if not templates:
        create_default_template()
        templates = [{
            'name': 'Стандартный',
            'path': os.path.join(TEMPLATES_FOLDER, 'standard.py'),
            'type': 'template'
        }]
    
    return templates

def create_default_template():
    """Создает стандартный шаблон бота"""
    template_code = '''import logging
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import Dispatcher
from aiogram.utils import executor

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Токен бота будет вставлен автоматически
TOKEN = "{token}"

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

@dp.message_handler(commands=['start', 'help'])
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    await message.answer(
        "🤖 <b>Бот успешно запущен!</b>\\n\\n"
        "🔹 Этот бот создан через Bot Manager CyberNet\\n"
        "🔹 Владелец: {owner_id}\\n"
        "🔹 Версия: 1.0",
        parse_mode="HTML"
    )

@dp.message_handler(commands=['id'])
async def cmd_id(message: types.Message):
    """Показывает ID пользователя"""
    await message.answer(
        f"🆔 <b>Ваш ID:</b> <code>{message.from_user.id}</code>",
        parse_mode="HTML"
    )

@dp.message_handler()
async def echo_message(message: types.Message):
    """Эхо-обработчик"""
    await message.answer(f"📨 Вы написали: {message.text}")

if __name__ == "__main__":
    logger.info("🚀 Бот запускается...")
    executor.start_polling(dp, skip_updates=True)
'''
    
    os.makedirs(TEMPLATES_FOLDER, exist_ok=True)
    with open(os.path.join(TEMPLATES_FOLDER, 'standard.py'), 'w', encoding='utf-8') as f:
        f.write(template_code)

def create_bot_from_template(bot_token: str, owner_id: int, bot_name: str, template_path: str) -> Tuple[bool, str]:
    """Создает бота из шаблона"""
    try:
        # Читаем шаблон
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()
        
        # Заменяем переменные
        bot_code = template_content.replace("{token}", bot_token).replace("{owner_id}", str(owner_id))
        
        # Создаем файл бота в папке bots
        bot_filename = os.path.join(BOTS_FOLDER, f"{bot_name}.py")
        
        with open(bot_filename, 'w', encoding='utf-8') as f:
            f.write(bot_code)
        
        return True, bot_filename
    except Exception as e:
        return False, str(e)

# ========== ФУНКЦИИ ДЛЯ НАСТРОЕК ==========
def get_settings():
    """Получает настройки из БД"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        settings = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        return settings
    except Exception as e:
        logger.error(f"Ошибка получения настроек: {e}")
        return {}

def save_setting(key: str, value: str):
    """Сохраняет настройку в БД"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, value)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения настройки: {e}")
        return False

# ========== БАЗА ДАННЫХ ==========
def init_database():
    """Инициализация базы данных"""
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        # Создаем таблицу bots если не существует
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bots'")
        if not cursor.fetchone():
            logger.info("📊 Создаю таблицу bots...")
            cursor.execute('''
                CREATE TABLE bots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_token TEXT UNIQUE NOT NULL,
                    bot_name TEXT,
                    owner_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'stopped',
                    bot_config TEXT DEFAULT '{}',
                    template_name TEXT DEFAULT 'custom',
                    bot_filename TEXT,
                    last_started TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        # Создаем таблицу bot_logs если не существует
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bot_logs'")
        if not cursor.fetchone():
            logger.info("📊 Создаю таблицу bot_logs...")
            cursor.execute('''
                CREATE TABLE bot_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id INTEGER,
                    log_level TEXT,
                    message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (bot_id) REFERENCES bots (id)
                )
            ''')
        
        # Создаем таблицу bot_stats если не существует
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bot_stats'")
        if not cursor.fetchone():
            logger.info("📊 Создаю таблицу bot_stats...")
            cursor.execute('''
                CREATE TABLE bot_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id INTEGER,
                    users_count INTEGER DEFAULT 0,
                    codes_sent INTEGER DEFAULT 0,
                    last_activity TIMESTAMP,
                    FOREIGN KEY (bot_id) REFERENCES bots (id)
                )
            ''')
        
        # Создаем таблицу settings если не существует
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
        if not cursor.fetchone():
            logger.info("📊 Создаю таблицу settings...")
            cursor.execute('''
                CREATE TABLE settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Добавляем настройки по умолчанию
            default_settings = [
                ('theme', 'dark'),
                ('auto_start', 'false'),
                ('notifications', 'true'),
                ('backup_days', '7'),
                ('max_bots', '50'),
                ('language', 'ru'),
                ('timezone', 'Europe/Moscow')
            ]
            
            for key, value in default_settings:
                cursor.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, value)
                )
        
        # Создаем таблицу для анкет пользователей
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_questionnaires'")
        if not cursor.fetchone():
            logger.info("📊 Создаю таблицу user_questionnaires...")
            cursor.execute('''
                CREATE TABLE user_questionnaires (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    q1_answer TEXT,
                    q2_answer TEXT,
                    q3_answer TEXT,
                    status TEXT DEFAULT 'pending',
                    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_by INTEGER,
                    reviewed_at TIMESTAMP
                )
            ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")

init_database()

# ========== ОСНОВНОЙ БОТ-МЕНЕДЖЕР ==========
storage = MemoryStorage()
bot = Bot(token=MANAGER_TOKEN)
dp = Dispatcher(bot, storage=storage)

# ========== КЛАВИАТУРЫ ==========
def create_main_menu_keyboard():
    """Создает главное меню"""
    keyboard = ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )
    
    keyboard.row(
        KeyboardButton("🖥 Панель управления"),
        KeyboardButton("⚙️ Мои боты")
    )
    
    keyboard.row(
        KeyboardButton("➕ Создать бота"),
        KeyboardButton("⚡ Управление ботами")
    )
    
    keyboard.row(
        KeyboardButton("📊 Аналитика"),
        KeyboardButton("🔧 Инструменты")
    )
    
    keyboard.row(
        KeyboardButton("🎛 Настройки"),
        KeyboardButton("📦 Бекапы")
    )
    
    # Добавляем кнопку управления анкетами для админов
    keyboard.row(KeyboardButton("📋 Управление анкетами"))
    
    return keyboard

def create_admin_keyboard():
    """Клавиатура администратора"""
    keyboard = ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )
    
    keyboard.row(
        KeyboardButton("📋 Управление анкетами"),
        KeyboardButton("👑 Админ панель")
    )
    
    keyboard.row(
        KeyboardButton("📊 Статистика анкет"),
        KeyboardButton("📁 Экспорт анкет")
    )
    
    keyboard.row(
        KeyboardButton("🔄 Обновить базу"),
        KeyboardButton("🧹 Очистить данные")
    )
    
    keyboard.row(KeyboardButton("⬅️ Главное меню"))
    
    return keyboard

def create_questionnaire_keyboard():
    """Клавиатура для заполнения анкеты"""
    keyboard = ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )
    
    keyboard.row(
        KeyboardButton("📝 Заполнить анкету"),
        KeyboardButton("❓ Зачем нужна анкета?")
    )
    
    keyboard.row(KeyboardButton("⬅️ Назад"))
    
    return keyboard

def create_questionnaires_admin_keyboard():
    """Клавиатура для управления анкетами (админ)"""
    keyboard = InlineKeyboardMarkup(row_width=3)
    
    # Статистика по статусам
    stats = get_questionnaire_stats()
    
    keyboard.row(
        InlineKeyboardButton(f"📭 Входящие ({stats.get('pending', 0)})", 
                           callback_data="quest_status_pending"),
        InlineKeyboardButton(f"✅ Одобренные ({stats.get('approved', 0)})", 
                           callback_data="quest_status_approved"),
        InlineKeyboardButton(f"❌ Отклоненные ({stats.get('rejected', 0)})", 
                           callback_data="quest_status_rejected")
    )
    
    keyboard.row(
        InlineKeyboardButton(f"📊 Все анкеты ({stats.get('total', 0)})", 
                           callback_data="quest_status_all"),
        InlineKeyboardButton("📈 Статистика", callback_data="quest_stats")
    )
    
    keyboard.row(
        InlineKeyboardButton("📁 Экспорт в CSV", callback_data="quest_export_csv"),
        InlineKeyboardButton("🔄 Обновить", callback_data="quest_refresh")
    )
    
    keyboard.row(InlineKeyboardButton("❌ Закрыть", callback_data="quest_close"))
    
    return keyboard

def create_bot_detail_keyboard(bot_id: int):
    """Клавиатура для детального управления ботом"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    keyboard.row(
        InlineKeyboardButton("🚀 Запустить", callback_data=f"bot_start_{bot_id}"),
        InlineKeyboardButton("🛑 Остановить", callback_data=f"bot_stop_{bot_id}")
    )
    
    keyboard.row(
        InlineKeyboardButton("🔄 Перезапустить", callback_data=f"bot_restart_{bot_id}"),
        InlineKeyboardButton("📊 Статистика", callback_data=f"bot_stats_{bot_id}")
    )
    
    keyboard.row(
        InlineKeyboardButton("📝 Просмотреть логи", callback_data=f"bot_logs_{bot_id}"),
        InlineKeyboardButton("⚙️ Настройки", callback_data=f"bot_settings_{bot_id}")
    )
    
    keyboard.row(
        InlineKeyboardButton("🗑 Удалить бота", callback_data=f"bot_delete_{bot_id}"),
        InlineKeyboardButton("✏️ Переименовать", callback_data=f"bot_rename_{bot_id}")
    )
    
    keyboard.row(
        InlineKeyboardButton("⬅️ Назад к списку", callback_data="bots_back"),
        InlineKeyboardButton("📋 Главное меню", callback_data="bots_main")
    )
    
    return keyboard

def create_bot_list_keyboard(bots_data, current_page=0, per_page=5):
    """Клавиатура для списка ботов с пагинацией"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    # Рассчитываем срез для текущей страницы
    start_idx = current_page * per_page
    end_idx = start_idx + per_page
    page_bots = bots_data[start_idx:end_idx]
    
    # Добавляем ботов текущей страницы
    for bot_info in page_bots:
        bot_id = bot_info['id']
        bot_name = bot_info['bot_name'][:15] if bot_info['bot_name'] else f"Бот #{bot_id}"
        is_running = bot_id in running_processes
        status_icon = "✅" if is_running else "❌"
        
        keyboard.add(
            InlineKeyboardButton(
                f"{status_icon} {bot_name}",
                callback_data=f"bot_view_{bot_id}"
            )
        )
    
    # Добавляем пагинацию если нужно
    total_pages = (len(bots_data) + per_page - 1) // per_page
    
    if total_pages > 1:
        pagination_row = []
        
        if current_page > 0:
            pagination_row.append(
                InlineKeyboardButton("⬅️ Назад", callback_data=f"bots_page_{current_page-1}")
            )
        
        pagination_row.append(
            InlineKeyboardButton(f"📄 {current_page+1}/{total_pages}", callback_data="bots_page_current")
        )
        
        if current_page < total_pages - 1:
            pagination_row.append(
                InlineKeyboardButton("Вперед ➡️", callback_data=f"bots_page_{current_page+1}")
            )
        
        keyboard.row(*pagination_row)
    
    # Кнопки действий
    keyboard.row(
        InlineKeyboardButton("➕ Новый бот", callback_data="create_bot"),
        InlineKeyboardButton("🔄 Обновить", callback_data="bots_refresh")
    )
    
    keyboard.row(
        InlineKeyboardButton("🚀 Запустить всех", callback_data="start_all_bots"),
        InlineKeyboardButton("🛑 Остановить всех", callback_data="stop_all_bots")
    )
    
    keyboard.row(InlineKeyboardButton("❌ Закрыть", callback_data="bots_close"))
    
    return keyboard

def create_analytics_keyboard():
    """Клавиатура для раздела аналитики"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    keyboard.row(
        InlineKeyboardButton("📊 Статистика системы", callback_data="analytics_system"),
        InlineKeyboardButton("🤖 Статистика ботов", callback_data="analytics_bots")
    )
    
    keyboard.row(
        InlineKeyboardButton("👤 Статистика пользователей", callback_data="analytics_users"),
        InlineKeyboardButton("📈 Графики", callback_data="analytics_graphs")
    )
    
    keyboard.row(
        InlineKeyboardButton("📅 За период", callback_data="analytics_period"),
        InlineKeyboardButton("📋 Отчет", callback_data="analytics_report")
    )
    
    keyboard.row(
        InlineKeyboardButton("🔄 Обновить", callback_data="analytics_refresh"),
        InlineKeyboardButton("❌ Закрыть", callback_data="analytics_close")
    )
    
    return keyboard

def create_manage_bots_keyboard():
    """Клавиатура для управления ботами"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    keyboard.row(
        InlineKeyboardButton("🚀 Запустить всех", callback_data="start_all_bots"),
        InlineKeyboardButton("🛑 Остановить всех", callback_data="stop_all_bots")
    )
    
    keyboard.row(
        InlineKeyboardButton("🔄 Перезапустить всех", callback_data="restart_all_bots"),
        InlineKeyboardButton("📊 Мониторинг", callback_data="monitoring_bots")
    )
    
    keyboard.row(
        InlineKeyboardButton("📝 Просмотреть логи", callback_data="view_all_logs"),
        InlineKeyboardButton("⚙️ Настройки всех", callback_data="settings_all_bots")
    )
    
    keyboard.row(
        InlineKeyboardButton("🗑 Удалить неактивные", callback_data="delete_inactive"),
        InlineKeyboardButton("🧹 Очистить логи", callback_data="clear_logs")
    )
    
    keyboard.row(InlineKeyboardButton("❌ Закрыть", callback_data="manage_close"))
    
    return keyboard

def create_tools_keyboard():
    """Клавиатура для раздела инструментов"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    keyboard.row(
        KeyboardButton("💻 Система"),
        KeyboardButton("📊 Мониторинг")
    )
    
    keyboard.row(
        KeyboardButton("🗃 База данных"),
        KeyboardButton("🛠 Тех. обслуживание")
    )
    
    keyboard.row(
        KeyboardButton("🧹 Очистка системы"),
        KeyboardButton("🔍 Диагностика")
    )
    
    keyboard.row(KeyboardButton("⬅️ Назад в меню"))
    
    return keyboard

def create_settings_keyboard():
    """Клавиатура для настроек"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    # Получаем текущие настройки
    settings = get_settings()
    theme_icon = "🌙" if settings.get('theme') == 'dark' else "☀️"
    notifications_icon = "🔔" if settings.get('notifications') == 'true' else "🔕"
    auto_start_icon = "✅" if settings.get('auto_start') == 'true' else "❌"
    
    keyboard.row(
        InlineKeyboardButton(f"{theme_icon} Тема: {'Темная' if settings.get('theme') == 'dark' else 'Светлая'}", 
                           callback_data="setting_theme"),
        InlineKeyboardButton(f"{notifications_icon} Уведомления: {'Вкл' if settings.get('notifications') == 'true' else 'Выкл'}", 
                           callback_data="setting_notifications")
    )
    
    keyboard.row(
        InlineKeyboardButton(f"{auto_start_icon} Автозапуск", callback_data="setting_autostart"),
        InlineKeyboardButton(f"🗣️ Язык: {settings.get('language', 'ru')}", callback_data="setting_language")
    )
    
    keyboard.row(
        InlineKeyboardButton("⏱️ Часовой пояс", callback_data="setting_timezone"),
        InlineKeyboardButton("🗑️ Очистить данные", callback_data="setting_clear_data")
    )
    
    keyboard.row(
        InlineKeyboardButton("🔄 Сбросить настройки", callback_data="setting_reset"),
        InlineKeyboardButton("💾 Экспорт настроек", callback_data="setting_export")
    )
    
    keyboard.row(InlineKeyboardButton("❌ Закрыть", callback_data="close_settings"))
    
    return keyboard

def create_questionnaire_question1_keyboard():
    """Клавиатура для первого вопроса анкеты"""
    keyboard = ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )
    
    keyboard.row(
        KeyboardButton("Да, регулярно"),
        KeyboardButton("Иногда")
    )
    
    keyboard.row(
        KeyboardButton("Планирую начать"),
        KeyboardButton("Нет, я новичок")
    )
    
    keyboard.row(KeyboardButton("⬅️ Назад"))
    
    return keyboard

def create_questionnaire_question2_keyboard():
    """Клавиатура для второго вопроса анкеты"""
    keyboard = ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )
    
    keyboard.row(
        KeyboardButton("Информационный"),
        KeyboardButton("Торговый")
    )
    
    keyboard.row(
        KeyboardButton("Развлекательный"),
        KeyboardButton("Сервисный")
    )
    
    keyboard.row(KeyboardButton("⬅️ Назад"))
    
    return keyboard

def create_questionnaire_question3_keyboard():
    """Клавиатура для третьего вопроса анкеты"""
    keyboard = ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )
    
    keyboard.row(
        KeyboardButton("Просто попробовать"),
        KeyboardButton("Для бизнеса")
    )
    
    keyboard.row(
        KeyboardButton("Для общения"),
        KeyboardButton("Для обучения")
    )
    
    keyboard.row(KeyboardButton("⬅️ Назад"))
    
    return keyboard

# ========== ОБРАБОТЧИК КОМАНД ==========
@dp.message_handler(commands=['start', 'menu', 'help', 'главная'])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Нет username"
    full_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()
    
    # Проверяем подписку
    is_subscribed = await check_subscription(user_id, bot)
    
    if not is_subscribed and user_id not in ADMIN_IDS:
        await send_subscription_request(message)
        return
    
    # Для администраторов показываем админскую клавиатуру
    if user_id in ADMIN_IDS:
        welcome_text = (
            f"🔷 <b>Bot Manager CyberNet</b> 🔷\n\n"
            f"Добро пожаловать, <b>Администратор</b>!\n\n"
            
            f"<b>👑 Ваши права:</b>\n"
            f"• 📋 Управление анкетами пользователей\n"
            f"• 👁 Просмотр всех ботов\n"
            f"• ⚙️ Полный доступ к настройкам\n"
            f"• 📊 Статистика системы\n\n"
            
            f"<b>📈 Статистика:</b>\n"
            f"• 🖥 Ботов в системе: <b>{len(running_processes)} активных</b>\n"
            f"• ⏱ Время работы: <b>{datetime.now().strftime('%H:%M:%S')}</b>\n"
            f"• 👤 Ваш ID: <code>{user_id}</code>\n\n"
            
            f"<i>👇 Используйте кнопки ниже для навигации:</i>"
        )
        
        await send_photo_with_caption(message, "admin_menu", welcome_text, create_admin_keyboard())
        return
    
    # Проверяем статус анкеты для обычных пользователей
    questionnaire_status = get_questionnaire_status(user_id)
    
    if questionnaire_status == "not_submitted":
        # Показываем меню с анкетой с фоткой
        welcome_text = (
            f"🔷 <b>Bot Manager CyberNet</b> 🔷\n\n"
            f"Добро пожаловать, <b>{full_name or 'Пользователь'}</b>!\n\n"
            f"<i>Для доступа к функции менеджера необходимо заполнить анкету.</i>\n\n"
            f"<b>📋 Требования:</b>\n"
            f"• ✅ Подписка на канал <b>{REQUIRED_CHANNEL}</b>\n"
            f"• 📝 Заполненная анкета (3 вопроса)\n"
            f"• ⏱ Ожидание проверки модератором\n\n"
            f"<b>Статус:</b>\n"
            f"• 📢 Подписка: ✅ <b>АКТИВНА</b>\n"
            f"• 📝 Анкета: ❌ <b>НЕ ЗАПОЛНЕНА</b>\n\n"
            f"<i>👇 Заполните анкету для продолжения:</i>"
        )
        
        await send_photo_with_caption(message, "send_anket", welcome_text, create_questionnaire_keyboard())
        return
        
    elif questionnaire_status == "pending":
        # Анкета на рассмотрении с фоткой
        pending_text = (
            f"🔷 <b>Bot Manager CyberNet</b> 🔷\n\n"
            f"Добро пожаловать, <b>{full_name or 'Пользователь'}</b>!\n\n"
            f"<b>📊 Ваша анкета находится на рассмотрении</b>\n\n"
            f"<b>Статус:</b>\n"
            f"• 📢 Подписка: ✅ <b>АКТИВНА</b>\n"
            f"• 📝 Анкета: ⏳ <b>НА РАССМОТРЕНИИ</b>\n\n"
            f"<i>Пожалуйста, ожидайте проверки модератором.</i>\n"
            f"<i>Обычно это занимает до 24 часов.</i>\n\n"
            f"<b>🔷 Спасибо за понимание!</b>"
        )
        
        await send_photo_with_caption(message, "pending", pending_text)
        return
        
    elif questionnaire_status == "rejected":
        # Анкета отклонена с фоткой
        rejected_text = (
            f"🔷 <b>Bot Manager CyberNet</b> 🔷\n\n"
            f"Добро пожаловать, <b>{full_name or 'Пользователь'}</b>!\n\n"
            f"<b>🚫 ВАША АНКЕТА ОТКЛОНЕНА</b>\n\n"
            f"<b>Статус:</b>\n"
            f"• 📢 Подписка: ✅ <b>АКТИВНА</b>\n"
            f"• 📝 Анкета: ❌ <b>ОТКЛОНЕНА</b>\n\n"
            f"<i>К сожалению, ваша анкета не прошла проверку.</i>\n"
            f"<i>Вы можете заполнить её заново, нажав кнопку ниже:</i>"
        )
        
        keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.row(KeyboardButton("📝 Заполнить анкету заново"))
        keyboard.row(KeyboardButton("⬅️ Назад"))
        
        await send_photo_with_caption(message, "rejected", rejected_text, keyboard)
        return
    
    elif questionnaire_status == "approved":
        # Если пользователь прошел проверки, показываем главное меню
        welcome_text = (
            f"🔷 <b>Bot Manager CyberNet</b> 🔷\n\n"
            f"Добро пожаловать, <b>{full_name or 'Пользователь'}</b>!\n\n"
            
            f"<b>🎯 Ключевые возможности:</b>\n"
            f"• 🚀 Умный запуск ботов\n"
            f"• 📊 Детальная аналитика\n"
            f"• 🛡 Автоматические бекапы\n"
            f"• ⚡ Мониторинг производительности\n"
            f"• 🖥 Продвинутый интерфейс\n\n"
            
            f"<b>📈 Статистика:</b>\n"
            f"• 🖥 Ботов в системе: <b>{len(running_processes)} активных</b>\n"
            f"• ⏱ Время работы: <b>{datetime.now().strftime('%H:%M:%S')}</b>\n"
            f"• 🛡 Ваш статус: <b>Проверенный пользователь</b>\n\n"
            
            f"<i>👇 Используйте кнопки ниже для навигации:</i>"
        )
        
        await send_photo_with_caption(message, "main_menu", welcome_text, create_main_menu_keyboard())

# ========== ОБРАБОТЧИКИ ДЛЯ КНОПОК МЕНЮ ==========
@dp.message_handler(lambda m: m.text == "⚙️ Мои боты")
async def my_bots_handler(message: types.Message):
    """Обработчик кнопки Мои боты"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        # Проверяем доступ для обычных пользователей
        is_subscribed = await check_subscription(user_id, bot)
        if not is_subscribed:
            await send_subscription_request(message)
            return
        
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await message.answer("⏳ Ваша анкета еще не одобрена. Пожалуйста, подождите.")
            return
    
    try:
        # Получаем боты пользователя
        if user_id in ADMIN_IDS:
            # Админ видит все боты
            bots_data = get_all_bots(limit=100)
        else:
            # Обычный пользователь видит только свои боты
            bots_data = get_user_bots(user_id)
        
        if not bots_data:
            await send_photo_with_caption(message, "no_bots", 
                f"📭 <b>У ВАС НЕТ БОТОВ</b> 📭\n\n"
                f"<i>Создайте первого бота, нажав кнопку '➕ Создать бота'</i>\n\n"
                f"<b>Как создать бота:</b>\n"
                f"1. Нажмите '➕ Создать бота'\n"
                f"2. Получите токен у @BotFather\n"
                f"3. Следуйте инструкциям\n\n"
                f"<b>🎯 Преимущества создания бота:</b>\n"
                f"• Автоматическое управление\n"
                f"• Мониторинг производительности\n"
                f"• Бекапы и безопасность\n"
                f"• Аналитика и статистика")
            return
        
        # Статистика
        total_bots = len(bots_data)
        running_bots = sum(1 for bot_info in bots_data if bot_info['id'] in running_processes)
        
        bot_list_text = f"⚙️ <b>ВАШИ БОТЫ</b> ⚙️\n\n"
        
        bot_list_text += f"<b>📊 СТАТИСТИКА:</b>\n"
        bot_list_text += f"┌ Всего ботов: <b>{total_bots}</b>\n"
        bot_list_text += f"├ Запущено: <b>{running_bots}</b> ✅\n"
        bot_list_text += f"└ Остановлено: <b>{total_bots - running_bots}</b> ❌\n\n"
        
        bot_list_text += f"<b>📋 СПИСОК БОТОВ:</b>\n"
        
        for i, bot_info in enumerate(bots_data[:5], 1):
            bot_id = bot_info['id']
            bot_name = bot_info['bot_name'] or f"Бот #{bot_id}"
            is_running = bot_id in running_processes
            status_icon = "✅" if is_running else "❌"
            status_text = "ЗАПУЩЕН" if is_running else "ОСТАНОВЛЕН"
            
            bot_list_text += (
                f"<b>{i}. {bot_name}</b>\n"
                f"   ├ 🆔 ID: <code>{bot_id}</code>\n"
                f"   ├ 📊 Статус: {status_icon} {status_text}\n"
                f"   └ 🗂 Шаблон: {bot_info.get('template_name', 'стандартный')}\n\n"
            )
        
        if len(bots_data) > 5:
            bot_list_text += f"<i>...и еще {len(bots_data) - 5} ботов</i>\n\n"
        
        bot_list_text += f"<i>👇 Выберите бота для управления:</i>"
        
        await send_photo_with_caption(message, "my_bots", bot_list_text, create_bot_list_keyboard(bots_data))
        
    except Exception as e:
        logger.error(f"Ошибка получения списка ботов: {e}")
        await message.answer(f"❌ Ошибка: {str(e)[:100]}")

@dp.message_handler(lambda m: m.text == "📊 Аналитика")
async def analytics_handler(message: types.Message):
    """Обработчик кнопки Аналитика"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        # Проверяем доступ для обычных пользователей
        is_subscribed = await check_subscription(user_id, bot)
        if not is_subscribed:
            await send_subscription_request(message)
            return
        
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await message.answer("⏳ Ваша анкета еще не одобрена. Пожалуйста, подождите.")
            return
    
    # Получаем статистику системы
    stats = get_system_stats()
    
    analytics_text = (
        f"📊 <b>АНАЛИТИКА СИСТЕМЫ</b> 📊\n\n"
        
        f"<b>🤖 СТАТИСТИКА БОТОВ:</b>\n"
        f"┌ Всего ботов: <b>{stats.get('total_bots', 0)}</b>\n"
        f"├ Запущено: <b>{stats.get('running_bots', 0)}</b> ✅\n"
        f"├ Остановлено: <b>{stats.get('total_bots', 0) - stats.get('running_bots', 0)}</b> ❌\n"
        f"└ Активных процессов: <b>{stats.get('running_processes', 0)}</b>\n\n"
        
        f"<b>👤 СТАТИСТИКА ПОЛЬЗОВАТЕЛЕЙ:</b>\n"
        f"┌ Всего анкет: <b>{stats.get('total_quests', 0)}</b>\n"
        f"├ Ожидает проверки: <b>{stats.get('pending_quests', 0)}</b>\n"
        f"└ Всего логов: <b>{stats.get('total_logs', 0)}</b>\n\n"
        
        f"<b>💻 СИСТЕМНЫЕ РЕСУРСЫ:</b>\n"
        f"┌ 💾 Память: <b>{stats.get('memory_percent', 0):.1f}%</b>\n"
        f"├ ⚡ CPU: <b>{stats.get('cpu_percent', 0):.1f}%</b>\n"
        f"└ 💿 Диск: <b>{stats.get('disk_percent', 0):.1f}%</b>\n\n"
        
        f"<b>⏱ СЕРВЕРНОЕ ВРЕМЯ:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"<b>🆔 ВАШ ID:</b> {user_id}\n\n"
        
        f"<i>👇 Выберите раздел аналитики:</i>"
    )
    
    await send_photo_with_caption(message, "analytics", analytics_text, create_analytics_keyboard())

@dp.message_handler(lambda m: m.text == "⚡ Управление ботами")
async def manage_bots_handler(message: types.Message):
    """Обработчик кнопки Управление ботами"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        # Проверяем доступ для обычных пользователей
        is_subscribed = await check_subscription(user_id, bot)
        if not is_subscribed:
            await send_subscription_request(message)
            return
        
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await message.answer("⏳ Ваша анкета еще не одобрена. Пожалуйста, подождите.")
            return
    
    # Получаем статистику ботов
    stats = get_system_stats()
    
    manage_text = (
        f"⚡ <b>УПРАВЛЕНИЕ БОТАМИ</b> ⚡\n\n"
        
        f"<b>📊 СТАТИСТИКА:</b>\n"
        f"┌ Всего ботов: <b>{stats.get('total_bots', 0)}</b>\n"
        f"├ Запущено: <b>{stats.get('running_bots', 0)}</b>\n"
        f"└ Остановлено: <b>{stats.get('total_bots', 0) - stats.get('running_bots', 0)}</b>\n\n"
        
        f"<b>🚀 МАССОВЫЕ ДЕЙСТВИЯ:</b>\n"
        f"• Запуск всех ботов одновременно\n"
        f"• Остановка всех ботов\n"
        f"• Перезапуск всех ботов\n"
        f"• Мониторинг производительности\n\n"
        
        f"<b>🛠 ТЕХНИЧЕСКИЕ ДЕЙСТВИЯ:</b>\n"
        f"• Просмотр логов всех ботов\n"
        f"• Очистка старых логов\n"
        f"• Удаление неактивных ботов\n"
        f"• Настройка всех ботов\n\n"
        
        f"<i>⚠️ Внимание: Массовые действия применяются ко всем ботам!</i>\n\n"
        
        f"<i>👇 Выберите действие:</i>"
    )
    
    await send_photo_with_caption(message, "manage_bots", manage_text, create_manage_bots_keyboard())

@dp.message_handler(lambda m: m.text == "🔧 Инструменты")
async def tools_handler(message: types.Message):
    """Обработчик кнопки Инструменты"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        # Проверяем доступ для обычных пользователей
        is_subscribed = await check_subscription(user_id, bot)
        if not is_subscribed:
            await send_subscription_request(message)
            return
        
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await message.answer("⏳ Ваша анкета еще не одобрена. Пожалуйста, подождите.")
            return
    
    tools_text = (
        f"🔧 <b>ИНСТРУМЕНТЫ СИСТЕМЫ</b> 🔧\n\n"
        
        f"<b>🛠 ДОСТУПНЫЕ ИНСТРУМЕНТЫ:</b>\n"
        f"• <b>💻 Система</b> - информация о системе\n"
        f"• <b>📊 Мониторинг</b> - мониторинг ресурсов\n"
        f"• <b>🗃 База данных</b> - управление БД\n"
        f"• <b>🛠 Тех. обслуживание</b> - технические работы\n"
        f"• <b>🧹 Очистка системы</b> - очистка данных\n"
        f"• <b>🔍 Диагностика</b> - диагностика проблем\n\n"
        
        f"<b>🎯 ЦЕЛЬ ИНСТРУМЕНТОВ:</b>\n"
        f"• Оптимизация производительности\n"
        f"• Обеспечение стабильности\n"
        f"• Устранение проблем\n"
        f"• Профилактика сбоев\n\n"
        
        f"<i>⚠️ Используйте инструменты осторожно!</i>\n\n"
        
        f"<i>👇 Выберите инструмент:</i>"
    )
    
    await send_photo_with_caption(message, "tools", tools_text, create_tools_keyboard())

@dp.message_handler(lambda m: m.text == "💻 Система")
async def system_tools_handler(message: types.Message):
    """Обработчик инструмента Система"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        # Проверяем доступ для обычных пользователей
        is_subscribed = await check_subscription(user_id, bot)
        if not is_subscribed:
            await send_subscription_request(message)
            return
        
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await message.answer("⏳ Ваша анкета еще не одобрена. Пожалуйста, подождите.")
            return
    
    # Получаем информацию о системе
    try:
        import platform
        
        system_info = {
            "Система": platform.system(),
            "Версия": platform.release(),
            "Архитектура": platform.machine(),
            "Процессор": platform.processor(),
            "Python": platform.python_version(),
            "Путь к Python": sys.executable
        }
        
        # Информация о памяти
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        system_text = (
            f"💻 <b>ИНФОРМАЦИЯ О СИСТЕМЕ</b> 💻\n\n"
            
            f"<b>🖥 ОС И ПЛАТФОРМА:</b>\n"
        )
        
        for key, value in system_info.items():
            system_text += f"• <b>{key}:</b> {value}\n"
        
        system_text += f"\n<b>💾 РЕСУРСЫ:</b>\n"
        system_text += f"• <b>Оперативная память:</b> {memory.total // (1024**3)} GB ({memory.percent}% использовано)\n"
        system_text += f"• <b>Дисковое пространство:</b> {disk.total // (1024**3)} GB ({disk.percent}% использовано)\n"
        
        system_text += f"\n<b>📂 ПАПКИ СИСТЕМЫ:</b>\n"
        system_text += f"• <b>Боты:</b> {BOTS_FOLDER}/\n"
        system_text += f"• <b>Шаблоны:</b> {TEMPLATES_FOLDER}/\n"
        system_text += f"• <b>Изображения:</b> {IMAGES_FOLDER}/\n"
        system_text += f"• <b>База данных:</b> bot_manager.db\n"
        
        system_text += f"\n<b>⏱ ВРЕМЯ РАБОТЫ:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        keyboard = InlineKeyboardMarkup()
        keyboard.row(
            InlineKeyboardButton("🔄 Обновить", callback_data="system_refresh"),
            InlineKeyboardButton("📊 Детальная информация", callback_data="system_detailed")
        )
        keyboard.row(InlineKeyboardButton("❌ Закрыть", callback_data="system_close"))
        
        await send_photo_with_caption(message, "system_info", system_text, keyboard)
        
    except Exception as e:
        logger.error(f"Ошибка получения информации о системе: {e}")
        await message.answer(f"❌ Ошибка получения информации о системе: {str(e)[:200]}")

@dp.message_handler(lambda m: m.text == "📊 Мониторинг")
async def monitoring_tools_handler(message: types.Message):
    """Обработчик инструмента Мониторинг"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        # Проверяем доступ для обычных пользователей
        is_subscribed = await check_subscription(user_id, bot)
        if not is_subscribed:
            await send_subscription_request(message)
            return
        
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await message.answer("⏳ Ваша анкета еще не одобрена. Пожалуйста, подождите.")
            return
    
    # Получаем статистику мониторинга
    stats = get_system_stats()
    
    # Информация о процессах
    processes_info = []
    for bot_id, process_info in list(running_processes.items())[:10]:  # Берем первые 10
        uptime = datetime.now() - process_info['started_at']
        hours = uptime.seconds // 3600
        minutes = (uptime.seconds % 3600) // 60
        
        processes_info.append(
            f"• Бот #{bot_id}: PID {process_info['pid']}, {hours}ч {minutes}м\n"
        )
    
    monitoring_text = (
        f"📊 <b>МОНИТОРИНГ СИСТЕМЫ</b> 📊\n\n"
        
        f"<b>⚡ РЕАЛЬНОЕ ВРЕМЯ:</b>\n"
        f"┌ 💾 Память: <b>{stats.get('memory_percent', 0):.1f}%</b>\n"
        f"├ ⚡ CPU: <b>{stats.get('cpu_percent', 0):.1f}%</b>\n"
        f"└ 💿 Диск: <b>{stats.get('disk_percent', 0):.1f}%</b>\n\n"
        
        f"<b>🤖 АКТИВНЫЕ ПРОЦЕССЫ ({len(running_processes)}):</b>\n"
    )
    
    if processes_info:
        for info in processes_info:
            monitoring_text += info
    else:
        monitoring_text += "<i>Нет активных процессов</i>\n"
    
    if len(running_processes) > 10:
        monitoring_text += f"\n<i>...и еще {len(running_processes) - 10} процессов</i>\n"
    
    monitoring_text += f"\n<b>📈 ЗАГРУЗКА СИСТЕМЫ:</b>\n"
    
    # Простая визуализация загрузки
    def get_bar(percent):
        filled = int(percent / 10)
        return "█" * filled + "░" * (10 - filled)
    
    monitoring_text += f"• Память: {get_bar(stats.get('memory_percent', 0))} {stats.get('memory_percent', 0):.1f}%\n"
    monitoring_text += f"• CPU: {get_bar(stats.get('cpu_percent', 0))} {stats.get('cpu_percent', 0):.1f}%\n"
    monitoring_text += f"• Диск: {get_bar(stats.get('disk_percent', 0))} {stats.get('disk_percent', 0):.1f}%\n"
    
    monitoring_text += f"\n<b>⏱ ОБНОВЛЕНО:</b> {datetime.now().strftime('%H:%M:%S')}"
    
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("🔄 Обновить", callback_data="monitoring_refresh"),
        InlineKeyboardButton("📋 Список процессов", callback_data="monitoring_processes")
    )
    keyboard.row(InlineKeyboardButton("🚀 Запущенные боты", callback_data="monitoring_bots_list"))
    keyboard.row(InlineKeyboardButton("❌ Закрыть", callback_data="monitoring_close"))
    
    await send_photo_with_caption(message, "monitoring", monitoring_text, keyboard)

@dp.message_handler(lambda m: m.text == "🗃 База данных")
async def database_tools_handler(message: types.Message):
    """Обработчик инструмента База данных"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        # Проверяем доступ для обычных пользователей
        is_subscribed = await check_subscription(user_id, bot)
        if not is_subscribed:
            await send_subscription_request(message)
            return
        
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await message.answer("⏳ Ваша анкета еще не одобрена. Пожалуйста, подождите.")
            return
    
    try:
        conn = sqlite3.connect('bot_manager.db')
        cursor = conn.cursor()
        
        # Получаем информацию о таблицах
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        
        db_text = f"🗃 <b>БАЗА ДАННЫХ</b> 🗃\n\n"
        
        db_text += f"<b>📂 ТАБЛИЦЫ В БАЗЕ:</b>\n"
        
        table_stats = []
        for table in tables:
            table_name = table[0]
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            table_stats.append((table_name, count))
        
        for table_name, count in table_stats:
            db_text += f"• <b>{table_name}:</b> {count} записей\n"
        
        # Размер файла БД
        db_size = os.path.getsize('bot_manager.db') / 1024  # в KB
        
        db_text += f"\n<b>📊 СТАТИСТИКА БАЗЫ:</b>\n"
        db_text += f"• <b>Размер файла:</b> {db_size:.2f} KB\n"
        db_text += f"• <b>Количество таблиц:</b> {len(tables)}\n"
        db_text += f"• <b>Всего записей:</b> {sum(count for _, count in table_stats)}\n"
        
        # Проверяем соединение
        cursor.execute("SELECT 1")
        test_result = cursor.fetchone()
        
        db_text += f"• <b>Статус соединения:</b> {'✅ Активно' if test_result else '❌ Ошибка'}\n"
        
        conn.close()
        
        db_text += f"\n<b>🛠 ДЕЙСТВИЯ С БАЗОЙ:</b>\n"
        db_text += f"• Создать резервную копию\n"
        db_text += f"• Восстановить из бекапа\n"
        db_text += f"• Оптимизировать базу\n"
        db_text += f"• Очистить старые записи\n"
        
        db_text += f"\n<b>⏱ ПРОВЕРКА:</b> {datetime.now().strftime('%H:%M:%S')}"
        
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.row(
            InlineKeyboardButton("💾 Создать бекап", callback_data="db_backup"),
            InlineKeyboardButton("🔄 Восстановить", callback_data="db_restore")
        )
        keyboard.row(
            InlineKeyboardButton("⚡ Оптимизировать", callback_data="db_optimize"),
            InlineKeyboardButton("🧹 Очистить", callback_data="db_clean")
        )
        keyboard.row(
            InlineKeyboardButton("📊 Статистика", callback_data="db_stats"),
            InlineKeyboardButton("🔄 Обновить", callback_data="db_refresh")
        )
        keyboard.row(InlineKeyboardButton("❌ Закрыть", callback_data="db_close"))
        
        await send_photo_with_caption(message, "database", db_text, keyboard)
        
    except Exception as e:
        logger.error(f"Ошибка работы с базой данных: {e}")
        await message.answer(f"❌ Ошибка работы с базой данных: {str(e)[:200]}")

@dp.message_handler(lambda m: m.text == "🛠 Тех. обслуживание")
async def maintenance_tools_handler(message: types.Message):
    """Обработчик инструмента Тех. обслуживание"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        # Проверяем доступ для обычных пользователей
        is_subscribed = await check_subscription(user_id, bot)
        if not is_subscribed:
            await send_subscription_request(message)
            return
        
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await message.answer("⏳ Ваша анкета еще не одобрена. Пожалуйста, подождите.")
            return
    
    maintenance_text = (
        f"🛠 <b>ТЕХНИЧЕСКОЕ ОБСЛУЖИВАНИЕ</b> 🛠\n\n"
        
        f"<b>🎯 ДЕЙСТВИЯ ОБСЛУЖИВАНИЯ:</b>\n"
        f"• <b>🔍 Проверка системы</b> - диагностика проблем\n"
        f"• <b>⚡ Оптимизация</b> - улучшение производительности\n"
        f"• <b>🔄 Обновление</b> - обновление компонентов\n"
        f"• <b>🧹 Очистка</b> - удаление временных файлов\n"
        f"• <b>📊 Восстановление</b> - восстановление данных\n"
        f"• <b>🛡 Безопасность</b> - проверка безопасности\n\n"
        
        f"<b>⚠️ ВНИМАНИЕ:</b>\n"
        f"• Некоторые действия могут требовать перезапуска\n"
        f"• Рекомендуется создавать бекапы\n"
        f"• Выполняйте действия последовательно\n"
        f"• Проверяйте результат после каждого действия\n\n"
        
        f"<b>📅 РЕКОМЕНДАЦИИ:</b>\n"
        f"• Выполняйте техобслуживание регулярно\n"
        f"• Создавайте бекапы перед действиями\n"
        f"• Проверяйте логи после обслуживания\n"
        f"• Тестируйте систему после работ\n\n"
        
        f"<i>👇 Выберите действие обслуживания:</i>"
    )
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        InlineKeyboardButton("🔍 Проверка системы", callback_data="maintenance_check"),
        InlineKeyboardButton("⚡ Оптимизация", callback_data="maintenance_optimize")
    )
    keyboard.row(
        InlineKeyboardButton("🔄 Обновление", callback_data="maintenance_update"),
        InlineKeyboardButton("🧹 Очистка", callback_data="maintenance_clean")
    )
    keyboard.row(
        InlineKeyboardButton("📊 Восстановление", callback_data="maintenance_restore"),
        InlineKeyboardButton("🛡 Безопасность", callback_data="maintenance_security")
    )
    keyboard.row(
        InlineKeyboardButton("🚀 Перезапуск системы", callback_data="maintenance_restart"),
        InlineKeyboardButton("📋 Отчет", callback_data="maintenance_report")
    )
    keyboard.row(InlineKeyboardButton("❌ Закрыть", callback_data="maintenance_close"))
    
    await send_photo_with_caption(message, "maintenance", maintenance_text, keyboard)

@dp.message_handler(lambda m: m.text == "🧹 Очистка системы")
async def cleanup_tools_handler(message: types.Message):
    """Обработчик инструмента Очистка системы"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        # Проверяем доступ для обычных пользователей
        is_subscribed = await check_subscription(user_id, bot)
        if not is_subscribed:
            await send_subscription_request(message)
            return
        
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await message.answer("⏳ Ваша анкета еще не одобрена. Пожалуйста, подождите.")
            return
    
    cleanup_text = (
        f"🧹 <b>ОЧИСТКА СИСТЕМЫ</b> 🧹\n\n"
        
        f"<b>🗑 ЧТО МОЖНО ОЧИСТИТЬ:</b>\n"
        f"• <b>📝 Логи ботов</b> - старые записи логов\n"
        f"• <b>📊 Временные файлы</b> - временные данные\n"
        f"• <b>🗃 Кэш системы</b> - кэшированные файлы\n"
        f"• <b>📦 Неиспользуемые боты</b> - неактивные боты\n"
        f"• <b>📋 Старые анкеты</b> - обработанные анкеты\n"
        f"• <b>🚫 Ошибки базы</b> - поврежденные записи\n\n"
        
        f"<b>⚠️ ПРЕДУПРЕЖДЕНИЕ:</b>\n"
        f"• Удаленные данные нельзя восстановить!\n"
        f"• Рекомендуется создать бекап перед очисткой\n"
        f"• Очищайте только ненужные данные\n"
        f"• Проверяйте что именно будет удалено\n\n"
        
        f"<b>🎯 РЕКОМЕНДАЦИИ:</b>\n"
        f"• Очищайте логи старше 30 дней\n"
        f"• Удаляйте неиспользуемых ботов\n"
        f"• Архивируйте важные данные\n"
        f"• Регулярно проводите очистку\n\n"
        
        f"<i>👇 Выберите что очистить:</i>"
    )
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        InlineKeyboardButton("📝 Очистить логи", callback_data="cleanup_logs"),
        InlineKeyboardButton("📊 Временные файлы", callback_data="cleanup_temp")
    )
    keyboard.row(
        InlineKeyboardButton("🗃 Очистить кэш", callback_data="cleanup_cache"),
        InlineKeyboardButton("🤖 Неиспользуемые боты", callback_data="cleanup_unused_bots")
    )
    keyboard.row(
        InlineKeyboardButton("📋 Старые анкеты", callback_data="cleanup_old_quests"),
        InlineKeyboardButton("🚫 Ошибки базы", callback_data="cleanup_db_errors")
    )
    keyboard.row(
        InlineKeyboardButton("💾 Создать бекап", callback_data="cleanup_backup"),
        InlineKeyboardButton("🧹 Полная очистка", callback_data="cleanup_full")
    )
    keyboard.row(InlineKeyboardButton("❌ Закрыть", callback_data="cleanup_close"))
    
    await send_photo_with_caption(message, "cleanup", cleanup_text, keyboard)

@dp.message_handler(lambda m: m.text == "🔍 Диагностика")
async def diagnostics_tools_handler(message: types.Message):
    """Обработчик инструмента Диагностика"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        # Проверяем доступ для обычных пользователей
        is_subscribed = await check_subscription(user_id, bot)
        if not is_subscribed:
            await send_subscription_request(message)
            return
        
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await message.answer("⏳ Ваша анкета еще не одобрена. Пожалуйста, подождите.")
            return
    
    diagnostics_text = (
        f"🔍 <b>ДИАГНОСТИКА СИСТЕМЫ</b> 🔍\n\n"
        
        f"<b>🎯 ЦЕЛИ ДИАГНОСТИКИ:</b>\n"
        f"• Выявление проблем и ошибок\n"
        f"• Проверка работоспособности\n"
        f"• Оптимизация производительности\n"
        f"• Предотвращение сбоев\n\n"
        
        f"<b>📋 ЧТО ПРОВЕРЯЕТСЯ:</b>\n"
        f"• <b>🖥 Системные ресурсы</b> - память, CPU, диск\n"
        f"• <b>🤖 Работа ботов</b> - процессы и логи\n"
        f"• <b>🗃 База данных</b> - целостность и доступность\n"
        f"• <b>🌐 Сеть и соединения</b> - интернет и API\n"
        f"• <b>🛡 Безопасность</b> - доступы и права\n"
        f"• <b>⚡ Производительность</b> - скорость и отклик\n\n"
        
        f"<b>📊 РЕЗУЛЬТАТЫ ДИАГНОСТИКИ:</b>\n"
        f"• Подробный отчет о состоянии\n"
        f"• Рекомендации по улучшению\n"
        f"• Список обнаруженных проблем\n"
        f"• Предложения по решению\n\n"
        
        f"<i>👇 Запустите диагностику системы:</i>"
    )
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        InlineKeyboardButton("🚀 Быстрая диагностика", callback_data="diagnostics_quick"),
        InlineKeyboardButton("🔍 Полная диагностика", callback_data="diagnostics_full")
    )
    keyboard.row(
        InlineKeyboardButton("🤖 Диагностика ботов", callback_data="diagnostics_bots"),
        InlineKeyboardButton("🗃 Диагностика БД", callback_data="diagnostics_db")
    )
    keyboard.row(
        InlineKeyboardButton("🌐 Проверка сети", callback_data="diagnostics_network"),
        InlineKeyboardButton("⚡ Проверка производительности", callback_data="diagnostics_performance")
    )
    keyboard.row(
        InlineKeyboardButton("📋 Отчет о проблемах", callback_data="diagnostics_report"),
        InlineKeyboardButton("🔄 Обновить", callback_data="diagnostics_refresh")
    )
    keyboard.row(InlineKeyboardButton("❌ Закрыть", callback_data="diagnostics_close"))
    
    await send_photo_with_caption(message, "diagnostics", diagnostics_text, keyboard)

# ========== ОБРАБОТЧИКИ CALLBACK-QUERY ДЛЯ БОТОВ ==========
@dp.callback_query_handler(lambda c: c.data.startswith('bot_view_'))
async def bot_view_handler(callback_query: types.CallbackQuery):
    """Просмотр детальной информации о боте"""
    user_id = callback_query.from_user.id
    
    if user_id not in ADMIN_IDS:
        # Проверяем доступ для обычных пользователей
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await bot.answer_callback_query(callback_query.id, "❌ Ваша анкета не одобрена")
            return
    
    bot_id = int(callback_query.data.replace('bot_view_', ''))
    
    # Получаем информацию о боте
    bot_info = get_bot_info(bot_id)
    
    if not bot_info:
        await bot.answer_callback_query(callback_query.id, "❌ Бот не найден")
        return
    
    # Проверяем права доступа
    if user_id not in ADMIN_IDS and bot_info['owner_id'] != user_id:
        await bot.answer_callback_query(callback_query.id, "❌ Нет доступа к этому боту")
        return
    
    # Форматируем информацию
    is_running = bot_id in running_processes
    status_icon = "✅" if is_running else "❌"
    status_text = "ЗАПУЩЕН" if is_running else "ОСТАНОВЛЕН"
    
    # Время работы если запущен
    uptime_text = ""
    if is_running:
        uptime = datetime.now() - running_processes[bot_id]['started_at']
        hours = uptime.seconds // 3600
        minutes = (uptime.seconds % 3600) // 60
        uptime_text = f"⏱ <b>Время работы:</b> {hours}ч {minutes}м\n"
    
    bot_detail_text = (
        f"🤖 <b>ДЕТАЛЬНАЯ ИНФОРМАЦИЯ О БОТЕ</b> 🤖\n\n"
        
        f"<b>📋 ОСНОВНАЯ ИНФОРМАЦИЯ:</b>\n"
        f"┌ 🆔 ID бота: <b>#{bot_info['id']}</b>\n"
        f"├ 📛 Имя: <b>{bot_info['bot_name']}</b>\n"
        f"├ 👑 Владелец: <code>{bot_info['owner_id']}</code>\n"
        f"├ 📊 Статус: {status_icon} <b>{status_text}</b>\n"
        f"├ 🗂 Шаблон: <b>{bot_info.get('template_name', 'стандартный')}</b>\n"
        f"└ 📅 Создан: {bot_info['created_at']}\n\n"
        
        f"{uptime_text}"
        
        f"<b>📁 ФАЙЛЫ:</b>\n"
        f"• <b>Файл бота:</b> <code>{os.path.basename(bot_info.get('bot_filename', 'не указан'))}</code>\n"
        f"• <b>Расположение:</b> <code>{BOTS_FOLDER}/</code>\n\n"
        
        f"<b>⚡ ТЕКУЩЕЕ СОСТОЯНИЕ:</b>\n"
    )
    
    if is_running:
        process_info = running_processes[bot_id]
        bot_detail_text += f"• <b>PID процесса:</b> {process_info['pid']}\n"
        bot_detail_text += f"• <b>Имя процесса:</b> {process_info['name']}\n"
        bot_detail_text += f"• <b>Запущен:</b> {process_info['started_at'].strftime('%H:%M:%S')}\n"
    else:
        bot_detail_text += f"• <i>Бот в настоящее время остановлен</i>\n"
    
    bot_detail_text += f"\n<i>👇 Выберите действие:</i>"
    
    await bot.edit_message_caption(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        caption=bot_detail_text,
        reply_markup=create_bot_detail_keyboard(bot_id),
        parse_mode="HTML"
    )
    
    await bot.answer_callback_query(callback_query.id, f"🤖 Бот #{bot_id}")

@dp.callback_query_handler(lambda c: c.data.startswith('bot_start_'))
async def bot_start_handler(callback_query: types.CallbackQuery):
    """Запуск бота"""
    user_id = callback_query.from_user.id
    
    if user_id not in ADMIN_IDS:
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await bot.answer_callback_query(callback_query.id, "❌ Ваша анкета не одобрена")
            return
    
    bot_id = int(callback_query.data.replace('bot_start_', ''))
    
    # Проверяем права доступа
    bot_info = get_bot_info(bot_id)
    if not bot_info:
        await bot.answer_callback_query(callback_query.id, "❌ Бот не найден")
        return
    
    if user_id not in ADMIN_IDS and bot_info['owner_id'] != user_id:
        await bot.answer_callback_query(callback_query.id, "❌ Нет доступа к этому боту")
        return
    
    # Проверяем не запущен ли уже
    if bot_id in running_processes:
        await bot.answer_callback_query(callback_query.id, "✅ Бот уже запущен")
        return
    
    # Запускаем бота
    success, message = start_bot_process(
        bot_id=bot_id,
        owner_id=bot_info['owner_id'],
        bot_filename=bot_info['bot_filename']
    )
    
    if success:
        update_bot_status(bot_id, 'running')
        await bot.answer_callback_query(callback_query.id, "🚀 Бот запущен")
        
        # Обновляем информацию о боте
        await bot_view_handler(callback_query)
    else:
        await bot.answer_callback_query(callback_query.id, f"❌ {message}")

@dp.callback_query_handler(lambda c: c.data.startswith('bot_stop_'))
async def bot_stop_handler(callback_query: types.CallbackQuery):
    """Остановка бота"""
    user_id = callback_query.from_user.id
    
    if user_id not in ADMIN_IDS:
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await bot.answer_callback_query(callback_query.id, "❌ Ваша анкета не одобрена")
            return
    
    bot_id = int(callback_query.data.replace('bot_stop_', ''))
    
    # Проверяем права доступа
    bot_info = get_bot_info(bot_id)
    if not bot_info:
        await bot.answer_callback_query(callback_query.id, "❌ Бот не найден")
        return
    
    if user_id not in ADMIN_IDS and bot_info['owner_id'] != user_id:
        await bot.answer_callback_query(callback_query.id, "❌ Нет доступа к этому боту")
        return
    
    # Проверяем запущен ли
    if bot_id not in running_processes:
        await bot.answer_callback_query(callback_query.id, "❌ Бот не запущен")
        return
    
    # Останавливаем бота
    success, message = stop_bot_process(bot_id)
    
    if success:
        update_bot_status(bot_id, 'stopped')
        await bot.answer_callback_query(callback_query.id, "🛑 Бот остановлен")
        
        # Обновляем информацию о боте
        await bot_view_handler(callback_query)
    else:
        await bot.answer_callback_query(callback_query.id, f"❌ {message}")

@dp.callback_query_handler(lambda c: c.data.startswith('bot_delete_'))
async def bot_delete_handler(callback_query: types.CallbackQuery):
    """Удаление бота"""
    user_id = callback_query.from_user.id
    
    if user_id not in ADMIN_IDS:
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await bot.answer_callback_query(callback_query.id, "❌ Ваша анкета не одобрена")
            return
    
    bot_id = int(callback_query.data.replace('bot_delete_', ''))
    
    # Проверяем права доступа
    bot_info = get_bot_info(bot_id)
    if not bot_info:
        await bot.answer_callback_query(callback_query.id, "❌ Бот не найден")
        return
    
    if user_id not in ADMIN_IDS and bot_info['owner_id'] != user_id:
        await bot.answer_callback_query(callback_query.id, "❌ Нет доступа к этому боту")
        return
    
    # Подтверждение удаления
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        InlineKeyboardButton("✅ Да, удалить", callback_data=f"bot_delete_confirm_{bot_id}"),
        InlineKeyboardButton("❌ Нет, отмена", callback_data=f"bot_view_{bot_id}")
    )
    
    await bot.edit_message_caption(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        caption=(
            f"🗑 <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b> 🗑\n\n"
            f"Вы действительно хотите удалить бота <b>#{bot_id} - {bot_info['bot_name']}</b>?\n\n"
            f"<b>⚠️ Это действие нельзя отменить!</b>\n\n"
            f"<b>Что будет удалено:</b>\n"
            f"• Запись бота из базы данных\n"
            f"• Файл бота: {os.path.basename(bot_info['bot_filename'])}\n"
            f"• Логи и статистика бота\n\n"
            f"<i>👇 Подтвердите удаление:</i>"
        ),
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    
    await bot.answer_callback_query(callback_query.id, "⚠️ Подтвердите удаление")

@dp.callback_query_handler(lambda c: c.data.startswith('bot_delete_confirm_'))
async def bot_delete_confirm_handler(callback_query: types.CallbackQuery):
    """Подтверждение удаления бота"""
    user_id = callback_query.from_user.id
    bot_id = int(callback_query.data.replace('bot_delete_confirm_', ''))
    
    # Удаляем бота
    success, message = delete_bot(bot_id)
    
    if success:
        await bot.answer_callback_query(callback_query.id, "✅ Бот удален")
        
        # Возвращаемся к списку ботов
        await my_bots_handler(types.Message(
            chat=types.Chat(id=callback_query.from_user.id),
            message_id=0,
            from_user=callback_query.from_user
        ))
    else:
        await bot.answer_callback_query(callback_query.id, f"❌ {message}")

@dp.callback_query_handler(lambda c: c.data == 'start_all_bots')
async def start_all_bots_handler(callback_query: types.CallbackQuery):
    """Запуск всех ботов"""
    user_id = callback_query.from_user.id
    
    if user_id not in ADMIN_IDS:
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await bot.answer_callback_query(callback_query.id, "❌ Ваша анкета не одобрена")
            return
    
    # Получаем всех ботов
    if user_id in ADMIN_IDS:
        bots = get_all_bots()
    else:
        bots = get_user_bots(user_id)
    
    # Фильтруем не запущенные боты
    bots_to_start = [bot_info for bot_info in bots if bot_info['id'] not in running_processes]
    
    if not bots_to_start:
        await bot.answer_callback_query(callback_query.id, "✅ Все боты уже запущены")
        return
    
    # Запускаем ботов
    started_count = 0
    errors = []
    
    for bot_info in bots_to_start[:10]:  # Ограничиваем 10 ботами за раз
        try:
            success, message = start_bot_process(
                bot_id=bot_info['id'],
                owner_id=bot_info['owner_id'],
                bot_filename=bot_info.get('bot_filename', '')
            )
            
            if success:
                update_bot_status(bot_info['id'], 'running')
                started_count += 1
            else:
                errors.append(f"Бот #{bot_info['id']}: {message}")
        except Exception as e:
            errors.append(f"Бот #{bot_info['id']}: {str(e)}")
    
    # Формируем результат
    result_text = f"🚀 Запущено ботов: {started_count}/{len(bots_to_start)}"
    
    if errors:
        result_text += f"\n❌ Ошибки: {len(errors)}"
    
    await bot.answer_callback_query(callback_query.id, result_text)
    
    # Обновляем список ботов
    await my_bots_handler(types.Message(
        chat=types.Chat(id=callback_query.from_user.id),
        message_id=0,
        from_user=callback_query.from_user
    ))

@dp.callback_query_handler(lambda c: c.data == 'stop_all_bots')
async def stop_all_bots_handler(callback_query: types.CallbackQuery):
    """Остановка всех ботов"""
    user_id = callback_query.from_user.id
    
    if user_id not in ADMIN_IDS:
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await bot.answer_callback_query(callback_query.id, "❌ Ваша анкета не одобрена")
            return
    
    # Получаем запущенные боты
    if user_id in ADMIN_IDS:
        running_bots_ids = list(running_processes.keys())
    else:
        # Для обычного пользователя - только его запущенные боты
        user_bots = get_user_bots(user_id)
        running_bots_ids = [bot_info['id'] for bot_info in user_bots if bot_info['id'] in running_processes]
    
    if not running_bots_ids:
        await bot.answer_callback_query(callback_query.id, "✅ Нет запущенных ботов")
        return
    
    # Останавливаем ботов
    stopped_count = 0
    errors = []
    
    for bot_id in running_bots_ids[:10]:  # Ограничиваем 10 ботами за раз
        try:
            success, message = stop_bot_process(bot_id)
            
            if success:
                update_bot_status(bot_id, 'stopped')
                stopped_count += 1
            else:
                errors.append(f"Бот #{bot_id}: {message}")
        except Exception as e:
            errors.append(f"Бот #{bot_id}: {str(e)}")
    
    # Формируем результат
    result_text = f"🛑 Остановлено ботов: {stopped_count}/{len(running_bots_ids)}"
    
    if errors:
        result_text += f"\n❌ Ошибки: {len(errors)}"
    
    await bot.answer_callback_query(callback_query.id, result_text)
    
    # Обновляем список ботов
    await my_bots_handler(types.Message(
        chat=types.Chat(id=callback_query.from_user.id),
        message_id=0,
        from_user=callback_query.from_user
    ))

@dp.callback_query_handler(lambda c: c.data.startswith('bots_page_'))
async def bots_page_handler(callback_query: types.CallbackQuery):
    """Обработчик пагинации списка ботов"""
    user_id = callback_query.from_user.id
    
    if user_id not in ADMIN_IDS:
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await bot.answer_callback_query(callback_query.id, "❌ Ваша анкета не одобрена")
            return
    
    try:
        page_num = int(callback_query.data.replace('bots_page_', ''))
        
        # Получаем боты пользователя
        if user_id in ADMIN_IDS:
            bots_data = get_all_bots(limit=100)
        else:
            bots_data = get_user_bots(user_id)
        
        # Обновляем сообщение с новой страницей
        bot_list_text = f"⚙️ <b>ВАШИ БОТЫ - Страница {page_num+1}</b> ⚙️\n\n"
        
        total_bots = len(bots_data)
        running_bots = sum(1 for bot_info in bots_data if bot_info['id'] in running_processes)
        
        bot_list_text += f"<b>📊 СТАТИСТИКА:</b>\n"
        bot_list_text += f"┌ Всего ботов: <b>{total_bots}</b>\n"
        bot_list_text += f"├ Запущено: <b>{running_bots}</b> ✅\n"
        bot_list_text += f"└ Остановлено: <b>{total_bots - running_bots}</b> ❌\n\n"
        
        bot_list_text += f"<i>👇 Выберите бота для управления:</i>"
        
        await bot.edit_message_caption(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            caption=bot_list_text,
            reply_markup=create_bot_list_keyboard(bots_data, page_num),
            parse_mode="HTML"
        )
        
        await bot.answer_callback_query(callback_query.id, f"📄 Страница {page_num+1}")
        
    except Exception as e:
        logger.error(f"Ошибка пагинации ботов: {e}")
        await bot.answer_callback_query(callback_query.id, "❌ Ошибка")

@dp.callback_query_handler(lambda c: c.data == 'bots_refresh')
async def bots_refresh_handler(callback_query: types.CallbackQuery):
    """Обновление списка ботов"""
    await my_bots_handler(types.Message(
        chat=types.Chat(id=callback_query.from_user.id),
        message_id=0,
        from_user=callback_query.from_user
    ))
    await bot.answer_callback_query(callback_query.id, "🔄 Список обновлен")

@dp.callback_query_handler(lambda c: c.data == 'bots_back')
async def bots_back_handler(callback_query: types.CallbackQuery):
    """Возврат к списку ботов"""
    await my_bots_handler(types.Message(
        chat=types.Chat(id=callback_query.from_user.id),
        message_id=0,
        from_user=callback_query.from_user
    ))
    await bot.answer_callback_query(callback_query.id, "⬅️ Возврат к списку")

@dp.callback_query_handler(lambda c: c.data == 'bots_main')
async def bots_main_handler(callback_query: types.CallbackQuery):
    """Возврат в главное меню"""
    await cmd_start(types.Message(
        chat=types.Chat(id=callback_query.from_user.id),
        message_id=0,
        from_user=callback_query.from_user
    ))
    await bot.answer_callback_query(callback_query.id, "📋 Главное меню")

@dp.callback_query_handler(lambda c: c.data == 'bots_close')
async def bots_close_handler(callback_query: types.CallbackQuery):
    """Закрытие раздела ботов"""
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.answer_callback_query(callback_query.id, "❌ Раздел закрыт")

# ========== ОБРАБОТЧИКИ CALLBACK-QUERY ДЛЯ АНАЛИТИКИ ==========
@dp.callback_query_handler(lambda c: c.data == 'analytics_system')
async def analytics_system_handler(callback_query: types.CallbackQuery):
    """Аналитика системы"""
    user_id = callback_query.from_user.id
    
    if user_id not in ADMIN_IDS:
        questionnaire_status = get_questionnaire_status(user_id)
        if questionnaire_status != "approved":
            await bot.answer_callback_query(callback_query.id, "❌ Ваша анкета не одобрена")
            return
    
    # Получаем детальную статистику системы
    stats = get_system_stats()
    
    analytics_text = (
        f"📊 <b>АНАЛИТИКА СИСТЕМЫ</b> 📊\n\n"
        
        f"<b>🤖 СТАТИСТИКА БОТОВ:</b>\n"
        f"• Всего ботов: <b>{stats.get('total_bots', 0)}</b>\n"
        f"• Запущено: <b>{stats.get('running_bots', 0)}</b> ({stats.get('running_bots', 0)/max(stats.get('total_bots', 1), 1)*100:.1f}%)\n"
        f"• Остановлено: <b>{stats.get('total_bots', 0) - stats.get('running_bots', 0)}</b>\n"
        f"• Активных процессов: <b>{stats.get('running_processes', 0)}</b>\n\n"
        
        f"<b>👤 СТАТИСТИКА ПОЛЬЗОВАТЕЛЕЙ:</b>\n"
        f"• Всего анкет: <b>{stats.get('total_quests', 0)}</b>\n"
        f"• Ожидает проверки: <b>{stats.get('pending_quests', 0)}</b>\n"
        f"• Всего логов: <b>{stats.get('total_logs', 0)}</b>\n\n"
        
        f"<b>💻 СИСТЕМНЫЕ РЕСУРСЫ:</b>\n"
    )
    
    # Графики использования ресурсов
    def get_resource_bar(percent):
        filled = int(percent / 5)  # 20 ступеней
        return "█" * filled + "░" * (20 - filled)
    
    analytics_text += f"• 💾 Память: {get_resource_bar(stats.get('memory_percent', 0))} {stats.get('memory_percent', 0):.1f}%\n"
    analytics_text += f"• ⚡ CPU: {get_resource_bar(stats.get('cpu_percent', 0))} {stats.get('cpu_percent', 0):.1f}%\n"
    analytics_text += f"• 💿 Диск: {get_resource_bar(stats.get('disk_percent', 0))} {stats.get('disk_percent', 0):.1f}%\n\n"
    
    # Оценка состояния системы
    system_status = "✅ ОТЛИЧНО"
    if stats.get('memory_percent', 0) > 90 or stats.get('cpu_percent', 0) > 90:
        system_status = "⚠️ ВНИМАНИЕ"
    if stats.get('memory_percent', 0) > 95 or stats.get('cpu_percent', 0) > 95:
        system_status = "❌ КРИТИЧЕСКО"
    
    analytics_text += f"<b>📈 ОЦЕНКА СИСТЕМЫ:</b> {system_status}\n\n"
    
    # Рекомендации
    analytics_text += f"<b>🎯 РЕКОМЕНДАЦИИ:</b>\n"
    
    if stats.get('memory_percent', 0) > 80:
        analytics_text += "• 🔽 Уменьшите количество запущенных ботов\n"
    
    if stats.get('cpu_percent', 0) > 80:
        analytics_text += "• ⚡ Оптимизируйте работу ботов\n"
    
    if stats.get('disk_percent', 0) > 80:
        analytics_text += "• 🧹 Очистите дисковое пространство\n"
    
    if not any([stats.get('memory_percent', 0) > 80, stats.get('cpu_percent', 0) > 80, stats.get('disk_percent', 0) > 80]):
        analytics_text += "• ✅ Система работает оптимально\n"
    
    analytics_text += f"\n<b>⏱ ОБНОВЛЕНО:</b> {datetime.now().strftime('%H:%M:%S')}"
    
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("🔄 Обновить", callback_data="analytics_refresh"),
        InlineKeyboardButton("📋 Назад", callback_data="analytics_back")
    )
    
    await bot.edit_message_caption(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        caption=analytics_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    
    await bot.answer_callback_query(callback_query.id, "📊 Статистика системы")

@dp.callback_query_handler(lambda c: c.data == 'analytics_refresh')
async def analytics_refresh_handler(callback_query: types.CallbackQuery):
    """Обновление аналитики"""
    await analytics_handler(types.Message(
        chat=types.Chat(id=callback_query.from_user.id),
        message_id=0,
        from_user=callback_query.from_user
    ))
    await bot.answer_callback_query(callback_query.id, "🔄 Данные обновлены")

@dp.callback_query_handler(lambda c: c.data == 'analytics_back')
async def analytics_back_handler(callback_query: types.CallbackQuery):
    """Возврат к меню аналитики"""
    await analytics_handler(types.Message(
        chat=types.Chat(id=callback_query.from_user.id),
        message_id=0,
        from_user=callback_query.from_user
    ))
    await bot.answer_callback_query(callback_query.id, "⬅️ Назад")

@dp.callback_query_handler(lambda c: c.data == 'analytics_close')
async def analytics_close_handler(callback_query: types.CallbackQuery):
    """Закрытие раздела аналитики"""
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.answer_callback_query(callback_query.id, "❌ Раздел закрыт")

# ========== ОБРАБОТЧИКИ CALLBACK-QUERY ДЛЯ УПРАВЛЕНИЯ БОТАМИ ==========
@dp.callback_query_handler(lambda c: c.data == 'manage_close')
async def manage_close_handler(callback_query: types.CallbackQuery):
    """Закрытие раздела управления"""
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
    await bot.answer_callback_query(callback_query.id, "❌ Раздел закрыт")

# [Остальные обработчики остаются без изменений...]

# ========== ЗАПУСК БОТА ==========
if __name__ == "__main__":
    try:
        print("\n" + "="*80)
        print("🔷" + " Bot Manager CyberNet ".center(76) + "🔷")
        print("="*80)
        print(f"🔑 Токен: {MANAGER_TOKEN[:15]}...")
        print(f"👑 Администраторы: {ADMIN_IDS}")
        print(f"📢 Канал для подписки: {REQUIRED_CHANNEL}")
        print(f"⏱ Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)
        print(f"📂 Папки:")
        print(f"   • Изображения: {IMAGES_FOLDER}/")
        print(f"   • Боты: {BOTS_FOLDER}/")
        print(f"   • Шаблоны: {TEMPLATES_FOLDER}/")
        print("="*80)
        print("🔷 СИСТЕМА БЕЗОПАСНОСТИ:")
        print("   • Обязательная подписка на канал")
        print("   • Анкетирование новых пользователей")
        print("   • Модерация анкет администраторами")
        print("   • Защита от неавторизованного доступа")
        print("="*80)
        print("🚀 ИСПРАВЛЕННЫЕ РАЗДЕЛЫ:")
        print("   • 📊 Аналитика - полная статистика системы")
        print("   • ⚙️ Мои боты - управление своими ботами")
        print("   • ⚡ Управление ботами - массовые действия")
        print("   • 🔧 Инструменты - системные инструменты")
        print("="*80)
        print("🤖 ФУНКЦИОНАЛ БОТОВ:")
        print("   • Запуск/остановка отдельных ботов")
        print("   • Массовый запуск/остановка всех ботов")
        print("   • Просмотр детальной информации о боте")
        print("   • Управление правами доступа")
        print("="*80)
        print("💡 Для начала работы отправьте команду /start в Telegram")
        print("="*80)
        
        executor.start_polling(dp, skip_updates=True)
        
    except KeyboardInterrupt:
        print("\n\n🛑 Bot Manager CyberNet остановлен пользователем")
    except Exception as e:
        print(f"\n\n❌ Критическая ошибка запуска: {e}")
        traceback.print_exc()
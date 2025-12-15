"""
Конфигурация Bot Manager CyberNet
"""

import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# ========== НАСТРОЙКИ БОТА ==========
MANAGER_TOKEN = os.getenv("MANAGER_TOKEN", "8258712810:AAFPsRukN8UMS8S-dpl0sFrj3zNAq0T6Ytk")
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@Gamma404")

# ========== АДМИНИСТРАТОРЫ ==========
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "8545483002").split(',')]

# ========== НАСТРОЙКИ БАЗЫ ДАННЫХ ==========
DB_NAME = os.getenv("DB_NAME", "bot_manager.db")
DB_BACKUP_DIR = os.getenv("DB_BACKUP_DIR", "backups")
DB_BACKUP_DAYS = int(os.getenv("DB_BACKUP_DAYS", "7"))

# ========== НАСТРОЙКИ БОТОВ ==========
MAX_BOTS_PER_USER = int(os.getenv("MAX_BOTS_PER_USER", "10"))
BOT_TEMPLATES_DIR = os.getenv("BOT_TEMPLATES_DIR", "templates")
BOTS_DIR = os.getenv("BOTS_DIR", "bots")

# ========== НАСТРОЙКИ БЕЗОПАСНОСТИ ==========
QUESTIONNAIRE_REQUIRED = os.getenv("QUESTIONNAIRE_REQUIRED", "true").lower() == "true"
SUBSCRIPTION_REQUIRED = os.getenv("SUBSCRIPTION_REQUIRED", "true").lower() == "true"

# ========== НАСТРОЙКИ ЛОГИРОВАНИЯ ==========
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "bot_manager.log")
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# ========== НАСТРОЙКИ СИСТЕМЫ ==========
MAX_MEMORY_USAGE = int(os.getenv("MAX_MEMORY_USAGE", "80"))  # в процентах
MAX_CPU_USAGE = int(os.getenv("MAX_CPU_USAGE", "80"))        # в процентах
AUTO_CLEANUP_DAYS = int(os.getenv("AUTO_CLEANUP_DAYS", "30"))
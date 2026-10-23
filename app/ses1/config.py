# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# 1. Telegram Bot Token (حصل عليه من BotFather)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# 2. Administrative Controls (ضع الـ Telegram ID الخاص بك هنا)
# يمكنك إضافة معرفات أخرى لو رغبت في إضافة أدمن مساعد معك مستقبلاً
ADMIN_IDS = [
   5756077206,  # استبدل هذا الرقم بـ ID الحساب الخاص بك في تليجرام
]

# 3. Structural Storage Paths (مسارات تخزين قاعدة البيانات والملفات)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database", "vetbot.db")
STORAGE_DIR = os.path.join(BASE_DIR, "storage")

# 4. Safe Directory Initialization (إنشاء المجلدات تلقائياً إذا لم تكن موجودة)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(STORAGE_DIR, exist_ok=True)
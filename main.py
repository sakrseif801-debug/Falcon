import sys
import os

# 🌐 تم تعطيل إعدادات البروكسي لأننا بنشغل البوت محلياً على اللابتوب
# os.environ['HTTP_PROXY'] = 'http://proxy.server:3128'
# os.environ['HTTPS_PROXY'] = 'http://proxy.server:3128'

import telebot
import threading
from telebot.apihelper import ApiTelegramException

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import BOT_TOKEN
from database.db_handler import init_db, run_quiz_ranking_worker
from handlers.admin import register_admin_handlers
from handlers.student import register_student_handlers
from handlers.common import register_common_handlers, register_unregistered_handler

if not BOT_TOKEN or ":" not in BOT_TOKEN or len(BOT_TOKEN) < 35:
    raise RuntimeError(
        "BOT_TOKEN is missing or invalid. Copy the complete token from BotFather into .env."
    )

# 1. تهيئة وإنشاء قاعدة البيانات والجداول تلقائياً عند التشغيل
print("Initializing database...")
init_db()

# 2. ربط توكن البوت وتأكيد الاتصال بالـ API
bot = telebot.TeleBot(BOT_TOKEN)

# 🛑 خطوة الأمان: تنظيف أي طلبات معلقة (Pending Updates) من السيرفر بشكل محمي ضد ضعف الإنترنت
print("Clearing pending updates from Telegram servers...")
try:
    # تم رفع الـ timeout إلى 60 ثانية لتجنب الـ TimeoutError في الشبكات الضعيفة
    bot.delete_webhook(drop_pending_updates=True, timeout=60)
    print("Pending updates cleared successfully!")
except Exception as e:
    print(f"⚠️ Warning: Could not clear pending updates due to network lag: {e}")
    print("Continuing execution anyway...")

# 3. تسجيل وربط جميع المراحل والـ Handlers معاً بالترتيب الصحيح والمحمي
# واجهة الطالب في البداية لضمان التقاط qans_ و st_ بقوة وبدون تداخل
register_common_handlers(bot)   # أوامر الترحيب التلقائية ودليل الاستخدام العربي
register_student_handlers(bot)  # واجهة الطلاب والتصفح والكويزات الإلكترونية
register_admin_handlers(bot)    # لوحة تحكم الأدمن والإضافات والإعلانات
register_unregistered_handler(bot)

# 4. بدء تشغيل البوت الفعلي ومراقبة الرسائل بدون توقف
if __name__ == "__main__":
    print("VETBOT is now online and running via infinity polling! 🐾")
    ranking_stop_event = threading.Event()
    ranking_worker = threading.Thread(
        target=run_quiz_ranking_worker,
        args=(bot, ranking_stop_event),
        name="quiz-ranking-worker",
        daemon=True
    )
    ranking_worker.start()
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except ApiTelegramException as e:
        if getattr(e, "error_code", None) == 409:
            print("ERROR: Telegram rejected polling because another bot instance is already running.")
            print("Stop the other VETBOT process, then start this bot once.")
        else:
            print(f"Telegram API error: {e}")
    except Exception as e:
        print(f"Polling crashed with error: {e}")
    finally:
        ranking_stop_event.set()
      
# handlers/common.py
from telebot import types
from config import ADMIN_IDS
from database.db_handler import (
    add_user_if_not_exists, get_admin_role, get_user_profile, get_user_status,
    get_db_connection,
    mark_blocked_notice_sent, mark_guide_seen, save_bot_rating
)
from handlers.admin import admin_states

GUIDE_STAGES = [
    "👋 **Welcome to VETBOT!**\n\nأهلًا بك في VETBOT، مساحتك التعليمية التي تجمع لك منهجك الدراسي، المحاضرات، السكاشن، الملفات، التسجيلات، والاختبارات في مكان واحد. الجولة دي هتشرح لك كل خطوة بالتفصيل علشان تستخدم البوت بسهولة وتعرف دائمًا تعمل إيه بعد كده.",
    "🔐 **تسجيلك داخل البوت**\n\nأول ما تدخل البوت، يتم تسجيل حساب Telegram الخاص بك تلقائيًا حتى ترتبط به سنتك الدراسية ونتائجك واختباراتك. استخدم حسابك الشخصي الصحيح، ولا تستخدم حساب شخص آخر؛ لأن كل إجاباتك ودرجاتك تُحفظ على حسابك أنت.",
    "🎓 **حدد منهجك الدراسي**\n\nمن القائمة الرئيسية اضغط **Materials**. في أول مرة اختار **السنة الدراسية** ثم **الترم** الصحيح. الاختيار ده مهم جدًا لأن البوت هيعرض لك المواد والمحتوى والاختبارات الخاصة بسنتك وترمك فقط.",
    "📚 **تصفح المواد بالترتيب**\n\nبعد اختيار السنة والترم، اختار المادة التي تريد مذاكرتها. بعد ذلك اختار **Lectures** للمحاضرات أو **Sections** للسكاشن والعملي، ثم اختار عنوان المحاضرة أو السكشن للوصول إلى محتواه.",
    "📄 **الملفات والتسجيلات**\n\nداخل كل محاضرة أو سكشن ستجد **Files** لفتح الملفات المرفوعة مثل PDF وWord والصور، و**Records** لسماع التسجيلات الصوتية. اقرأ النص المصاحب للملف إن وُجد؛ لأنه قد يحتوي على تنبيه أو ترتيب للمذاكرة. يمكن أن يحتوي المحتوى على أكثر من ملف أو تسجيل.",
    "📝 **طريقة حل الاختبار**\n\nاضغط **Quiz** ثم اختر الاختبار المناسب. سيظهر لك سؤال واحد في كل مرة ومعه الاختيارات على أزرار. اقرأ السؤال والاختيارات جيدًا واضغط إجابتك، وبعدها ينتقل البوت للسؤال التالي حتى تنتهي من الاختبار.",
    "⚠️ **شروط مهمة للاختبارات**\n\nلكل اختبار **محاولة رسمية واحدة فقط**. لا تبدأ الاختبار إلا عندما تكون مستعدًا، ولا تضغط على إجابة عشوائية أو تدخل من حساب شخص آخر. بعد تسجيل إجابتك لا يمكن تغييرها، فلا تغلق المحادثة أثناء الحل وتأكد من اتصال الإنترنت لديك.",
    "📊 **النتيجة والمراجعة**\n\nبعد آخر سؤال ستظهر درجتك وعدد إجاباتك الصحيحة، وستظهر لك مراجعة للأسئلة التي أخطأت فيها مع إجابتك والإجابة الصحيحة. استخدم المراجعة للتعلم وفهم الخطأ، وليس فقط لمعرفة الدرجة.",
    "🏆 **الترتيب والشهادة**\n\nبعد مرور **24 ساعة** على نشر الاختبار، تصلك رسالة بنتيجتك وترتيبك. الترتيب يعتمد على **الدرجة الأعلى أولًا**، وعند تساوي الدرجات يكون **الأسبق في تسليم الاختبار** هو صاحب الترتيب الأفضل. يحصل صاحب المركز الأول على شهادة تقدير من VETBOT تُرسل له كصورة.",
    "📢 **قواعد الاستخدام**\n\nاستخدم البوت للتعلم فقط، واحترم زملاءك والإدارة. لا ترسل رسائل أو ملفات غير مناسبة، ولا تحاول الغش أو استخدام أكثر من حساب للحصول على أفضلية. لا تعِد إرسال محتوى البوت خارج الغرض التعليمي أو تنسب إجابات غيرك لنفسك. مخالفة القواعد قد تؤدي إلى تقييد أو إيقاف الوصول إلى البوت.",
    "🛠️ **ماذا تفعل الإدارة؟**\n\nالإدارة مسؤولة عن إضافة المواد والمحاضرات والسكاشن والملفات والتسجيلات والاختبارات، إرسال الإعلانات، متابعة النتائج والترتيب، وحل مشكلات الحسابات. لا تستطيع الإدارة تغيير إجابتك بعد تسجيلها، ولا إعادة محاولة اختبار إلا وفق النظام المعتمد.",
    "✅ **أنت جاهز للبدء**\n\nابدأ باختيار سنتك وترمك، ثم افتح أول مادة، اقرأ أو اسمع المحتوى، وبعدها اختبر فهمك. ذاكر بانتظام، راجع أخطاءك، والتزم بقواعد الاستخدام. اضغط الزر التالي لعرض الكيبورد الرئيسية والبدء في VETBOT.",
]

def show_guide_stage(bot, chat_id, stage):
    markup = types.InlineKeyboardMarkup(row_width=1)
    if stage < len(GUIDE_STAGES) - 1:
        markup.add(types.InlineKeyboardButton("➡️ Next", callback_data=f"guide_next_{stage + 1}"))
    else:
        markup.add(types.InlineKeyboardButton("✅ Start Using VETBOT", callback_data="guide_finish"))
    return bot.send_message(chat_id, GUIDE_STAGES[stage], reply_markup=markup, parse_mode="Markdown")

def show_guide_start(bot, chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("▶️ Start", callback_data="guide_start"))
    bot.send_message(
        chat_id,
        "🌟 **Welcome to VETBOT**\n\nاضغط Start علشان تبدأ جولة التعرف على البوت وتعرف تستخدم كل إمكانياته.",
        reply_markup=markup,
        parse_mode="Markdown"
    )

def is_admin(user_id):
    return get_admin_role(user_id, ADMIN_IDS) is not None

def blocked_message(bot, chat_id):
    bot.send_message(
        chat_id,
        "🚫 لا يمكنك الدخول إلى البوت حاليًا.\n\n"
        "قوانين الصداقة لا تتجزأ ❤️\n"
        "لا يمكن السماح لك بالدخول مرة أخرى إلا بعد مراجعة وموافقة الإدارة.\n\n"
        "يرجى التواصل مع الإدارة إذا كنت ترى أن الحظر تم بالخطأ."
    )

def send_welcome(bot, message, show_keyboard=True):
    user_id = message.from_user.id
    if get_user_status(user_id) == 'banned':
        blocked_message(bot, message.chat.id)
        return
    username = message.from_user.username or "Unknown"
    full_name = " ".join(
        part for part in (message.from_user.first_name, message.from_user.last_name) if part
    ) or "Student"

    add_user_if_not_exists(user_id, username, full_name)
    admin_states.pop(user_id, None)

    markup = build_main_keyboard(user_id) if show_keyboard else types.ReplyKeyboardRemove()

    bot.send_message(
        message.chat.id,
        f"Welcome **{full_name}** to **VETBOT** 🐾\nChoose an option from below:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

def build_main_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🎓 Materials"),
        types.KeyboardButton("👤 My Profile"),
        types.KeyboardButton("⭐ Rate VETBOT")
    )
    if is_admin(user_id):
        markup.add(types.KeyboardButton("🛠️ Admin Panel"))
    return markup

def show_main_menu(bot, user_id, chat_id):
    profile = get_user_profile(user_id)
    full_name = (profile['full_name'] if profile else None) or "Student"
    bot.send_message(
        chat_id,
        f"Welcome **{full_name}** to **VETBOT** 🐾\nChoose an option from below:",
        reply_markup=build_main_keyboard(user_id),
        parse_mode="Markdown"
    )

def register_common_handlers(bot):
    @bot.message_handler(func=lambda message: get_user_status(message.from_user.id) == 'banned')
    def reject_banned_message(message):
        profile = get_user_profile(message.from_user.id)
        if not profile or profile['status'] != 'banned' or profile['blocked_notice_sent']:
            return
        blocked_message(bot, message.chat.id)
        mark_blocked_notice_sent(message.from_user.id)

    @bot.callback_query_handler(func=lambda call: get_user_status(call.from_user.id) == 'banned')
    def reject_banned_callback(call):
        bot.answer_callback_query(call.id, "🚫 Your access to this bot is currently blocked.")

    @bot.message_handler(commands=['start'])
    def handle_start_command(message):
        profile = get_user_profile(message.from_user.id)
        needs_guide = not profile or not profile['guide_seen']
        if needs_guide:
            send_welcome(bot, message, show_keyboard=False)
            show_guide_start(bot, message.chat.id)
        else:
            send_welcome(bot, message)

    @bot.message_handler(commands=['guide'])
    def handle_guide_command(message):
        send_welcome(bot, message, show_keyboard=False)
        show_guide_start(bot, message.chat.id)

    @bot.callback_query_handler(func=lambda call: call.data == "guide_start")
    def guide_start(call):
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "🚀 **Let's begin!**",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )
        show_guide_stage(bot, call.message.chat.id, 0)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("guide_next_"))
    def guide_next(call):
        stage = call.data.replace("guide_next_", "")
        if not stage.isdigit() or int(stage) >= len(GUIDE_STAGES):
            bot.answer_callback_query(call.id, "Invalid step.")
            return
        bot.answer_callback_query(call.id)
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        show_guide_stage(bot, call.message.chat.id, int(stage))

    @bot.callback_query_handler(func=lambda call: call.data == "guide_finish")
    def guide_finish(call):
        mark_guide_seen(call.from_user.id)
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "✅ **You are ready!**\n\nاستخدم أزرار القائمة الرئيسية وابدأ رحلتك.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )
        show_main_menu(bot, call.from_user.id, call.message.chat.id)

    @bot.message_handler(func=lambda message: message.text == "⭐ Rate VETBOT")
    def rate_bot_start(message):
        markup = types.InlineKeyboardMarkup(row_width=1)
        for rating, label in [(5, "⭐ Excellent"), (4, "👍 Very Good"), (3, "🙂 Good"), (2, "😐 Needs Improvement"), (1, "❌ Poor")]:
            markup.add(types.InlineKeyboardButton(label, callback_data=f"rating_{rating}"))
        bot.send_message(message.chat.id, "How would you rate your VETBOT experience?", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("rating_"))
    def save_rating_choice(call):
        rating = call.data.replace("rating_", "")
        if not rating.isdigit() or not 1 <= int(rating) <= 5:
            bot.answer_callback_query(call.id, "Invalid rating.")
            return
        bot.answer_callback_query(call.id, "Your rating was saved.")
        save_bot_rating(call.from_user.id, int(rating))
        bot.register_next_step_handler(
            bot.send_message(call.message.chat.id, "Send an optional comment, or type /skip."),
            save_rating_comment
        )
        bot.edit_message_text(
            "✅ Thank you for your rating!\n\nYour feedback is private and visible only to the Main Admin.",
            call.message.chat.id,
            call.message.message_id
        )

    def save_rating_comment(message):
        if message.text and message.text.strip() != "/skip":
            profile = get_user_profile(message.from_user.id)
            if profile:
                conn = get_db_connection()
                conn.execute(
                    'UPDATE bot_ratings SET comment=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?',
                    (message.text.strip(), message.from_user.id)
                )
                conn.commit()
                conn.close()
        bot.send_message(message.chat.id, "✅ Your feedback was saved privately.")

    # 5. معالجة زر الرجوع العام النصي كأمان إضافي في أي وقت
    @bot.message_handler(func=lambda message: message.text == "🔙 Back to Main Menu")
    def back_to_main_menu_global(message):
        send_welcome(bot, message)

def register_unregistered_handler(bot):
    @bot.message_handler(func=lambda message: not get_user_profile(message.from_user.id))
    def start_unregistered_user(message):
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
        send_welcome(bot, message, show_keyboard=False)
        show_guide_start(bot, message.chat.id)
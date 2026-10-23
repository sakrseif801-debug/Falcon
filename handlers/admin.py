# handlers/admin.py
import os
import sys
import threading
from telebot import types

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ADMIN_IDS, STORAGE_DIR
from database.db_handler import (
    add_subject, add_content, add_content_file, add_quiz_metadata, 
    reset_all_students_academic, reset_single_student_academic, 
    get_users_for_broadcast, get_db_connection, get_content_files, 
    delete_content_file, get_admin_role, admin_has_scope, get_subject_scope,
    get_content_scope, upsert_assistant_admin, list_assistant_admins,
    get_admin_scopes, get_assistant_admins, remove_assistant_admin, publish_quiz, get_quiz_context,
    get_students_for_scope, get_all_students, create_quiz_from_questions,
    claim_content_ready_notification, get_bot_rating_summary, get_bot_ratings
    , get_content_file_for_content, get_quiz_for_content, delete_quiz,
    get_user_profile, get_admin_account, set_user_status, log_moderation_action, handle_telegram_delivery_failure
    , get_students_for_scope
)
from utils.quiz_parser import parse_docx_and_save_quiz
from services.ai_quiz_generator import AIQuizGeneratorService

admin_states = {}
ALL_SCOPES = [(f"{year}{suffix} Year", f"Semester {semester}") for year, suffix in [(1, 'st'), (2, 'nd'), (3, 'rd'), (4, 'th'), (5, 'th')] for semester in (1, 2)]

def register_admin_handlers(bot):
    media_group_buffers = {}

    def queue_media_group(message, mode, callback):
        key = (message.from_user.id, message.media_group_id, mode)
        entry = media_group_buffers.setdefault(key, [])
        entry.append(message)
        if len(entry) == 1:
            timer = threading.Timer(1.0, flush_media_group, args=(key, callback))
            timer.daemon = True
            timer.start()

    def flush_media_group(key, callback):
        messages = media_group_buffers.pop(key, [])
        if messages:
            callback(messages)


    def role_for(user_id):
        return get_admin_role(user_id, ADMIN_IDS)

    def is_admin(message):
        return role_for(message.from_user.id) is not None

    def is_main_admin(user_id):
        return user_id in ADMIN_IDS

    def has_scope(user_id, academic_year, semester):
        return admin_has_scope(user_id, academic_year, semester, ADMIN_IDS)

    def can(user_id, permission):
        role = role_for(user_id)
        permissions = {
            'content_admin': {'add_subject', 'add_title', 'add_record', 'add_file',
                              'delete_subject', 'delete_title', 'delete_record', 'delete_file', 'broadcast'},
            'file_admin': {'add_record', 'add_file'}
        }
        return role == 'main_admin' or permission in permissions.get(role, set())

    def deny(call):
        bot.answer_callback_query(call.id, "You are not allowed to perform this action.")

    def subject_allowed(user_id, subject_id):
        if is_main_admin(user_id):
            return True
        scope = get_subject_scope(subject_id)
        return bool(scope and has_scope(user_id, scope['academic_year'], scope['semester']))

    def content_allowed(user_id, content_id):
        if is_main_admin(user_id):
            return True
        scope = get_content_scope(content_id)
        return bool(scope and has_scope(user_id, scope['academic_year'], scope['semester']))

    def notify_content_ready(content_id):
        context = claim_content_ready_notification(content_id)
        if not context:
            return
        message = (
            "📚 **المحتوى أصبح جاهزًا**\n\n"
            f"📖 المادة: {context['subject_name']}\n"
            f"📄 المحتوى: {context['content_title']}\n\n"
            "تمت إضافة الملفات والتسجيلات ويمكنك البدء بالمذاكرة الآن."
        )
        for student_id in get_students_for_scope(context['academic_year'], context['semester']):
            try:
                bot.send_message(student_id, message, parse_mode="Markdown")
            except Exception as error:
                handle_telegram_delivery_failure(bot, student_id, error)

    def notify_new_quiz(quiz_id):
        context = get_quiz_context(quiz_id)
        if not context:
            return
        message = (
            "📚 **تم إضافة اختبار جديد**\n\n"
            f"📖 المادة: {context['subject_name']}\n"
            f"📄 المحتوى: {context['content_title']}\n\n"
            "📝 اختبار جديد متاح الآن.\n"
            "⏱️ لديك 24 ساعة للمشاركة والدخول ضمن الترتيب.\n"
            "بعد انتهاء المدة سيتم إعلان الدرجة والترتيب بين المشاركين."
        )
        for student_id in get_students_for_scope(context['academic_year'], context['semester']):
            try:
                bot.send_message(student_id, message, parse_mode="Markdown")
            except Exception as error:
                handle_telegram_delivery_failure(bot, student_id, error)
                print(f"Could not notify student {student_id} about quiz {quiz_id}: {error}")

    def get_content_info(content_id):
        """دالة مساعدة لجلب معلومات المحتوى"""
        conn = get_db_connection()
        content = conn.execute('SELECT * FROM contents WHERE content_id=?', (content_id,)).fetchone()
        conn.close()
        return content

    def build_admin_keyboard(user_id):
        role = role_for(user_id)
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        if role == 'main_admin':
            markup.add(
                types.KeyboardButton("➕ Add Content"),
                types.KeyboardButton("❌ Delete Content")
            )
            markup.add(
                types.KeyboardButton("📢 Announcement"),
                types.KeyboardButton("🔄 Reset All Students"),
                types.KeyboardButton("👤 Reset Single Student"),
                types.KeyboardButton("👥 Manage Admins"),
                types.KeyboardButton("👤 User Management"),
                types.KeyboardButton("📊 Student Count"),
                types.KeyboardButton("📬 Admin Announcement"),
                types.KeyboardButton("📊 Bot Ratings")
            )
        elif role == 'content_admin':
            markup.add(
                types.KeyboardButton("🧩 Manage Content"),
                types.KeyboardButton("❌ Delete Content"),
                types.KeyboardButton("📢 Broadcast")
            )
        elif role == 'file_admin':
            markup.add(types.KeyboardButton("📤 Upload Files / Records"))
        markup.add(types.KeyboardButton("🔙 Back to Student Menu"))
        return markup

    def build_student_keyboard():
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(
            types.KeyboardButton("🎓 Materials"),
            types.KeyboardButton("👤 My Profile")
        )
        return markup

    @bot.message_handler(func=lambda message: message.text == "🛠️ Admin Panel" and is_admin(message))
    def open_admin_panel(message):
        # تصفير أي حالة قديمة عند فتح اللوحة[cite: 8]
        admin_states.pop(message.from_user.id, None)
        
        bot.send_message(
            message.chat.id,
            "Welcome to the **VETBOT Admin Panel**.",
            reply_markup=build_admin_keyboard(message.from_user.id),
            parse_mode="Markdown"
        )

    # عند الضغط على زر الرجوع من قائمة الأدمن، بنشغل أمر start برمجياً ليرجعه للطالب[cite: 8]
    @bot.message_handler(func=lambda message: message.text == "🔙 Back to Student Menu" and is_admin(message))
    def back_to_student_menu(message):
        # استدعاء الترحيب الرئيسي للطالب لتغيير الكيبورد[cite: 8]
        bot.clear_step_handler_by_chat_id(chat_id=message.chat.id)
        admin_states.pop(message.from_user.id, None)
        
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(
            types.KeyboardButton("🎓 Materials"),
            types.KeyboardButton("👤 My Profile"),
            types.KeyboardButton("🛠️ Admin Panel")
        )
        bot.send_message(
            message.chat.id,
            "🔄 Returned to Student Main Menu.",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    @bot.message_handler(func=lambda message: message.text == "👥 Manage Admins" and is_main_admin(message.from_user.id))
    def manage_admins_menu(message):
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("➕ Add Assistant Admin", callback_data="adm_manage_add"),
            types.InlineKeyboardButton("📋 View Assistant Admins", callback_data="adm_manage_view"),
            types.InlineKeyboardButton("🔄 Change Role / Scope", callback_data="adm_manage_change"),
            types.InlineKeyboardButton("❌ Remove Assistant Admin", callback_data="adm_manage_remove"),
            types.InlineKeyboardButton("🔙 Back", callback_data="adm_manage_back")
        )
        bot.send_message(message.chat.id, "Assistant Admin Management:", reply_markup=markup)

    @bot.message_handler(func=lambda message: message.text == "👤 User Management" and is_main_admin(message.from_user.id))
    def user_management_menu(message):
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🚫 Ban User", callback_data="mod_ban_start"),
            types.InlineKeyboardButton("🔓 Unban User", callback_data="mod_unban_start"),
            types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_main")
        )
        bot.send_message(message.chat.id, "👤 User Management", reply_markup=markup)

    @bot.message_handler(func=lambda message: message.text == "📊 Student Count" and is_main_admin(message.from_user.id))
    def student_count_menu(message):
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("🌐 All Students", callback_data="adm_count_all"))
        for index, (year, semester) in enumerate(ALL_SCOPES):
            markup.add(types.InlineKeyboardButton(
                f"{year} - {semester}", callback_data=f"adm_count_{index}"
            ))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_count_back"))
        bot.send_message(message.chat.id, "اختر السنة الدراسية والترم:", reply_markup=markup)

    @bot.message_handler(func=lambda message: message.text == "📊 Bot Ratings" and is_main_admin(message.from_user.id))
    def bot_ratings_menu(message):
        summary = {row['rating']: row['count'] for row in get_bot_rating_summary()}
        markup = types.InlineKeyboardMarkup(row_width=1)
        for rating, label in [(5, "⭐ Excellent"), (4, "👍 Very Good"), (3, "🙂 Good"), (2, "😐 Needs Improvement"), (1, "❌ Poor")]:
            markup.add(types.InlineKeyboardButton(
                f"{label}: {summary.get(rating, 0)}", callback_data=f"admin_rating_{rating}"
            ))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_rating_back"))
        bot.send_message(message.chat.id, "📊 Bot Ratings (Main Admin only):", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("admin_rating_") and (call.data.replace("admin_rating_", "").isdigit() or call.data == "admin_rating_back"))
    def bot_rating_details(call):
        if not is_main_admin(call.from_user.id):
            deny(call)
            return
        value = call.data.replace("admin_rating_", "")
        if value == "back":
            bot.answer_callback_query(call.id)
            bot.delete_message(call.message.chat.id, call.message.message_id)
            return
        if not value.isdigit() or not 1 <= int(value) <= 5:
            bot.answer_callback_query(call.id, "Invalid rating.")
            return
        labels = {5: "Excellent", 4: "Very Good", 3: "Good", 2: "Needs Improvement", 1: "Poor"}
        rows = get_bot_ratings(int(value))
        if not rows:
            text = f"{labels[int(value)]}\n\nNo ratings in this category."
        else:
            blocks = []
            for row in rows:
                name = row['full_name'] or "Unknown"
                username = f"@{row['username']}" if row['username'] else "No username"
                comment = row['comment'] or "No comment"
                blocks.append(f"👤 {name}\n🆔 {row['user_id']}\n🔹 {username}\n💬 {comment}")
            text = f"{labels[int(value)]} ratings:\n\n" + "\n\n".join(blocks)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_rating_summary"))
        bot.answer_callback_query(call.id)
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "admin_rating_summary")
    def bot_rating_summary(call):
        if not is_main_admin(call.from_user.id):
            deny(call)
            return
        summary = {row['rating']: row['count'] for row in get_bot_rating_summary()}
        markup = types.InlineKeyboardMarkup(row_width=1)
        for rating, label in [(5, "⭐ Excellent"), (4, "👍 Very Good"), (3, "🙂 Good"), (2, "😐 Needs Improvement"), (1, "❌ Poor")]:
            markup.add(types.InlineKeyboardButton(f"{label}: {summary.get(rating, 0)}", callback_data=f"admin_rating_{rating}"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_rating_back"))
        bot.answer_callback_query(call.id)
        bot.edit_message_text("📊 Bot Ratings (Main Admin only):", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_count_"))
    def show_student_count(call):
        if not is_main_admin(call.from_user.id):
            deny(call)
            return
        value = call.data.replace("adm_count_", "")
        if value == "back":
            bot.answer_callback_query(call.id)
            bot.delete_message(call.message.chat.id, call.message.message_id)
            return
        if value == "all":
            count = len(get_all_students())
            bot.answer_callback_query(call.id)
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_count_back"))
            bot.edit_message_text(
                f"📊 Total registered students in VETBOT: {count}",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            return
        if not value.isdigit() or int(value) >= len(ALL_SCOPES):
            bot.answer_callback_query(call.id, "اختيار غير صالح.")
            return
        year, semester = ALL_SCOPES[int(value)]
        count = len(get_students_for_scope(year, semester))
        bot.answer_callback_query(call.id)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_count_back"))
        bot.edit_message_text(
            f"📊 عدد الطلاب المسجلين\n\n{year} - {semester}: {count}",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )

    @bot.message_handler(func=lambda message: message.text == "📬 Admin Announcement" and is_main_admin(message.from_user.id))
    def admin_announcement_start(message):
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("Content Admin", callback_data="adm_bc_role_content"),
            types.InlineKeyboardButton("File Admin", callback_data="adm_bc_role_file")
        )
        markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_bc_cancel"))
        bot.send_message(message.chat.id, "Choose the assistant admin type:", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_bc_role_"))
    def admin_announcement_role(call):
        if not is_main_admin(call.from_user.id):
            deny(call)
            return
        role = call.data.replace("adm_bc_role_", "")
        if role not in {"content", "file"}:
            deny(call)
            return
        admin_states[call.from_user.id] = {"admin_broadcast_role": f"{role}_admin"}
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🌐 All admins of this type", callback_data="adm_bc_scope_all"))
        for index, (year, semester) in enumerate(ALL_SCOPES):
            markup.add(types.InlineKeyboardButton(
                f"{year} - {semester}", callback_data=f"adm_bc_scope_{index}"
            ))
        markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_bc_cancel"))
        bot.edit_message_text("Choose all admins or a specific academic scope:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_bc_scope_"))
    def admin_announcement_scope(call):
        if not is_main_admin(call.from_user.id):
            deny(call)
            return
        state = admin_states.get(call.from_user.id)
        if not state:
            deny(call)
            return
        value = call.data.replace("adm_bc_scope_", "")
        if value == "all":
            state["admin_broadcast_scope"] = None
        elif value.isdigit() and int(value) < len(ALL_SCOPES):
            state["admin_broadcast_scope"] = ALL_SCOPES[int(value)]
        else:
            bot.answer_callback_query(call.id, "Invalid scope.")
            return
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "Send the message now. Text, file, image, audio, video, or any other type is supported.")
        bot.register_next_step_handler(msg, process_admin_broadcast_message)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_bc_cancel")
    def cancel_admin_announcement(call):
        if not is_main_admin(call.from_user.id):
            deny(call)
            return
        admin_states.pop(call.from_user.id, None)
        bot.answer_callback_query(call.id)
        bot.edit_message_text("❌ Admin announcement cancelled.", call.message.chat.id, call.message.message_id)

    def process_admin_broadcast_message(message):
        user_id = message.from_user.id
        state = admin_states.get(user_id)
        if not state or not is_main_admin(user_id):
            return
        if message.media_group_id:
            state["admin_broadcast_media_group_id"] = message.media_group_id
            queue_media_group(message, "admin_broadcast", process_admin_broadcast_batch)
            return
        state["admin_broadcast_messages"] = [(message.chat.id, message.message_id)]
        send_admin_broadcast_confirmation(message.chat.id)

    @bot.message_handler(func=lambda message: bool(
        message.media_group_id
        and admin_states.get(message.from_user.id, {}).get("admin_broadcast_media_group_id") == message.media_group_id
    ))
    def collect_admin_broadcast_media_group(message):
        queue_media_group(message, "admin_broadcast", process_admin_broadcast_batch)

    def process_admin_broadcast_batch(messages):
        if not messages:
            return
        user_id = messages[0].from_user.id
        state = admin_states.get(user_id)
        if not state:
            return
        state["admin_broadcast_messages"] = [
            (message.chat.id, message.message_id) for message in messages
        ]
        state.pop("admin_broadcast_media_group_id", None)
        send_admin_broadcast_confirmation(messages[0].chat.id)

    def send_admin_broadcast_confirmation(chat_id):
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Confirm Send", callback_data="adm_bc_confirm"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="adm_bc_cancel")
        )
        bot.send_message(chat_id, "⚠️ Confirm sending this message to the selected admins?", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_bc_confirm")
    def confirm_admin_announcement(call):
        user_id = call.from_user.id
        state = admin_states.get(user_id)
        if not is_main_admin(user_id) or not state:
            deny(call)
            return
        role = state.get("admin_broadcast_role")
        scope = state.get("admin_broadcast_scope")
        if scope:
            recipients = get_assistant_admins(role, scope[0], scope[1])
        else:
            recipients = get_assistant_admins(role)
        messages = state.get("admin_broadcast_messages", [])
        if not messages or not recipients:
            bot.answer_callback_query(call.id, "No message or matching admins found.")
            return
        success_count = 0
        for target_id in recipients:
            try:
                bot.send_message(target_id, "📢 Message from Main Admin")
                for source_chat_id, source_message_id in messages:
                    bot.copy_message(target_id, source_chat_id, source_message_id)
                success_count += 1
            except Exception as error:
                handle_telegram_delivery_failure(bot, target_id, error)
        bot.answer_callback_query(call.id)
        bot.edit_message_text(f"✅ Message sent to {success_count} admins.", call.message.chat.id, call.message.message_id)
        log_moderation_action(0, user_id, "ADMIN_BROADCAST_SENT")
        admin_states.pop(user_id, None)

    @bot.callback_query_handler(func=lambda call: call.data in {"mod_ban_start", "mod_unban_start"})
    def moderation_action_start(call):
        if not is_main_admin(call.from_user.id):
            deny(call)
            return
        action = 'ban' if call.data == 'mod_ban_start' else 'unban'
        admin_states[call.from_user.id] = {'moderation_action': action}
        prompt = "أرسل Telegram User ID للمستخدم الذي تريد حظره." if action == 'ban' else "أرسل Telegram User ID لإلغاء الحظر."
        msg = bot.send_message(call.message.chat.id, prompt)
        bot.register_next_step_handler(msg, process_moderation_user_id)

    def process_moderation_user_id(message):
        admin_id = message.from_user.id
        state = admin_states.get(admin_id)
        if not state or not is_main_admin(admin_id):
            return
        target_text = (message.text or '').strip()
        if not target_text.isdigit():
            bot.send_message(message.chat.id, "❌ يجب إرسال Telegram User ID صحيح. تم إلغاء العملية.")
            admin_states.pop(admin_id, None)
            return
        target_id = int(target_text)
        if target_id in ADMIN_IDS:
            bot.send_message(message.chat.id, "❌ لا يمكن حظر Main Admin.")
            admin_states.pop(admin_id, None)
            return
        profile = get_user_profile(target_id)
        if not profile:
            bot.send_message(message.chat.id, "❌ هذا المستخدم غير مسجل في البوت.")
            admin_states.pop(admin_id, None)
            return
        state['target_user_id'] = target_id
        label = 'حظر' if state['moderation_action'] == 'ban' else 'إلغاء حظر'
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Confirm", callback_data="mod_confirm"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="mod_cancel")
        )
        bot.send_message(message.chat.id, f"⚠️ هل أنت متأكد من {label} المستخدم؟\n\nUser ID: `{target_id}`", parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "mod_confirm")
    def moderation_confirm(call):
        if not is_main_admin(call.from_user.id):
            deny(call)
            return
        state = admin_states.get(call.from_user.id)
        if not state or 'target_user_id' not in state:
            deny(call)
            return
        target_id = state['target_user_id']
        if target_id in ADMIN_IDS:
            deny(call)
            return
        action = state['moderation_action']
        if action == 'ban':
            reset_single_student_academic(target_id)
            try:
                from handlers.student import student_quiz_states
                student_quiz_states.pop(target_id, None)
            except Exception:
                pass
        set_user_status(target_id, 'banned' if action == 'ban' else 'active')
        log_moderation_action(target_id, call.from_user.id, 'BAN_USER' if action == 'ban' else 'UNBAN_USER')
        bot.edit_message_text("✅ تم تنفيذ العملية بنجاح.\n\nUser ID: " + str(target_id), call.message.chat.id, call.message.message_id)
        admin_states.pop(call.from_user.id, None)

    @bot.callback_query_handler(func=lambda call: call.data == "mod_cancel")
    def moderation_cancel(call):
        if is_main_admin(call.from_user.id):
            admin_states.pop(call.from_user.id, None)
            bot.edit_message_text("❌ تم إلغاء العملية.", call.message.chat.id, call.message.message_id)

    @bot.message_handler(func=lambda message: message.text == "🤖 AI Quiz Generation" and can(message.from_user.id, 'add_ai_quiz'))
    def direct_ai_quiz_start(message):
        user_id = message.from_user.id
        admin_states[user_id] = {'action_type': 'add_ai_quiz'}
        if role_for(user_id) == 'content_admin':
            send_ai_subject_selection(message)
        else:
            send_ai_year_selection(message)

    def send_ai_year_selection(message):
        markup = types.InlineKeyboardMarkup(row_width=1)
        for year in ("1st Year", "2nd Year", "3rd Year", "4th Year", "5th Year"):
            markup.add(types.InlineKeyboardButton(year, callback_data=f"adm_yr_{year}"))
        markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_back_main"))
        bot.send_message(message.chat.id, "Select the Academic Year for the AI Quiz:", reply_markup=markup)

    def send_ai_subject_selection(message):
        user_id = message.from_user.id
        conn = get_db_connection()
        subjects = conn.execute('SELECT * FROM subjects ORDER BY academic_year, semester, subject_name').fetchall()
        conn.close()
        subjects = [subject for subject in subjects if subject_allowed(user_id, subject['subject_id'])]
        markup = types.InlineKeyboardMarkup(row_width=1)
        if not subjects:
            markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_back_main"))
            bot.send_message(message.chat.id, "❌ No subjects are available in your scope.", reply_markup=markup)
            return
        for subject in subjects:
            markup.add(types.InlineKeyboardButton(subject['subject_name'], callback_data=f"adm_ai_sub_{subject['subject_id']}"))
        markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_back_main"))
        bot.send_message(message.chat.id, "Select the Subject for the AI Quiz:", reply_markup=markup)

    def ensure_main_admin(call):
        if not is_main_admin(call.from_user.id):
            deny(call)
            return False
        return True

    def show_role_menu(call, mode):
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("Content Admin", callback_data=f"adm_role_content_{mode}"),
            types.InlineKeyboardButton("File Upload Admin", callback_data=f"adm_role_file_{mode}"),
            types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_manage_back")
        )
        bot.edit_message_text("Choose the assistant role:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_manage_add")
    def manage_add_selected(call):
        if ensure_main_admin(call):
            show_role_menu(call, 'add')

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_role_"))
    def assistant_role_selected(call):
        if not ensure_main_admin(call):
            return
        _, _, role_name, mode = call.data.split("_")
        role = 'content_admin' if role_name == 'content' else 'file_admin'
        previous_state = admin_states.get(call.from_user.id, {})
        admin_states[call.from_user.id] = {
            'manage_mode': mode,
            'assistant_role': role,
            'scope_indexes': set(),
            'target_admin_id': previous_state.get('target_admin_id')
        }
        show_scope_menu(call)

    def show_scope_menu(call):
        state = admin_states[call.from_user.id]
        markup = types.InlineKeyboardMarkup(row_width=2)
        for index, (year, semester) in enumerate(ALL_SCOPES):
            mark = "✅ " if index in state['scope_indexes'] else ""
            markup.add(types.InlineKeyboardButton(f"{mark}{year} - {semester}", callback_data=f"adm_scope_{index}"))
        markup.add(types.InlineKeyboardButton("🌐 All Years / All Terms", callback_data="adm_scope_all"))
        markup.add(types.InlineKeyboardButton("✅ Continue", callback_data="adm_scope_done"))
        markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_manage_back"))
        bot.edit_message_text("Choose one or more allowed academic scopes:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_scope_") and call.data.replace("adm_scope_", "").isdigit())
    def assistant_scope_selected(call):
        if not ensure_main_admin(call):
            return
        if call.data == 'adm_scope_all':
            admin_states[call.from_user.id]['scope_indexes'] = set(range(len(ALL_SCOPES)))
        else:
            index = int(call.data.replace('adm_scope_', ''))
            indexes = admin_states[call.from_user.id]['scope_indexes']
            if index in indexes:
                indexes.remove(index)
            else:
                indexes.add(index)
        show_scope_menu(call)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_scope_done")
    def assistant_scope_done(call):
        if not ensure_main_admin(call):
            return
        state = admin_states.get(call.from_user.id)
        if not state or not state['scope_indexes']:
            bot.answer_callback_query(call.id, "Choose at least one scope.")
            return
        msg = bot.send_message(call.message.chat.id, "Send the Telegram User ID only:")
        bot.register_next_step_handler(msg, process_assistant_id)

    def process_assistant_id(message):
        user_id = message.from_user.id
        state = admin_states.get(user_id)
        if not state or not is_main_admin(user_id):
            return
        target_text = message.text.strip()
        if not target_text.isdigit() or int(target_text) in ADMIN_IDS:
            bot.send_message(message.chat.id, "Invalid ID or Main Admin ID. Operation cancelled.")
            admin_states.pop(user_id, None)
            return
        state['target_admin_id'] = int(target_text)
        scopes = [ALL_SCOPES[index] for index in state['scope_indexes']]
        scope_text = 'All Years / All Terms' if len(scopes) == len(ALL_SCOPES) else ', '.join(f'{year} - {semester}' for year, semester in scopes)
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("✅ Confirm", callback_data="adm_confirm_admin"), types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_manage_back"))
        bot.send_message(message.chat.id, f"User ID: {target_text}\nRole: {state['assistant_role']}\nScope: {scope_text}", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_confirm_admin")
    def confirm_assistant_admin(call):
        if not ensure_main_admin(call):
            return
        state = admin_states.get(call.from_user.id)
        if not state or 'target_admin_id' not in state:
            deny(call)
            return
        scopes = [ALL_SCOPES[index] for index in state['scope_indexes']]
        upsert_assistant_admin(state['target_admin_id'], state['assistant_role'], scopes)
        bot.edit_message_text("✅ Assistant Admin created successfully.", call.message.chat.id, call.message.message_id)
        try:
            bot.send_message(
                state['target_admin_id'],
                "✅ You have been added as an Assistant Admin. Your Admin Panel is ready.",
                reply_markup=build_admin_keyboard(state['target_admin_id'])
            )
        except Exception as error:
            bot.send_message(
                call.message.chat.id,
                f"⚠️ Admin saved, but Telegram could not send the keyboard to user {state['target_admin_id']}. They must press /start first."
            )
        admin_states.pop(call.from_user.id, None)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_manage_view")
    def manage_view_admins(call):
        if not ensure_main_admin(call):
            return
        rows = list_assistant_admins()
        if not rows:
            text = "No Assistant Admins configured."
        else:
            blocks = []
            for row in rows:
                scopes = get_admin_scopes(row['user_id'])
                scope_text = 'All Years / All Terms' if len(scopes) == len(ALL_SCOPES) else ', '.join(f"{s['academic_year']} - {s['semester']}" for s in scopes)
                blocks.append(f"ID: {row['user_id']}\nRole: {row['role']}\nScope: {scope_text}")
            text = '\n\n'.join(blocks)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_manage_back"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_manage_remove")
    def manage_remove_admins(call):
        if not ensure_main_admin(call):
            return
        markup = types.InlineKeyboardMarkup(row_width=1)
        for row in list_assistant_admins():
            markup.add(types.InlineKeyboardButton(f"❌ Remove {row['user_id']} ({row['role']})", callback_data=f"adm_remove_{row['user_id']}"))
        markup.add(types.InlineKeyboardButton("🆔 Remove by Telegram ID", callback_data="adm_remove_by_id"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_manage_back"))
        bot.edit_message_text("Select an Assistant Admin to remove:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_remove_by_id")
    def remove_admin_by_id_start(call):
        if not ensure_main_admin(call):
            return
        msg = bot.send_message(call.message.chat.id, "Send the Assistant Admin Telegram User ID:")
        bot.register_next_step_handler(msg, process_remove_admin_by_id)

    def process_remove_admin_by_id(message):
        if not is_main_admin(message.from_user.id):
            return
        target_text = (message.text or '').strip()
        if not target_text.isdigit() or int(target_text) in ADMIN_IDS:
            bot.send_message(message.chat.id, "❌ Invalid ID or Main Admin ID.")
            return
        account = get_admin_account(int(target_text))
        if not account:
            bot.send_message(message.chat.id, "❌ Assistant Admin not found.")
            return
        admin_states[message.from_user.id] = {'remove_admin_id': int(target_text)}
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Confirm Remove", callback_data="adm_confirm_remove"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="adm_cancel_remove")
        )
        bot.send_message(message.chat.id, f"⚠️ Remove Assistant Admin?\n\nID: `{target_text}`\nRole: {account['role']}", parse_mode='Markdown', reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_remove_"))
    def remove_admin_selected(call):
        if not ensure_main_admin(call):
            return
        target_id = int(call.data.replace('adm_remove_', ''))
        account = get_admin_account(target_id)
        if not account:
            deny(call)
            return
        scopes = get_admin_scopes(target_id)
        scope_text = ', '.join(f"{scope['academic_year']} - {scope['semester']}" for scope in scopes) or 'Not assigned'
        admin_states[call.from_user.id] = {'remove_admin_id': target_id}
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Confirm Remove", callback_data="adm_confirm_remove"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="adm_cancel_remove")
        )
        bot.edit_message_text(
    f"⚠️ Remove Assistant Admin?\n\n👤 ID: {target_id}\nRole: {account['role']}\nScope: {scope_text}",
    call.message.chat.id, 
    call.message.message_id, 
    parse_mode=None, 
    reply_markup=markup
)

    @bot.callback_query_handler(func=lambda call: call.data in {"adm_confirm_remove", "adm_cancel_remove"})
    def confirm_remove_admin(call):
        if not ensure_main_admin(call):
            return
        state = admin_states.get(call.from_user.id)
        if not state or 'remove_admin_id' not in state:
            deny(call)
            return
        target_id = state['remove_admin_id']
        if call.data == 'adm_cancel_remove':
            bot.edit_message_text("❌ Removal cancelled.", call.message.chat.id, call.message.message_id)
        else:
            remove_assistant_admin(target_id)
            log_moderation_action(target_id, call.from_user.id, 'ASSISTANT_ADMIN_REMOVED')
            bot.edit_message_text("✅ Assistant Admin removed successfully.", call.message.chat.id, call.message.message_id)
            try:
                bot.send_message(target_id, "Your Assistant Admin role has been removed.", reply_markup=build_student_keyboard())
            except Exception as error:
                handle_telegram_delivery_failure(bot, target_id, error)
        admin_states.pop(call.from_user.id, None)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_manage_change")
    def manage_change_admins(call):
        if not ensure_main_admin(call):
            return
        markup = types.InlineKeyboardMarkup(row_width=1)
        for row in list_assistant_admins():
            markup.add(types.InlineKeyboardButton(f"🔄 Change {row['user_id']} ({row['role']})", callback_data=f"adm_change_{row['user_id']}"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_manage_back"))
        bot.edit_message_text("Select an Assistant Admin to change:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_change_"))
    def change_admin_selected(call):
        if ensure_main_admin(call):
            admin_states[call.from_user.id] = {'manage_mode': 'change', 'target_admin_id': int(call.data.replace('adm_change_', ''))}
            show_role_menu(call, 'change')

    @bot.callback_query_handler(func=lambda call: call.data == "adm_manage_back")
    def manage_admins_back(call):
        if ensure_main_admin(call):
            admin_states.pop(call.from_user.id, None)
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            bot.send_message(call.message.chat.id, "Returned to Admin Panel.")

    # --- القائمة الرئيسية للحذف ---
    @bot.message_handler(func=lambda message: message.text == "❌ Delete Content" and is_admin(message))
    def delete_content_main_menu(message):
        user_id = message.from_user.id
        markup = types.InlineKeyboardMarkup(row_width=1)
        if can(user_id, 'delete_subject'):
            markup.add(types.InlineKeyboardButton("🗑️ Delete Subject (حذف مادة بالكامل)", callback_data="adm_del_subject"))
        if can(user_id, 'delete_title'):
            markup.add(types.InlineKeyboardButton("🗑️ Delete Lecture/Section Title (حذف عنوان)", callback_data="adm_del_title"))
        if can(user_id, 'delete_record') or can(user_id, 'delete_file') or can(user_id, 'delete_quiz'):
            markup.add(types.InlineKeyboardButton("🗑️ Delete File / Record / Quiz", callback_data="adm_del_assets"))
        markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_back_main"))
        bot.send_message(message.chat.id, "What do you want to delete?", reply_markup=markup)

    @bot.message_handler(func=lambda message: message.text == "🧩 Manage Content" and role_for(message.from_user.id) == 'content_admin')
    def content_admin_manage_content(message):
        add_content_main_menu(message)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_del_"))
    def delete_action_selected(call):
        action = call.data.replace("adm_del_", "")
        if action == 'assets':
            admin_states[call.from_user.id] = {"action_type": "delete_assets"}
            show_year_selection(call)
            return
        if not can(call.from_user.id, f"delete_{action}"):
            deny(call)
            return
        admin_states[call.from_user.id] = {"action_type": f"delete_{action}"}
        show_year_selection(call)

    # --- القائمة الرئيسية للإضافة ---
    @bot.message_handler(func=lambda message: message.text == "➕ Add Content" and is_admin(message))
    def add_content_main_menu(message):
        markup = types.InlineKeyboardMarkup(row_width=1)
        user_id = message.from_user.id
        if can(user_id, 'add_subject'):
            markup.add(types.InlineKeyboardButton("📚 Add New Subject (مادة جديدة)", callback_data="adm_add_subject"))
        if can(user_id, 'add_title'):
            markup.add(types.InlineKeyboardButton("📖 Add Lecture/Section Title", callback_data="adm_add_title"))
        if can(user_id, 'add_record'):
            markup.add(types.InlineKeyboardButton("🎙️ Add Audio Record", callback_data="adm_add_record"))
        if can(user_id, 'add_file'):
            markup.add(types.InlineKeyboardButton("📄 Add File (PDF/Word/Images)", callback_data="adm_add_file"))
        if can(user_id, 'add_quiz'):
            markup.add(types.InlineKeyboardButton("📝 Add MCQ Quiz (.docx)", callback_data="adm_add_quiz"))
        if is_main_admin(user_id):
            markup.add(types.InlineKeyboardButton("🤖 AI Quiz Generation", callback_data="adm_add_ai_quiz"))
        markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_back_main"))
        bot.send_message(message.chat.id, "What do you want to add?", reply_markup=markup)

    @bot.message_handler(func=lambda message: message.text == "📤 Upload Files / Records" and role_for(message.from_user.id) == 'file_admin')
    def file_admin_upload_menu(message):
        add_content_main_menu(message)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_add_"))
    def add_action_selected(call):
        action = call.data.replace("adm_add_", "")
        if not can(call.from_user.id, f"add_{action}"):
            deny(call)
            return
        admin_states[call.from_user.id] = {"action_type": f"add_{action}"}
        if action == 'ai_quiz' and role_for(call.from_user.id) == 'content_admin':
            show_ai_subject_selection(call)
        else:
            show_year_selection(call)

    def show_ai_subject_selection(call):
        user_id = call.from_user.id
        conn = get_db_connection()
        subjects = conn.execute('SELECT * FROM subjects ORDER BY academic_year, semester, subject_name').fetchall()
        conn.close()
        subjects = [subject for subject in subjects if subject_allowed(user_id, subject['subject_id'])]
        markup = types.InlineKeyboardMarkup(row_width=1)
        if not subjects:
            markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_back_main"))
            bot.edit_message_text("❌ No subjects are available in your scope.", call.message.chat.id, call.message.message_id, reply_markup=markup)
            return
        for subject in subjects:
            markup.add(types.InlineKeyboardButton(subject['subject_name'], callback_data=f"adm_ai_sub_{subject['subject_id']}"))
        markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_back_main"))
        bot.edit_message_text("Select the Subject for the AI Quiz:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    def show_ai_content_type_menu(call):
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("📖 Lecture", callback_data="adm_ai_type_lecture"),
            types.InlineKeyboardButton("🔬 Section", callback_data="adm_ai_type_section")
        )
        markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_back_main"))
        bot.edit_message_text("Select Lecture or Section:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    def show_ai_content_selection(call, content_type):
        user_id = call.from_user.id
        subject_id = admin_states[user_id]['subject_id']
        conn = get_db_connection()
        contents = conn.execute(
            'SELECT * FROM contents WHERE subject_id=? AND content_type=? ORDER BY content_id',
            (subject_id, content_type)
        ).fetchall()
        conn.close()
        contents = [content for content in contents if content_allowed(user_id, content['content_id'])]
        markup = types.InlineKeyboardMarkup(row_width=1)
        if not contents:
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_ai_back_type"))
            bot.edit_message_text(f"❌ No {content_type}s found for this subject.", call.message.chat.id, call.message.message_id, reply_markup=markup)
            return
        for content in contents:
            markup.add(types.InlineKeyboardButton(content['title'], callback_data=f"adm_ai_content_{content['content_id']}"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_ai_back_type"))
        bot.edit_message_text(f"Select the {content_type.capitalize()}:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    def show_ai_question_count_menu(call):
        markup = types.InlineKeyboardMarkup(row_width=2)
        for count in (20, 40, 60, 80, 100):
            markup.add(types.InlineKeyboardButton(f"{count} Questions", callback_data=f"adm_ai_count_{count}"))
        markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="adm_back_main"))
        bot.edit_message_text("Select the number of questions:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_ai_sub_"))
    def ai_subject_selected(call):
        user_id = call.from_user.id
        if user_id not in admin_states or not can(user_id, 'add_ai_quiz'):
            deny(call)
            return
        subject_id = call.data.replace('adm_ai_sub_', '')
        if not subject_allowed(user_id, subject_id):
            deny(call)
            return
        admin_states[user_id]['subject_id'] = subject_id
        show_ai_content_type_menu(call)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_ai_type_"))
    def ai_content_type_selected(call):
        user_id = call.from_user.id
        if user_id not in admin_states or not can(user_id, 'add_ai_quiz'):
            deny(call)
            return
        content_type = call.data.replace('adm_ai_type_', '')
        admin_states[user_id]['content_type'] = content_type
        show_ai_content_selection(call, content_type)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_ai_content_"))
    def ai_content_selected(call):
        user_id = call.from_user.id
        content_id = call.data.replace('adm_ai_content_', '')
        if user_id not in admin_states or not can(user_id, 'add_ai_quiz') or not content_allowed(user_id, content_id):
            deny(call)
            return
        admin_states[user_id]['content_id'] = content_id
        show_ai_question_count_menu(call)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_ai_back_type")
    def ai_back_to_content_type(call):
        if call.from_user.id in admin_states:
            show_ai_content_type_menu(call)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_ai_count_"))
    def ai_question_count_selected(call):
        user_id = call.from_user.id
        if user_id not in admin_states or not can(user_id, 'add_ai_quiz'):
            deny(call)
            return
        try:
            question_count = int(call.data.replace('adm_ai_count_', ''))
        except ValueError:
            deny(call)
            return
        if question_count not in AIQuizGeneratorService.ALLOWED_COUNTS:
            deny(call)
            return
        content_id = admin_states[user_id].get('content_id')
        if not content_id or not content_allowed(user_id, content_id):
            deny(call)
            return
        admin_states[user_id]['question_count'] = question_count
        bot.edit_message_text("🤖 Generating Quiz... Please wait.", call.message.chat.id, call.message.message_id)
        try:
            questions = AIQuizGeneratorService().generate(bot, content_id, question_count)
            quiz_id = create_quiz_from_questions(content_id, questions)
            publish_quiz(quiz_id)
            notify_new_quiz(quiz_id)
        except Exception as error:
            print(f"AI quiz generation failed for content {content_id}: {error}")
            if '20 MB' in str(error):
                bot.send_message(
                    call.message.chat.id,
                    "❌ Unable to read the source file because Telegram limits bot downloads to 20 MB. "
                    "Please split the file into smaller files and upload them to this Lecture/Section."
                )
            else:
                bot.send_message(call.message.chat.id, "❌ Unable to generate the quiz. Please try again.")
        else:
            bot.send_message(call.message.chat.id, f"✅ Quiz {quiz_id} created and published successfully.")
        finally:
            admin_states.pop(user_id, None)

    def show_year_selection(call):
        markup = types.InlineKeyboardMarkup(row_width=1)
        years = ["1st Year", "2nd Year", "3rd Year", "4th Year", "5th Year"]
        for y in years:
            markup.add(types.InlineKeyboardButton(y, callback_data=f"adm_yr_{y}"))
        
        # إضافة زر إلغاء/رجوع للقائمة الرئيسية للأدمن[cite: 8]
        markup.add(types.InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_back_main"))
        bot.edit_message_text("Select the Academic Year:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    # معالجة زر الرجوع للقائمة الرئيسية للوحة الأدمن Inline[cite: 8]
    @bot.callback_query_handler(func=lambda call: call.data == "adm_back_main")
    def inline_back_to_main_admin(call):
        admin_states.pop(call.from_user.id, None)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.send_message(call.message.chat.id, "Main Admin Panel Options:")

    # --- خطوات اختيار السنة والترم والمادة المشتركة ---
    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_yr_"))
    def handle_year_selection(call):
        user_id = call.from_user.id
        if user_id not in admin_states:
            bot.answer_callback_query(call.id, "This admin action expired. Please open the Admin Panel again.")
            return

        year = call.data.replace("adm_yr_", "")
        admin_states[user_id]["year"] = year
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("Semester 1", callback_data="adm_sem_1"), types.InlineKeyboardButton("Semester 2", callback_data="adm_sem_2"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_year"))
        bot.edit_message_text("Select the Semester:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_back_to_year")
    def back_to_year_step(call):
        show_year_selection(call)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_sem_"))
    def handle_semester_selection(call):
        sem_num = call.data.replace("adm_sem_", "")
        semester = f"Semester {sem_num}"
        user_id = call.from_user.id
        if user_id not in admin_states:
            bot.answer_callback_query(call.id, "This admin action expired. Please open the Admin Panel again.")
            return

        if not has_scope(user_id, admin_states[user_id]["year"], semester):
            bot.answer_callback_query(call.id, "This academic scope is not assigned to you.")
            return

        admin_states[user_id]["semester"] = semester
        
        full_action = admin_states[user_id]["action_type"]
        
        if full_action == "add_subject":
            msg = bot.send_message(call.message.chat.id, "Please type the **New Subject Name**:")
            bot.register_next_step_handler(msg, process_add_subject_name)
        else:
            show_subject_selection_menu(call, user_id, semester)

    def show_subject_selection_menu(call, user_id, semester):
        conn = get_db_connection()
        subjects = conn.execute('SELECT * FROM subjects WHERE academic_year=? AND semester=?', 
                                (admin_states[user_id]["year"], semester)).fetchall()
        conn.close()
        subjects = [subject for subject in subjects if subject_allowed(user_id, subject['subject_id'])]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        if not subjects:
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_sem"))
            bot.edit_message_text("❌ No subjects found for this semester.", call.message.chat.id, call.message.message_id, reply_markup=markup)
            return
            
        for sub in subjects:
            markup.add(types.InlineKeyboardButton(sub['subject_name'], callback_data=f"adm_sub_{sub['subject_id']}"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_sem"))
        bot.edit_message_text("Select the Subject:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_back_to_sem")
    def back_to_semester_step(call):
        user_id = call.from_user.id
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("Semester 1", callback_data="adm_sem_1"), types.InlineKeyboardButton("Semester 2", callback_data="adm_sem_2"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_year"))
        bot.edit_message_text("Select the Semester:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    def process_add_subject_name(message):
        user_id = message.from_user.id
        if user_id not in admin_states: return
        sub_name = message.text.strip()
        add_subject(sub_name, admin_states[user_id]["year"], admin_states[user_id]["semester"])
        bot.send_message(message.chat.id, f"✅ Subject '{sub_name}' added successfully!")
        admin_states.pop(user_id, None)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_sub_"))
    def handle_subject_selection(call):
        sub_id = call.data.replace("adm_sub_", "")
        user_id = call.from_user.id
        if user_id not in admin_states or not subject_allowed(user_id, sub_id):
            deny(call)
            return
        admin_states[user_id]["subject_id"] = sub_id
        full_action = admin_states[user_id]["action_type"]
        if not can(user_id, full_action):
            deny(call)
            return
        
        if full_action == "delete_subject":
            conn = get_db_connection()
            conn.execute('DELETE FROM subjects WHERE subject_id=?', (sub_id,))
            conn.commit()
            conn.close()
            bot.edit_message_text("✅ Subject and everything inside it deleted successfully!", call.message.chat.id, call.message.message_id)
            admin_states.pop(user_id, None)
            return

        if full_action == "add_title":
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton("Lecture", callback_data="adm_type_lecture"), types.InlineKeyboardButton("Section", callback_data="adm_type_section"))
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_sub"))
            bot.edit_message_text("Is it a Lecture or a Section?", call.message.chat.id, call.message.message_id, reply_markup=markup)
        elif full_action == "add_ai_quiz":
            show_ai_content_type_menu(call)
        else:
            show_content_target_menu(call, user_id, sub_id)

    @bot.callback_query_handler(func=lambda call: call.data == "adm_back_to_sub")
    def back_to_subject_step(call):
        user_id = call.from_user.id
        show_subject_selection_menu(call, user_id, admin_states[user_id]["semester"])

    def show_content_target_menu(call, user_id, sub_id):
        conn = get_db_connection()
        contents = conn.execute('SELECT * FROM contents WHERE subject_id=?', (sub_id,)).fetchall()
        conn.close()
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        if not contents:
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_sub"))
            bot.edit_message_text("❌ No Lectures/Sections found in this subject.", call.message.chat.id, call.message.message_id, reply_markup=markup)
            return
            
        for c in contents:
            markup.add(types.InlineKeyboardButton(c['title'], callback_data=f"adm_tg_c_{c['content_id']}"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_sub"))
        bot.edit_message_text("Select the Lecture/Section target:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_type_"))
    def add_title_type_selected(call):
        if call.from_user.id not in admin_states or not can(call.from_user.id, 'add_title'):
            deny(call)
            return
        c_type = call.data.replace("adm_type_", "")
        admin_states[call.from_user.id]["content_type"] = c_type
        msg = bot.send_message(call.message.chat.id, "Type the sequential title (e.g., Lecture 1):")
        bot.register_next_step_handler(msg, process_add_title_finish)

    def process_add_title_finish(message):
        user_id = message.from_user.id
        if user_id not in admin_states: return
        title = message.text.strip()
        add_content(subject_id=admin_states[user_id]["subject_id"], content_type=admin_states[user_id]["content_type"], title=title)
        bot.send_message(message.chat.id, f"✅ Title '{title}' created successfully!")
        admin_states.pop(user_id, None)

    # --- معالجة المحاضرة المستهدفة ورفع الميديا والحذف ---
    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_tg_c_"))
    def handle_content_target_selection(call):
        content_id = call.data.replace("adm_tg_c_", "")
        user_id = call.from_user.id
        if user_id not in admin_states or not content_allowed(user_id, content_id):
            deny(call)
            return
        admin_states[user_id]["content_id"] = content_id
        full_action = admin_states[user_id]["action_type"]
        if not can(user_id, full_action):
            deny(call)
            return
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if full_action == "delete_title":
            cursor.execute('DELETE FROM contents WHERE content_id=?', (content_id,))
            conn.commit()
            bot.edit_message_text("✅ Title and assets deleted!", call.message.chat.id, call.message.message_id)
            admin_states.pop(user_id, None)
        elif full_action == "delete_record":
            show_file_delete_menu(call, content_id, 'audio', "audio records")
        elif full_action == "delete_file":
            show_file_delete_menu(call, content_id, 'non_audio', "files")
        elif full_action == "delete_assets":
            show_delete_resource_type_menu(call, user_id)
        elif full_action == "delete_quiz":
            show_quiz_delete_menu(call, content_id)
        elif full_action == "add_record":
            msg = bot.send_message(call.message.chat.id, "Please send/upload the Record file:")
            bot.register_next_step_handler(msg, process_media_upload_finish)
        elif full_action == "add_file":
            msg = bot.send_message(call.message.chat.id, "Please upload the File document:")
            bot.register_next_step_handler(msg, process_media_upload_finish)
        elif full_action == "add_quiz":
            msg = bot.send_message(call.message.chat.id, "Type a title for this Quiz:")
            bot.register_next_step_handler(msg, process_get_quiz_title)
            
        conn.close()

    @bot.callback_query_handler(func=lambda call: call.data == "adm_back_to_tgt")
    def back_to_target_step(call):
        user_id = call.from_user.id
        show_content_target_menu(call, user_id, admin_states[user_id]["subject_id"])

    def show_delete_resource_type_menu(call, user_id):
        markup = types.InlineKeyboardMarkup(row_width=1)
        if can(user_id, 'delete_file'):
            markup.add(types.InlineKeyboardButton("📄 File", callback_data="adm_delete_type_file"))
        if can(user_id, 'delete_record'):
            markup.add(types.InlineKeyboardButton("🎙 Recording", callback_data="adm_delete_type_record"))
        if can(user_id, 'delete_quiz'):
            markup.add(types.InlineKeyboardButton("📝 Quiz", callback_data="adm_delete_type_quiz"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_sub"))
        bot.edit_message_text("What do you want to delete?", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm_delete_type_"))
    def delete_resource_type_selected(call):
        user_id = call.from_user.id
        state = admin_states.get(user_id)
        if not state or state.get('action_type') != 'delete_assets':
            deny(call)
            return
        resource_type = call.data.replace('adm_delete_type_', '')
        action_map = {'file': 'delete_file', 'record': 'delete_record', 'quiz': 'delete_quiz'}
        action = action_map.get(resource_type)
        if not action or not can(user_id, action):
            deny(call)
            return
        state['action_type'] = action
        if action == 'delete_file':
            show_file_delete_menu(call, state['content_id'], 'non_audio', 'files')
        elif action == 'delete_record':
            show_file_delete_menu(call, state['content_id'], 'audio', 'audio records')
        else:
            show_quiz_delete_menu(call, state['content_id'])

    @bot.callback_query_handler(func=lambda call: call.data.startswith("f_select_qz_"))
    def select_quiz_for_delete(call):
        user_id = call.from_user.id
        state = admin_states.get(user_id)
        quiz_id = call.data.replace("f_select_qz_", "")
        content_id = state.get('content_id') if state else None
        if not state or not can(user_id, 'delete_quiz') or not content_id or not content_allowed(user_id, content_id) or not get_quiz_for_content(quiz_id, content_id):
            deny(call)
            return
        admin_states[user_id]['delete_resource_id'] = quiz_id
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Yes, Delete", callback_data="f_confirm_quiz"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="f_cancel_delete")
        )
        bot.edit_message_text("⚠️ Are you sure you want to delete this Quiz?", call.message.chat.id, call.message.message_id, reply_markup=markup)

    def show_file_delete_menu(call, content_id, file_category, label, page=0):
        files = get_content_files(content_id)
        if file_category == 'audio':
            files = [file for file in files if file['file_type'] == 'audio']
        else:
            files = [file for file in files if file['file_type'] != 'audio']

        page_size = 20
        page_count = max((len(files) + page_size - 1) // page_size, 1)
        page = max(0, min(page, page_count - 1))
        visible_files = files[page * page_size:(page + 1) * page_size]
        markup = types.InlineKeyboardMarkup(row_width=1)
        if not files:
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_tgt"))
            bot.edit_message_text(f"❌ No {label} found in this lecture.", call.message.chat.id, call.message.message_id, reply_markup=markup)
            return

        for file in visible_files:
            file_name = file['file_name'] or f"File {file['file_id']}"
            markup.add(types.InlineKeyboardButton(
                f"🗑️ Delete: {file_name}",
                callback_data=f"f_select_file_{file['file_id']}"
            ))
        navigation = []
        if page > 0:
            navigation.append(types.InlineKeyboardButton("⬅️ Previous", callback_data=f"f_page_{file_category}_{page - 1}"))
        if page < page_count - 1:
            navigation.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"f_page_{file_category}_{page + 1}"))
        if navigation:
            markup.row(*navigation)
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_tgt"))
        bot.edit_message_text(f"Select the {label[:-1]} to delete:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    def show_quiz_delete_menu(call, content_id, page=0):
        conn = get_db_connection()
        quizzes = conn.execute('SELECT * FROM quizzes WHERE content_id=? ORDER BY quiz_id', (content_id,)).fetchall()
        conn.close()
        page_size = 20
        page_count = max((len(quizzes) + page_size - 1) // page_size, 1)
        page = max(0, min(page, page_count - 1))
        visible_quizzes = quizzes[page * page_size:(page + 1) * page_size]
        markup = types.InlineKeyboardMarkup(row_width=1)
        if not quizzes:
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_tgt"))
            bot.edit_message_text("📂 No quizzes found in this Lecture/Section.", call.message.chat.id, call.message.message_id, reply_markup=markup)
            return
        for quiz in visible_quizzes:
            markup.add(types.InlineKeyboardButton(f"📝 {quiz['quiz_title']}", callback_data=f"f_select_qz_{quiz['quiz_id']}"))
        navigation = []
        if page > 0:
            navigation.append(types.InlineKeyboardButton("⬅️ Previous", callback_data=f"q_page_{page - 1}"))
        if page < page_count - 1:
            navigation.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"q_page_{page + 1}"))
        if navigation:
            markup.row(*navigation)
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="adm_back_to_tgt"))
        bot.edit_message_text("Select the Quiz to delete:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("f_page_"))
    def file_delete_page(call):
        user_id = call.from_user.id
        state = admin_states.get(user_id)
        if not state or not content_allowed(user_id, state.get('content_id')):
            deny(call)
            return
        _, _, file_category, page = call.data.split('_')
        label = 'audio records' if file_category == 'audio' else 'files'
        show_file_delete_menu(call, state['content_id'], file_category, label, int(page))

    @bot.callback_query_handler(func=lambda call: call.data.startswith("q_page_"))
    def quiz_delete_page(call):
        user_id = call.from_user.id
        state = admin_states.get(user_id)
        if not state or not is_main_admin(user_id) or not content_allowed(user_id, state.get('content_id')):
            deny(call)
            return
        show_quiz_delete_menu(call, state['content_id'], int(call.data.replace('q_page_', '')))

    @bot.callback_query_handler(func=lambda call: call.data.startswith("f_select_file_"))
    def select_file_for_delete(call):
        user_id = call.from_user.id
        state = admin_states.get(user_id)
        file_id = call.data.replace("f_select_file_", "")
        content_id = state.get('content_id') if state else None
        if not state or not content_id or not can(user_id, state.get('action_type', '')) or not content_allowed(user_id, content_id):
            deny(call)
            return
        file_type = 'audio' if state['action_type'] == 'delete_record' else None
        file_row = get_content_file_for_content(file_id, content_id, file_type)
        if not file_row or (state['action_type'] == 'delete_file' and file_row['file_type'] == 'audio'):
            deny(call)
            return
        admin_states[user_id]['delete_resource_id'] = file_id
        name = file_row['file_name'] or 'selected file'
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Yes, Delete", callback_data="f_confirm_file"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="f_cancel_delete")
        )
        bot.edit_message_text(f"⚠️ Are you sure you want to delete:\n📄 {name}?", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "f_confirm_file")
    def confirm_file_delete(call):
        user_id = call.from_user.id
        state = admin_states.get(user_id)
        if not state or not content_allowed(user_id, state.get('content_id')) or not can(user_id, state.get('action_type', '')):
            deny(call)
            return
        file_type = 'audio' if state['action_type'] == 'delete_record' else None
        file_row = get_content_file_for_content(state['delete_resource_id'], state['content_id'], file_type)
        if not file_row or (state['action_type'] == 'delete_file' and file_row['file_type'] == 'audio'):
            deny(call)
            return
        delete_content_file(file_row['file_id'])
        bot.send_message(call.message.chat.id, "✅ Deleted successfully.")
        show_file_delete_menu(call, state['content_id'], 'audio' if file_type == 'audio' else 'non_audio', 'audio records' if file_type == 'audio' else 'files')

    @bot.callback_query_handler(func=lambda call: call.data == "f_confirm_quiz")
    def confirm_quiz_delete(call):
        user_id = call.from_user.id
        state = admin_states.get(user_id)
        if not state or not is_main_admin(user_id) or not content_allowed(user_id, state.get('content_id')):
            deny(call)
            return
        if not delete_quiz(state['delete_resource_id'], state['content_id']):
            deny(call)
            return
        bot.send_message(call.message.chat.id, "✅ Deleted successfully.")
        show_quiz_delete_menu(call, state['content_id'])

    @bot.callback_query_handler(func=lambda call: call.data == "f_cancel_delete")
    def cancel_delete(call):
        user_id = call.from_user.id
        state = admin_states.get(user_id)
        if not state:
            deny(call)
            return
        show_content_target_menu(call, user_id, state['subject_id'])

    def process_get_quiz_title(message):
        user_id = message.from_user.id
        if user_id not in admin_states or not is_main_admin(user_id): return
        admin_states[user_id]["quiz_title"] = message.text.strip()
        msg = bot.send_message(message.chat.id, "Now upload the Quiz Word file (`.docx`):")
        bot.register_next_step_handler(msg, process_media_upload_finish)

    @bot.message_handler(func=lambda message: bool(
        message.media_group_id
        and admin_states.get(message.from_user.id, {}).get('upload_media_group_id') == message.media_group_id
    ))
    def collect_upload_media_group(message):
        user_id = message.from_user.id
        state = admin_states[user_id]
        queue_media_group(message, 'upload', process_upload_media_group)

    def process_media_upload_finish(message):
        user_id = message.from_user.id
        if user_id not in admin_states: return
        state = admin_states[user_id]
        if message.media_group_id and state.get('action_type') == 'add_file':
            state['upload_media_group_id'] = message.media_group_id
            state.setdefault('upload_media_group_messages', []).append(message)
            queue_media_group(message, 'upload', process_upload_media_group)
            return
        content_id = state["content_id"]
        full_action = state["action_type"]
        if full_action == "add_quiz" and not is_main_admin(user_id):
            admin_states.pop(user_id, None)
            bot.send_message(message.chat.id, "❌ Only the Main Admin can manage quizzes.")
            return
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if full_action == "add_record":
            file_id = message.audio.file_id if message.audio else (message.voice.file_id if message.voice else None)
            if file_id:
                file_name = message.audio.file_name if message.audio else 'Voice Record'
                add_content_file(content_id, 'audio', file_id, file_name or 'Lecture Record', message.caption)
                notify_content_ready(content_id)
                bot.send_message(message.chat.id, "✅ Record linked successfully!")
            else:
                bot.send_message(message.chat.id, "❌ Error: Invalid audio.")
        elif full_action == "add_file":
            file_id = message.document.file_id if message.document else (message.photo[-1].file_id if message.photo else None)
            if file_id:
                file_type = 'image' if message.photo else 'document'
                file_name = message.document.file_name if message.document else 'Image'
                add_content_file(content_id, file_type, file_id, file_name, message.caption)
                notify_content_ready(content_id)
                bot.send_message(message.chat.id, "✅ File linked successfully!")
            else:
                bot.send_message(message.chat.id, "❌ Error: Invalid file.")
        elif full_action == "add_quiz":
            if message.document and message.document.file_name.endswith('.docx'):
                file_info = bot.get_file(message.document.file_id)
                downloaded_file = bot.download_file(file_info.file_path)
                temp_path = os.path.join(STORAGE_DIR, message.document.file_name)
                with open(temp_path, 'wb') as new_file:
                    new_file.write(downloaded_file)
                
                quiz_id = add_quiz_metadata(content_id, state["quiz_title"])
                q_count = parse_docx_and_save_quiz(temp_path, quiz_id)
                if os.path.exists(temp_path): os.remove(temp_path)
                
                if q_count > 0:
                    publish_quiz(quiz_id)
                    notify_new_quiz(quiz_id)
                    bot.send_message(message.chat.id, f"✅ '{state['quiz_title']}' added! Extracted {q_count} questions.")
                else:
                    cursor.execute('DELETE FROM quizzes WHERE quiz_id=?', (quiz_id,))
                    conn.commit()
                    bot.send_message(message.chat.id, "❌ Word parsing failure. Make sure structure is correct.")
            else:
                bot.send_message(message.chat.id, "❌ Please upload a valid .docx file.")
        conn.close()
        admin_states.pop(user_id, None)

    def process_upload_media_group(messages):
        if not messages:
            return
        user_id = messages[0].from_user.id
        state = admin_states.get(user_id)
        if not state:
            return
        content_id = state['content_id']
        saved_count = 0
        for message in messages:
            file_id = message.document.file_id if message.document else (message.photo[-1].file_id if message.photo else None)
            if not file_id:
                continue
            file_type = 'image' if message.photo else 'document'
            file_name = message.document.file_name if message.document else 'Image'
            add_content_file(content_id, file_type, file_id, file_name, message.caption)
            saved_count += 1
        notify_content_ready(content_id)
        bot.send_message(messages[0].chat.id, f"✅ {saved_count} files linked successfully!")
        admin_states.pop(user_id, None)

    # --- الإعلانات ---
    @bot.message_handler(func=lambda message: message.text in {"📢 Announcement", "📢 Broadcast Announcement", "📢 Broadcast"} and (is_main_admin(message.from_user.id) or can(message.from_user.id, 'broadcast')))
    def broadcast_start(message):
        if not is_main_admin(message.from_user.id):
            scopes = get_admin_scopes(message.from_user.id)
            admin_states[message.from_user.id] = {
                "broadcast_scopes": [(scope['academic_year'], scope['semester']) for scope in scopes]
            }
            prompt = bot.send_message(message.chat.id, "Send the announcement now. You can send text, PDF, Word, image, audio, video, or any other message type.")
            bot.register_next_step_handler(prompt, process_broadcast_send)
            return
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("🌐 Broadcast to Everyone", callback_data="bc_target_all"))
        years = ["1st", "2nd", "3rd", "4th", "5th"]
        for y in years:
            markup.add(
                types.InlineKeyboardButton(f"{y} Year - Sem 1", callback_data=f"bc_target_{y} Year_Semester 1"),
                types.InlineKeyboardButton(f"{y} Year - Sem 2", callback_data=f"bc_target_{y} Year_Semester 2")
            )
        bot.send_message(message.chat.id, "Select Target Audience:", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("bc_target_"))
    def broadcast_target_selected(call):
        if not is_main_admin(call.from_user.id):
            deny(call)
            return
        target = call.data.replace("bc_target_", "")
        admin_states[call.from_user.id] = {"broadcast_target": target}
        msg = bot.send_message(call.message.chat.id, "Send the announcement now. You can send text, PDF, Word, image, audio, video, or any other message type.")
        bot.register_next_step_handler(msg, process_broadcast_send)

    def process_broadcast_send(message):
        user_id = message.from_user.id
        if user_id not in admin_states: return
        state = admin_states[user_id]
        if message.media_group_id:
            state['broadcast_media_group_id'] = message.media_group_id
            queue_media_group(message, 'broadcast', process_broadcast_media_group)
            return
        state['broadcast_message_chat_id'] = message.chat.id
        state['broadcast_message_id'] = message.message_id
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Confirm Send", callback_data="bc_confirm_send"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel_send")
        )
        bot.send_message(message.chat.id, "⚠️ Confirm this broadcast?", reply_markup=markup)

    @bot.message_handler(func=lambda message: bool(
        message.media_group_id
        and admin_states.get(message.from_user.id, {}).get('broadcast_media_group_id') == message.media_group_id
    ))
    def collect_broadcast_media_group(message):
        queue_media_group(message, 'broadcast', process_broadcast_media_group)

    def process_broadcast_media_group(messages):
        if not messages:
            return
        user_id = messages[0].from_user.id
        state = admin_states.get(user_id)
        if not state:
            return
        state['broadcast_messages'] = [
            (message.chat.id, message.message_id) for message in messages
        ]
        state.pop('broadcast_media_group_id', None)
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Confirm Send", callback_data="bc_confirm_send"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel_send")
        )
        bot.send_message(messages[0].chat.id, "⚠️ Confirm this broadcast?", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data in {"bc_confirm_send", "bc_cancel_send"})
    def confirm_broadcast_send(call):
        user_id = call.from_user.id
        state = admin_states.get(user_id)
        if not state or not (is_main_admin(user_id) or role_for(user_id) == 'content_admin'):
            deny(call)
            return
        if call.data == 'bc_cancel_send':
            admin_states.pop(user_id, None)
            bot.edit_message_text("❌ Broadcast cancelled.", call.message.chat.id, call.message.message_id)
            return
        broadcast_messages = state.get('broadcast_messages')
        if not broadcast_messages:
            broadcast_messages = [(
                state.get('broadcast_message_chat_id'),
                state.get('broadcast_message_id')
            )]
        if not broadcast_messages[0][0] or not broadcast_messages[0][1]:
            deny(call)
            return
        if is_main_admin(user_id):
            target = state["broadcast_target"]
            user_list = get_users_for_broadcast() if target == "all" else get_users_for_broadcast(target.split("_")[0], target.split("_")[1])
        else:
            user_list = []
            for year, semester in state.get('broadcast_scopes', []):
                user_list.extend(get_students_for_scope(year, semester))
            user_list = list(dict.fromkeys(user_list))
        success_count = 0
        for target_user_id in user_list:
            try:
                for source_chat_id, source_message_id in broadcast_messages:
                    bot.copy_message(target_user_id, source_chat_id, source_message_id)
                success_count += 1
            except Exception as error:
                handle_telegram_delivery_failure(bot, target_user_id, error)
        bot.edit_message_text(f"📢 Sent to {success_count} students.", call.message.chat.id, call.message.message_id)
        log_moderation_action(0, user_id, 'BROADCAST_SENT')
        admin_states.pop(user_id, None)

    # --- 🔄 التصفير الشامل لجميع الطلاب بمسح كافة ملفاتهم وسجلاتهم نهائياً ---
    @bot.message_handler(func=lambda message: message.text == "🔄 Reset All Students" and is_main_admin(message.from_user.id))
    def reset_students_confirm(message):
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("Yes", callback_data="conf_global_reset"), types.InlineKeyboardButton("Cancel", callback_data="can_global_reset"))
        bot.send_message(message.chat.id, "⚠️ Are you sure you want to reset all student activity? Admin accounts and moderation status will be preserved.", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data in ["conf_global_reset", "can_global_reset"])
    def process_reset_callback(call):
        if not is_main_admin(call.from_user.id):
            deny(call)
            return
        if call.data == "conf_global_reset":
            reset_all_students_academic()
            log_moderation_action(0, call.from_user.id, 'ALL_STUDENTS_RESET')
            bot.edit_message_text("✅ All student activity has been reset. Admin accounts and moderation status were preserved.", call.message.chat.id, call.message.message_id)
        else:
            bot.edit_message_text("❌ Cancelled.", call.message.chat.id, call.message.message_id)

    # --- 👤 تصفير طالب واحد محدد وحذف ملفه وسجلاته تماماً (Reset Single Student) ---
    @bot.message_handler(func=lambda message: message.text == "👤 Reset Single Student" and is_main_admin(message.from_user.id))
    def reset_single_student_start(message):
        user_id = message.from_user.id
        admin_states[user_id] = {"action_type": "reset_single_student"}
        msg = bot.send_message(message.chat.id, "🔢 أرسل Telegram User ID لإعادة تجربة الطالب إلى البداية:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_reset_single_student)

    def process_reset_single_student(message):
        user_id = message.from_user.id
        if user_id not in admin_states: return
        
        target_id_str = message.text.strip()
        if not target_id_str.isdigit():
            bot.send_message(message.chat.id, "❌ خطأ! يجب إرسال رقم ID صحيح ومكون من أرقام فقط. تم إلغاء العملية.")
            admin_states.pop(user_id, None)
            return
            
        target_user_id = int(target_id_str)
        if not get_user_profile(target_user_id):
            bot.send_message(message.chat.id, "❌ هذا المستخدم غير مسجل في البوت.")
            admin_states.pop(user_id, None)
            return
        admin_states[user_id]['reset_target_id'] = target_user_id
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Confirm Reset", callback_data="reset_student_confirm"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="reset_student_cancel")
        )
        bot.send_message(message.chat.id, f"⚠️ Reset Student Experience?\n\nUser ID: `{target_user_id}`", parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data in {"reset_student_confirm", "reset_student_cancel"})
    def reset_student_callback(call):
        user_id = call.from_user.id
        state = admin_states.get(user_id)
        if not is_main_admin(user_id) or not state or 'reset_target_id' not in state:
            deny(call)
            return
        if call.data == 'reset_student_cancel':
            bot.edit_message_text("❌ Reset cancelled.", call.message.chat.id, call.message.message_id)
        else:
            target_id = state['reset_target_id']
            reset_single_student_academic(target_id)
            try:
                from handlers.student import student_quiz_states
                student_quiz_states.pop(target_id, None)
            except Exception:
                pass
            log_moderation_action(target_id, user_id, 'STUDENT_RESET')
            bot.edit_message_text(f"✅ Student experience reset successfully.\n\nUser ID: `{target_id}`", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        admin_states.pop(user_id, None)
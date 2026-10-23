# handlers/student.py
import os
import sys
import inspect
from telebot import types

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_handler import (
    get_user_profile, add_user_if_not_exists, update_user_academic, get_subjects_by_tier,
    get_contents_by_subject, get_quiz_questions, save_quiz_result, get_db_connection,
    save_individual_answer, get_student_wrong_answers_from_db, check_student_quiz_completed,
    get_content_files, get_content_file, record_quiz_submission,
    get_quiz_context, is_quiz_result_available
)

student_quiz_states = {}

def register_student_handlers(bot):

    @bot.message_handler(func=lambda message: message.text in {"🎓 Materials", "🎓 Browse Academic Materials"})
    def browse_materials_start(message):
        user_id = message.from_user.id
        user = get_user_profile(user_id)
        # التحقق بدقة من حفظ السنة الدراسية والترم مسبقاً لعدم إظهار شاشة الاختيار كل مرة
        if user and user['academic_year'] and user['semester']:
            show_student_subjects(message, user['academic_year'], user['semester'])
        else:
            show_student_year_selection(message, bot)

    def show_student_year_selection(message, bot):
        markup = types.InlineKeyboardMarkup(row_width=1)
        years = ["1st Year", "2nd Year", "3rd Year", "4th Year", "5th Year"]
        for y in years:
            markup.add(types.InlineKeyboardButton(y, callback_data=f"st_yr_{y}"))
        bot.send_message(message.chat.id, "Please select your Academic Year:", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("st_yr_"))
    def student_year_selected(call):
        year = call.data.replace("st_yr_", "")
        student_quiz_states[call.from_user.id] = {"selected_year": year}
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("Semester 1", callback_data="st_sem_1"), types.InlineKeyboardButton("Semester 2", callback_data="st_sem_2"))
        bot.edit_message_text("Please select the Semester:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("st_sem_"))
    def student_semester_selected(call):
        user_id = call.from_user.id
        if user_id not in student_quiz_states: return
        sem_num = call.data.replace("st_sem_", "")
        semester = f"Semester {sem_num}"
        year = student_quiz_states[user_id]["selected_year"]
        update_user_academic(user_id, year, semester)
        student_quiz_states.pop(user_id, None)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        show_student_subjects(call.message, year, semester)

    def show_student_subjects(message, year, semester):
        subjects = get_subjects_by_tier(year, semester)
        markup = types.InlineKeyboardMarkup(row_width=1)
        if not subjects:
            bot.send_message(message.chat.id, f"📚 No subjects uploaded yet for your year ({year} - {semester}).")
            return
        for sub in subjects:
            markup.add(types.InlineKeyboardButton(sub['subject_name'], callback_data=f"st_sub_{sub['subject_id']}"))
        bot.send_message(message.chat.id, f"📚 Available Subjects ({year} - {semester}):", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("st_sub_"))
    def view_subject_options(call):
        sub_id = call.data.replace("st_sub_", "")
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("📄 Lectures", callback_data=f"st_lst_{sub_id}_lecture"),
            types.InlineKeyboardButton("🔬 Sections", callback_data=f"st_lst_{sub_id}_section")
        )
        markup.add(types.InlineKeyboardButton("🔙 Back to Subjects", callback_data="st_back_to_subjects"))
        bot.edit_message_text("Choose content type:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "st_back_to_subjects")
    def student_back_to_subjects_list(call):
        user = get_user_profile(call.from_user.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        if user and user['academic_year'] and user['semester']:
            show_student_subjects(call.message, user['academic_year'], user['semester'])
        else:
            show_student_year_selection(call.message, bot)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("st_lst_"))
    def view_content_list(call):
        _, _, sub_id, c_type = call.data.split("_")
        
        sig = inspect.signature(get_contents_by_subject)
        params_count = len(sig.parameters)
        
        if params_count >= 2:
            contents = get_contents_by_subject(sub_id, c_type)
        else:
            contents = get_contents_by_subject(sub_id)
            if contents:
                filtered = []
                for c in contents:
                    ctype_val = c['content_type'] if 'content_type' in c.keys() else ''
                    if str(ctype_val).lower() == str(c_type).lower():
                        filtered.append(c)
                contents = filtered
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        if not contents:
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"st_sub_{sub_id}"))
            bot.edit_message_text(f"ℹ️ No {c_type}s uploaded yet.", call.message.chat.id, call.message.message_id, reply_markup=markup)
            return
            
        for c in contents:
            markup.add(types.InlineKeyboardButton(c['title'], callback_data=f"st_item_{c['content_id']}"))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"st_sub_{sub_id}"))
        bot.edit_message_text(f"Select {c_type.capitalize()}:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("st_item_"))
    def open_content_item(call):
        content_id = call.data.replace("st_item_", "")
        conn = get_db_connection()
        item = conn.execute('SELECT * FROM contents WHERE content_id = ?', (content_id,)).fetchone()
        conn.close()
        
        if not item: return
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("📄 Files", callback_data=f"get_fil_{content_id}"),
            types.InlineKeyboardButton("🎙️ Records", callback_data=f"get_rec_{content_id}"),
            types.InlineKeyboardButton("📝 Quiz", callback_data=f"st_qz_menu_{content_id}"),
            types.InlineKeyboardButton("🔙 Back", callback_data=f"st_lst_{item['subject_id']}_{item['content_type']}")
        )
        bot.edit_message_text(f"Options for **{item['title']}**:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("get_rec_"))
    def delivery_record(call):
        content_id = call.data.replace("get_rec_", "")
        files = [file for file in get_content_files(content_id) if file['file_type'] == 'audio']
        if not files:
            bot.answer_callback_query(call.id, "❌ No record uploaded for this topic.")
            return

        for file in files:
            caption = file['caption_text'] or file['file_name'] or "🎙️ Lecture Record"
            bot.send_audio(call.message.chat.id, file['telegram_file_id'], caption=caption)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("get_fil_"))
    def delivery_files(call):
        content_id = call.data.replace("get_fil_", "")
        files = [file for file in get_content_files(content_id) if file['file_type'] != 'audio']
        if not files:
            bot.answer_callback_query(call.id, "❌ No files uploaded for this topic.")
            return

        for file in files:
            caption = file['caption_text'] or file['file_name'] or "📄 Lecture File Asset"
            if file['file_type'] == 'image':
                bot.send_photo(call.message.chat.id, file['telegram_file_id'], caption=caption)
            else:
                bot.send_document(call.message.chat.id, file['telegram_file_id'], caption=caption)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("st_qz_menu_"))
    def show_quizzes_list(call):
        content_id = call.data.replace("st_qz_menu_", "")
        conn = get_db_connection()
        quizzes = conn.execute('SELECT * FROM quizzes WHERE content_id=?', (content_id,)).fetchall()
        conn.close()
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        if not quizzes:
            bot.answer_callback_query(call.id, "❌ No quizzes added yet.")
            return
            
        for qz in quizzes:
            markup.add(types.InlineKeyboardButton(
                f"📝 {qz['quiz_title']}",
                callback_data=f"st_qz_strt_{qz['quiz_id']}"
            ))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"st_item_{content_id}"))
        bot.edit_message_text("Choose a Quiz to test your knowledge:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("st_qz_strt_"))
    def start_student_quiz(call):
        user_id = call.from_user.id
        quiz_id = int(call.data.replace("st_qz_strt_", ""))
        telegram_name = " ".join(
            part for part in (call.from_user.first_name, call.from_user.last_name) if part
        ) or "Student"
        add_user_if_not_exists(user_id, call.from_user.username or "Unknown", telegram_name)
        
        # فحص ما إذا كان الطالب قد أنهى هذا الاختبار مسبقاً (غير مسموح له بدخوله مرة أخرى)
        completed_result = check_student_quiz_completed(user_id, quiz_id)
        if completed_result:
            if is_quiz_result_available(quiz_id):
                bot.answer_callback_query(call.id, "📊 تم إعلان النتيجة والمراجعة.")
                show_quiz_report_message(call.message.chat.id, user_id, quiz_id, completed_result)
            else:
                bot.answer_callback_query(call.id, "⏳ النتيجة ستظهر بعد انتهاء موعد الترتيب.")
                bot.send_message(
                    call.message.chat.id,
                    "⏳ **تم تسليم الاختبار بنجاح**\n\n"
                    "لا يمكنك دخول الاختبار مرة أخرى. انتظر حتى ينتهي موعد الترتيب، "
                    "وسنرسل لك درجتك، وبعدها يمكنك الضغط على الاختبار لمراجعة الأسئلة والإجابات الصحيحة.",
                    parse_mode="Markdown"
                )
            return

        questions = get_quiz_questions(quiz_id)
        if not questions:
            bot.answer_callback_query(call.id, "❌ This quiz doesn't have any questions yet.")
            return
            
        student_quiz_states[user_id] = {
            "quiz_id": quiz_id, 
            "questions": questions, 
            "current_index": 0, 
            "score": 0, 
            "total": len(questions),
            "wrong_answers": []
        }
        
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        send_quiz_question(call.message.chat.id, user_id)

    def show_quiz_report_message(chat_id, user_id, quiz_id, result_row):
        total_val = result_row['total'] if 'total' in result_row.keys() else result_row['score']
        msg = f"📊 **نتيجتك المحزنة لهذا الاختبار:**\n\n🎯 الدرجة: `{result_row['score']} / {total_val}`\n"
        
        try:
            wrong_db = get_student_wrong_answers_from_db(user_id, quiz_id)
        except Exception:
            wrong_db = None

        if wrong_db:
            full_report_text = "\n❌ **مراجعة للأسئلة التي أخطأت بها:**\n"
            for i, w in enumerate(wrong_db, 1):
                u_letter = w['selected_option']
                c_letter = w['correct_option']
                options_map = {"A": w['option_a'], "B": w['option_b'], "C": w['option_c'], "D": w['option_d']}
                
                block = f"\n**{i}) {w['question_text']}**\n"
                block += f"❌ إجابتك السابقة: `{u_letter}) {options_map.get(u_letter, '')}`\n"
                block += f"✅ الإجابة الصحيحة: `{c_letter}) {options_map.get(c_letter, '')}`\n"
                block += "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                
                if len(msg) + len(block) > 3500:
                    bot.send_message(chat_id, msg, parse_mode="Markdown")
                    msg = block
                else:
                    msg += block
            bot.send_message(chat_id, msg, parse_mode="Markdown")
        else:
            msg += "\n💯 لقد حصلت على درجة كاملة بدون أخطاء!"
            bot.send_message(chat_id, msg, parse_mode="Markdown")

    def send_quiz_question(chat_id, user_id):
        if user_id not in student_quiz_states: return
        state = student_quiz_states[user_id]
        idx = state["current_index"]
        q = state["questions"][idx]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"A) {q['option_a']}", callback_data="qans_A"),
            types.InlineKeyboardButton(f"B) {q['option_b']}", callback_data="qans_B"),
            types.InlineKeyboardButton(f"C) {q['option_c']}", callback_data="qans_C"),
            types.InlineKeyboardButton(f"D) {q['option_d']}", callback_data="qans_D")
        )
        bot.send_message(chat_id, f"📊 **Question {idx + 1} / {state['total']}**\n\n{q['question_text']}", reply_markup=markup, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("qans_"))
    def process_quiz_answer(call):
        user_id = call.from_user.id
        
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
            
        if user_id not in student_quiz_states: 
            return
            
        state = student_quiz_states[user_id]
        idx = state["current_index"]
        
        if idx >= state["total"]:
            student_quiz_states.pop(user_id, None)
            return

        selected_ans = call.data.replace("qans_", "").strip().upper()
        current_q = state["questions"][idx]
        question_id = current_q['question_id']
        
        is_correct = 1 if selected_ans == current_q['correct_option'].strip().upper() else 0
        
        try:
            save_individual_answer(user_id, state["quiz_id"], question_id, selected_ans, is_correct)
        except Exception as e:
            print(f"Error saving individual answer: {e}")
        
        if is_correct:
            state["score"] += 1
        else:
            state["wrong_answers"].append({
                "question": current_q['question_text'],
                "user_ans": selected_ans,
                "correct_ans": current_q['correct_option'].strip().upper(),
                "options": {
                    "A": current_q['option_a'],
                    "B": current_q['option_b'],
                    "C": current_q['option_c'],
                    "D": current_q['option_d']
                }
            })
            
        state["current_index"] += 1
        
        # حذف رسالة السؤال الحالي فور اختيار الإجابة
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        if state["current_index"] < state["total"]:
            send_quiz_question(call.message.chat.id, user_id)
        else:
            # 🏁 اختيار آخر إجابة (الوصول لنهاية الاختبار وإظهار النتيجة والتقرير)
            quiz_id = state["quiz_id"]
            score = state["score"]
            total = state["total"]
            wrong_report = state["wrong_answers"]
            
            student_quiz_states.pop(user_id, None)
            
            try:
                save_quiz_result(user_id, quiz_id, score, total)
                record_quiz_submission(user_id, quiz_id, score, total)
            except Exception as e:
                print(f"Database Warning: Could not save final score: {e}")
            
            results_available = is_quiz_result_available(quiz_id)
            if results_available:
                header_msg = f"🎉 **تم الانتهاء من الاختبار بنجاح!**\n\n🎯 درجتك النهائية: `{score} / {total}`\n"
                bot.send_message(call.message.chat.id, header_msg, parse_mode="Markdown")
            else:
                bot.send_message(
                    call.message.chat.id,
                    "✅ **تم تسليم الاختبار بنجاح**\n\n"
                    "تم تسجيل إجاباتك. لن تظهر الدرجة أو مراجعة الأسئلة قبل انتهاء موعد الترتيب. "
                    "سنرسل لك النتيجة تلقائيًا بعد انتهاء الـ24 ساعة.",
                    parse_mode="Markdown"
                )

            if wrong_report and results_available:
                current_chunk = "❌ **مراجعة للأسئلة التي أخطأت بها:**\n"
                
                for i, w in enumerate(wrong_report, 1):
                    u_letter = w['user_ans']
                    c_letter = w['correct_ans']
                    
                    block = f"\n**{i}) {w['question']}**\n"
                    block += f"❌ إجابتك الخاطئة: `{u_letter}) {w['options'].get(u_letter, '')}`\n"
                    block += f"✅ الإجابة الصحيحة: `{c_letter}) {w['options'].get(c_letter, '')}`\n"
                    block += "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                    
                    if len(current_chunk) + len(block) > 3500:
                        bot.send_message(call.message.chat.id, current_chunk, parse_mode="Markdown")
                        current_chunk = block
                    else:
                        current_chunk += block
                        
                # إرسال القطعة الأخيرة المتبقية ضماناً لعدم ضياع أي سؤال
                if current_chunk:
                    bot.send_message(call.message.chat.id, current_chunk, parse_mode="Markdown")
            elif results_available:
                bot.send_message(call.message.chat.id, "💯 ممتاز! لقد أجبت على جميع الأسئلة بشكل صحيح.", parse_mode="Markdown")

    @bot.message_handler(func=lambda message: message.text in {"👤 My Profile", "👤 My Profile / بياناتي"})
    def show_student_profile(message):
        user_id = message.from_user.id
        first_name = message.from_user.first_name or ""
        last_name = message.from_user.last_name or ""
        telegram_name = f"{first_name} {last_name}".strip() or "لا يوجد اسم مسجل"
        
        try:
            user_data = get_user_profile(user_id)
        except Exception as e:
            print(f"Error fetching user profile: {e}")
            user_data = None

        academic_year = "⚠️ لم تحدد بعد"
        semester = "⚠️ لم تحدد بعد"

        if user_data:
            try:
                if user_data['academic_year']:
                    academic_year = user_data['academic_year']
            except Exception:
                pass

            try:
                if user_data['semester']:
                    semester = user_data['semester']
            except Exception:
                pass

        profile_msg = (
            "👤 📋 **الملف الشخصي للطالب / Student Profile**\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
            "🆔 **معرف التليجرام (ID):**\n"
            f"👤 اسم الحساب: `{telegram_name}`\n"
            f"🔢 رقم الآيدي: `{user_id}`\n\n"
            "📚 **البيانات الأكاديمية الحالية:**\n"
            f"🗓️ السنة الدراسية: ` {academic_year} `\n"
            f"⏱️ الترم الدراسي: ` {semester} `\n\n"
            "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            "⚠️ _لا يمكنك تغيير السنة الدراسية إلا عن طريق مسح البيانات من الإدارة._"
        )

        bot.send_message(message.chat.id, profile_msg, parse_mode="Markdown")
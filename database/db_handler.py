# database/db_handler.py
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from config import DB_PATH, ADMIN_IDS

# مجلد خاص لحفظ ملفات الطلاب المستقلة
STUDENTS_DIR = os.path.join(os.path.dirname(DB_PATH), "students")

def get_db_connection():
    """الاتصال بقاعدة البيانات العامة (المواد، المحاضرات، الأسئلة)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_student_db_connection(user_id):
    """الاتصال بقاعدة البيانات الخاصة بالطالب (ملف مستقل لكل طالب)"""
    os.makedirs(STUDENTS_DIR, exist_ok=True)
    student_db_path = os.path.join(STUDENTS_DIR, f"{user_id}.db")

    conn = sqlite3.connect(student_db_path)
    conn.row_factory = sqlite3.Row

    cursor = conn.cursor()
    # نتأكد دائمًا من وجود جداول الطالب حتى في الملفات القديمة/الناقصة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS profile (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            academic_year TEXT,
            semester TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS student_answers (
            answer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER,
            question_id INTEGER,
            selected_option TEXT,
            is_correct INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quiz_results (
            result_id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER,
            score INTEGER,
            total INTEGER,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    profile_columns = {row[1] for row in conn.execute('PRAGMA table_info(profile)').fetchall()}
    if 'status' not in profile_columns:
        conn.execute("ALTER TABLE profile ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if 'telegram_blocked' not in profile_columns:
        conn.execute('ALTER TABLE profile ADD COLUMN telegram_blocked INTEGER NOT NULL DEFAULT 0')
    if 'blocked_notice_sent' not in profile_columns:
        conn.execute('ALTER TABLE profile ADD COLUMN blocked_notice_sent INTEGER NOT NULL DEFAULT 0')
    if 'guide_seen' not in profile_columns:
        conn.execute('ALTER TABLE profile ADD COLUMN guide_seen INTEGER NOT NULL DEFAULT 0')

    # Ensure unique per-question / per-quiz records across older student DB files that were created before the uniqueness fix.
    conn.execute('CREATE INDEX IF NOT EXISTS idx_student_answers_quiz_question ON student_answers (quiz_id, question_id)')
    conn.execute('DELETE FROM student_answers WHERE rowid NOT IN (SELECT MIN(rowid) FROM student_answers GROUP BY quiz_id, question_id)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_student_answers_unique_per_question ON student_answers (quiz_id, question_id)')

    conn.execute('CREATE INDEX IF NOT EXISTS idx_quiz_results_quiz_id ON quiz_results (quiz_id)')
    conn.execute('DELETE FROM quiz_results WHERE rowid NOT IN (SELECT MIN(rowid) FROM quiz_results GROUP BY quiz_id)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_quiz_results_unique_per_quiz ON quiz_results (quiz_id)')

    conn.commit()
    
    return conn

def init_db():
    """تهيئة قاعدة البيانات العامة للبوت (المواد والمحتوى فقط)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # جدول المواد
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subjects (
            subject_id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_name TEXT NOT NULL,
            academic_year TEXT NOT NULL,
            semester TEXT NOT NULL
        )
    ''')
    
    # جدول المحاضرات/السكاشن
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contents (
            content_id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER NOT NULL,
            content_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary_text TEXT,
            ready_notification_sent INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (subject_id) REFERENCES subjects (subject_id) ON DELETE CASCADE
        )
    ''')

    content_columns = {row[1] for row in cursor.execute('PRAGMA table_info(contents)').fetchall()}
    if 'ready_notification_sent' not in content_columns:
        cursor.execute('ALTER TABLE contents ADD COLUMN ready_notification_sent INTEGER NOT NULL DEFAULT 0')
    
    # جدول ملفات المحتوى (يدعم ملفات متعددة لكل محتوى)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS content_files (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER NOT NULL,
            file_type TEXT NOT NULL,  -- 'document', 'audio', etc.
            telegram_file_id TEXT NOT NULL,
            file_name TEXT,
            caption_text TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (content_id) REFERENCES contents (content_id) ON DELETE CASCADE
        )
    ''')

    file_columns = {row[1] for row in cursor.execute('PRAGMA table_info(content_files)').fetchall()}
    if 'caption_text' not in file_columns:
        cursor.execute('ALTER TABLE content_files ADD COLUMN caption_text TEXT')

    # Keep the legacy columns available, then copy their existing values into
    # the multi-file table without creating duplicate rows on later starts.
    content_columns = {row[1] for row in cursor.execute('PRAGMA table_info(contents)').fetchall()}
    if 'file_id' not in content_columns:
        cursor.execute('ALTER TABLE contents ADD COLUMN file_id TEXT')

    cursor.execute('''
        INSERT INTO content_files (content_id, file_type, telegram_file_id, file_name)
        SELECT content_id, 'audio', file_id, 'Lecture Record'
        FROM contents
        WHERE file_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM content_files
              WHERE content_id = contents.content_id
                AND file_type = 'audio'
                AND telegram_file_id = contents.file_id
          )
    ''')
    cursor.execute('''
        INSERT INTO content_files (content_id, file_type, telegram_file_id, file_name)
        SELECT content_id, 'document', summary_text, 'Lecture File Asset'
        FROM contents
        WHERE summary_text IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM content_files
              WHERE content_id = contents.content_id
                AND file_type = 'document'
                AND telegram_file_id = contents.summary_text
          )
    ''')
    
    # جدول ميتاداتا الكويزات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quizzes (
            quiz_id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER NOT NULL,
            quiz_title TEXT NOT NULL,
            FOREIGN KEY (content_id) REFERENCES contents (content_id) ON DELETE CASCADE
        )
    ''')

    quiz_columns = {row[1] for row in cursor.execute('PRAGMA table_info(quizzes)').fetchall()}
    if 'published_at' not in quiz_columns:
        cursor.execute('ALTER TABLE quizzes ADD COLUMN published_at TEXT')
    if 'ranking_end_at' not in quiz_columns:
        cursor.execute('ALTER TABLE quizzes ADD COLUMN ranking_end_at TEXT')
    if 'ranking_finalized' not in quiz_columns:
        cursor.execute('ALTER TABLE quizzes ADD COLUMN ranking_finalized INTEGER NOT NULL DEFAULT 0')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quiz_rankings (
            ranking_id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            percentage REAL NOT NULL,
            submitted_at TEXT NOT NULL,
            academic_year TEXT NOT NULL,
            semester TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            content_title TEXT NOT NULL,
            rank INTEGER,
            notification_sent INTEGER NOT NULL DEFAULT 0,
            certificate_sent INTEGER NOT NULL DEFAULT 0,
            UNIQUE(quiz_id, student_id),
            FOREIGN KEY (quiz_id) REFERENCES quizzes(quiz_id) ON DELETE CASCADE
        )
    ''')

    ranking_columns = {row[1] for row in cursor.execute('PRAGMA table_info(quiz_rankings)').fetchall()}
    if 'certificate_sent' not in ranking_columns:
        cursor.execute('ALTER TABLE quiz_rankings ADD COLUMN certificate_sent INTEGER NOT NULL DEFAULT 0')
    
    # جدول الأسئلة الكلية
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            question_id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            option_d TEXT NOT NULL,
            correct_option TEXT NOT NULL,
            FOREIGN KEY (quiz_id) REFERENCES quizzes (quiz_id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_accounts (
            user_id INTEGER PRIMARY KEY,
            role TEXT NOT NULL CHECK(role IN ('content_admin', 'file_admin')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_scopes (
            scope_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            academic_year TEXT NOT NULL,
            semester TEXT NOT NULL,
            UNIQUE(user_id, academic_year, semester),
            FOREIGN KEY (user_id) REFERENCES admin_accounts(user_id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS moderation_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_user_id INTEGER NOT NULL,
            admin_user_id INTEGER,
            action TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_ratings (
            user_id INTEGER PRIMARY KEY,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# --- دمج وإصلاح دوال الطلاب لتتعامل مع ملفاتهم المستقلة ---

def add_user_if_not_exists(user_id, username, full_name):
    conn = get_student_db_connection(user_id)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM profile WHERE user_id = ?', (user_id,))
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO profile (user_id, username, full_name, academic_year, semester)
                VALUES (?, ?, ?, NULL, NULL)
            ''', (user_id, username, full_name))
            conn.commit()
        else:
            cursor.execute(
                'UPDATE profile SET username=?, full_name=? WHERE user_id=?',
                (username, full_name, user_id)
            )
            conn.commit()
    finally:
        conn.close()

def get_user_profile(user_id):
    conn = get_student_db_connection(user_id)
    row = conn.execute('SELECT * FROM profile WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    return row

def save_bot_rating(user_id, rating, comment=None):
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO bot_ratings (user_id, rating, comment)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            rating=excluded.rating,
            comment=excluded.comment,
            updated_at=CURRENT_TIMESTAMP
    ''', (user_id, rating, comment))
    conn.commit()
    conn.close()

def get_bot_rating_summary():
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT rating, COUNT(*) AS count
        FROM bot_ratings
        GROUP BY rating
        ORDER BY rating DESC
    ''').fetchall()
    conn.close()
    return rows

def get_bot_ratings(rating):
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT * FROM bot_ratings
        WHERE bot_ratings.rating=?
        ORDER BY bot_ratings.updated_at DESC
    ''', (rating,)).fetchall()
    conn.close()
    result = []
    for row in rows:
        profile = get_user_profile(row['user_id'])
        result.append({
            'user_id': row['user_id'],
            'rating': row['rating'],
            'comment': row['comment'],
            'updated_at': row['updated_at'],
            'full_name': profile['full_name'] if profile else None,
            'username': profile['username'] if profile else None,
        })
    return result

def mark_guide_seen(user_id):
    conn = get_student_db_connection(user_id)
    try:
        conn.execute('UPDATE profile SET guide_seen=1 WHERE user_id=?', (user_id,))
        conn.commit()
    finally:
        conn.close()

def get_user_status(user_id):
    profile = get_user_profile(user_id)
    return profile['status'] if profile and 'status' in profile.keys() else 'active'

def set_user_status(user_id, status):
    conn = get_student_db_connection(user_id)
    try:
        conn.execute(
            'UPDATE profile SET status=?, blocked_notice_sent=0 WHERE user_id=?',
            (status, user_id)
        )
        conn.commit()
    finally:
        conn.close()

def mark_telegram_blocked(user_id):
    conn = get_student_db_connection(user_id)
    try:
        conn.execute('UPDATE profile SET telegram_blocked=1 WHERE user_id=?', (user_id,))
        conn.commit()
    finally:
        conn.close()

def record_telegram_block_event(user_id):
    conn = get_student_db_connection(user_id)
    try:
        previous = conn.execute('SELECT telegram_blocked FROM profile WHERE user_id=?', (user_id,)).fetchone()
        conn.execute('UPDATE profile SET telegram_blocked=1 WHERE user_id=?', (user_id,))
        conn.commit()
        return not previous or not previous['telegram_blocked']
    finally:
        conn.close()

def mark_blocked_notice_sent(user_id):
    conn = get_student_db_connection(user_id)
    try:
        conn.execute('UPDATE profile SET blocked_notice_sent=1 WHERE user_id=?', (user_id,))
        conn.commit()
    finally:
        conn.close()

def log_moderation_action(target_user_id, admin_user_id, action):
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO moderation_log (target_user_id, admin_user_id, action) VALUES (?, ?, ?)',
        (target_user_id, admin_user_id, action)
    )
    conn.commit()
    conn.close()

def handle_telegram_delivery_failure(bot, user_id, error):
    error_code = getattr(error, 'error_code', None)
    description = str(getattr(error, 'description', error)).lower()
    if error_code != 403 and 'blocked by the user' not in description and 'user is deactivated' not in description:
        return False
    if not record_telegram_block_event(user_id):
        return True
    log_moderation_action(user_id, None, 'TELEGRAM_BLOCK_DETECTED')
    profile = get_user_profile(user_id)
    if profile:
        message = (
            "🚫 **User Blocked the Bot**\n\n"
            f"👤 User ID: `{user_id}`\n"
            f"📛 Name: {profile['full_name'] or 'Not available'}\n"
            f"🔹 Username: @{profile['username'] or 'Not available'}\n"
            f"📚 Year: {profile['academic_year'] or 'Not available'}\n"
            f"📖 Term: {profile['semester'] or 'Not available'}"
        )
        try:
            bot.send_message(ADMIN_IDS[0], message, parse_mode='Markdown')
        except Exception:
            pass
    return True

def update_user_academic(user_id, year, semester):
    conn = get_student_db_connection(user_id)
    try:
        cursor = conn.cursor()
        # محاولة التحديث أولاً لو كان الصف موجوداً
        cursor.execute('UPDATE profile SET academic_year = ?, semester = ? WHERE user_id = ?', (year, semester, user_id))
        # إذا لم يكن الصف موجوداً، يتم إدخاله مباشرة لمنع ضياع البيانات
        if cursor.rowcount == 0:
            cursor.execute('INSERT INTO profile (user_id, academic_year, semester) VALUES (?, ?, ?)', (user_id, year, semester))
        conn.commit()
    finally:
        conn.close()

def save_individual_answer(user_id, quiz_id, question_id, selected_option, is_correct):
    conn = get_student_db_connection(user_id)
    try:
        conn.execute('''
            INSERT INTO student_answers (quiz_id, question_id, selected_option, is_correct)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(quiz_id, question_id) DO UPDATE SET
                selected_option=excluded.selected_option,
                is_correct=excluded.is_correct
        ''', (quiz_id, question_id, selected_option, is_correct))
        conn.commit()
    finally:
        conn.close()

def save_quiz_result(user_id, quiz_id, score, total):
    conn = get_student_db_connection(user_id)
    try:
        conn.execute('''
            INSERT INTO quiz_results (quiz_id, score, total, completed_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(quiz_id) DO UPDATE SET
                score=excluded.score,
                total=excluded.total,
                completed_at=CURRENT_TIMESTAMP
        ''', (quiz_id, score, total))
        conn.commit()
    except Exception as e:
        print(f"Error saving quiz result: {e}")
    finally:
        conn.close()

def check_student_quiz_completed(user_id, quiz_id):
    """التحقق مما إذا كان الطالب قد أنهى هذا الاختبار مسبقاً من سجله الفردي"""
    conn = get_student_db_connection(user_id)
    try:
        row = conn.execute('SELECT * FROM quiz_results WHERE quiz_id = ?', (quiz_id,)).fetchone()
        return row
    except Exception:
        return None
    finally:
        conn.close()

def get_student_wrong_answers_from_db(user_id, quiz_id):
    # نربط بين ملف الطالب لنجيب إجاباته وجدول الأسئلة العام لنجيب نص السؤال والاختيارات
    student_conn = get_student_db_connection(user_id)
    wrong_answers = student_conn.execute('SELECT * FROM student_answers WHERE quiz_id = ? AND is_correct = 0', (quiz_id,)).fetchall()
    student_conn.close()
    
    global_conn = get_db_connection()
    report_rows = []
    for wa in wrong_answers:
        q_row = global_conn.execute('SELECT * FROM questions WHERE question_id = ?', (wa['question_id'],)).fetchone()
        if q_row:
            report_rows.append({
                'question_text': q_row['question_text'],
                'selected_option': wa['selected_option'],
                'correct_option': q_row['correct_option'],
                'option_a': q_row['option_a'],
                'option_b': q_row['option_b'],
                'option_c': q_row['option_c'],
                'option_d': q_row['option_d']
            })
    global_conn.close()
    return report_rows

# --- دوال الإضافة العامة للأدمن ---
def add_subject(name, year, semester):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO subjects (subject_name, academic_year, semester) VALUES (?, ?, ?)', (name, year, semester))
    conn.commit()
    sub_id = cursor.lastrowid
    conn.close()
    return sub_id

def add_content(subject_id, content_type, title, summary_text=None):
    """إنشاء محتوى جديد (بدون ملفات)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO contents (subject_id, content_type, title, summary_text) VALUES (?, ?, ?, ?)',
                   (subject_id, content_type, title, summary_text))
    conn.commit()
    c_id = cursor.lastrowid
    conn.close()
    return c_id

def add_content_file(content_id, file_type, telegram_file_id, file_name=None, caption_text=None):
    """إضافة ملف جديد لمحتوى موجود"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO content_files
        (content_id, file_type, telegram_file_id, file_name, caption_text)
        VALUES (?, ?, ?, ?, ?)
    ''', (content_id, file_type, telegram_file_id, file_name, caption_text))
    conn.commit()
    f_id = cursor.lastrowid
    conn.close()
    return f_id

def claim_content_ready_notification(content_id):
    conn = get_db_connection()
    try:
        row = conn.execute('''
            SELECT contents.title AS content_title, subjects.subject_name,
                   subjects.academic_year, subjects.semester
            FROM contents
            JOIN subjects ON subjects.subject_id=contents.subject_id
            WHERE contents.content_id=?
              AND contents.ready_notification_sent=0
              AND EXISTS (
                  SELECT 1 FROM content_files
                  WHERE content_id=? AND file_type='audio'
              )
              AND EXISTS (
                  SELECT 1 FROM content_files
                  WHERE content_id=? AND file_type<>'audio'
              )
        ''', (content_id, content_id, content_id)).fetchone()
        if not row:
            return None
        updated = conn.execute('''
            UPDATE contents SET ready_notification_sent=1
            WHERE content_id=? AND ready_notification_sent=0
        ''', (content_id,))
        if updated.rowcount != 1:
            return None
        conn.commit()
        return row
    finally:
        conn.close()

def get_content_files(content_id):
    """جلب كل الملفات الخاصة بمحتوى معين"""
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM content_files WHERE content_id=? ORDER BY uploaded_at ASC, file_id ASC', (content_id,)).fetchall()
    conn.close()
    return rows

def get_content_file(file_id):
    """جلب ملف واحد حسب ID"""
    conn = get_db_connection()
    row = conn.execute('SELECT * FROM content_files WHERE file_id=?', (file_id,)).fetchone()
    conn.close()
    return row

def delete_content_file(file_id):
    """حذف ملف معين"""
    conn = get_db_connection()
    file_row = conn.execute('SELECT content_id FROM content_files WHERE file_id=?', (file_id,)).fetchone()
    conn.execute('DELETE FROM content_files WHERE file_id=?', (file_id,))
    if file_row:
        content_id = file_row['content_id']
        has_audio = conn.execute(
            "SELECT 1 FROM content_files WHERE content_id=? AND file_type='audio' LIMIT 1",
            (content_id,)
        ).fetchone()
        has_file = conn.execute(
            "SELECT 1 FROM content_files WHERE content_id=? AND file_type<>'audio' LIMIT 1",
            (content_id,)
        ).fetchone()
        if not has_audio or not has_file:
            conn.execute('UPDATE contents SET ready_notification_sent=0 WHERE content_id=?', (content_id,))
    conn.commit()
    conn.close()

def get_content_file_for_content(file_id, content_id, file_type=None):
    conn = get_db_connection()
    query = 'SELECT * FROM content_files WHERE file_id=? AND content_id=?'
    params = [file_id, content_id]
    if file_type:
        query += ' AND file_type=?'
        params.append(file_type)
    row = conn.execute(query, params).fetchone()
    conn.close()
    return row

def get_quiz_for_content(quiz_id, content_id):
    conn = get_db_connection()
    row = conn.execute('SELECT * FROM quizzes WHERE quiz_id=? AND content_id=?', (quiz_id, content_id)).fetchone()
    conn.close()
    return row

def delete_quiz(quiz_id, content_id):
    conn = get_db_connection()
    try:
        row = conn.execute('SELECT quiz_id FROM quizzes WHERE quiz_id=? AND content_id=?', (quiz_id, content_id)).fetchone()
        if not row:
            return False
        conn.execute('DELETE FROM questions WHERE quiz_id=?', (quiz_id,))
        conn.execute('DELETE FROM quiz_rankings WHERE quiz_id=?', (quiz_id,))
        conn.execute('DELETE FROM quizzes WHERE quiz_id=? AND content_id=?', (quiz_id, content_id))
        conn.commit()
        delete_student_quiz_records(quiz_id)
        return True
    finally:
        conn.close()

def delete_student_quiz_records(quiz_id):
    if not os.path.exists(STUDENTS_DIR):
        return
    for filename in os.listdir(STUDENTS_DIR):
        if not filename.endswith('.db'):
            continue
        path = os.path.join(STUDENTS_DIR, filename)
        conn = sqlite3.connect(path)
        try:
            conn.execute('DELETE FROM student_answers WHERE quiz_id=?', (quiz_id,))
            conn.execute('DELETE FROM quiz_results WHERE quiz_id=?', (quiz_id,))
            conn.commit()
        finally:
            conn.close()

def add_quiz_metadata(content_id, quiz_title):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO quizzes (content_id, quiz_title) VALUES (?, ?)', (content_id, quiz_title))
    conn.commit()
    q_id = cursor.lastrowid
    conn.close()
    return q_id

def publish_quiz(quiz_id):
    published_at = datetime.now(timezone.utc)
    ranking_end_at = published_at + timedelta(hours=24)
    conn = get_db_connection()
    conn.execute('''
        UPDATE quizzes
        SET published_at=?, ranking_end_at=?, ranking_finalized=0
        WHERE quiz_id=?
    ''', (published_at.isoformat(), ranking_end_at.isoformat(), quiz_id))
    conn.commit()
    conn.close()

def get_quiz_context(quiz_id):
    conn = get_db_connection()
    row = conn.execute('''
        SELECT quizzes.quiz_id, quizzes.quiz_title, quizzes.published_at,
               quizzes.ranking_end_at, quizzes.ranking_finalized,
               subjects.academic_year, subjects.semester,
               subjects.subject_name, contents.title AS content_title,
               contents.content_type
        FROM quizzes
        JOIN contents ON contents.content_id=quizzes.content_id
        JOIN subjects ON subjects.subject_id=contents.subject_id
        WHERE quizzes.quiz_id=?
    ''', (quiz_id,)).fetchone()
    conn.close()
    return row

def is_quiz_result_available(quiz_id, now=None):
    """Quiz results remain hidden until the ranking window ends for published quizzes."""
    context = get_quiz_context(quiz_id)
    if not context or not context['published_at'] or not context['ranking_end_at']:
        return False
    now = now or datetime.now(timezone.utc)
    ranking_end_at = datetime.fromisoformat(context['ranking_end_at'])
    return now >= ranking_end_at

def get_students_for_scope(academic_year, semester):
    if not os.path.exists(STUDENTS_DIR):
        return []
    student_ids = []
    for filename in os.listdir(STUDENTS_DIR):
        if not filename.endswith('.db'):
            continue
        user_id = filename[:-3]
        if not user_id.isdigit():
            continue
        profile = get_user_profile(int(user_id))
        if profile and profile['academic_year'] == academic_year and profile['semester'] == semester and not profile['telegram_blocked']:
            student_ids.append(int(user_id))
    return student_ids

def get_all_students():
    if not os.path.exists(STUDENTS_DIR):
        return []
    student_ids = []
    for filename in os.listdir(STUDENTS_DIR):
        if not filename.endswith('.db'):
            continue
        user_id = filename[:-3]
        if not user_id.isdigit():
            continue
        profile = get_user_profile(int(user_id))
        if profile and not profile['telegram_blocked']:
            student_ids.append(int(user_id))
    return student_ids

def record_quiz_submission(user_id, quiz_id, score, total, submitted_at=None):
    context = get_quiz_context(quiz_id)
    if not context or not context['published_at'] or not context['ranking_end_at']:
        return False
    submitted_at = submitted_at or datetime.now(timezone.utc)
    ranking_end_at = datetime.fromisoformat(context['ranking_end_at'])
    if submitted_at > ranking_end_at:
        return False
    percentage = (score / total * 100) if total else 0
    conn = get_db_connection()
    conn.execute('''
        INSERT OR IGNORE INTO quiz_rankings
        (quiz_id, student_id, score, total, percentage, submitted_at,
         academic_year, semester, subject_name, content_title)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        quiz_id, user_id, score, total, percentage, submitted_at.isoformat(),
        context['academic_year'], context['semester'], context['subject_name'],
        context['content_title']
    ))
    inserted = conn.execute('SELECT changes()').fetchone()[0] == 1
    conn.commit()
    conn.close()
    return inserted

def get_quizzes_ready_for_finalization(now=None):
    now = (now or datetime.now(timezone.utc)).isoformat()
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT quiz_id FROM quizzes
        WHERE published_at IS NOT NULL
          AND ranking_end_at IS NOT NULL
          AND ranking_end_at <= ?
          AND ranking_finalized=0
    ''', (now,)).fetchall()
    conn.close()
    return [row['quiz_id'] for row in rows]

def finalize_quiz_ranking(quiz_id):
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT ranking_id, score, submitted_at
        FROM quiz_rankings
        WHERE quiz_id=?
    ''', (quiz_id,)).fetchall()
    sorted_rows = sorted(rows, key=lambda row: (-row['score'], row['submitted_at']))
    for rank, row in enumerate(sorted_rows, 1):
        conn.execute('UPDATE quiz_rankings SET rank=? WHERE ranking_id=?', (rank, row['ranking_id']))
    conn.execute('UPDATE quizzes SET ranking_finalized=1 WHERE quiz_id=?', (quiz_id,))
    conn.commit()
    conn.close()

def get_unnotified_quiz_rankings(quiz_id):
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT * FROM quiz_rankings
        WHERE quiz_id=? AND notification_sent=0 AND rank IS NOT NULL
        ORDER BY ranking_id
    ''', (quiz_id,)).fetchall()
    conn.close()
    return rows

def mark_quiz_ranking_notification_sent(ranking_id):
    conn = get_db_connection()
    conn.execute('UPDATE quiz_rankings SET notification_sent=1 WHERE ranking_id=?', (ranking_id,))
    conn.commit()
    conn.close()

def get_quiz_winner(quiz_id):
    conn = get_db_connection()
    row = conn.execute('''
        SELECT * FROM quiz_rankings
        WHERE quiz_id=? AND rank=1
        ORDER BY ranking_id
        LIMIT 1
    ''', (quiz_id,)).fetchone()
    conn.close()
    return row

def mark_quiz_certificate_sent(ranking_id):
    conn = get_db_connection()
    conn.execute('UPDATE quiz_rankings SET certificate_sent=1 WHERE ranking_id=?', (ranking_id,))
    conn.commit()
    conn.close()

def add_quiz_question(quiz_id, q_text, a, b, c, d, correct):
    conn = get_db_connection()
    conn.execute('INSERT INTO questions (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option) VALUES (?, ?, ?, ?, ?, ?, ?)',
                 (quiz_id, q_text, a, b, c, d, correct))
    conn.commit()
    conn.close()

def create_quiz_from_questions(content_id, questions):
    conn = get_db_connection()
    try:
        quiz_number = conn.execute('SELECT COUNT(*) FROM quizzes WHERE content_id=?', (content_id,)).fetchone()[0] + 1
        cursor = conn.execute(
            'INSERT INTO quizzes (content_id, quiz_title) VALUES (?, ?)',
            (content_id, f'Quiz {quiz_number}')
        )
        quiz_id = cursor.lastrowid
        conn.executemany('''
            INSERT INTO questions
            (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', [(
            quiz_id,
            question['question'],
            question['options']['A'],
            question['options']['B'],
            question['options']['C'],
            question['options']['D'],
            question['correctAnswer']
        ) for question in questions])
        conn.commit()
        return quiz_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def get_subjects_by_tier(year, semester):
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM subjects WHERE academic_year=? AND semester=?', (year, semester)).fetchall()
    conn.close()
    return rows

def get_contents_by_subject(subject_id):
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM contents WHERE subject_id=?', (subject_id,)).fetchall()
    conn.close()
    return rows

def get_admin_account(user_id):
    conn = get_db_connection()
    row = conn.execute('SELECT * FROM admin_accounts WHERE user_id=?', (user_id,)).fetchone()
    conn.close()
    return row

def get_admin_role(user_id, main_admin_ids):
    if user_id in main_admin_ids:
        return 'main_admin'
    account = get_admin_account(user_id)
    return account['role'] if account else None

def get_admin_scopes(user_id):
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM admin_scopes WHERE user_id=? ORDER BY academic_year, semester', (user_id,)).fetchall()
    conn.close()
    return rows

def get_assistant_admins(role, academic_year=None, semester=None):
    conn = get_db_connection()
    if academic_year and semester:
        rows = conn.execute('''
            SELECT DISTINCT admin_accounts.user_id
            FROM admin_accounts
            JOIN admin_scopes ON admin_scopes.user_id=admin_accounts.user_id
            WHERE admin_accounts.role=?
              AND admin_scopes.academic_year=?
              AND admin_scopes.semester=?
            ORDER BY admin_accounts.user_id
        ''', (role, academic_year, semester)).fetchall()
    else:
        rows = conn.execute(
            'SELECT user_id FROM admin_accounts WHERE role=? ORDER BY user_id',
            (role,)
        ).fetchall()
    conn.close()
    return [row['user_id'] for row in rows]

def admin_has_scope(user_id, academic_year, semester, main_admin_ids):
    if user_id in main_admin_ids:
        return True
    conn = get_db_connection()
    row = conn.execute('''
        SELECT 1 FROM admin_scopes
        WHERE user_id=? AND academic_year=? AND semester=?
    ''', (user_id, academic_year, semester)).fetchone()
    conn.close()
    return row is not None

def get_subject_scope(subject_id):
    conn = get_db_connection()
    row = conn.execute('SELECT academic_year, semester FROM subjects WHERE subject_id=?', (subject_id,)).fetchone()
    conn.close()
    return row

def get_content_scope(content_id):
    conn = get_db_connection()
    row = conn.execute('''
        SELECT subjects.academic_year, subjects.semester
        FROM contents JOIN subjects ON subjects.subject_id=contents.subject_id
        WHERE contents.content_id=?
    ''', (content_id,)).fetchone()
    conn.close()
    return row

def upsert_assistant_admin(user_id, role, scopes):
    conn = get_db_connection()
    conn.execute('INSERT OR REPLACE INTO admin_accounts (user_id, role) VALUES (?, ?)', (user_id, role))
    conn.execute('DELETE FROM admin_scopes WHERE user_id=?', (user_id,))
    conn.executemany(
        'INSERT INTO admin_scopes (user_id, academic_year, semester) VALUES (?, ?, ?)',
        [(user_id, year, semester) for year, semester in scopes]
    )
    conn.commit()
    conn.close()

def list_assistant_admins():
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM admin_accounts ORDER BY user_id').fetchall()
    conn.close()
    return rows

def remove_assistant_admin(user_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM admin_scopes WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM admin_accounts WHERE user_id=?', (user_id,))
    conn.commit()
    conn.close()

def get_quiz_questions(quiz_id):
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM questions WHERE quiz_id=?', (quiz_id,)).fetchall()
    conn.close()
    return rows

def get_users_for_broadcast(year=None, semester=None):
    # لجلب آيديهات الطلاب للإذاعة نمر على كافة الملفات الموجودة في مجلد الطلاب
    user_ids = []
    if not os.path.exists(STUDENTS_DIR):
        return user_ids
        
    for f in os.listdir(STUDENTS_DIR):
        if f.endswith(".db"):
            u_id = f.replace(".db", "")
            if u_id.isdigit():
                u_id = int(u_id)
                if year and semester:
                    profile = get_user_profile(u_id)
                    if profile and profile['academic_year'] == year and profile['semester'] == semester and not profile['telegram_blocked']:
                        user_ids.append(u_id)
                else:
                    user_ids.append(u_id)
    return user_ids

# --- دوال الحذف والـ Reset الكلي والفردي الصارم عن طريق مسح الملفات 🗑️ ---

def reset_single_student_academic(user_id):
    """Reset the complete bot experience while preserving moderation status and identity."""
    student_db_path = os.path.join(STUDENTS_DIR, f"{user_id}.db")
    if os.path.exists(student_db_path):
        conn = get_student_db_connection(user_id)
        conn.execute('''
            UPDATE profile
            SET academic_year=NULL, semester=NULL, guide_seen=0, blocked_notice_sent=0
            WHERE user_id=?
        ''', (user_id,))
        conn.execute('DELETE FROM student_answers')
        conn.execute('DELETE FROM quiz_results')
        conn.commit()
        conn.close()
    conn = get_db_connection()
    conn.execute('DELETE FROM quiz_rankings WHERE student_id=?', (user_id,))
    conn.commit()
    conn.close()

def reset_all_students_academic():
    """Reset every student experience without deleting status, identity, or admin data."""
    if os.path.exists(STUDENTS_DIR):
        for f in os.listdir(STUDENTS_DIR):
            if f.endswith(".db"):
                try:
                    reset_single_student_academic(int(f[:-3]))
                except Exception:
                    pass

def build_quiz_motivation_message(score, total):
    percentage = (score / total * 100) if total else 0
    if percentage >= 85:
        return "🌟 أداء رائع جدًا! استمر على نفس المستوى وأنت من أوائل الدفعة."
    if percentage >= 60:
        return "💪 نتيجة جيدة، ومع مراجعة الأخطاء هتوصل لمستوى أعلى في الاختبار القادم."
    return "🚀 لا تقلق، البداية دايمًا فيها تحدي؛ راجع الأخطاء خطوة بخطوة وهتتحسن بسرعة."

def process_expired_quiz_rankings(bot):
    for quiz_id in get_quizzes_ready_for_finalization():
        finalize_quiz_ranking(quiz_id)
        context = get_quiz_context(quiz_id)
        if not context:
            continue
        for ranking in get_unnotified_quiz_rankings(quiz_id):
            percentage = f"{ranking['percentage']:.2f}".rstrip('0').rstrip('.')
            motivation_message = build_quiz_motivation_message(ranking['score'], ranking['total'])
            certificate_note = (
                "🏅 مبروك! أنت الأول في هذا الاختبار وستصلك الشهادة الآن."
                if ranking['rank'] == 1
                else "ℹ️ الشهادة في هذا الاختبار مخصصة للمركز الأول فقط."
            )
            message = (
                "🎉 **نتائج الاختبار**\n\n"
                f"📚 المادة: {ranking['subject_name']}\n"
                f"📖 المحتوى: {ranking['content_title']}\n\n"
                f"📊 نتيجتك: {percentage}% ({ranking['score']} / {ranking['total']})\n"
                f"🏆 ترتيبك: {ranking['rank']}\n"
                f"{motivation_message}\n"
                f"{certificate_note}\n\n"
                "ادخل على الاختبار مرة أخرى لمراجعة الأسئلة وإجاباتك والإجابات الصحيحة."
            )
            try:
                bot.send_message(ranking['student_id'], message, parse_mode="Markdown")
            except Exception as error:
                handle_telegram_delivery_failure(bot, ranking['student_id'], error)
                print(f"Could not notify student {ranking['student_id']} about quiz {quiz_id}: {error}")
            else:
                mark_quiz_ranking_notification_sent(ranking['ranking_id'])

        winner = get_quiz_winner(quiz_id)
        if winner and not winner['certificate_sent']:
            certificate_path = None
            try:
                from services.certificate_generator import create_certificate
                certificate_path = create_certificate(
                    winner['student_id'], context, winner['score'], winner['total']
                )
                with open(certificate_path, 'rb') as certificate_file:
                    bot.send_photo(
                        winner['student_id'],
                        certificate_file,
                        caption="Certificate of Appreciation from VETBOT"
                    )
                mark_quiz_certificate_sent(winner['ranking_id'])
            except Exception as error:
                handle_telegram_delivery_failure(bot, winner['student_id'], error)
                print(f"Could not send winner certificate for quiz {quiz_id}: {error}")
            finally:
                if certificate_path and os.path.exists(certificate_path):
                    os.remove(certificate_path)

def run_quiz_ranking_worker(bot, stop_event, interval_seconds=60):
    while not stop_event.is_set():
        try:
            process_expired_quiz_rankings(bot)
        except Exception as error:
            print(f"Quiz ranking worker error: {error}")
        stop_event.wait(interval_seconds)
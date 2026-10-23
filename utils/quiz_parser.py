# utils/quiz_parser.py
import docx
import re

def parse_docx_and_save_quiz(file_path, quiz_id):
    """
    مفسر ذكي ومرن لقراءة الأسئلة من ملف الوورد وحفظها في قاعدة البيانات.
    يتحمل انقسام الأسئلة بين الصفحات، اختلاف الترقيم، والمسافات الزائدة تماماً.
    """
    # استدعاء الدالة من الـ db_handler هنا محلياً داخل الدالة لمنع الـ Import Loop 🛠️
    from database.db_handler import add_quiz_question

    doc = docx.Document(file_path)
    questions_list = []
    
    current_q = {
        'text': '',
        'options': {},
        'correct': None
    }
    
    # أنماط التعرف الذكي باستخدام التعبيرات المنتظمة (Regex)
    # 1. نمط التعرف على الاختيارات: مثل A) أو B. أو C - أو دمج مسافات
    option_pattern = re.compile(r'^\s*([A-D])[\s\)\.\-]', re.IGNORECASE)
    # 2. نمط التعرف على الإجابة الصحيحة: مثل Answer: A أو Correct: B أو الإجابة: C أو الجواب : D
    answer_pattern = re.compile(r'^\s*(Answer|Correct|الإجابة|الجواب)\s*:\s*([A-D])', re.IGNORECASE)
    
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue  # تخطي الأسطر الفارغة تماماً لمنع الأخطاء الناتجة عن فواصل الصفحات
            
        # أولاً: فحص ما إذا كان السطر هو سطر الإجابة الصحيحة
        ans_match = answer_pattern.match(text)
        if ans_match:
            current_q['correct'] = ans_match.group(2).upper()
            continue
            
        # ثانياً: فحص ما إذا كان السطر عبارة عن اختيار (A, B, C, D)
        opt_match = option_pattern.match(text)
        if opt_match:
            letter = opt_match.group(1).upper()
            # تنظيف نص الاختيار من الحرف والعلامة التي بجانبه (مثل تحويل "A) Cell" إلى "Cell")
            opt_text = re.sub(r'^\s*[A-D][\s\)\.\-]+\s*', '', text, flags=re.IGNORECASE).strip()
            current_q['options'][letter] = opt_text
            continue
            
        # ثالثاً: إذا لم يكن إجابة أو اختيار، إذن هو نص السؤال (أو تكملة له بسبب انقسام الصفحات)
        # تنظيف أي كلمات مفتاحية زائدة أو ترقيم في بداية السؤال مثل "1." أو "Question:"
        clean_text = re.sub(r'^\s*(Question|\d+)?[\s\:\.\-]*', '', text, flags=re.IGNORECASE).strip()
        
        # إذا كان لدينا سؤال سابق مكتمل تماماً (نص + اختيارات + إجابة) وجاء نص جديد، 
        # نقوم بحفظ السؤال القديم في القائمة أولاً ونبدأ في بناء سؤال جديد
        if current_q['text'] and len(current_q['options']) >= 2 and current_q['correct']:
            questions_list.append(current_q)
            current_q = {'text': clean_text, 'options': {}, 'correct': None}
        else:
            # لو كنا لسه بنبني في السؤال الحالي أو بنكمل نص السؤال المكسور بين صفحتين
            if not current_q['text']:
                current_q['text'] = clean_text
            else:
                current_q['text'] += " " + clean_text

    # إضافة آخر سؤال في الملف بعد خروجنا من الحلقة (لو كان مكتملاً)
    if current_q['text'] and len(current_q['options']) >= 2 and current_q['correct']:
        questions_list.append(current_q)

    # الآن نقوم بحفظ جميع الأسئلة المستخرجة بنجاح داخل قاعدة البيانات
    saved_count = 0
    for q in questions_list:
        # تأمين الاختيارات الأربعة؛ إذا نقص أحدها يتم تعويضه بنص فارغ حتى لا تضرب الداتابيز
        opt_a = q['options'].get('A', '')
        opt_b = q['options'].get('B', '')
        opt_c = q['options'].get('C', '')
        opt_d = q['options'].get('D', '')
        
        if q['text'] and q['correct']:
            add_quiz_question(quiz_id, q['text'], opt_a, opt_b, opt_c, opt_d, q['correct'])
            saved_count += 1
            
    return saved_count
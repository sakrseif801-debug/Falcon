import os
import platform
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "--quiet"])
    from PIL import Image, ImageDraw, ImageFont

from database.db_handler import get_user_profile


def _font(size, bold=False, emoji=False):
    system = platform.system()
    
    if system == "Windows":
        windows_fonts = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
        if emoji:
            filenames = ("seguiemj.ttf", "seguisym.ttf")
        else:
            filenames = ("segoeuib.ttf", "tahomabd.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "tahoma.ttf", "arial.ttf")
        for filename in filenames:
            path = os.path.join(windows_fonts, filename)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
    else:
        linux_dirs = [
            "/usr/share/fonts/truetype/dejavu",
            "/usr/share/fonts/truetype/liberation",
            "/usr/share/fonts/truetype/msttcorefonts",
            "/usr/share/fonts/truetype/freefont"
        ]
        if emoji:
            filenames = ("DejaVuSans.ttf",)
        else:
            filenames = ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf") if bold else ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "arial.ttf")
        
        for font_dir in linux_dirs:
            for filename in filenames:
                path = os.path.join(font_dir, filename)
                if os.path.exists(path):
                    return ImageFont.truetype(path, size)
                    
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _draw_student_name(draw, name, center_x, y, size):
    """Draw the original Telegram name, switching fonts for emoji characters."""
    runs = []
    for character in unicodedata.normalize("NFKC", str(name or "Student")):
        is_emoji = ord(character) >= 0x1F000 or character in "©®™❤⭐"
        if runs and runs[-1][0] == is_emoji:
            runs[-1][1] += character
        else:
            runs.append([is_emoji, character])
    widths = [draw.textlength(text, font=_font(size, True, emoji)) for emoji, text in runs]
    if sum(widths) > 1190:
        scale = 1190 / sum(widths)
        size = max(32, int(size * scale))
        widths = [draw.textlength(text, font=_font(size, True, emoji)) for emoji, text in runs]
    x = center_x - sum(widths) / 2
    for (emoji, text), width in zip(runs, widths):
        draw.text((x + width / 2, y), text, fill="#172333", font=_font(size, True, emoji), anchor="mm")
        x += width


def create_certificate(student_id, quiz_context, score, total):
    profile = get_user_profile(student_id)
    student_name = profile['full_name'] if profile else "Student"
    content_title = quiz_context['content_title'] if 'content_title' in quiz_context.keys() else ''
    width, height = 1600, 1100
    image = Image.new("RGB", (width, height), "#f7f2e5")
    draw = ImageDraw.Draw(image)

    # A warm paper background with a subtle vertical color wash.
    for y in range(height):
        blend = y / height
        color = tuple(int(start + (end - start) * blend) for start, end in zip((247, 242, 229), (232, 240, 234)))
        draw.line((0, y, width, y), fill=color)

    navy, green, gold, muted = "#203852", "#1d6b55", "#b68a2b", "#687a82"
    draw.rectangle((24, 24, width - 24, height - 24), outline=gold, width=9)
    draw.rectangle((47, 47, width - 47, height - 47), outline=navy, width=3)
    draw.rectangle((74, 74, width - 74, height - 74), outline="#d8c58f", width=1)

    # Corner flourishes keep the certificate formal without making it noisy.
    for x, y, sign_x, sign_y in ((78, 78, 1, 1), (width - 78, 78, -1, 1), (78, height - 78, 1, -1), (width - 78, height - 78, -1, -1)):
        draw.arc((x - 34, y - 34, x + 34, y + 34), 0, 360, fill=gold, width=3)
        draw.line((x, y, x + 72 * sign_x, y), fill=gold, width=3)
        draw.line((x, y, x, y + 72 * sign_y), fill=gold, width=3)

    draw.ellipse((width // 2 - 48, 105, width // 2 + 48, 201), fill=green, outline=gold, width=5)
    draw.text((width // 2, 153), "V", fill="#f7f2e5", font=_font(62, True), anchor="mm")
    draw.text((width // 2, 244), "VETBOT", fill=navy, font=_font(46, True), anchor="mm")
    draw.text((width // 2, 325), "CERTIFICATE OF APPRECIATION", fill=gold, font=_font(64, True), anchor="mm")
    draw.line((440, 373, 1160, 373), fill=gold, width=3)
    draw.text((width // 2, 425), "This certificate is proudly presented to", fill=navy, font=_font(30), anchor="mm")
    _draw_student_name(draw, student_name, width // 2, 520, 62)
    draw.line((390, 580, 1210, 580), fill="#c6d4c9", width=2)
    draw.text((width // 2, 633), f"for achieving First Place in {quiz_context['quiz_title']}", fill=navy, font=_font(31), anchor="mm")
    if content_title:
        draw.text((width // 2, 676), f"Lecture/Section: {content_title}", fill=muted, font=_font(24), anchor="mm")

    score_box = (355, 708, 1245, 820)
    draw.rounded_rectangle(score_box, radius=18, fill="#ffffff66", outline="#c9d6c9", width=2)
    draw.text((520, 764), quiz_context['subject_name'], fill=green, font=_font(30, True), anchor="mm")
    draw.line((800, 730, 800, 798), fill="#c9d6c9", width=2)
    percentage = round(score / total * 100) if total else 0
    draw.text((1015, 764), f"Score  {score}/{total}  |  {percentage}%", fill=navy, font=_font(29, True), anchor="mm")

    year = quiz_context['academic_year'] if 'academic_year' in quiz_context.keys() else ''
    semester = quiz_context['semester'] if 'semester' in quiz_context.keys() else ''
    draw.text((width // 2, 867), f"{year}  •  {semester}", fill=muted, font=_font(25), anchor="mm")
    draw.text((width // 2, 934), f"Issued by VETBOT  •  {datetime.now().strftime('%Y-%m-%d')}", fill=muted, font=_font(22), anchor="mm")
    draw.text((width // 2, 985), "Keep learning. Keep rising.", fill=green, font=_font(21, True), anchor="mm")

    output = tempfile.NamedTemporaryFile(prefix="vetbot_certificate_", suffix=".png", delete=False)
    output.close()
    image.save(output.name, "PNG")
    return output.name
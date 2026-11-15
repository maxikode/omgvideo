import ast
import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import aiohttp
import imageio_ffmpeg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("omgvideo")

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

WIDTH, HEIGHT, FPS = 720, 405, 24
MAX_SECONDS = 300  # 5 minutes
MAX_COMMANDS = 40
MAX_VIDEO_BYTES = 48_000_000
BG = (18, 20, 30)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health server listening on port %s", port)


HELP = """🎬 OMGVideo

Я создаю короткие MP4-анимации двумя способами:

1. Пришли описание обычными словами:
«Красный шар плавно летит слева направо».

2. Пришли код в поддерживаемом формате:
circle(id="ball", x=100, y=200, radius=35, color="red")
move(id="ball", x=600, y=200, duration=3)
text(value="Привет!", x=360, y=70, color="white")

Координаты: x от 0 до 720, y от 0 до 405.
Поддерживаются circle, rect, text и move.
Максимальная длина ролика — 5 минут.

Команды:
/start — помощь
/help — помощь

Для текстовой генерации нужна переменная GROQ_API_KEY в Render.
"""

# This is a small declarative animation language, NOT arbitrary Python.
# The submitted text is parsed as an AST; it is never exec()'d or eval()'d.
ALLOWED = {"circle", "rect", "text", "move"}
COLORS = {
    "red": (245, 75, 75), "blue": (70, 140, 245),
    "green": (70, 200, 125), "yellow": (250, 210, 70),
    "white": (245, 245, 250), "black": (10, 10, 10),
    "purple": (170, 110, 230), "orange": (245, 150, 65),
    "pink": (245, 125, 180), "cyan": (75, 215, 220),
}

def parse_script(source: str):
    if len(source) > 6000:
        raise ValueError("Код слишком длинный (максимум 6000 символов).")
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"Синтаксическая ошибка в строке {exc.lineno}.") from exc

    commands = []
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            raise ValueError("Разрешены только вызовы circle(), rect(), text() и move().")
        call = node.value
        if not isinstance(call.func, ast.Name) or call.func.id not in ALLOWED:
            raise ValueError("Недопустимая команда. Используй circle, rect, text или move.")
        # For backward compatibility, move("ball", x=..., y=..., duration=...)
        # is accepted as well as move(id="ball", ...).
        if any(k.arg is None for k in call.keywords):
            raise ValueError("Распаковка параметров не поддерживается.")
        values = {}
        if call.args:
            if call.func.id != "move" or len(call.args) != 1:
                raise ValueError("Для circle, rect и text используй именованные параметры.")
            arg = call.args[0]
            if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                raise ValueError('Первый параметр move должен быть ID объекта, например move("ball", ...).')
            values["id"] = arg.value
        for kw in call.keywords:
            value = kw.value
            if not isinstance(value, ast.Constant) or not isinstance(value.value, (str, int, float)):
                raise ValueError("Параметры должны быть простыми числами или строками.")
            values[kw.arg] = value.value
        commands.append((call.func.id, values))
        if len(commands) > MAX_COMMANDS:
            raise ValueError(f"Максимум {MAX_COMMANDS} команд в одном ролике.")

    if not commands:
        raise ValueError("Код пустой.")
    return validate_commands(commands)

def validate_commands(commands):
    objects = {}
    timeline = []
    current_time = 0.0
    for name, p in commands:
        if name in ("circle", "rect"):
            obj_id = p.get("id")
            if not isinstance(obj_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,24}", obj_id):
                raise ValueError(f'{name}: укажи id, например id="ball".')
            if obj_id in objects:
                raise ValueError(f"Объект {obj_id} уже существует.")
            x, y = float(p.get("x", -1)), float(p.get("y", -1))
            if not (0 <= x <= WIDTH and 0 <= y <= HEIGHT):
                raise ValueError("Координаты должны находиться внутри кадра.")
            color = p.get("color", "blue")
            if color not in COLORS:
                raise ValueError("Неизвестный цвет. Доступные: " + ", ".join(COLORS))
            if name == "circle":
                size = float(p.get("radius", 0))
                if not 2 <= size <= 180:
                    raise ValueError("Радиус круга должен быть от 2 до 180.")
                obj = {"type": name, "x": x, "y": y, "radius": size, "color": color}
            else:
                w, h = float(p.get("width", 0)), float(p.get("height", 0))
                if not (2 <= w <= WIDTH and 2 <= h <= HEIGHT):
                    raise ValueError("Размер прямоугольника должен быть в пределах кадра.")
                obj = {"type": name, "x": x, "y": y, "width": w, "height": h, "color": color}
            objects[obj_id] = obj
            timeline.append((current_time, "create", obj_id, obj.copy()))
        elif name == "text":
            value = p.get("value")
            if not isinstance(value, str) or len(value) > 100:
                raise ValueError('text: укажи value="Текст" (до 100 символов).')
            x, y = float(p.get("x", -1)), float(p.get("y", -1))
            if not (0 <= x <= WIDTH and 0 <= y <= HEIGHT):
                raise ValueError("Координаты текста должны быть внутри кадра.")
            color = p.get("color", "white")
            if color not in COLORS:
                raise ValueError("Неизвестный цвет.")
            obj_id = f"text_{len(objects)}"
            obj = {"type": "text", "value": value, "x": x, "y": y, "color": color}
            objects[obj_id] = obj
            timeline.append((current_time, "create", obj_id, obj.copy()))
        elif name == "move":
            obj_id = p.get("id")
            if obj_id not in objects:
                raise ValueError(f"Сначала создай объект {obj_id!r}.")
            x, y = float(p.get("x", -1)), float(p.get("y", -1))
            duration = float(p.get("duration", 1))
            if not (0 <= x <= WIDTH and 0 <= y <= HEIGHT and 0.1 <= duration <= MAX_SECONDS):
                raise ValueError("Некорректные координаты или длительность движения.")
            start = current_time
            current_time += duration
            timeline.append((start, "move", obj_id, {"x": x, "y": y, "duration": duration}))
        if current_time > MAX_SECONDS:
            raise ValueError(f"Длительность не должна превышать {MAX_SECONDS} секунд.")
    total = max(2.0, current_time)
    if total > MAX_SECONDS:
        raise ValueError(f"Длительность ролика не должна превышать {MAX_SECONDS // 60} минут.")
    return timeline, total

def render_video(commands, output_path):
    timeline, total = validate_commands(commands)
    frames = int(total * FPS)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    import subprocess
    stderr_file = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        [ffmpeg, "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
         "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS),
         "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "28", "-maxrate", "1200k", "-bufsize", "2400k",
         "-threads", "2", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(output_path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr_file
    )
    try:
        for frame_no in range(frames):
            t = frame_no / FPS
            image = Image.new("RGB", (WIDTH, HEIGHT), BG)
            draw = ImageDraw.Draw(image)
            state = {}
            for start, kind, obj_id, data in timeline:
                if kind == "create" and t >= start:
                    state[obj_id] = data.copy()
                elif kind == "move" and obj_id in state and t >= start:
                    old_x, old_y = state[obj_id]["x"], state[obj_id]["y"]
                    progress = min(1.0, max(0.0, (t - start) / data["duration"]))
                    state[obj_id]["x"] = old_x + (data["x"] - old_x) * progress
                    state[obj_id]["y"] = old_y + (data["y"] - old_y) * progress
            for obj in state.values():
                color = COLORS[obj["color"]]
                x, y = obj["x"], obj["y"]
                if obj["type"] == "circle":
                    r = obj["radius"]
                    draw.ellipse((x-r, y-r, x+r, y+r), fill=color)
                elif obj["type"] == "rect":
                    w, h = obj["width"], obj["height"]
                    draw.rounded_rectangle((x-w/2, y-h/2, x+w/2, y+h/2), radius=8, fill=color)
                else:
                    try:
                        font = ImageFont.truetype("DejaVuSans.ttf", 30)
                    except OSError:
                        font = ImageFont.load_default()
                    draw.text((x, y), obj["value"], fill=color, font=font, anchor="mm")
            proc.stdin.write(image.tobytes())
        proc.stdin.close()
        code = proc.wait(timeout=900)
        if code:
            stderr_file.seek(0)
            stderr = stderr_file.read()
            raise RuntimeError(stderr.decode("utf-8", "ignore")[-1200:])
    except Exception:
        if proc.poll() is None:
            proc.kill()
        raise
    finally:
        stderr_file.close()

async def generate_script(description: str):
    if not GROQ_API_KEY:
        raise ValueError("Текстовая генерация пока не настроена. Добавь GROQ_API_KEY в Render.")
    prompt = (
        "Create a short animation using ONLY these DSL calls, one per line: "
        'circle(id="ball", x=100, y=200, radius=30, color="red"), '
        'rect(id="box", x=100, y=200, width=80, height=50, color="blue"), '
        'text(value="Hello", x=360, y=60, color="white"), '
        'move(id="ball", x=600, y=200, duration=3). '
        "Coordinates x 0..720, y 0..405. Colors: red, blue, green, yellow, "
        "white, black, purple, orange, pink, cyan. Max 40 calls. "
        "Maximum animation duration is 300 seconds (5 minutes); total move durations must not exceed 300 seconds. "
        "No markdown, no explanation, no other syntax. User description: " + description[:1000]
    )
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 700}
        ) as response:
            data = await response.json()
            if response.status != 200:
                raise ValueError("Groq API вернул ошибку: " + str(data.get("error", {}).get("message", response.status))[:300])
            content = data["choices"][0]["message"]["content"]
            content = (content or "").strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:python)?\s*", "", content, count=1, flags=re.IGNORECASE)
                content = re.sub(r"\s*\`\`\`\s*$", "", content, count=1)
            if not content:
                raise ValueError("Groq вернул пустой сценарий. Попробуй описать анимацию подробнее.")
            return parse_script(content)

async def make_and_send(message: Message, commands):
    status = await message.answer("🎬 Создаю анимацию. Это может занять немного времени…")
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "omgvideo.mp4"
            await asyncio.to_thread(render_video, commands, output)
            if output.stat().st_size > MAX_VIDEO_BYTES:
                raise RuntimeError("Получилось слишком большое видео для отправки в Telegram. Уменьши длительность или количество объектов.")
            from aiogram.types import FSInputFile
            await message.answer_video(FSInputFile(output, filename="omgvideo.mp4"),
                                       caption="🎞️ Готово! Анимация создана в OMGVideo.")
        await status.delete()
    except Exception as exc:
        log.exception("Render failed")
        await status.edit_text("Не удалось создать видео: " + str(exc)[:700])

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения Render.")
    start_health_server()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(CommandStart())
    @dp.message(F.text == "/help")
    async def help_handler(message: Message):
        await message.answer(HELP)

    @dp.message(F.text)
    async def text_handler(message: Message):
        source = message.text.strip()
        if source.startswith("/"):
            return
        if source.startswith("```"):
            source = re.sub(r"^\`\`\`(?:python)?\s*", "", source, count=1, flags=re.IGNORECASE)
            source = re.sub(r"\s*```$", "", source, count=1)
        try:
            if re.search(r"\b(circle|rect|text|move)\s*\(", source):
                commands = parse_script(source)
            else:
                commands = await generate_script(source)
            await make_and_send(message, commands)
        except ValueError as exc:
            await message.answer("⚠️ " + str(exc) + "\n\n/help — примеры и инструкции.")
        except Exception:
            log.exception("Message handling failed")
            await message.answer("⚠️ Не получилось обработать запрос. Попробуй ещё раз.")

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

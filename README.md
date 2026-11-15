# OMGVideo

Telegram bot that creates short MP4 animations from a small, safe animation language or from a natural-language prompt (Groq API).

## Features
- Send a description in Russian or English to generate an animation (requires GROQ_API_KEY).
- Send DSL commands to render an animation without an AI API.
- MP4 output, 720x405, 24 FPS, up to 5 minutes (300 seconds).

## Animation language
The bot does NOT execute arbitrary Python. It parses a restricted set of declarative commands.

```python
circle(id="ball", x=100, y=200, radius=35, color="red")
move(id="ball", x=600, y=200, duration=3)
text(value="Hello!", x=360, y=70, color="white")
```

Supported commands:
- circle(id, x, y, radius, color)
- rect(id, x, y, width, height, color)
- text(value, x, y, color)
- move(id, x, y, duration); the first move argument may also be written positionally, e.g. `move("ball", x=600, y=200, duration=3)`

Coordinates are pixels in a 720x405 frame. Colors: red, blue, green, yellow, white, black, purple, orange, pink, cyan.

## Deploy on Render (Free Web Service)
Create a **Web Service** from this repository (not a Background Worker).

- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `python bot.py`
- Instance type: Free
- Health check path: `/`

Add environment variables:
- `BOT_TOKEN`: token from BotFather
- `GROQ_API_KEY`: Groq API key (needed only for text prompts)
- `GROQ_MODEL`: optional; defaults to `llama-3.3-70b-versatile`

The app runs Telegram long polling and a small HTTP health server on Render's `PORT`. The free Render web service can spin down after a period without inbound HTTP traffic, so the bot may be temporarily unavailable while it sleeps and can take time to wake up. A health endpoint does not prevent free-instance sleep. Do not run a second copy of the bot with the same Telegram token at the same time, because Telegram polling will conflict.

## Safety note
This is intentionally not an arbitrary Python execution service. The bot parses a tiny allowlisted DSL and never calls exec/eval on user input. This reduces code-execution risk substantially. Keep dependencies updated and never add arbitrary imports, file access, shell commands, or network access to user scripts.

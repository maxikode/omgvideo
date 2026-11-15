# OMGVideo

Telegram bot that creates short MP4 animations from a small, safe animation language or from a natural-language prompt (Groq API).

## Features
- Send a description in Russian or English to generate an animation (requires GROQ_API_KEY).
- Send DSL commands to render an animation without an AI API.
- MP4 output, 720x405, 24 FPS, up to 12 seconds.

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
- move(id, x, y, duration)

Coordinates are pixels in a 720x405 frame. Colors: red, blue, green, yellow, white, black, purple, orange, pink, cyan.

## Deploy on Render
Create a Background Worker from this repository. Build command: `pip install -r requirements.txt`. Start command: `python bot.py`. Add environment variables:
- BOT_TOKEN: token from BotFather
- GROQ_API_KEY: Groq API key (needed only for text prompts)
- GROQ_MODEL: optional; defaults to `llama-3.3-70b-versatile`

The free Render worker availability/limits can change; if the free plan is not offered for Background Workers, choose an available worker plan. Do not deploy this as a web service: it uses Telegram long polling and does not need a public HTTP port.

## Safety note
This is intentionally not an arbitrary Python execution service. The bot parses a tiny allowlisted DSL and never calls exec/eval on user input. This reduces code-execution risk substantially. Keep dependencies updated and never add arbitrary imports, file access, shell commands, or network access to user scripts.

import os
import discord
from discord.ext import commands
import requests
from flask import Flask
import threading

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=10000)


BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def repair_file_with_ai(content: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }
    data = {
        "model": "llama-3.1-70b-versatile",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert JSON/XML repair engine. "
                    "Fix malformed JSON or XML while preserving ALL values. "
                    "Do not invent new values. Do not remove objects. "
                    "Return ONLY the corrected file."
                )
            },
            {
                "role": "user",
                "content": content
            }
        ],
        "temperature": 0
    }

    resp = requests.post(url, headers=headers, json=data)
    resp.raise_for_status()
    result = resp.json()
    return result["choices"][0]["message"]["content"].strip()

@bot.command()
async def fixai(ctx):
    if not ctx.message.attachments:
        await ctx.send("Attach a JSON/XML file to fix.")
        return

    attachment = ctx.message.attachments[0]
    raw_bytes = await attachment.read()
    raw_text = raw_bytes.decode("utf-8", errors="replace")

    await ctx.send("🛠 Sending file to AI for repair…")

    try:
        fixed = repair_file_with_ai(raw_text)
    except Exception as e:
        await ctx.send(f"❌ AI ERROR: {e}")
        return

    from io import BytesIO
    buf = BytesIO(fixed.encode("utf-8"))

    out_name = attachment.filename.replace(".", "_fixed.")
    await ctx.send(file=discord.File(buf, out_name))

threading.Thread(target=run_flask).start()
bot.run(BOT_TOKEN)

import os
import discord
from discord.ext import commands
from groq import Groq
from flask import Flask
import threading

# ============================
# Flask Web Server (for Render)
# ============================

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=10000)


# ============================
# Environment Variables
# ============================

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

print("DISCORD_BOT_TOKEN:", "SET" if BOT_TOKEN else "MISSING")
print("GROQ_API_KEY:", "SET" if GROQ_API_KEY else "MISSING")


# ============================
# Discord Bot Setup
# ============================

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


# ============================
# Groq AI Client
# ============================

client = Groq(api_key=GROQ_API_KEY)


# ============================
# AI Repair Function
# ============================

def repair_file_with_ai(content: str) -> str:
    completion = client.chat.completions.create(
        model="llama-3.1-70b-versatile",
        messages=[
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
        temperature=0
    )

    return completion.choices[0].message.content.strip()


# ============================
# !fixai Command
# ============================

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


# ============================
# Start Flask + Discord Bot
# ============================

threading.Thread(target=run_flask, daemon=True).start()
bot.run(BOT_TOKEN)

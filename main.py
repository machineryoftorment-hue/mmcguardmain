import os
import asyncio
import json
import discord
from discord.ext import commands
import requests

# ============================
# 0. BASIC CONFIG
# ============================

INTENTS = discord.Intents.default()
INTENTS.message_content = True

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

bot = commands.Bot(command_prefix="!", intents=INTENTS)

# ============================
# 1. PROFANITY FILTER + WEBHOOK RESEND (SKELETON)
# ============================

# Put your existing profanity list here
PROFANITY = ["fuck", "shit", "cunt", "bitch"]

FUNNY_REPLACEMENTS = {
    "fuck": "hug",
    "shit": "poop nugget",
    "cunt": "silly goose",
    "bitch": "spicy friend",
}

async def filter_and_resend_message(message: discord.Message):
    """
    Example skeleton: replace profanity and resend via webhook.
    Wire this into on_message if you already have logic.
    """
    content = message.content
    lowered = content.lower()

    changed = False
    for bad, funny in FUNNY_REPLACEMENTS.items():
        if bad in lowered:
            content = content.replace(bad, funny)
            changed = True

    if not changed:
        return

    # Delete original
    try:
        await message.delete()
    except discord.Forbidden:
        pass

    # Resend via webhook (you can adapt to your existing webhook setup)
    if message.channel.webhooks:
        webhook = message.channel.webhooks[0]
    else:
        webhook = await message.channel.create_webhook(name="MMC Guard")

    await webhook.send(
        content,
        username=message.author.display_name,
        avatar_url=message.author.display_avatar.url,
    )

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Profanity filter
    await filter_and_resend_message(message)

    # Let commands still work
    await bot.process_commands(message)

# ============================
# 2. AI REPAIR ENGINE (OLLAMA)
# ============================

async def call_ai_repair_engine(content: str) -> str:
    """
    Calls local Ollama (llama3) to repair malformed JSON/XML.
    Assumes Ollama is running in the same container:
    `ollama serve` and `ollama pull llama3`.
    """

    url = "http://localhost:11434/api/generate"

    prompt = (
        "You are an expert JSON/XML repair engine.\n"
        "Fix the following malformed JSON or XML while preserving ALL values.\n"
        "Do not invent new values.\n"
        "Do not remove objects.\n"
        "Return ONLY the corrected file.\n\n"
        f"{content}"
    )

    payload = {
        "model": "llama3",
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(url, json=payload, timeout=120)
        data = response.json()
        return data.get("response", "AI ERROR: No response field in Ollama output.")
    except Exception as e:
        return f"AI ERROR: {e}"

# ============================
# 3. !fixai COMMAND
# ============================

@bot.command(name="fixai")
async def fixai(ctx: commands.Context):
    """
    Reads the last attached JSON/XML file in the channel,
    sends it to Ollama for repair, and returns the fixed file.
    """

    # Find the last attachment
    if not ctx.message.attachments:
        await ctx.send("Attach a JSON/XML file to the !fixai command.")
        return

    attachment = ctx.message.attachments[0]

    try:
        raw_bytes = await attachment.read()
        raw_text = raw_bytes.decode("utf-8", errors="replace")
    except Exception:
        await ctx.send("Could not read the attached file.")
        return

    await ctx.send("🛠 Running AI repair via Ollama… this may take a few seconds.")

    fixed = await call_ai_repair_engine(raw_text)

    if fixed.startswith("AI ERROR:"):
        await ctx.send(f"❌ {fixed}")
        return

    # Try to detect extension
    filename = attachment.filename
    if filename.lower().endswith(".json"):
        out_name = filename.replace(".json", "_fixed.json")
    elif filename.lower().endswith(".xml"):
        out_name = filename.replace(".xml", "_fixed.xml")
    else:
        out_name = filename + "_fixed.txt"

    # Send as file
    try:
        from io import BytesIO
        buf = BytesIO(fixed.encode("utf-8"))
        await ctx.send(file=discord.File(buf, filename=out_name))
    except Exception:
        await ctx.send("AI repaired content:\n```text\n" + fixed[:1900] + "\n```")

# ============================
# 4. BOT START
# ============================

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN is not set.")
    else:
        bot.run(BOT_TOKEN)

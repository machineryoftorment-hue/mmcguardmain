import os
import discord
from discord.ext import commands
from flask import Flask
import threading
import re
import json
import requests   # Needed for Groq AI calls

# -------------------------
# Flask server (keeps Render alive)
# -------------------------

app = Flask(__name__)

@app.route('/')
def home():
    return "MMCGuard is running"


# -------------------------
# Discord bot setup
# -------------------------

INTENTS = discord.Intents.default()
INTENTS.message_content = True

BOT_PREFIX = "!"
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Swear → funny replacement
PROFANITY_MAP = {
    "fuck": "fork",
    "fuk": "fork",
    "f*ck": "fork",
    "f**k": "fork",

    "shit": "poopoo",
    "sh1t": "poopoo",
    "sh!t": "poopoo",

    "bitch": "goose",
    "bish": "goose",
    "biatch": "goose",

    "bastard": "barnacle",

    "ass": "butt",
    "azz": "butt",
    "a$$": "butt",

    "dick": "noodle",
    "dik": "noodle",
    "d1ck": "noodle",

    "cunt": "sea cucumber",
    "c*nt": "sea cucumber",

    "bullshit": "bullsneeze",
    "bullsh*t": "bullsneeze",

    "motherfucker": "motherhugger",
    "motherf*cker": "motherhugger",

    "asshole": "butthole",
    "dickhead": "noodlehead",
    "shithead": "poopoohead",
    "fuckface": "forkface",

    "twat": "twig",
    "prick": "pinecone",
}

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=INTENTS)


# -------------------------
# Fuzzy profanity replacer
# -------------------------

def replace_profanity(text: str) -> str:
    cleaned = text
    for bad, funny in PROFANITY_MAP.items():
        fuzzy = "".join([f"{re.escape(c)}+" for c in bad])
        pattern = re.compile(fuzzy, re.IGNORECASE)
        cleaned = pattern.sub(funny, cleaned)
    return cleaned


# -------------------------
# Webhook helper
# -------------------------

async def get_or_create_webhook(channel: discord.TextChannel) -> discord.Webhook:
    hooks = await channel.webhooks()
    for hook in hooks:
        if hook.name == "MMCGuardFilter":
            return hook
    return await channel.create_webhook(name="MMCGuardFilter")
# -------------------------
# Groq AI Repair Engine
# -------------------------

async def call_ai_repair_engine(content: str) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "ERROR: GROQ_API_KEY is not set in Render."

    url = "https://api.groq.com/openai/v1/chat/completions"

    payload = {
    "model": "gpt-oss-20b",
    "messages": [
        {
            "role": "system",
            "content": (
                "You are an expert JSON/XML repair engine. "
                "Your job is to fix malformed JSON or XML while preserving ALL values. "
                "Do not invent new values. Do not remove objects. "
                "Do not reorder objects unless absolutely required. "
                "Return ONLY the corrected file with no explanation."
            )
        },
        {
            "role": "user",
            "content": content
        }
    ],
    "temperature": 0
}


    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers)

    try:
        return response.json()["choices"][0]["message"]["content"]
    except Exception:
        return "AI ERROR:\n" + response.text


# -------------------------
# Commands
# -------------------------

@bot.command(name="filterinfo")
async def filter_info(ctx: commands.Context):
    await ctx.send(
        "I replace spicy words with dumb funny ones.\n"
        "Current map:\n"
        + "\n".join([f"- {bad} → {funny}" for bad, funny in PROFANITY_MAP.items()])
    )


# -------------------------
# !validate (unchanged)
# -------------------------

@bot.command(name="validate")
async def validate(ctx):
    if not ctx.message.attachments:
        return await ctx.send("Please upload a file with the command.")

    attachment = ctx.message.attachments[0]
    file_bytes = await attachment.read()
    content = file_bytes.decode("utf-8", errors="ignore")

    # JSON validation
    if content.strip().startswith("{"):
        try:
            json.loads(content)
            return await ctx.send("✅ JSON is valid.")
        except Exception as e:
            return await ctx.send(f"❌ JSON is invalid:\n{e}")

    # XML validation
    if content.strip().startswith("<"):
        try:
            open_tags = []
            lines = content.splitlines()

            for i, line in enumerate(lines, start=1):
                stripped = line.strip()

                if stripped.startswith("<") and not stripped.startswith("</") and ">" in stripped:
                    tag = stripped.split(">")[0].replace("<", "").replace("/", "").split(" ")[0]
                    if tag:
                        open_tags.append(tag)

                if stripped.startswith("</"):
                    tag = stripped.replace("</", "").replace(">", "").strip()
                    if not open_tags or open_tags[-1] != tag:
                        return await ctx.send(f"❌ XML invalid at line {i}: mismatched closing tag </{tag}>")
                    open_tags.pop()

            if open_tags:
                return await ctx.send(f"❌ XML invalid: missing closing tag for <{open_tags[-1]}>")

            return await ctx.send("✅ XML appears structurally valid.")

        except Exception as e:
            return await ctx.send(f"❌ XML is invalid:\n{e}")

    return await ctx.send("Unknown format. Must start with `{` or `<`.")


# -------------------------
# !fixai — Groq AI Repair
# -------------------------

@bot.command(name="fixai")
async def fixai(ctx):
    if not ctx.message.attachments:
        return await ctx.send("Please upload a JSON or XML file with the command.")

    attachment = ctx.message.attachments[0]
    raw = (await attachment.read()).decode("utf-8", errors="ignore")

    await ctx.send("🧠 Sending file to AI repair engine...")

    fixed = await call_ai_repair_engine(raw)

    filename = "fixed_ai.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(fixed)

    await ctx.send(
        content="Here is your AI‑repaired file:",
        file=discord.File(filename)
    )
# -------------------------
# Events
# -------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Fuzzy profanity → funny webhook replacer is online.")
    print("Groq AI repair engine active.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    original = message.content
    cleaned = replace_profanity(original)

    # If no profanity detected, process commands normally
    if cleaned == original:
        await bot.process_commands(message)
        return

    # Try deleting the original message
    try:
        await message.delete()
    except discord.Forbidden:
        # If bot lacks permission, fallback to sending cleaned message
        await message.channel.send(
            f"🧼 **Cleaned message from {message.author.mention}:**\n{cleaned}"
        )
        return

    # Send cleaned message via webhook
    webhook = await get_or_create_webhook(message.channel)

    await webhook.send(
        content=cleaned,
        username=message.author.display_name,
        avatar_url=message.author.display_avatar.url,
    )

    await bot.process_commands(message)


# -------------------------
# Flask runner + bot runner
# -------------------------

def run_flask():
    app.run(host="0.0.0.0", port=10000)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Set DISCORD_BOT_TOKEN env var or hardcode your token.")

    threading.Thread(target=run_flask).start()
    bot.run(TOKEN)

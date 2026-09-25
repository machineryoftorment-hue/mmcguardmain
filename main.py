import os
import discord
from discord.ext import commands
from flask import Flask
import threading
import re
import json
import xml.etree.ElementTree as ET  # still here if you want to use it elsewhere

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
TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # Make sure Render uses this exact name

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
# File Fixer Engine (JSON + upgraded XML)
# -------------------------

def fix_json(text):
    try:
        return json.dumps(json.loads(text), indent=4)
    except:
        try:
            text = text.replace(",]", "]")
            text = text.replace(",}", "}")
            text = text.replace("\n", "")
            return json.dumps(json.loads(text), indent=4)
        except Exception as e:
            return f"JSON Fixer Error: {e}"

def fix_xml(text):
    lines = text.splitlines()
    fixed_lines = []
    tag_stack = []

    for line in lines:
        stripped = line.strip()

        # Keep empty lines as-is
        if not stripped:
            fixed_lines.append(line)
            continue

        # Fix illegal characters
        stripped = stripped.replace("&", "&amp;")

        # Detect opening tags
        if stripped.startswith("<") and not stripped.startswith("</") and ">" in stripped:
            tag = stripped.split(">")[0].replace("<", "").replace("/", "").strip()
            if " " in tag:
                tag = tag.split(" ")[0]
            if tag:
                tag_stack.append(tag)

        # Detect closing tags
        if stripped.startswith("</"):
            tag = stripped.replace("</", "").replace(">", "").strip()
            if tag_stack and tag_stack[-1] == tag:
                tag_stack.pop()
            else:
                # Auto-fix mismatched closing tags
                if tag_stack:
                    stripped = f"</{tag_stack[-1]}>"
                    tag_stack.pop()

        fixed_lines.append(stripped)

    # Auto-close any remaining tags
    while tag_stack:
        fixed_lines.append(f"</{tag_stack.pop()}>")

    return "\n".join(fixed_lines)


# -------------------------
# Events
# -------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Fuzzy profanity → funny webhook replacer is online.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    original = message.content
    cleaned = replace_profanity(original)

    if cleaned == original:
        await bot.process_commands(message)
        return

    try:
        await message.delete()
    except discord.Forbidden:
        await message.channel.send(
            f"🧼 **Cleaned message from {message.author.mention}:**\n{cleaned}"
        )
        return

    webhook = await get_or_create_webhook(message.channel)

    await webhook.send(
        content=cleaned,
        username=message.author.display_name,
        avatar_url=message.author.display_avatar.url,
    )

    await bot.process_commands(message)


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


@bot.command(name="fixupload")
async def fixupload(ctx):
    if not ctx.message.attachments:
        return await ctx.send("Please upload a file with the command.")

    attachment = ctx.message.attachments[0]
    file_bytes = await attachment.read()
    content = file_bytes.decode("utf-8", errors="ignore")

    if content.strip().startswith("{"):
        fixed = fix_json(content)
        filename = "fixed.json"
    elif content.strip().startswith("<"):
        fixed = fix_xml(content)
        filename = "fixed.xml"
    else:
        return await ctx.send("Unknown format. File must start with `{` for JSON or `<` for XML`.")

    with open(filename, "w", encoding="utf-8") as f:
        f.write(fixed)

    await ctx.send(
        content="Here is your fixed file:",
        file=discord.File(filename)
    )


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

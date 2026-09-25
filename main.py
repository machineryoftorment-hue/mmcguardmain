import os
import discord
from discord.ext import commands
from flask import Flask
import threading
import re
import json
import zipfile
import tempfile

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
# MAX SAFE POWER JSON FIXER
# -------------------------

def fix_json(text):
    original = text

    # First attempt: strict load
    try:
        json.loads(text)
        return text  # Already valid, preserve indentation
    except:
        pass

    # Safe comma insertion rules
    def insert_commas(t):
        out = []
        i = 0
        length = len(t)

        while i < length:
            out.append(t[i])

            # Insert comma between JSON fields when required
            if t[i] in ['"', '}', ']']:
                # Look ahead safely
                j = i + 1
                while j < length and t[j] in [' ', '\n', '\t']:
                    j += 1

                # If next non-space char starts a new field or object/array
                if j < length and t[j] in ['"', '{', '[']:
                    # Only insert if missing
                    if t[i] != ',':
                        out.append(',')

            i += 1

        return "".join(out)

    # Apply safe comma insertion
    text = insert_commas(text)

    # Remove trailing commas safely
    text = text.replace(",]", "]")
    text = text.replace(",}", "}")

    # Try loading again
    try:
        json.loads(text)
        return text  # Preserve indentation
    except Exception as e:
        return f"❌ Cannot safely fix JSON without assumptions.\n{e}"


# -------------------------
# MAX SAFE POWER XML FIXER
# -------------------------

def fix_xml(text):
    lines = text.splitlines()
    fixed_lines = []
    tag_stack = []

    for line in lines:
        stripped = line.rstrip()  # preserve indentation, remove trailing spaces only

        # Preserve blank lines
        if stripped.strip() == "":
            fixed_lines.append(line)
            continue

        # Preserve comments exactly
        if stripped.strip().startswith("<!--"):
            fixed_lines.append(line)
            continue

        # Escape illegal characters
        safe_line = stripped.replace("&", "&amp;")

        # Detect invalid closing tags like </!-->
        if safe_line.strip().startswith("</"):
            tagname = safe_line.strip()[2:].split(">")[0]

            # If tagname contains non-alpha characters, skip
            if not tagname.isalpha():
                continue

        # Opening tag detection
        if safe_line.strip().startswith("<") and not safe_line.strip().startswith("</"):
            if ">" in safe_line:
                tag = safe_line.strip().split(">")[0].replace("<", "").replace("/", "").split(" ")[0]
                if tag:
                    tag_stack.append(tag)

        # Closing tag detection
        if safe_line.strip().startswith("</"):
            tag = safe_line.strip().replace("</", "").replace(">", "").strip()

            # If tag matches the last opened tag, close it
            if tag_stack and tag_stack[-1] == tag:
                tag_stack.pop()
            else:
                # Skip mismatched closing tags
                continue

        fixed_lines.append(line)

    # Auto-close remaining tags (safe)
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


# -------------------------
# !validate
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
# !fixupload
# -------------------------

@bot.command(name="fixupload")
async def fixupload(ctx):
    if not ctx.message.attachments:
        return await ctx.send("Please upload a file with the command.")

    attachment = ctx.message.attachments[0]
    file_bytes = await attachment.read()
    content = file_bytes.decode("utf-8", errors="ignore")

    # JSON
    if content.strip().startswith("{"):
        fixed = fix_json(content)
        filename = "fixed.json"

    # XML
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
# !fixfolder (ZIP repair)
# -------------------------

@bot.command(name="fixfolder")
async def fixfolder(ctx):
    if not ctx.message.attachments:
        return await ctx.send("Please upload a ZIP file with the command.")

    attachment = ctx.message.attachments[0]

    if not attachment.filename.lower().endswith(".zip"):
        return await ctx.send("Please upload a .zip file.")

    temp_dir = tempfile.mkdtemp()

    zip_path = os.path.join(temp_dir, "input.zip")
    output_zip_path = os.path.join(temp_dir, "fixed.zip")

    with open(zip_path, "wb") as f:
        f.write(await attachment.read())

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(temp_dir)

    for root, dirs, files in os.walk(temp_dir):
        for file in files:
            if file.endswith(".zip"):
                continue

            full_path = os.path.join(root, file)

            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            if content.strip().startswith("{"):
                fixed = fix_json(content)
            elif content.strip().startswith("<"):
                fixed = fix_xml(content)
            else:
                fixed = content

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(fixed)

    with zipfile.ZipFile(output_zip_path, "w") as zip_out:
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                if file == "fixed.zip":
                    continue
                full_path = os.path.join(root, file)
                arcname = os.path.relpath(full_path, temp_dir)
                zip_out.write(full_path, arcname)

    await ctx.send(
        content="Here is your fixed ZIP folder:",
        file=discord.File(output_zip_path)
    )


# -------------------------
# Flask runner + bot runner
# -------------------------

def run_flask():
    app.run(host="0.0.0.0", port=10000)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Set DISCORD_BOT_TOKEN env var or hardcode your token.")

    # Keep Render alive
    threading.Thread(target=run_flask).start()

    # Start Discord bot
    bot.run(TOKEN)

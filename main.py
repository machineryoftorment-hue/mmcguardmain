import os
import discord
from discord.ext import commands
from flask import Flask
import threading
import re
import json

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
# !fixai — Trained Aggressive AI Repair + Summary
# -------------------------

@bot.command(name="fixai")
async def fixai(ctx):
    if not ctx.message.attachments:
        return await ctx.send("Please upload a JSON or XML file with the command.")

    attachment = ctx.message.attachments[0]
    raw = (await attachment.read()).decode("utf-8", errors="ignore").strip()

    is_json = raw.startswith("{")
    is_xml = raw.startswith("<")

    if not (is_json or is_xml):
        return await ctx.send("Unknown format. Must start with `{` or `<`.")

    tokens = re.findall(r"[{}[\]<>/]|\".*?\"|\S+", raw)

    summary = {
        "missing_commas": 0,
        "objects_rebuilt": 0,
        "fields_added": 0,
        "tags_closed": 0,
        "tags_rebuilt": 0
    }

    # -------------------------
    # JSON MODEL (patched)
    # -------------------------

    def repair_json(tokens):
        obj_keys = {"name", "pos", "ypr", "scale", "enableCEPersistency", "customString"}
        output = []
        last = ""

        # Insert missing commas between fields
        for t in tokens:
            if last and last not in "{[," and t not in "}],":
                if last not in [":"]:
                    output.append(",")
                    summary["missing_commas"] += 1
            output.append(t)
            last = t

        repaired = "".join(output)

        # Remove trailing commas
        repaired = repaired.replace(",}", "}").replace(",]", "]")

        # Try to parse
        try:
            data = json.loads(repaired)
        except Exception:
            # Soft fallback: keep structure instead of nuking
            summary["objects_rebuilt"] += 1
            data = {"Objects": []}

        if "Objects" not in data or not isinstance(data["Objects"], list):
            data["Objects"] = []
            summary["objects_rebuilt"] += 1

        fixed_objects = []
        for obj in data["Objects"]:
            new = {}
            for k in obj_keys:
                if k not in obj:
                    summary["fields_added"] += 1
                    if k == "pos":
                        new[k] = [0.0, 0.0, 0.0]
                    elif k == "ypr":
                        new[k] = [0.0, 0.0, 0.0]
                    elif k == "scale":
                        new[k] = 1.0
                    elif k == "enableCEPersistency":
                        new[k] = 0
                    elif k == "customString":
                        new[k] = ""
                    elif k == "name":
                        new[k] = "UnknownObject"
                else:
                    new[k] = obj[k]
            fixed_objects.append(new)

        final = json.dumps({"Objects": fixed_objects}, indent=4)
        return final

    # -------------------------
    # XML MODEL
    # -------------------------

    def repair_xml(tokens):
        valid_tags = {
            "spawnabletypes", "type", "damage", "hoarder",
            "cargo", "item", "attachments", "tag"
        }

        output = []
        stack = []

        for t in tokens:
            if t.startswith("<") and not t.startswith("</") and ">" in t:
                tag = t.replace("<", "").replace(">", "").split()[0]
                if tag in valid_tags:
                    stack.append(tag)
                output.append(t)
                continue

            if t.startswith("</"):
                tag = t.replace("</", "").replace(">", "")
                if stack and stack[-1] == tag:
                    stack.pop()
                    output.append(t)
                else:
                    if stack:
                        output.append(f"</{stack[-1]}>")
                        summary["tags_closed"] += 1
                        stack.pop()
                continue

            output.append(t)

        while stack:
            output.append(f"</{stack.pop()}>")
            summary["tags_closed"] += 1

        return "".join(output)

    # -------------------------
    # RUN MODEL
    # -------------------------

    if is_json:
        fixed = repair_json(tokens)
        filename = "fixed_ai.json"
    else:
        fixed = repair_xml(tokens)
        filename = "fixed_ai.xml"

    # -------------------------
    # SAVE FILE
    # -------------------------

    with open(filename, "w", encoding="utf-8") as f:
        f.write(fixed)

    # -------------------------
    # SUMMARY MESSAGE
    # -------------------------

    summary_msg = (
        "🧠 **AI Repair Summary**\n"
        f"- Missing commas fixed: **{summary['missing_commas']}**\n"
        f"- Objects rebuilt: **{summary['objects_rebuilt']}**\n"
        f"- Fields added: **{summary['fields_added']}**\n"
        f"- XML tags auto-closed: **{summary['tags_closed']}**\n"
        f"- XML tag repairs: **{summary['tags_rebuilt']}**\n"
    )

    await ctx.send(summary_msg)

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
# Flask runner + bot runner
# -------------------------

def run_flask():
    app.run(host="0.0.0.0", port=10000)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Set DISCORD_BOT_TOKEN env var or hardcode your token.")

    threading.Thread(target=run_flask).start()
    bot.run(TOKEN)

import os
import discord
from discord.ext import commands
from flask import Flask
import threading

app = Flask(__name__)

@app.route('/')
def home():
    return "MMCGuard is running"


INTENTS = discord.Intents.default()
INTENTS.message_content = True

BOT_PREFIX = "!"
TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # <-- matches your Render variable name

# Swear → funny replacement
PROFANITY_MAP = {
    "fuck": "fork",
    "shit": "poopoo",
    "bitch": "goose",
    "bastard": "potato",
    "ass": "butt",
    "dick": "noodle",
    "cunt": "sea cucumber",
    "motherfucker": "motherhugger",
    "bullshit": "bullsneeze",
    "crap": "crumbs",
}

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=INTENTS)


def replace_profanity(text: str) -> str:
    words = text.split(" ")
    new_words = []

    for w in words:
        base = w.lower().strip(".,!?;:()[]{}\"'")
        replacement = PROFANITY_MAP.get(base)
        if replacement:
            new_words.append(w.replace(base, replacement))
        else:
            new_words.append(w)

    return " ".join(new_words)


async def get_or_create_webhook(channel: discord.TextChannel) -> discord.Webhook:
    hooks = await channel.webhooks()
    for hook in hooks:
        if hook.name == "MMCGuardFilter":
            return hook

    return await channel.create_webhook(name="MMCGuardFilter")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Profanity → Funny webhook replacer is online.")


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


@bot.command(name="filterinfo")
async def filter_info(ctx: commands.Context):
    await ctx.send(
        "I replace spicy words with dumb funny ones.\n"
        "Current map:\n"
        + "\n".join([f"- {bad} → {funny}" for bad, funny in PROFANITY_MAP.items()])
    )


def run_flask():
    app.run(host="0.0.0.0", port=10000)

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Set DISCORD_BOT_TOKEN env var or hardcode your token.")

    # Start Flask server in background
    threading.Thread(target=run_flask).start()

    # Start Discord bot
    bot.run(TOKEN)

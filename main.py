import os
import discord
from discord.ext import commands

INTENTS = discord.Intents.default()
INTENTS.message_content = True

BOT_PREFIX = "!"
TOKEN = os.getenv("DISCORD_TOKEN")

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
    # Try to find an existing webhook
    hooks = await channel.webhooks()
    for hook in hooks:
        if hook.name == "MMCGuardFilter":
            return hook

    # Otherwise create one
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

    # If nothing changed, do nothing
    if cleaned == original:
        await bot.process_commands(message)
        return

    # Delete original message
    try:
        await message.delete()
    except discord.Forbidden:
        # If we cannot delete, fallback to normal send
        await message.channel.send(
            f"🧼 **Cleaned message from {message.author.mention}:**\n{cleaned}"
        )
        return

    # Send cleaned message as webhook
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


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Set DISCORD_TOKEN env var or hardcode your token.")
    bot.run(TOKEN)

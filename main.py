import discord
from discord.ext import commands
import os
import asyncio
import random
from dotenv import load_dotenv

# -----------------------------
# KEEP-ALIVE WEB SERVER (Render)
# -----------------------------
from flask import Flask
import threading

app = Flask(__name__)

@app.route('/')
def home():
    return "MMC Guard is alive!"

def run_keepalive():
    app.run(host='0.0.0.0', port=8080)

threading.Thread(target=run_keepalive).start()
# -----------------------------

# -----------------------------
# DATABASE
# -----------------------------
import database
database.init_db()

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

CONNECTION_CHANNEL_ID = int(os.getenv("CONNECTION_CHANNEL_ID"))
EXPLOSIVE_CHANNEL_ID = int(os.getenv("EXPLOSIVE_CHANNEL_ID"))
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID"))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# DayZ++ explosive whitelist
DAYZPP_EXPLOSIVES = {
    "plastic explosive",
    "improvised explosive",
    "claymore",
    "land mine",
    "ied urban",
    "ied land",
    "m67",
    "rgd5",
    "flashbang",
    "grenade"
}

def is_explosive(item: str) -> bool:
    item = item.lower().strip()
    return any(explosive in item for explosive in DAYZPP_EXPLOSIVES)

# -----------------------------------------
# MULTI‑USER GIF‑ON‑PING SYSTEM
# -----------------------------------------
PING_GIFS = {
    962113157105586193: [
        "https://tenor.com/view/but-why-tho-huh-whatt-gif-22573689"
    ],
    1384939661700497629: [
        "https://tenor.com/view/peek-hiding-gif-17577724"
    ]
}

# Handle player join/leave
async def handle_player_event(username: str, event_type: str):
    channel = bot.get_channel(CONNECTION_CHANNEL_ID)
    if not channel:
        return

    if event_type == "connected":
        database.set_player_status(username, "online")
        await channel.send(f"🟢 **{username}** connected to the server.")
    elif event_type == "disconnected":
        database.set_player_status(username, "offline")
        await channel.send(f"🔴 **{username}** disconnected from the server.")

# Handle explosive placement
async def handle_explosive_placement(username: str, item: str):
    channel = bot.get_channel(EXPLOSIVE_CHANNEL_ID)
    if not channel:
        return

    database.log_explosive(username, item)

    admin_role = discord.utils.get(channel.guild.roles, id=ADMIN_ROLE_ID)
    alert_msg = f"💣 **{username}** placed an explosive: `{item}`"

    if admin_role:
        await channel.send(f"{admin_role.mention} {alert_msg}")
    else:
        await channel.send(alert_msg)

# ---------------------------------------------------
# MAIN MESSAGE HANDLER (includes GIF-on-ping feature)
# ---------------------------------------------------
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # GIF-on-ping
    for user in message.mentions:
        if message.reference is not None:
            continue

        if user.id in PING_GIFS:
            gif = random.choice(PING_GIFS[user.id])
            await message.channel.send(gif)

    # DayZ++ Webhook Parsing
    if message.channel.id not in (CONNECTION_CHANNEL_ID, EXPLOSIVE_CHANNEL_ID):
        await bot.process_commands(message)
        return

    log = message.content.lower()

    if "connected" in log:
        username = log.split("connected")[0].strip()
        await handle_player_event(username, "connected")
        return

    if "disconnected" in log:
        username = log.split("disconnected")[0].strip()
        await handle_player_event(username, "disconnected")
        return

    if "placed" in log:
        username = log.split("placed")[0].strip()
        item = log.split("placed")[-1].strip().replace(".", "")
        if is_explosive(item):
            await handle_explosive_placement(username, item)
        return

    await bot.process_commands(message)

# -----------------------------
# SLASH COMMANDS
# -----------------------------

@bot.tree.command(name="forcesync", description="Force refresh slash commands")
async def forcesync(interaction: discord.Interaction):
    await bot.tree.sync()
    await interaction.response.send_message("🔥 Slash commands fully resynced.", ephemeral=True)

@bot.tree.command(name="ping", description="Check if MMC Guard is online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! MMC Guard is active.", ephemeral=True)

@bot.tree.command(name="order", description="View all orders for a username")
async def order(interaction: discord.Interaction, username: str):
    orders = database.get_orders(username.lower())

    if not orders:
        await interaction.response.send_message(
            f"📭 No orders found for **{username}**.",
            ephemeral=True
        )
        return

    msg = f"📦 **Orders for {username}:**\n\n"
    for i, item in enumerate(orders, start=1):
        msg += f"{i}. {item}\n"

    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="addorder", description="Add an order from a message ID")
async def addorder(interaction: discord.Interaction, message_id: str, username: str):
    try:
        msg_id_int = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)
        return

    msg = None
    for channel in interaction.guild.text_channels:
        try:
            msg = await channel.fetch_message(msg_id_int)
            break
        except:
            continue

    if msg is None:
        await interaction.response.send_message("❌ Message not found.", ephemeral=True)
        return

    content = msg.content.strip()
    if not content:
        await interaction.response.send_message("❌ Message has no text content.", ephemeral=True)
        return

    database.add_order(username.lower(), content)

    await interaction.response.send_message(
        f"✅ Added order for **{username}**:\n`{content}`",
        ephemeral=True
    )

@bot.tree.command(name="orders", description="View all stored orders")
async def orders(interaction: discord.Interaction):
    rows = database.get_all_orders()

    if not rows:
        await interaction.response.send_message("📭 No orders stored.", ephemeral=True)
        return

    msg = "📦 **All Orders:**\n\n"
    for row in rows:
        msg += f"**{row['username']}**: {row['content']}\n"

    await interaction.response.send_message(msg, ephemeral=True)

# Sync slash commands
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"MMC Guard online as {bot.user}")

# Auto‑restart on crash
async def start_bot():
    while True:
        try:
            await bot.start(TOKEN)
        except Exception as e:
            print(f"Bot crashed with error: {e}")
            print("Restarting in 5 seconds...")
            await asyncio.sleep(5)
        except KeyboardInterrupt:
            await bot.close()
            break

asyncio.run(start_bot())

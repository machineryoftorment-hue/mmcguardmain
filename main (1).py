import discord
from discord.ext import commands
import json
import os
import asyncio
import time
import random
from dotenv import load_dotenv

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

# JSON storage
PLAYER_FILE = "players.json"
ORDER_FILE = "orders.json"

def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[DEBUG] Failed to load JSON {path}: {e}")
        return {}

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        print(f"[DEBUG] Saved JSON to {path}")
    except Exception as e:
        print(f"[DEBUG] Failed to save JSON {path}: {e}")

player_data = load_json(PLAYER_FILE)
order_data = load_json(ORDER_FILE)

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
        player_data[username] = {"status": "online"}
        save_json(PLAYER_FILE, player_data)
        await channel.send(f"🟢 **{username}** connected to the server.")
    elif event_type == "disconnected":
        if username in player_data:
            player_data[username]["status"] = "offline"
            save_json(PLAYER_FILE, player_data)
        await channel.send(f"🔴 **{username}** disconnected from the server.")

# Handle explosive placement
async def handle_explosive_placement(username: str, item: str):
    channel = bot.get_channel(EXPLOSIVE_CHANNEL_ID)
    if not channel:
        return

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
    # Ignore bot messages
    if message.author == bot.user:
        return

    # -----------------------------------------
    # GIF‑ON‑PING SYSTEM (NO GIFS IN REPLIES)
    # -----------------------------------------
    for user in message.mentions:

        # NEW RULE: Ignore GIF triggers if the message is a reply
        if message.reference is not None:
            continue

        if user.id in PING_GIFS:
            gif_list = PING_GIFS[user.id]
            gif = random.choice(gif_list)

            try:
                await message.channel.send(gif)
            except Exception as e:
                print(f"[DEBUG] Failed to send GIF for {user.id}: {e}")

    # -----------------------------------------
    # DayZ++ Webhook Parsing
    # -----------------------------------------
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
    user_key = username.lower()

    if user_key not in order_data or not order_data[user_key]:
        await interaction.response.send_message(
            f"📭 No orders found for **{username}**.",
            ephemeral=True
        )
        return

    msg = f"📦 **Orders for {username}:**\n\n"
    for i, item in enumerate(order_data[user_key], start=1):
        msg += f"{i}. {item}\n"

    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="addorder", description="Add an order from a message ID")
async def addorder(interaction: discord.Interaction, message_id: str, username: str):
    user_key = username.lower()

    try:
        msg_id_int = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)
        return

    print(f"[DEBUG] Searching for message ID {msg_id_int}")

    msg = None
    for channel in interaction.guild.text_channels:
        try:
            print(f"[DEBUG] Checking channel: {channel.name}")
            msg = await channel.fetch_message(msg_id_int)
            print(f"[DEBUG] FOUND message in {channel.name}")
            break
        except discord.Forbidden:
            print(f"[DEBUG] Forbidden in {channel.name}")
        except discord.NotFound:
            print(f"[DEBUG] Not found in {channel.name}")
        except Exception as e:
            print(f"[DEBUG] Error in {channel.name}: {e}")

    if msg is None:
        await interaction.response.send_message("❌ Message not found in any channel.", ephemeral=True)
        return

    content = msg.content.strip()
    print(f"[DEBUG] Message content: '{content}'")

    if not content:
        await interaction.response.send_message("❌ Message has no text content.", ephemeral=True)
        return

    await interaction.response.send_message(
        f"✅ Added order for **{username}**:\n`{content}`",
        ephemeral=True
    )

    try:
        if user_key not in order_data:
            order_data[user_key] = []
        order_data[user_key].append(content)
        save_json(ORDER_FILE, order_data)
        print("[DEBUG] Order saved successfully.")
    except Exception as e:
        print(f"[DEBUG] JSON SAVE ERROR: {e}")

@bot.tree.command(name="orders", description="View all stored orders")
async def orders(interaction: discord.Interaction):
    if not order_data:
        await interaction.response.send_message("📭 No orders stored.", ephemeral=True)
        return

    msg = "📦 **All Orders:**\n\n"
    for user, items in order_data.items():
        msg += f"**{user}**:\n"
        for item in items:
            msg += f" • {item}\n"
        msg += "\n"

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

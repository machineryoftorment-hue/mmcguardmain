import os
import json
import threading
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from flask import Flask, request, jsonify, render_template, redirect, url_for
import discord
from discord.ext import commands
from discord import app_commands

import psycopg2
import psycopg2.extras
import requests

# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mmcguard")

# =========================
# CONFIG
# =========================

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
NITRADO_TOKEN = os.environ.get("NITRADO_TOKEN", None)

GUILD_ID = 1404279040893911103
ADMIN_ROLE_ID = 1419520911471542413

DEFAULT_NITRADO_SERVER_ID = int(os.environ.get("NITRADO_SERVER_ID", "17649304"))
NITRADO_API_BASE = "https://api-us.nitrado.net"

bot_start_time = datetime.utcnow()

# =========================
# DISCORD BOT SETUP
# =========================

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# =========================
# FLASK APP
# =========================

app = Flask(__name__, static_folder="static", static_url_path="/static")

# =========================
# POSTGRESQL DATABASE
# =========================

def get_db():
    url = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


@app.route("/initdb")
def initdb():
    conn = get_db()
    cur = conn.cursor()

    # Zones table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS zones (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            action TEXT NOT NULL,
            points JSON NOT NULL
        );
    """)

    # Bot settings table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            id INTEGER PRIMARY KEY,
            kill_feed_channel BIGINT,
            explosive_feed_channel BIGINT,
            connection_feed_channel BIGINT,
            zone_alert_channel BIGINT,
            general_log_channel BIGINT,
            admin_alert_channel BIGINT
        );
    """)

    # Nitrado settings table (server_id only; token is env-based)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nitrado_settings (
            id INTEGER PRIMARY KEY,
            api_token TEXT,
            server_id BIGINT
        );
    """)

    # Ensure bot_settings row exists
    cur.execute("SELECT id FROM bot_settings WHERE id = 1")
    if cur.fetchone() is None:
        cur.execute("""
            INSERT INTO bot_settings (
                id,
                kill_feed_channel,
                explosive_feed_channel,
                connection_feed_channel,
                zone_alert_channel,
                general_log_channel,
                admin_alert_channel
            ) VALUES (1, 0, 0, 0, 0, 0, 0)
        """)

    # Ensure nitrado_settings row exists
    cur.execute("SELECT id FROM nitrado_settings WHERE id = 1")
    if cur.fetchone() is None:
        cur.execute("""
            INSERT INTO nitrado_settings (id, api_token, server_id)
            VALUES (1, NULL, %s)
        """, (DEFAULT_NITRADO_SERVER_ID,))

    conn.commit()
    cur.close()
    conn.close()

    return "Database initialized!"


# =========================
# BOT SETTINGS HELPERS
# =========================

def get_bot_settings() -> Dict[str, int]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bot_settings WHERE id = 1")
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return {
            "kill_feed_channel": 0,
            "explosive_feed_channel": 0,
            "connection_feed_channel": 0,
            "zone_alert_channel": 0,
            "general_log_channel": 0,
            "admin_alert_channel": 0,
        }

    return {
        "kill_feed_channel": row["kill_feed_channel"] or 0,
        "explosive_feed_channel": row["explosive_feed_channel"] or 0,
        "connection_feed_channel": row["connection_feed_channel"] or 0,
        "zone_alert_channel": row["zone_alert_channel"] or 0,
        "general_log_channel": row["general_log_channel"] or 0,
        "admin_alert_channel": row["admin_alert_channel"] or 0,
    }


def update_bot_settings(data: Dict[str, Any]) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE bot_settings
        SET
            kill_feed_channel = %s,
            explosive_feed_channel = %s,
            connection_feed_channel = %s,
            zone_alert_channel = %s,
            general_log_channel = %s,
            admin_alert_channel = %s
        WHERE id = 1
    """, (
        int(data.get("kill_feed_channel", 0) or 0),
        int(data.get("explosive_feed_channel", 0) or 0),
        int(data.get("connection_feed_channel", 0) or 0),
        int(data.get("zone_alert_channel", 0) or 0),
        int(data.get("general_log_channel", 0) or 0),
        int(data.get("admin_alert_channel", 0) or 0),
    ))
    conn.commit()
    cur.close()
    conn.close()


# =========================
# NITRADO SETTINGS HELPERS (SERVER ID)
# =========================

def get_nitrado_server_id() -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT server_id FROM nitrado_settings WHERE id = 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row.get("server_id"):
        return DEFAULT_NITRADO_SERVER_ID
    return int(row["server_id"])


def set_nitrado_server_id(server_id: int) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE nitrado_settings
        SET server_id = %s
        WHERE id = 1
    """, (server_id,))
    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Nitrado server ID updated to {server_id}")


# =========================
# ZONE HELPERS
# =========================

def get_all_zones():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM zones ORDER BY id ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def add_zone(name: str, action: str, points_json: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO zones (name, action, points) VALUES (%s, %s, %s)",
        (name, action, points_json)
    )
    conn.commit()
    cur.close()
    conn.close()


def delete_zone(zone_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM zones WHERE id = %s", (zone_id,))
    conn.commit()
    cur.close()
    conn.close()


# =========================
# DISCORD CHANNEL ACCESS
# =========================

def get_channel_by_id(channel_id: int) -> Optional[discord.TextChannel]:
    if channel_id == 0:
        return None
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return None
    return guild.get_channel(channel_id)


def reload_channel_settings() -> Dict[str, Optional[discord.TextChannel]]:
    settings = get_bot_settings()
    return {
        "kill_feed": get_channel_by_id(settings["kill_feed_channel"]),
        "explosive_feed": get_channel_by_id(settings["explosive_feed_channel"]),
        "connection_feed": get_channel_by_id(settings["connection_feed_channel"]),
        "zone_alert": get_channel_by_id(settings["zone_alert_channel"]),
        "general_log": get_channel_by_id(settings["general_log_channel"]),
        "admin_alert": get_channel_by_id(settings["admin_alert_channel"]),
    }


CHANNELS_CACHE: Dict[str, Optional[discord.TextChannel]] = {}

# =========================
# NITRADO API WRAPPER (ENV TOKEN ONLY)
# =========================

class NitradoAPI:
    def __init__(self):
        self.base_url = NITRADO_API_BASE

    @property
    def server_id(self) -> int:
        return get_nitrado_server_id()

    @property
    def token(self) -> Optional[str]:
        return NITRADO_TOKEN  # ENV ONLY

    @property
    def headers(self) -> Dict[str, str]:
        if not self.token:
            return {}
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}/services/{self.server_id}{path}"

    def _post(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.token:
            logger.warning("NitradoAPI._post called with no token set")
            return None
        try:
            logger.info(f"POST {path} payload={payload}")
            resp = requests.post(self._url(path), headers=self.headers, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Nitrado POST {path} failed: {resp.status_code} {resp.text}")
                return None
            return resp.json()
        except Exception as e:
            logger.exception(f"Nitrado POST {path} exception: {e}")
            return None

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        if not self.token:
            logger.warning("NitradoAPI._get called with no token set")
            return None
        try:
            logger.info(f"GET {path}")
            resp = requests.get(self._url(path), headers=self.headers, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Nitrado GET {path} failed: {resp.status_code} {resp.text}")
                return None
            return resp.json()
        except Exception as e:
            logger.exception(f"Nitrado GET {path} exception: {e}")
            return None

    # ---- Server info / players ----

    def get_server_info(self) -> Optional[Dict[str, Any]]:
        return self._get("/gameservers")

    def get_online_players(self) -> Optional[List[Dict[str, Any]]]:
        data = self._get("/gameservers/games/players")
        if not data:
            return None
        players = data.get("data", {}).get("players", [])
        return players

    # ---- Server control ----

    def restart_server(self) -> bool:
        return self._post("/gameservers/games/commands/server/restart", {}) is not None

    def stop_server(self) -> bool:
        return self._post("/gameservers/games/commands/server/stop", {}) is not None

    def start_server(self) -> bool:
        return self._post("/gameservers/games/commands/server/start", {}) is not None

    # ---- Player management ----

    def ban_player(self, name: str) -> bool:
        return self._post("/gameservers/games/commands/players/ban", {"player": name}) is not None

    def unban_player(self, name: str) -> bool:
        return self._post("/gameservers/games/commands/players/unban", {"player": name}) is not None

    def kick_player(self, name: str) -> bool:
        return self._post("/gameservers/games/commands/players/kick", {"player": name}) is not None

    def whitelist_add(self, name: str) -> bool:
        return self._post("/gameservers/games/commands/players/whitelist/add", {"player": name}) is not None

    def whitelist_remove(self, name: str) -> bool:
        return self._post("/gameservers/games/commands/players/whitelist/remove", {"player": name}) is not None


nitrado_api = NitradoAPI()

# =========================
# DISCORD EVENTS & COMMANDS
# =========================

WIPED_COMMANDS = False  # one-time wipe flag


def user_is_admin(member: discord.Member) -> bool:
    return any(role.id == ADMIN_ROLE_ID for role in member.roles)


@bot.event
async def on_ready():
    global CHANNELS_CACHE, WIPED_COMMANDS

    CHANNELS_CACHE = reload_channel_settings()
    guild = bot.get_guild(GUILD_ID)

    if guild:
        if not WIPED_COMMANDS:
            try:
                logger.info("Wiping old slash commands from guild...")
                await tree.sync(guild=guild)
                cmds = await tree.fetch_commands(guild=guild)
                for cmd in cmds:
                    await cmd.delete()
                logger.info("Old commands deleted. Resyncing new commands...")
                await tree.sync(guild=guild)
                WIPED_COMMANDS = True
                logger.info("New commands synced.")
            except Exception as e:
                logger.exception(f"Failed to wipe commands: {e}")
        else:
            try:
                await tree.sync(guild=guild)
                logger.info(f"Synced commands to guild {guild.name} ({guild.id})")
            except Exception as e:
                logger.exception(f"Failed to sync commands: {e}")

    logger.info(f"Logged in as {bot.user} (guild {GUILD_ID})")


# ---- Token check command ----

@tree.command(name="checktoken", description="Check if the Nitrado API token is set and valid")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def checktoken(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    if not nitrado_api.token:
        await interaction.response.send_message("❌ No Nitrado token is set in environment variables (`NITRADO_TOKEN`).")
        return

    info = nitrado_api.get_server_info()
    if info is None:
        await interaction.response.send_message("❌ Token is set, but server info could not be retrieved. Check token, server ID, and Nitrado scopes.")
    else:
        await interaction.response.send_message("✅ Token is set and Nitrado API responded successfully.")


# ---- Dummy activate command (env mode) ----

@tree.command(name="activate", description="(Env mode) Inform users that token is stored in environment variables")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def activate(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🔐 This bot now uses **environment variables only** for the Nitrado token.\n"
        "Set `NITRADO_TOKEN` (and optionally `NITRADO_SERVER_ID`) in your Render environment settings.",
        ephemeral=True
    )


# ---- Set server ID ----

@tree.command(name="setserverid", description="Set the Nitrado server ID used by this bot")
@app_commands.describe(server_id="Nitrado service ID (numeric)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def setserverid(interaction: discord.Interaction, server_id: int):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    set_nitrado_server_id(server_id)
    await interaction.response.send_message(f"✅ Nitrado server ID has been updated to `{server_id}`.")


# ---- Ping server / API ----

@tree.command(name="pingserver", description="Ping Nitrado API and check basic connectivity")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def pingserver(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    if not nitrado_api.token:
        await interaction.response.send_message("❌ No Nitrado token set. Configure `NITRADO_TOKEN` in environment.", ephemeral=True)
        return

    info = nitrado_api.get_server_info()
    if info is None:
        await interaction.response.send_message("❌ Nitrado API did not respond successfully. Check token, server ID, and scopes.")
    else:
        await interaction.response.send_message("✅ Nitrado API is reachable and responded successfully.")


# ---- Status command ----

@tree.command(name="status", description="Show detailed Nitrado DayZ server status")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def status(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    info = nitrado_api.get_server_info()
    players = nitrado_api.get_online_players()

    if info is None:
        await interaction.response.send_message("❌ Could not retrieve server info. Check token and server ID.")
        return

    data = info.get("data", {})
    server = data.get("gameserver", {})
    name = server.get("name", "Unknown")
    status_str = server.get("status", "Unknown")
    slots = server.get("slots", "Unknown")
    region = server.get("location", "Unknown")
    ip = server.get("ip", "Unknown")
    port = server.get("port", "Unknown")

    player_count = len(players) if players else 0

    bot_uptime = datetime.utcnow() - bot_start_time
    uptime_str = str(timedelta(seconds=int(bot_uptime.total_seconds())))

    msg = (
        f"**Server Status**\n"
        f"Name: `{name}`\n"
        f"Status: `{status_str}`\n"
        f"Slots: `{slots}`\n"
        f"Region: `{region}`\n"
        f"IP: `{ip}`\n"
        f"Port: `{port}`\n"
        f"Server ID: `{nitrado_api.server_id}`\n"
        f"Online Players: `{player_count}`\n"
        f"Bot Uptime: `{uptime_str}`\n"
    )
    await interaction.response.send_message(msg)


# ---- Server info ----

@tree.command(name="serverinfo", description="Show basic Nitrado DayZ server info")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def serverinfo(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    info = nitrado_api.get_server_info()
    if info is None:
        await interaction.response.send_message("❌ Could not retrieve server info. Check token and server ID.")
        return

    data = info.get("data", {})
    server = data.get("gameserver", {})
    name = server.get("name", "Unknown")
    status_str = server.get("status", "Unknown")
    slots = server.get("slots", "Unknown")
    region = server.get("location", "Unknown")

    msg = (
        f"**Server Info**\n"
        f"Name: `{name}`\n"
        f"Status: `{status_str}`\n"
        f"Slots: `{slots}`\n"
        f"Region: `{region}`\n"
        f"Server ID: `{nitrado_api.server_id}`"
    )
    await interaction.response.send_message(msg)


# ---- Online players ----

@tree.command(name="online", description="List online players on the DayZ server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def online(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    players = nitrado_api.get_online_players()
    if players is None:
        await interaction.response.send_message("❌ Could not retrieve online players. Check token and server.")
        return

    if not players:
        await interaction.response.send_message("No players are currently online.")
        return

    lines = []
    for p in players:
        name = p.get("name", "Unknown")
        connected = p.get("connected_since", "Unknown")
        lines.append(f"- `{name}` (since: `{connected}`)")

    msg = "**Online Players:**\n" + "\n".join(lines)
    await interaction.response.send_message(msg)


# ---- Server control ----

@tree.command(name="restartserver", description="Restart the DayZ server (Nitrado)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def restartserver(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    ok = nitrado_api.restart_server()
    if ok:
        await interaction.response.send_message("🔄 Server restart command sent to Nitrado.")
    else:
        await interaction.response.send_message("❌ Failed to send restart command. Check token and server.")


@tree.command(name="stopserver", description="Stop the DayZ server (Nitrado)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def stopserver(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    ok = nitrado_api.stop_server()
    if ok:
        await interaction.response.send_message("🛑 Server stop command sent to Nitrado.")
    else:
        await interaction.response.send_message("❌ Failed to send stop command. Check token and server.")


@tree.command(name="startserver", description="Start the DayZ server (Nitrado)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def startserver(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    ok = nitrado_api.start_server()
    if ok:
        await interaction.response.send_message("▶️ Server start command sent to Nitrado.")
    else:
        await interaction.response.send_message("❌ Failed to send start command. Check token and server.")


# ---- Soft / hard restart ----

@tree.command(name="restartsoft", description="Soft restart the DayZ server (Nitrado)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def restartsoft(interaction: discord.Interaction):
    await restartserver.callback(interaction)  # reuse logic


@tree.command(name="restarthard", description="Hard restart (stop, wait, start) the DayZ server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def restarthard(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    await interaction.response.send_message("🧨 Hard restart initiated: stopping server, waiting 30 seconds, then starting.", ephemeral=True)

    ok_stop = nitrado_api.stop_server()
    if not ok_stop:
        await interaction.followup.send("❌ Failed to stop server. Aborting hard restart.")
        return

    time.sleep(30)

    ok_start = nitrado_api.start_server()
    if not ok_start:
        await interaction.followup.send("❌ Failed to start server after stop. Manual intervention may be required.")
        return

    await interaction.followup.send("✅ Hard restart completed: server stopped and started again.")


# ---- Player management ----

@tree.command(name="ban", description="Ban a player by name from the DayZ server")
@app_commands.describe(player="Player name to ban")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ban(interaction: discord.Interaction, player: str):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    ok = nitrado_api.ban_player(player)
    if ok:
        await interaction.response.send_message(f"🚫 Player `{player}` has been banned.")
    else:
        await interaction.response.send_message(f"❌ Failed to ban `{player}`. Check token and server.")


@tree.command(name="unban", description="Unban a player by name from the DayZ server")
@app_commands.describe(player="Player name to unban")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def unban(interaction: discord.Interaction, player: str):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    ok = nitrado_api.unban_player(player)
    if ok:
        await interaction.response.send_message(f"✅ Player `{player}` has been unbanned.")
    else:
        await interaction.response.send_message(f"❌ Failed to unban `{player}`. Check token and server.")


@tree.command(name="kick", description="Kick a player by name from the DayZ server")
@app_commands.describe(player="Player name to kick")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def kick(interaction: discord.Interaction, player: str):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    ok = nitrado_api.kick_player(player)
    if ok:
        await interaction.response.send_message(f"👢 Player `{player}` has been kicked.")
    else:
        await interaction.response.send_message(f"❌ Failed to kick `{player}`. Check token and server.")


@tree.command(name="whitelistadd", description="Add a player to the whitelist by name")
@app_commands.describe(player="Player name to whitelist")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def whitelistadd(interaction: discord.Interaction, player: str):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    ok = nitrado_api.whitelist_add(player)
    if ok:
        await interaction.response.send_message(f"✅ Player `{player}` has been added to the whitelist.")
    else:
        await interaction.response.send_message(f"❌ Failed to whitelist `{player}`. Check token and server.")


@tree.command(name="whitelistremove", description="Remove a player from the whitelist by name")
@app_commands.describe(player="Player name to remove from whitelist")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def whitelistremove(interaction: discord.Interaction, player: str):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    ok = nitrado_api.whitelist_remove(player)
    if ok:
        await interaction.response.send_message(f"✅ Player `{player}` has been removed from the whitelist.")
    else:
        await interaction.response.send_message(f"❌ Failed to remove `{player}` from whitelist. Check token and server.")


# ---- Optional manual wipe command ----

@tree.command(name="wipecommands", description="Admin-only: wipe all slash commands for this bot in this guild")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def wipecommands(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    try:
        cmds = await tree.fetch_commands(guild=guild)
        for cmd in cmds:
            await cmd.delete()
        await tree.sync(guild=guild)
        await interaction.response.send_message("🧹 All slash commands for this bot have been wiped from this guild.")
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to wipe commands: {e}")


# =========================
# FLASK ROUTES
# =========================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard/zones", methods=["GET", "POST"])
def zones_dashboard():
    if request.method == "POST":
        name = request.form.get("zone_name", "").strip()
        action = request.form.get("zone_action", "log").strip()
        points_json = request.form.get("points_json", "[]").strip()

        if name and points_json:
            add_zone(name, action, points_json)

        return redirect(url_for("zones_dashboard"))

    zones = get_all_zones()
    return render_template("zones.html", zones=zones)


@app.route("/dashboard/zones/delete/<int:zone_id>", methods=["POST"])
def delete_zone_route(zone_id: int):
    delete_zone(zone_id)
    return redirect(url_for("zones_dashboard"))


@app.route("/dashboard/discord-settings", methods=["GET", "POST"])
def discord_settings():
    global CHANNELS_CACHE

    if request.method == "POST":
        data = {
            "kill_feed_channel": request.form.get("kill_feed_channel", "0"),
            "explosive_feed_channel": request.form.get("explosive_feed_channel", "0"),
            "connection_feed_channel": request.form.get("connection_feed_channel", "0"),
            "zone_alert_channel": request.form.get("zone_alert_channel", "0"),
            "general_log_channel": request.form.get("general_log_channel", "0"),
            "admin_alert_channel": request.form.get("admin_alert_channel", "0"),
        }
        update_bot_settings(data)
        CHANNELS_CACHE = reload_channel_settings()
        return redirect(url_for("discord_settings"))

    settings = get_bot_settings()
    guild = bot.get_guild(GUILD_ID)
    channels = [ch for ch in guild.channels if isinstance(ch, discord.TextChannel)] if guild else []

    return render_template("discord_settings.html", settings=settings, channels=channels)


# ---- Dashboard status / API endpoints ----

@app.route("/dashboard/status")
def dashboard_status():
    info = nitrado_api.get_server_info()
    players = nitrado_api.get_online_players()

    server_data = {}
    if info:
        data = info.get("data", {})
        server = data.get("gameserver", {})
        server_data = {
            "name": server.get("name", "Unknown"),
            "status": server.get("status", "Unknown"),
            "slots": server.get("slots", "Unknown"),
            "region": server.get("location", "Unknown"),
            "ip": server.get("ip", "Unknown"),
            "port": server.get("port", "Unknown"),
            "server_id": nitrado_api.server_id,
        }

    player_list = []
    if players:
        for p in players:
            player_list.append({
                "name": p.get("name", "Unknown"),
                "connected_since": p.get("connected_since", "Unknown"),
            })

    bot_uptime = datetime.utcnow() - bot_start_time
    uptime_str = str(timedelta(seconds=int(bot_uptime.total_seconds())))

    return render_template(
        "status.html",
        server=server_data,
        players=player_list,
        bot_uptime=uptime_str,
    )


@app.route("/api/serverinfo", methods=["GET"])
def api_serverinfo():
    info = nitrado_api.get_server_info()
    if not info:
        return jsonify({"error": "Could not retrieve server info"}), 500
    return jsonify(info)


@app.route("/api/online", methods=["GET"])
def api_online():
    players = nitrado_api.get_online_players()
    if players is None:
        return jsonify({"error": "Could not retrieve online players"}), 500
    return jsonify(players)


@app.route("/api/status", methods=["GET"])
def api_status():
    info = nitrado_api.get_server_info()
    players = nitrado_api.get_online_players()

    if info is None:
        return jsonify({"error": "Could not retrieve server info"}), 500

    data = info.get("data", {})
    server = data.get("gameserver", {})
    player_count = len(players) if players else 0

    bot_uptime = datetime.utcnow() - bot_start_time
    uptime_str = str(timedelta(seconds=int(bot_uptime.total_seconds())))

    return jsonify({
        "server": {
            "name": server.get("name", "Unknown"),
            "status": server.get("status", "Unknown"),
            "slots": server.get("slots", "Unknown"),
            "region": server.get("location", "Unknown"),
            "ip": server.get("ip", "Unknown"),
            "port": server.get("port", "Unknown"),
            "server_id": nitrado_api.server_id,
        },
        "players": players or [],
        "player_count": player_count,
        "bot_uptime": uptime_str,
    })


@app.route("/api/zones", methods=["GET"])
def api_get_zones():
    zones = get_all_zones()
    return jsonify([
        {
            "id": z["id"],
            "name": z["name"],
            "action": z["action"],
            "points": json.loads(z["points"]),
        }
        for z in zones
    ])


# =========================
# RUN BOT + FLASK TOGETHER
# =========================

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


def run_bot():
    bot.run(TOKEN)


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    run_bot()

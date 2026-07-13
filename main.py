import os
import json
import threading
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List

from flask import Flask, render_template
import discord
from discord.ext import commands
from discord import app_commands

import psycopg2
import psycopg2.extras
import requests

from ftplib import FTP
from io import BytesIO

# ---------------------------------------------------------
# LOGGING
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mmcguard")

# ---------------------------------------------------------
# ENVIRONMENT VARIABLES
# ---------------------------------------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
NITRADO_TOKEN = os.getenv("NITRADO_TOKEN", None)
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1404279040893911103"))
ADMIN_ROLE_ID = int(os.getenv("DISCORD_ADMIN_ROLE_ID", "1419520911471542413"))
DEFAULT_NITRADO_SERVER_ID = int(os.getenv("NITRADO_SERVER_ID", "17649304"))
NITRADO_API_BASE = "https://api.nitrado.net"

FTP_HOST = os.getenv("FTP_HOST", "usmi121.gamedata.io")
FTP_PORT = int(os.getenv("FTP_PORT", "21"))
FTP_USER = os.getenv("FTP_USER", "ni9352260_806")
FTP_PASS = os.getenv("FTP_PASS", "")
FTP_BANS_PATH = os.getenv("FTP_BANS_PATH", "/dayzps/config/bans.txt")
FTP_WHITELIST_PATH = os.getenv("FTP_WHITELIST_PATH", "/dayzps/config/whitelist.txt")

# ---------------------------------------------------------
# JSON SETTINGS (ONLY FOR LOCATION FEED)
# ---------------------------------------------------------
SETTINGS_FILE = "settings.json"

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {"location_feed_channel": 0}
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)

# ---------------------------------------------------------
# DISCORD BOT SETUP
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Flask dashboard
app = Flask(__name__, static_folder="static", static_url_path="/static")

# ---------------------------------------------------------
# DATABASE FUNCTIONS
# ---------------------------------------------------------
def get_db():
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        cursor_factory=psycopg2.extras.RealDictCursor
    )

@app.route("/initdb")
def initdb():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS zones (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            action TEXT NOT NULL,
            points JSON NOT NULL
        );
    """)

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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS nitrado_settings_v2 (
            id INTEGER PRIMARY KEY,
            api_token TEXT,
            server_id BIGINT
        );
    """)

    cur.execute("SELECT id FROM bot_settings WHERE id = 1")
    if cur.fetchone() is None:
        cur.execute("INSERT INTO bot_settings VALUES (1,0,0,0,0,0,0)")

    cur.execute("SELECT id FROM nitrado_settings_v2 WHERE id = 1")
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO nitrado_settings_v2 VALUES (1,NULL,%s)",
            (DEFAULT_NITRADO_SERVER_ID,)
        )

    conn.commit()
    cur.close()
    conn.close()

    return "Database initialized!"

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
            "admin_alert_channel": 0
        }

    return {
        "kill_feed_channel": row["kill_feed_channel"] or 0,
        "explosive_feed_channel": row["explosive_feed_channel"] or 0,
        "connection_feed_channel": row["connection_feed_channel"] or 0,
        "zone_alert_channel": row["zone_alert_channel"] or 0,
        "general_log_channel": row["general_log_channel"] or 0,
        "admin_alert_channel": row["admin_alert_channel"] or 0,
    }

def get_nitrado_server_id() -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT server_id FROM nitrado_settings_v2 WHERE id = 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return int(row["server_id"]) if row and row.get("server_id") else DEFAULT_NITRADO_SERVER_ID

def set_nitrado_server_id(sid: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE nitrado_settings_v2 SET server_id=%s WHERE id=1", (sid,))
    conn.commit()
    cur.close()
    conn.close()

# ---------------------------------------------------------
# ZONES (DB KEPT)
# ---------------------------------------------------------
def get_all_zones():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM zones ORDER BY id ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def add_zone(name: str, action: str, points: Any):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO zones (name, action, points) VALUES (%s,%s,%s)", (name, action, points))
    conn.commit()
    cur.close()
    conn.close()

def delete_zone(zone_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM zones WHERE id=%s", (zone_id,))
    conn.commit()
    cur.close()
    conn.close()

# ---------------------------------------------------------
# FTP CLIENT
# ---------------------------------------------------------
class DayZFTP:
    def __init__(self):
        self.host = FTP_HOST
        self.port = FTP_PORT
        self.user = FTP_USER
        self.password = FTP_PASS

    def _connect(self):
        if not self.password:
            raise RuntimeError("FTP_PASS not set")
        ftp = FTP()
        ftp.connect(self.host, self.port, timeout=10)
        ftp.login(self.user, self.password)
        return ftp

    def read_file(self, path: str):
        ftp = self._connect()
        lines = []
        try:
            ftp.retrlines(f"RETR {path}", lambda line: lines.append(line))
        finally:
            ftp.quit()
        return "\n".join(lines) + "\n" if lines else ""

    def write_file(self, path: str, content: str):
        ftp = self._connect()
        try:
            bio = BytesIO(content.encode("utf-8"))
            ftp.storbinary(f"STOR {path}", bio)
        finally:
            ftp.quit()

ftp_client = DayZFTP()

def update_list_file(path: str, name: str, mode: str):
    try:
        try:
            content = ftp_client.read_file(path)
        except Exception:
            content = ""

        lines = [l.strip() for l in content.splitlines() if l.strip()]
        lname = name.strip()

        if mode == "add":
            if lname not in lines:
                lines.append(lname)
        elif mode == "remove":
            lines = [l for l in lines if l.lower() != lname.lower()]

        new_content = "\n".join(lines) + "\n" if lines else ""
        ftp_client.write_file(path, new_content)
        return True

    except Exception as e:
        logger.exception(f"FTP update failed for {path}: {e}")
        return False

# ---------------------------------------------------------
# NITRADO API
# ---------------------------------------------------------
class NitradoAPI:
    def __init__(self):
        self.base_url = "https://api.nitrado.net"

    @property
    def server_id(self):
        return get_nitrado_server_id()  # 17649304

@property
def token(self):
    # Render fix: always read from environment at runtime
    return os.getenv("NITRADO_TOKEN")


    @property
    def headers(self):
        if not self.token:
            return {}
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str):
        return f"{self.base_url}/services/{self.server_id}{path}"

    def _get(self, path: str):
        try:
            r = requests.get(self._url(path), headers=self.headers, timeout=10)
            if r.status_code == 200:
                return r.json()
            logger.error(f"Nitrado GET {path} failed: {r.status_code} {r.text}")
            return None
        except Exception as e:
            logger.exception(e)
            return None

    def _post(self, path: str):
        try:
            r = requests.post(self._url(path), headers=self.headers, timeout=10)
            if r.status_code == 200:
                return r.json()
            logger.error(f"Nitrado POST {path} failed: {r.status_code} {r.text}")
            return None
        except Exception as e:
            logger.exception(e)
            return None

    # -------------------------
    # STATUS
    # -------------------------
    def get_server_info(self):
        return self._get("/gameservers")

    # -------------------------
    # ONLINE PLAYERS
    # -------------------------
    def get_online_players(self):
        data = self._get("/gameservers/players")
        if not data:
            return []
        return data.get("data", {}).get("players", [])

    # -------------------------
    # PLAYER POSITIONS
    # -------------------------
    def get_player_positions(self):
        info = self.get_server_info()
        if not info:
            return []
        gs = info.get("data", {}).get("gameserver", {})
        players = gs.get("players", [])
        return players if isinstance(players, list) else []

    # -------------------------
    # SERVER CONTROL
    # -------------------------
    def restart_server(self):
        return self._post("/gameservers/restart", {}) is not None

    def stop_server(self):
        return self._post("/gameservers/stop", {}) is not None

    def start_server(self):
        return self._post("/gameservers/start", {}) is not None



nitrado_api = NitradoAPI()

# ---------------------------------------------------------
# DISCORD HELPERS
# ---------------------------------------------------------
def user_is_admin(member: discord.Member) -> bool:
    return any(r.id == ADMIN_ROLE_ID for r in member.roles)

def make_embed(title: str, description: str = "", color: discord.Color = discord.Color.blue()):
    e = discord.Embed(title=title, description=description, color=color)
    e.timestamp = datetime.utcnow()
    return e

# ---------------------------------------------------------
# BOT READY EVENT
# ---------------------------------------------------------
@bot.event
async def on_ready():
    guild = bot.get_guild(GUILD_ID)
    await tree.sync(guild=guild)

    # Start location feed background task
    bot.loop.create_task(location_feed_task())

    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")

# ---------------------------------------------------------
# /setlocationfeed
# ---------------------------------------------------------
@tree.command(name="setlocationfeed", description="Set the channel for player location logs")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def setlocationfeed(interaction: discord.Interaction, channel: discord.TextChannel):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return

    settings = load_settings()
    settings["location_feed_channel"] = channel.id
    save_settings(settings)

    await interaction.response.send_message(
        embed=make_embed("Location Feed Enabled", f"Logs will be sent to {channel.mention}.", discord.Color.green())
    )

# ---------------------------------------------------------
# /ban
# ---------------------------------------------------------
@tree.command(name="ban", description="Ban a player via Nitrado dashboard banlist")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ban(interaction: discord.Interaction, player_name: str):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    ok = nitrado_api.ban_player(player_name)
    if not ok:
        await interaction.followup.send(
            embed=make_embed("Ban Player", "❌ Failed to update Nitrado Banlist.", discord.Color.red()),
            ephemeral=True
        )
        return

    await interaction.followup.send(
        embed=make_embed("Ban Player", f"🔴 `{player_name}` added to Nitrado Banlist.", discord.Color.red()),
        ephemeral=True
    )

# ---------------------------------------------------------
# /unban
# ---------------------------------------------------------
@tree.command(name="unban", description="Unban a player via Nitrado dashboard banlist")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def unban(interaction: discord.Interaction, player_name: str):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    ok = nitrado_api.unban_player(player_name)
    if not ok:
        await interaction.followup.send(
            embed=make_embed("Unban Player", "❌ Failed to update Nitrado Banlist.", discord.Color.red()),
            ephemeral=True
        )
        return

    await interaction.followup.send(
        embed=make_embed("Unban Player", f"🟢 `{player_name}` removed from Nitrado Banlist.", discord.Color.green()),
        ephemeral=True
    )

# ---------------------------------------------------------
# /whitelist_add
# ---------------------------------------------------------
@tree.command(name="whitelist_add", description="Add a player to whitelist.txt via FTP")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def whitelist_add(interaction: discord.Interaction, player_name: str):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    ok = update_list_file(FTP_WHITELIST_PATH, player_name, "add")
    if not ok:
        await interaction.followup.send(
            embed=make_embed("Whitelist Add", "❌ Failed to update whitelist.txt.", discord.Color.red()),
            ephemeral=True
        )
        return

    await interaction.followup.send(
        embed=make_embed("Whitelist Add", f"🟢 `{player_name}` added to whitelist.txt.", discord.Color.green()),
        ephemeral=True
    )

# ---------------------------------------------------------
# /whitelist_remove
# ---------------------------------------------------------
@tree.command(name="whitelist_remove", description="Remove a player from whitelist.txt via FTP")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def whitelist_remove(interaction: discord.Interaction, player_name: str):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    ok = update_list_file(FTP_WHITELIST_PATH, player_name, "remove")
    if not ok:
        await interaction.followup.send(
            embed=make_embed("Whitelist Remove", "❌ Failed to update whitelist.txt.", discord.Color.red()),
            ephemeral=True
        )
        return

    await interaction.followup.send(
        embed=make_embed("Whitelist Remove", f"🟠 `{player_name}` removed from whitelist.txt.", discord.Color.orange()),
        ephemeral=True
    )

# ---------------------------------------------------------
# /restartserver
# ---------------------------------------------------------
@tree.command(name="restartserver", description="Restart the Nitrado server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def restartserver(interaction: discord.Interaction):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return

    await interaction.response.defer()

    ok = nitrado_api.restart_server()
    if ok:
        await interaction.followup.send(
            embed=make_embed("Server Restart", "🟠 Restart command sent.", discord.Color.orange())
        )
    else:
        await interaction.followup.send(
            embed=make_embed("Server Restart", "❌ Failed to restart server.", discord.Color.red())
        )

# ---------------------------------------------------------
# /stopserver
# ---------------------------------------------------------
@tree.command(name="stopserver", description="Stop the Nitrado server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def stopserver(interaction: discord.Interaction):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return

    await interaction.response.defer()

    ok = nitrado_api.stop_server()
    if ok:
        await interaction.followup.send(
            embed=make_embed("Server Stop", "🔴 Stop command sent.", discord.Color.red())
        )
    else:
        await interaction.followup.send(
            embed=make_embed("Server Stop", "❌ Failed to stop server.", discord.Color.red())
        )

# ---------------------------------------------------------
# /startserver
# ---------------------------------------------------------
@tree.command(name="startserver", description="Start the Nitrado server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def startserver(interaction: discord.Interaction):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return

    await interaction.response.defer()

    ok = nitrado_api.start_server()
    if ok:
        await interaction.followup.send(
            embed=make_embed("Server Start", "🟢 Start command sent.", discord.Color.green())
        )
    else:
        await interaction.followup.send(
            embed=make_embed("Server Start", "❌ Failed to start server.", discord.Color.red())
        )

# ---------------------------------------------------------
# /status
# ---------------------------------------------------------
@tree.command(name="status", description="Show Nitrado server status")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def status(interaction: discord.Interaction):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return

    info = nitrado_api.get_server_info()
    players = nitrado_api.get_online_players()

    if not info:
        await interaction.response.send_message(
            embed=make_embed("Server Status", "❌ Failed to contact Nitrado.", discord.Color.red())
        )
        return

    gs = info.get("data", {}).get("gameserver", {})
    name = gs.get("name", "Unknown")
    status_str = gs.get("status", "Unknown")
    slots = gs.get("slots", "Unknown")
    region = gs.get("location", "Unknown")
    ip = gs.get("ip", "Unknown")
    port = gs.get("port", "Unknown")
    player_count = len(players) if players else 0

    e = make_embed("Server Status", color=discord.Color.blue())
    e.add_field(name="Name", value=f"`{name}`", inline=False)
    e.add_field(name="Status", value=f"`{status_str}`", inline=True)
    e.add_field(name="Slots", value=f"`{slots}`", inline=True)
    e.add_field(name="Region", value=f"`{region}`", inline=True)
    e.add_field(name="IP", value=f"`{ip}`", inline=True)
    e.add_field(name="Port", value=f"`{port}`", inline=True)
    e.add_field(name="Players Online", value=f"`{player_count}`", inline=True)
    e.add_field(name="Server ID", value=f"`{nitrado_api.server_id}`", inline=True)

    await interaction.response.send_message(embed=e)

# ---------------------------------------------------------
# /online
# ---------------------------------------------------------
import a2s

@tree.command(name="online", description="Show online players using Steam Query")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def online(interaction: discord.Interaction):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return

    await interaction.response.defer()

    SERVER_IP = "109.230.243.79"
    QUERY_PORT = 10203

    try:
        info = a2s.info((SERVER_IP, QUERY_PORT))
        players = a2s.players((SERVER_IP, QUERY_PORT))

        if not players:
            await interaction.followup.send(
                embed=make_embed("Online Players", "No players online.", discord.Color.blue())
            )
            return

        description = ""
        for p in players:
            name = p.name or "Unknown"
            ping = f"{p.ping}ms" if hasattr(p, "ping") else "N/A"
            description += f"**{name}** — Ping: `{ping}`\n"

        embed = make_embed(
            f"🟢 Online Players ({len(players)})",
            description,
            discord.Color.green()
        )

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(
            embed=make_embed("Error", f"Could not query server.\n```\n{e}\n```", discord.Color.red())
        )
# ---------------------------------------------------------
# LOCATION FEED BACKGROUND TASK
# ---------------------------------------------------------
async def location_feed_task():
    await bot.wait_until_ready()
    logger.info("Starting player location feed...")

    while not bot.is_closed():
        settings = load_settings()
        channel_id = settings.get("location_feed_channel", 0)

        if channel_id != 0:
            channel = bot.get_channel(channel_id)
            if channel:
                players = nitrado_api.get_player_positions()

                for p in players:
                    name = p.get("name", "Unknown")
                    steam_id = p.get("id", "Unknown")
                    pos = p.get("position", {})

                    x = pos.get("x")
                    y = pos.get("y")

                    if x is not None and y is not None:
                        embed = make_embed(
                            "📍 Player Location",
                            f"**{name}** (`{steam_id}`)\n"
                            f"**X:** `{x}`\n"
                            f"**Y:** `{y}`",
                            discord.Color.blue()
                        )
                        await channel.send(embed=embed)

        await asyncio.sleep(5)

# ---------------------------------------------------------
# FLASK DASHBOARD
# ---------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

# ---------------------------------------------------------
# BOT + FLASK RUNNERS
# ---------------------------------------------------------
def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

def run_bot():
    bot.run(TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    run_bot()

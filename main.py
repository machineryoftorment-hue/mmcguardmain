import os
import sqlite3
import threading
import logging
import json
import re
from datetime import datetime

import discord
from discord.ext import commands
from discord import app_commands

from flask import Flask, redirect, request, session

import requests

# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mmcguard")

# -----------------------------
# Environment variables
# -----------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")
OAUTH_REDIRECT_URI = os.environ.get("OAUTH_REDIRECT_URI")

GUILD_ID = int(os.environ.get("GUILD_ID", "1404279040893911103"))
ADMIN_ROLE_ID = int(os.environ.get("ADMIN_ROLE_ID", "0"))

# Channel where Nitrado logs (with positions) are posted
LOG_FEED_CHANNEL_ID = int(os.environ.get("LOG_FEED_CHANNEL_ID", "0"))
EXPLOSIVE_CHANNEL_ID = int(os.environ.get("EXPLOSIVE_CHANNEL_ID", "0"))

DB_PATH = os.environ.get("DB_PATH", "mmcguard.db")

# World/map constants (Chernarus ~15360x15360, canvas 1024x1024)
WORLD_SIZE = 15360.0
CANVAS_SIZE = 1024.0
CANVAS_TO_WORLD = WORLD_SIZE / CANVAS_SIZE  # ~15


# -----------------------------
# SQLite helpers
# -----------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Settings: store Nitrado token + service_id
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            nitrado_token TEXT,
            service_id TEXT
        );
        """
    )

    # Players (for last known position)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            identity TEXT,
            x REAL,
            z REAL,
            y REAL,
            last_seen TIMESTAMP
        );
        """
    )

    # Explosives
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS explosives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_name TEXT,
            item TEXT,
            position TEXT,
            timestamp TIMESTAMP
        );
        """
    )

    # Kills
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS kills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            killer TEXT,
            victim TEXT,
            weapon TEXT,
            distance REAL,
            headshot INTEGER,
            timestamp TIMESTAMP
        );
        """
    )

    # Orders
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            description TEXT,
            created_at TIMESTAMP
        );
        """
    )

    # Zones
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            action TEXT,
            points TEXT
        );
        """
    )

    conn.commit()
    conn.close()


def get_settings():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT nitrado_token, service_id FROM settings WHERE id = 1;")
    row = cur.fetchone()
    conn.close()
    if row:
        return row["nitrado_token"], row["service_id"]
    return None, None


def set_settings(token: str, service_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO settings (id, nitrado_token, service_id)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET nitrado_token = excluded.nitrado_token,
                                     service_id = excluded.service_id;
        """,
        (token, service_id),
    )
    conn.commit()
    conn.close()


def log_explosive(player_name: str, item: str, position: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO explosives (player_name, item, position, timestamp) VALUES (?, ?, ?, ?);",
        (player_name, item, position, datetime.utcnow()),
    )
    conn.commit()
    conn.close()


def log_kill(killer: str, victim: str, weapon: str, distance: float, headshot: bool):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO kills (killer, victim, weapon, distance, headshot, timestamp)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (killer, victim, weapon, distance, 1 if headshot else 0, datetime.utcnow()),
    )
    conn.commit()
    conn.close()


def add_order(user_id: str, description: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO orders (user_id, description, created_at) VALUES (?, ?, ?);",
        (user_id, description, datetime.utcnow()),
    )
    conn.commit()
    conn.close()


def get_orders():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, description, created_at FROM orders ORDER BY id DESC;")
    rows = cur.fetchall()
    conn.close()
    return rows


def save_player_position(name: str, identity: str, x: float, z: float, y: float):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO players (name, identity, x, z, y, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            identity = excluded.identity,
            x = excluded.x,
            z = excluded.z,
            y = excluded.y,
            last_seen = excluded.last_seen;
        """,
        (name, identity, x, z, y, datetime.utcnow()),
    )
    conn.commit()
    conn.close()


def get_zones():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, action, points FROM zones ORDER BY id ASC;")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_zone(zone_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, action, points FROM zones WHERE id = ?;", (zone_id,))
    row = cur.fetchone()
    conn.close()
    return row


def update_zone(zone_id: int, name: str, action: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE zones SET name = ?, action = ? WHERE id = ?;",
        (name, action, zone_id),
    )
    conn.commit()
    conn.close()


def delete_zone(zone_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM zones WHERE id = ?;", (zone_id,))
    conn.commit()
    conn.close()


# -----------------------------
# Nitrado API wrapper (gameserver)
# -----------------------------
class NitradoAPI:
    def __init__(self):
        self.token, self.service_id = get_settings()
        self.base_url = "https://api.nitrado.net"

    def refresh_settings(self):
        self.token, self.service_id = get_settings()

    def _headers(self):
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    def is_connected(self) -> bool:
        return bool(self.token and self.service_id)

    def get_status(self):
        if not self.is_connected():
            return None
        url = f"{self.base_url}/services/{self.service_id}/gameservers"
        r = requests.get(url, headers=self._headers(), timeout=10)
        if r.status_code != 200:
            logger.warning(f"Nitrado get_status failed: {r.status_code} {r.text}")
            return None
        return r.json()

    def restart_server(self) -> bool:
        if not self.is_connected():
            return False
        url = f"{self.base_url}/services/{self.service_id}/gameservers/restart"
        r = requests.post(url, headers=self._headers(), timeout=10)
        if r.status_code != 200:
            logger.warning(f"Nitrado restart_server failed: {r.status_code} {r.text}")
            return False
        return True

    def get_players(self):
        if not self.is_connected():
            return []
        url = f"{self.base_url}/services/{self.service_id}/gameservers/games/players"
        r = requests.get(url, headers=self._headers(), timeout=10)
        if r.status_code != 200:
            logger.warning(f"Nitrado get_players failed: {r.status_code} {r.text}")
            return []
        try:
            data = r.json()
            return data.get("data", {}).get("players", [])
        except Exception as e:
            logger.exception("Failed to parse players JSON: %s", e)
            return []

    def _read_file(self, path: str) -> str | None:
        if not self.is_connected():
            return None
        url = f"{self.base_url}/services/{self.service_id}/gameservers/files"
        params = {"file": path}
        r = requests.get(url, headers=self._headers(), params=params, timeout=10)
        if r.status_code != 200:
            logger.warning(f"Nitrado read_file failed ({path}): {r.status_code} {r.text}")
            return None
        try:
            data = r.json()
            return data.get("data", {}).get("content", "")
        except Exception as e:
            logger.exception("Failed to parse file JSON: %s", e)
            return None

    def _write_file(self, path: str, content: str) -> bool:
        if not self.is_connected():
            return False
        url = f"{self.base_url}/services/{self.service_id}/gameservers/files"
        payload = {"file": path, "content": content}
        r = requests.post(url, headers=self._headers(), data=payload, timeout=10)
        if r.status_code != 200:
            logger.warning(f"Nitrado write_file failed ({path}): {r.status_code} {r.text}")
            return False
        return True

    def ban_player(self, name: str) -> bool:
        path = "/dayzxb/config/ban.txt"
        content = self._read_file(path)
        if content is None:
            return False
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        if name in lines:
            return True
        lines.append(name)
        new_content = "\n".join(lines) + "\n"
        ok = self._write_file(path, new_content)
        if ok:
            logger.info(f"[NitradoAPI] Banned {name}")
        return ok

    def unban_player(self, name: str) -> bool:
        path = "/dayzxb/config/ban.txt"
        content = self._read_file(path)
        if content is None:
            return False
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        if name not in lines:
            return True
        lines = [l for l in lines if l != name]
        new_content = "\n".join(lines) + "\n" if lines else ""
        ok = self._write_file(path, new_content)
        if ok:
            logger.info(f"[NitradoAPI] Unbanned {name}")
        return ok

    def whitelist_add(self, name: str) -> bool:
        path = "/dayzxb/config/whitelist.txt"
        content = self._read_file(path)
        if content is None:
            return False
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        if name in lines:
            return True
        lines.append(name)
        new_content = "\n".join(lines) + "\n"
        ok = self._write_file(path, new_content)
        if ok:
            logger.info(f"[NitradoAPI] Whitelist add {name}")
        return ok

    def whitelist_remove(self, name: str) -> bool:
        path = "/dayzxb/config/whitelist.txt"
        content = self._read_file(path)
        if content is None:
            return False
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        if name not in lines:
            return True
        lines = [l for l in lines if l != name]
        new_content = "\n".join(lines) + "\n" if lines else ""
        ok = self._write_file(path, new_content)
        if ok:
            logger.info(f"[NitradoAPI] Whitelist remove {name}")
        return ok


nitrado_api: NitradoAPI | None = None

# -----------------------------
# Discord bot setup
# -----------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await tree.sync()
        logger.info(f"Synced {len(synced)} commands.")
    except Exception as e:
        logger.exception("Failed to sync commands: %s", e)


def user_is_admin(member: discord.Member) -> bool:
    return any(r.id == ADMIN_ROLE_ID for r in member.roles)


def api_not_connected_msg(interaction: discord.Interaction):
    return interaction.response.send_message(
        "Nitrado API is not connected. Use `/activate` first.",
        ephemeral=True,
    )


# -----------------------------
# Zone math
# -----------------------------
def point_in_polygon(x: float, z: float, polygon_points):
    # Ray casting algorithm
    inside = False
    n = len(polygon_points)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, zi = polygon_points[i]["x"], polygon_points[i]["z"]
        xj, zj = polygon_points[j]["x"], polygon_points[j]["z"]
        intersect = ((zi > z) != (zj > z)) and (
            x < (xj - xi) * (z - zi) / (zj - zi + 1e-9) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


async def enforce_zones_for_player(name: str, x: float, z: float, y: float):
    zones = get_zones()
    if not zones:
        return

    for zone in zones:
        try:
            points = json.loads(zone["points"])
        except Exception:
            continue

        if not isinstance(points, list) or len(points) < 3:
            continue

        if point_in_polygon(x, z, points):
            action = zone["action"]
            logger.info(f"[ZONE] Player {name} inside zone '{zone['name']}' action={action}")

            # Simple enforcement:
            # log: do nothing
            # warn: send message in log channel
            # ban: ban via Nitrado
            # kick/tp: currently treated as warn/log (no direct kick API)
            channel = bot.get_channel(LOG_FEED_CHANNEL_ID)

            if action == "warn":
                if channel:
                    await channel.send(f"⚠ Player `{name}` entered restricted zone `{zone['name']}`.")
            elif action == "ban":
                if nitrado_api and nitrado_api.is_connected():
                    ok = nitrado_api.ban_player(name)
                    if channel:
                        if ok:
                            await channel.send(f"⛔ Player `{name}` banned for entering zone `{zone['name']}`.")
                        else:
                            await channel.send(
                                f"❌ Failed to ban `{name}` for zone `{zone['name']}`. Check Nitrado/API."
                            )
            elif action in ("kick", "tp"):
                if channel:
                    await channel.send(
                        f"⚠ (Simulated {action}) Player `{name}` in zone `{zone['name']}` at ({x:.1f},{z:.1f})."
                    )
            # log: nothing extra


# -----------------------------
# Slash commands
# -----------------------------
@tree.command(name="activate", description="Activate Nitrado integration with token and service ID")
@app_commands.describe(token="Your Nitrado long-life token", service_id="Your Nitrado service ID")
async def activate(interaction: discord.Interaction, token: str, service_id: str):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return

    set_settings(token, service_id)
    if nitrado_api:
        nitrado_api.refresh_settings()
    await interaction.response.send_message("Nitrado token and service ID saved.", ephemeral=True)


@tree.command(name="serverstatus", description="Get Nitrado server status")
async def serverstatus(interaction: discord.Interaction):
    if not nitrado_api or not nitrado_api.is_connected():
        await api_not_connected_msg(interaction)
        return

    data = nitrado_api.get_status()
    if not data:
        await interaction.response.send_message("Could not fetch server status. Check `/activate`.", ephemeral=True)
        return

    await interaction.response.send_message(f"Server status raw JSON:\n```json\n{data}\n```", ephemeral=True)


@tree.command(name="restartserver", description="Restart the Nitrado server")
async def restartserver(interaction: discord.Interaction):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return

    if not nitrado_api or not nitrado_api.is_connected():
        await api_not_connected_msg(interaction)
        return

    ok = nitrado_api.restart_server()
    if ok:
        await interaction.response.send_message("Server restart requested.", ephemeral=True)
    else:
        await interaction.response.send_message("Failed to request restart. Check `/activate`.", ephemeral=True)


@tree.command(name="players", description="List online players from Nitrado")
async def players(interaction: discord.Interaction):
    if not nitrado_api or not nitrado_api.is_connected():
        await api_not_connected_msg(interaction)
        return

    players_list = nitrado_api.get_players()
    if not players_list:
        await interaction.response.send_message("No players or failed to fetch.", ephemeral=True)
        return

    lines = []
    for p in players_list:
        name = p.get("name", "Unknown")
        lines.append(f"- {name}")
    msg = "\n".join(lines)
    await interaction.response.send_message(f"Online players:\n{msg}", ephemeral=True)


@tree.command(name="ban", description="Ban a player via Nitrado")
async def ban(interaction: discord.Interaction, player_name: str):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return

    if not nitrado_api or not nitrado_api.is_connected():
        await api_not_connected_msg(interaction)
        return

    ok = nitrado_api.ban_player(player_name)
    if ok:
        await interaction.response.send_message(f"Ban requested for `{player_name}`.", ephemeral=True)
    else:
        await interaction.response.send_message("Ban failed. Check logs and `/activate`.", ephemeral=True)


@tree.command(name="unban", description="Unban a player via Nitrado")
async def unban(interaction: discord.Interaction, player_name: str):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return

    if not nitrado_api or not nitrado_api.is_connected():
        await api_not_connected_msg(interaction)
        return

    ok = nitrado_api.unban_player(player_name)
    if ok:
        await interaction.response.send_message(f"Unban requested for `{player_name}`.", ephemeral=True)
    else:
        await interaction.response.send_message("Unban failed. Check logs and `/activate`.", ephemeral=True)


@tree.command(name="whitelist_add", description="Add a player to whitelist")
async def whitelist_add(interaction: discord.Interaction, player_name: str):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return

    if not nitrado_api or not nitrado_api.is_connected():
        await api_not_connected_msg(interaction)
        return

    ok = nitrado_api.whitelist_add(player_name)
    if ok:
        await interaction.response.send_message(f"Whitelist add requested for `{player_name}`.", ephemeral=True)
    else:
        await interaction.response.send_message("Whitelist add failed. Check logs and `/activate`.", ephemeral=True)


@tree.command(name="whitelist_remove", description="Remove a player from whitelist")
async def whitelist_remove(interaction: discord.Interaction, player_name: str):
    if not user_is_admin(interaction.user):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return

    if not nitrado_api or not nitrado_api.is_connected():
        await api_not_connected_msg(interaction)
        return

    ok = nitrado_api.whitelist_remove(player_name)
    if ok:
        await interaction.response.send_message(f"Whitelist remove requested for `{player_name}`.", ephemeral=True)
    else:
        await interaction.response.send_message("Whitelist remove failed. Check logs and `/activate`.", ephemeral=True)


@tree.command(name="addorder", description="Add an order")
async def addorder_cmd(interaction: discord.Interaction, description: str):
    add_order(str(interaction.user.id), description)
    await interaction.response.send_message("Order added.", ephemeral=True)


@tree.command(name="orders", description="List recent orders")
async def orders_cmd(interaction: discord.Interaction):
    rows = get_orders()
    if not rows:
        await interaction.response.send_message("No orders found.", ephemeral=True)
        return

    lines = []
    for r in rows[:20]:
        lines.append(f"#{r['id']} - <@{r['user_id']}>: {r['description']} ({r['created_at']})")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


# -----------------------------
# Feed parsing via on_message
# -----------------------------
PLAYER_POS_RE = re.compile(
    r'Player\s+"(?P<name>[^"]+)"\s+\(id=(?P<id>[^ )]+).*?pos=<(?P<x>[\d\.]+),\s*(?P<z>[\d\.]+),\s*(?P<y>[\d\.]+)>\)'
)


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    # Nitrado log feed with player positions
    if message.channel.id == LOG_FEED_CHANNEL_ID:
        for line in message.content.splitlines():
            m = PLAYER_POS_RE.search(line)
            if m:
                name = m.group("name")
                identity = m.group("id")
                x = float(m.group("x"))
                z = float(m.group("z"))
                y = float(m.group("y"))
                save_player_position(name, identity, x, z, y)
                await enforce_zones_for_player(name, x, z, y)

    # Explosive feed (optional extra logging)
    if message.channel.id == EXPLOSIVE_CHANNEL_ID:
        content = message.content
        try:
            if "placed" in content and "at" in content:
                parts = content.split("placed")
                player_name = parts[0].strip()
                rest = parts[1].split("at")
                item = rest[0].strip()
                position = rest[1].strip()
                log_explosive(player_name, item, position)
        except Exception as e:
            logger.exception("Failed to parse explosive message: %s", e)

    await bot.process_commands(message)


# -----------------------------
# Flask app + OAuth + Dashboard
# -----------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "supersecretkey")

DISCORD_API_BASE = "https://discord.com/api"
OAUTH_SCOPE = "identify guilds"


def render_sidebar(active: str) -> str:
    def item(label, href, key):
        base = "block p-2 rounded "
        if key == active:
            cls = base + "bg-gray-700"
        else:
            cls = base + "hover:bg-gray-700"
        return f"<a class='{cls}' href='{href}'>{label}</a>"

    return (
        "<div class='w-64 bg-gray-800 p-6 space-y-4'>"
        "<h1 class='text-2xl font-bold mb-6'>MMC Guard</h1>"
        f"{item('Home', '/dashboard', 'home')}"
        f"{item('Explosives', '/dashboard/explosives', 'explosives')}"
        f"{item('Kills', '/dashboard/kills', 'kills')}"
        f"{item('Orders', '/dashboard/orders', 'orders')}"
        f"{item('Server Status', '/dashboard/server', 'server')}"
        f"{item('Zones', '/dashboard/zones', 'zones')}"
        "<a class='block p-2 hover:bg-gray-700 rounded text-red-400' href='/logout'>Logout</a>"
        "</div>"
    )


@app.route("/")
def home():
    if "user" in session:
        return redirect("/dashboard")
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MMC Guard Login</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class='bg-gray-900 text-gray-200 flex items-center justify-center h-screen'>
        <div class='bg-gray-800 p-8 rounded shadow-lg text-center'>
            <h1 class='text-3xl font-bold mb-4'>MMC Guard Dashboard</h1>
            <p class='mb-6'>Welcome to the MMC Guard control panel.</p>
            <a href='/login' class='px-4 py-2 bg-blue-600 rounded hover:bg-blue-500'>Login with Discord</a>
        </div>
    </body>
    </html>
    """


@app.route("/login")
def login():
    return redirect(
        f"{DISCORD_API_BASE}/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={OAUTH_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={OAUTH_SCOPE}"
    )


@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    if not code:
        return "No code provided", 400

    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": OAUTH_SCOPE,
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    token_res = requests.post(f"{DISCORD_API_BASE}/oauth2/token", data=data, headers=headers)
    token_json = token_res.json()

    access_token = token_json.get("access_token")
    if not access_token:
        return "OAuth failed", 400

    user_res = requests.get(
        f"{DISCORD_API_BASE}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    user_json = user_res.json()

    session["user"] = user_json
    return redirect("/dashboard")


@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/login")

    sidebar = render_sidebar("home")
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>MMC Guard Dashboard</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class='bg-gray-900 text-gray-200'>
        <div class='flex h-screen'>
            {sidebar}
            <div class='flex-1 p-10'>
                <h2 class='text-3xl font-bold mb-4'>Welcome to MMC Guard</h2>
                <p>Select a menu item on the left.</p>
            </div>
        </div>
    </body>
    </html>
    """


@app.route("/dashboard/explosives")
def dashboard_explosives():
    if "user" not in session:
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT player_name, item, position, timestamp FROM explosives ORDER BY id DESC LIMIT 50;")
    rows = cur.fetchall()
    conn.close()

    sidebar = render_sidebar("explosives")
    items = "".join(
        f"<li class='mb-1'>{r['timestamp']} - {r['player_name']} placed {r['item']} at {r['position']}</li>"
        for r in rows
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Explosives</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class='bg-gray-900 text-gray-200'>
        <div class='flex h-screen'>
            {sidebar}
            <div class='flex-1 p-10'>
                <h2 class='text-3xl font-bold mb-4'>Explosives</h2>
                <ul class='list-disc ml-6'>
                    {items}
                </ul>
            </div>
        </div>
    </body>
    </html>
    """


@app.route("/dashboard/kills")
def dashboard_kills():
    if "user" not in session:
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT killer, victim, weapon, distance, headshot, timestamp FROM kills ORDER BY id DESC LIMIT 50;"
    )
    rows = cur.fetchall()
    conn.close()

    sidebar = render_sidebar("kills")
    items = ""
    for r in rows:
        hs = "HS" if r["headshot"] else ""
        items += (
            f"<li class='mb-1'>{r['timestamp']} - {r['killer']} killed {r['victim']} "
            f"with {r['weapon']} ({r['distance']}m) {hs}</li>"
        )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Kills</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class='bg-gray-900 text-gray-200'>
        <div class='flex h-screen'>
            {sidebar}
            <div class='flex-1 p-10'>
                <h2 class='text-3xl font-bold mb-4'>Kills</h2>
                <ul class='list-disc ml-6'>
                    {items}
                </ul>
            </div>
        </div>
    </body>
    </html>
    """


@app.route("/dashboard/orders")
def dashboard_orders():
    if "user" not in session:
        return redirect("/login")

    rows = get_orders()
    sidebar = render_sidebar("orders")
    items = "".join(
        f"<li class='mb-1'>#{r['id']} - {r['created_at']} - {r['user_id']}: {r['description']}</li>"
        for r in rows
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Orders</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class='bg-gray-900 text-gray-200'>
        <div class='flex h-screen'>
            {sidebar}
            <div class='flex-1 p-10'>
                <h2 class='text-3xl font-bold mb-4'>Orders</h2>
                <ul class='list-disc ml-6'>
                    {items}
                </ul>
            </div>
        </div>
    </body>
    </html>
    """


@app.route("/dashboard/server")
def dashboard_server():
    if "user" not in session:
        return redirect("/login")

    sidebar = render_sidebar("server")

    if not nitrado_api or not nitrado_api.is_connected():
        body = "<p>Nitrado API not connected. Use /activate.</p>"
    else:
        data = nitrado_api.get_status()
        if not data:
            body = "<p>Could not fetch status. Check /activate.</p>"
        else:
            body = f"<pre class='bg-gray-800 p-4 rounded text-xs overflow-auto'>{data}</pre>"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Server Status</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class='bg-gray-900 text-gray-200'>
        <div class='flex h-screen'>
            {sidebar}
            <div class='flex-1 p-10'>
                <h2 class='text-3xl font-bold mb-4'>Server Status</h2>
                {body}
            </div>
        </div>
    </body>
    </html>
    """


@app.route("/dashboard/zones")
def dashboard_zones():
    if "user" not in session:
        return redirect("/login")

    sidebar = render_sidebar("zones")
    zones = get_zones()

    rows_html = ""
    for z in zones:
        rows_html += (
            f"<tr>"
            f"<td class='border border-gray-700 px-2 py-1'>{z['id']}</td>"
            f"<td class='border border-gray-700 px-2 py-1'>{z['name']}</td>"
            f"<td class='border border-gray-700 px-2 py-1'>{z['action']}</td>"
            f"<td class='border border-gray-700 px-2 py-1'>"
            f"<a class='text-blue-400' href='/dashboard/zones/edit/{z['id']}'>Edit</a> | "
            f"<a class='text-red-400' href='/dashboard/zones/delete/{z['id']}'>Delete</a>"
            f"</td>"
            f"</tr>"
        )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Zone Editor</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/fabric.js/5.2.4/fabric.min.js"></script>
    </head>
    <body class='bg-gray-900 text-gray-200'>
        <div class='flex h-screen'>
            {sidebar}
            <div class='flex-1 p-10 overflow-auto'>
                <h2 class='text-3xl font-bold mb-4'>Zone Editor</h2>

                <h3 class='text-xl font-bold mb-2'>Existing Zones</h3>
                <table class='mb-6 border border-gray-700 text-sm'>
                    <tr class='bg-gray-800'>
                        <th class='border border-gray-700 px-2 py-1'>ID</th>
                        <th class='border border-gray-700 px-2 py-1'>Name</th>
                        <th class='border border-gray-700 px-2 py-1'>Action</th>
                        <th class='border border-gray-700 px-2 py-1'>Actions</th>
                    </tr>
                    {rows_html}
                </table>

                <h3 class='text-xl font-bold mt-4 mb-2'>Create New Zone</h3>

                <div class='mb-4'>
                    <label class='block mb-1'>Zone Name</label>
                    <input id='zoneName' class='p-2 bg-gray-800 border border-gray-700 rounded w-64'>
                </div>

                <div class='mb-4'>
                    <label class='block mb-1'>Action</label>
                    <select id='zoneAction' class='p-2 bg-gray-800 border border-gray-700 rounded w-64'>
                        <option value='log'>Log Only</option>
                        <option value='warn'>Warn Player</option>
                        <option value='kick'>Kick Player (simulated)</option>
                        <option value='ban'>Ban Player</option>
                        <option value='tp'>Teleport Out (simulated)</option>
                    </select>
                </div>

                <button onclick='saveZone()' class='px-4 py-2 bg-blue-600 rounded hover:bg-blue-500'>
                    Save Zone
                </button>

                <h3 class='text-xl font-bold mt-6 mb-2'>Chernarus Map</h3>

                <canvas id='mapCanvas' width='1024' height='1024' class='border border-gray-700'></canvas>

                <script>
                    const canvas = new fabric.Canvas('mapCanvas');

                    fabric.Image.fromURL('/static/chernarus.jpg', function(img) {{
                        img.scaleToWidth(1024);
                        canvas.setBackgroundImage(img, canvas.renderAll.bind(canvas));
                    }});

                    let polygonPoints = [];

                    canvas.on('mouse:down', function(opt) {{
                        const pointer = canvas.getPointer(opt.e);
                        polygonPoints.push({{ x: pointer.x, z: pointer.y }});

                        const circle = new fabric.Circle({{
                            radius: 4,
                            fill: 'red',
                            left: pointer.x,
                            top: pointer.y,
                            selectable: false
                        }});
                        canvas.add(circle);
                    }});

                    function saveZone() {{
                        const name = document.getElementById('zoneName').value;
                        const action = document.getElementById('zoneAction').value;

                        if (!name) {{
                            alert('Zone name is required');
                            return;
                        }}
                        if (polygonPoints.length < 3) {{
                            alert('You need at least 3 points for a zone.');
                            return;
                        }}

                        // Convert canvas coords (0-1024) to world coords (~0-15360)
                        const worldPoints = polygonPoints.map(p => {{
                            return {{
                                x: p.x * {CANVAS_TO_WORLD},
                                z: p.z * {CANVAS_TO_WORLD}
                            }};
                        }});

                        fetch('/dashboard/zones/save', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{
                                name: name,
                                action: action,
                                points: worldPoints
                            }})
                        }}).then(res => {{
                            if (res.ok) {{
                                alert('Zone saved!');
                                window.location.reload();
                            }} else {{
                                alert('Failed to save zone');
                            }}
                        }});
                    }}
                </script>

            </div>
        </div>
    </body>
    </html>
    """


@app.route("/dashboard/zones/save", methods=["POST"])
def save_zone():
    if "user" not in session:
        return "Unauthorized", 403

    data = request.json
    name = data.get("name", "").strip()
    action = data.get("action", "log")
    points = data.get("points", [])

    if not name:
        return "Name required", 400
    if not isinstance(points, list) or len(points) < 3:
        return "At least 3 points required", 400

    points_json = json.dumps(points)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO zones (name, action, points) VALUES (?, ?, ?)",
        (name, action, points_json),
    )
    conn.commit()
    conn.close()

    return "OK"


@app.route("/dashboard/zones/edit/<int:zone_id>", methods=["GET", "POST"])
def edit_zone(zone_id):
    if "user" not in session:
        return redirect("/login")

    zone = get_zone(zone_id)
    if not zone:
        return "Zone not found", 404

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        action = request.form.get("action", "log")
        if not name:
            return "Name required", 400
        update_zone(zone_id, name, action)
        return redirect("/dashboard/zones")

    sidebar = render_sidebar("zones")

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Edit Zone</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class='bg-gray-900 text-gray-200'>
        <div class='flex h-screen'>
            {sidebar}
            <div class='flex-1 p-10'>
                <h2 class='text-3xl font-bold mb-4'>Edit Zone #{zone['id']}</h2>
                <form method='POST' class='space-y-4'>
                    <div>
                        <label class='block mb-1'>Zone Name</label>
                        <input name='name' value='{zone['name']}' class='p-2 bg-gray-800 border border-gray-700 rounded w-64'>
                    </div>
                    <div>
                        <label class='block mb-1'>Action</label>
                        <select name='action' class='p-2 bg-gray-800 border border-gray-700 rounded w-64'>
                            <option value='log' {"selected" if zone['action']=="log" else ""}>Log Only</option>
                            <option value='warn' {"selected" if zone['action']=="warn" else ""}>Warn Player</option>
                            <option value='kick' {"selected" if zone['action']=="kick" else ""}>Kick Player (simulated)</option>
                            <option value='ban' {"selected" if zone['action']=="ban" else ""}>Ban Player</option>
                            <option value='tp' {"selected" if zone['action']=="tp" else ""}>Teleport Out (simulated)</option>
                        </select>
                    </div>
                    <button class='px-4 py-2 bg-blue-600 rounded hover:bg-blue-500'>Save</button>
                    <a href='/dashboard/zones' class='ml-4 text-gray-400'>Cancel</a>
                </form>
            </div>
        </div>
    </body>
    </html>
    """


@app.route("/dashboard/zones/delete/<int:zone_id>")
def delete_zone_route(zone_id):
    if "user" not in session:
        return redirect("/login")

    delete_zone(zone_id)
    return redirect("/dashboard/zones")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# -----------------------------
# Run Flask + Discord together
# -----------------------------
def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    init_db()
    nitrado_api = NitradoAPI()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    bot.run(DISCORD_TOKEN)

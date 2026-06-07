import os
import sqlite3
import threading
import logging
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

# Your Discord server ID (GUILD_ID)
GUILD_ID = int(os.environ.get("GUILD_ID", "1404279040893911103"))

ADMIN_ROLE_ID = int(os.environ.get("ADMIN_ROLE_ID", "0"))

CONNECTION_CHANNEL_ID = int(os.environ.get("CONNECTION_CHANNEL_ID", "0"))
EXPLOSIVE_CHANNEL_ID = int(os.environ.get("EXPLOSIVE_CHANNEL_ID", "0"))

DB_PATH = os.environ.get("DB_PATH", "mmcguard.db")

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

    # Players
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            steam_id TEXT,
            last_seen TIMESTAMP,
            status TEXT
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
        # This endpoint is typical for gameserver player list; adjust if needed.
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

    # For DayZ, bans/whitelists are usually managed via files (ban.txt, whitelist.txt).
    # Here we implement API calls that *fail cleanly* if something is wrong.
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
        # Adjust path if your ban file is different
        path = "/dayzxb/config/ban.txt"
        content = self._read_file(path)
        if content is None:
            return False

        lines = [l.strip() for l in content.splitlines() if l.strip()]
        if name in lines:
            # Already banned
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
            # Not in ban list, treat as success
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
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    # Connection feed
    if message.channel.id == CONNECTION_CHANNEL_ID:
        logger.info(f"[CONNECTION FEED] {message.content}")

    # Explosive feed
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
# Flask app + OAuth
# -----------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "supersecretkey")

DISCORD_API_BASE = "https://discord.com/api"
OAUTH_SCOPE = "identify guilds"


@app.route("/")
def home():
    if "user" in session:
        return f"""
        <h1>MMC Guard Dashboard</h1>
        <p>Logged in as: {session['user']['username']}#{session['user']['discriminator']}</p>
        <a href='/dashboard'>Go to Dashboard</a><br><br>
        <a href='/logout'>Logout</a>
        """
    return """
    <h1>MMC Guard Dashboard</h1>
    <p>Welcome to the MMC Guard control panel.</p>
    <a href='/login'>Login with Discord</a>
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

    return """
    <h1>MMC Guard Admin Dashboard</h1>
    <ul>
        <li><a href='/dashboard/explosives'>Explosives</a></li>
        <li><a href='/dashboard/kills'>Kills</a></li>
        <li><a href='/dashboard/orders'>Orders</a></li>
        <li><a href='/dashboard/server'>Server Status</a></li>
    </ul>
    <a href='/logout'>Logout</a>
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

    html = "<h1>Explosives</h1><ul>"
    for r in rows:
        html += f"<li>{r['timestamp']} - {r['player_name']} placed {r['item']} at {r['position']}</li>"
    html += "</ul><a href='/dashboard'>Back</a>"
    return html


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

    html = "<h1>Kills</h1><ul>"
    for r in rows:
        hs = "HS" if r["headshot"] else ""
        html += (
            f"<li>{r['timestamp']} - {r['killer']} killed {r['victim']} "
            f"with {r['weapon']} ({r['distance']}m) {hs}</li>"
        )
    html += "</ul><a href='/dashboard'>Back</a>"
    return html


@app.route("/dashboard/orders")
def dashboard_orders():
    if "user" not in session:
        return redirect("/login")

    rows = get_orders()
    html = "<h1>Orders</h1><ul>"
    for r in rows:
        html += f"<li>#{r['id']} - {r['created_at']} - {r['user_id']}: {r['description']}</li>"
    html += "</ul><a href='/dashboard'>Back</a>"
    return html


@app.route("/dashboard/server")
def dashboard_server():
    if "user" not in session:
        return redirect("/login")

    if not nitrado_api or not nitrado_api.is_connected():
        return "<h1>Server Status</h1><p>Nitrado API not connected. Use /activate.</p><a href='/dashboard'>Back</a>"

    data = nitrado_api.get_status()
    if not data:
        return "<h1>Server Status</h1><p>Could not fetch status. Check /activate.</p><a href='/dashboard'>Back</a>"

    return f"<h1>Server Status</h1><pre>{data}</pre><a href='/dashboard'>Back</a>"


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
    # Initialize DB BEFORE using NitradoAPI
    init_db()

    # Now safe to create NitradoAPI instance
    nitrado_api = NitradoAPI()

    # Start Flask in background
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Start Discord bot
    bot.run(DISCORD_TOKEN)

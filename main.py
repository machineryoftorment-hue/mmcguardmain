import os
import json
import threading
from typing import Dict, Any, Optional

from flask import Flask, request, jsonify, render_template, redirect, url_for
import discord
from discord.ext import commands
from discord import app_commands

import psycopg2
import psycopg2.extras
import requests

# =========================
# CONFIG
# =========================

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = 1404279040893911103
ADMIN_ROLE_ID = 1419520911471542413

NITRADO_SERVER_ID = 17649304
NITRADO_API_BASE = "https://api-us.nitrado.net"

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

    # Nitrado settings table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nitrado_settings (
            id INTEGER PRIMARY KEY,
            api_token TEXT
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
            INSERT INTO nitrado_settings (id, api_token)
            VALUES (1, NULL)
        """)

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
# NITRADO TOKEN HELPERS
# =========================

def get_nitrado_token() -> Optional[str]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT api_token FROM nitrado_settings WHERE id = 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return row["api_token"]


def set_nitrado_token(token: str) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE nitrado_settings
        SET api_token = %s
        WHERE id = 1
    """, (token,))
    conn.commit()
    cur.close()
    conn.close()


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
# NITRADO API WRAPPER
# =========================

class NitradoAPI:
    def __init__(self):
        self.base_url = NITRADO_API_BASE
        self.server_id = NITRADO_SERVER_ID

    @property
    def token(self) -> Optional[str]:
        return get_nitrado_token()

    @property
    def headers(self) -> Dict[str, str]:
        if not self.token:
            return {}
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}/services/{self.server_id}{path}"

    def get_server_info(self) -> Optional[Dict[str, Any]]:
        if not self.token:
            return None
        try:
            resp = requests.get(self._url("/gameservers"), headers=self.headers, timeout=10)
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None


nitrado_api = NitradoAPI()


# =========================
# DISCORD EVENTS & COMMANDS
# =========================

@bot.event
async def on_ready():
    global CHANNELS_CACHE
    CHANNELS_CACHE = reload_channel_settings()
    guild = bot.get_guild(GUILD_ID)
    if guild:
        try:
            await tree.sync(guild=guild)
            print(f"Synced commands to guild {guild.name} ({guild.id})")
        except Exception as e:
            print(f"Failed to sync commands: {e}")
    print(f"Logged in as {bot.user} (guild {GUILD_ID})")


def user_is_admin(member: discord.Member) -> bool:
    return any(role.id == ADMIN_ROLE_ID for role in member.roles)


@tree.command(name="activate", description="Activate or update the Nitrado API token")
@app_commands.describe(token="Your Nitrado long-life API token")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def activate(interaction: discord.Interaction, token: str):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "This command can only be used in a server.", ephemeral=True
        )
        return

    if not user_is_admin(interaction.user):
        await interaction.response.send_message(
            "You do not have permission to use this command.", ephemeral=True
        )
        return

    set_nitrado_token(token)
    await interaction.response.send_message(
        "✅ Nitrado API token has been saved.", ephemeral=True
    )


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


# =========================
# API ENDPOINTS
# =========================

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

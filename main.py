import os
import sqlite3
import threading
from typing import Dict, Any, Optional

from flask import Flask, request, jsonify, render_template, redirect, url_for
import discord
from discord.ext import commands

# =========================
# CONFIG
# =========================

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = 1404279040893911103
ADMIN_ROLE_ID = 1419520911471542413

DB_PATH = "mmcguard.db"

# =========================
# DISCORD BOT SETUP
# =========================

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# FLASK APP
# =========================

app = Flask(__name__, static_folder="static", static_url_path="/static")


# =========================
# DATABASE HELPERS
# =========================

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()

    # Zones table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            action TEXT NOT NULL,
            points TEXT NOT NULL
        )
        """
    )

    # Bot settings table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            kill_feed_channel INTEGER,
            explosive_feed_channel INTEGER,
            connection_feed_channel INTEGER,
            zone_alert_channel INTEGER,
            general_log_channel INTEGER,
            admin_alert_channel INTEGER
        )
        """
    )

    # Ensure row exists
    cur.execute("SELECT id FROM bot_settings WHERE id = 1")
    if cur.fetchone() is None:
        cur.execute(
            """
            INSERT INTO bot_settings (
                id,
                kill_feed_channel,
                explosive_feed_channel,
                connection_feed_channel,
                zone_alert_channel,
                general_log_channel,
                admin_alert_channel
            ) VALUES (1, 0, 0, 0, 0, 0, 0)
            """
        )

    conn.commit()
    conn.close()


def get_bot_settings() -> Dict[str, int]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bot_settings WHERE id = 1")
    row = cur.fetchone()
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
    cur.execute(
        """
        UPDATE bot_settings
        SET
            kill_feed_channel = ?,
            explosive_feed_channel = ?,
            connection_feed_channel = ?,
            zone_alert_channel = ?,
            general_log_channel = ?,
            admin_alert_channel = ?
        WHERE id = 1
        """,
        (
            int(data.get("kill_feed_channel", 0) or 0),
            int(data.get("explosive_feed_channel", 0) or 0),
            int(data.get("connection_feed_channel", 0) or 0),
            int(data.get("zone_alert_channel", 0) or 0),
            int(data.get("general_log_channel", 0) or 0),
            int(data.get("admin_alert_channel", 0) or 0),
        ),
    )
    conn.commit()
    conn.close()


def get_all_zones() -> list[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM zones ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return rows


def add_zone(name: str, action: str, points_json: str) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO zones (name, action, points) VALUES (?, ?, ?)",
        (name, action, points_json),
    )
    conn.commit()
    conn.close()


def delete_zone(zone_id: int) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM zones WHERE id = ?", (zone_id,))
    conn.commit()
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


@bot.event
async def on_ready():
    global CHANNELS_CACHE
    CHANNELS_CACHE = reload_channel_settings()
    print(f"Logged in as {bot.user} (guild {GUILD_ID})")


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


def get_guild_channels() -> list[discord.TextChannel]:
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return []
    return [ch for ch in guild.channels if isinstance(ch, discord.TextChannel)]


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
    channels = get_guild_channels()
    return render_template("discord_settings.html", settings=settings, channels=channels)


# =========================
# API ENDPOINTS
# =========================

@app.route("/api/zones", methods=["GET"])
def api_get_zones():
    zones = get_all_zones()
    return jsonify(
        [
            {
                "id": z["id"],
                "name": z["name"],
                "action": z["action"],
                "points": z["points"],
            }
            for z in zones
        ]
    )


# =========================
# RUN BOT + FLASK TOGETHER
# =========================

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


def run_bot():
    bot.run(TOKEN)


if __name__ == "__main__":
    init_db()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    run_bot()

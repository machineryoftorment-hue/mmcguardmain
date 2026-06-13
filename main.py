import os
import json
import threading
import logging
from datetime import datetime, timedelta

from typing import Dict, Any, Optional, List

from flask import Flask, request, jsonify, render_template
import discord
from discord.ext import commands
from discord import app_commands

import psycopg2
import psycopg2.extras
import requests

# -------------------------------------------------
# Logging
# -------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mmcguard")

# -------------------------------------------------
# Config / Environment
# -------------------------------------------------
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
NITRADO_TOKEN = os.environ.get("NITRADO_TOKEN", None)

GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "1404279040893911103"))
ADMIN_ROLE_ID = int(os.environ.get("DISCORD_ADMIN_ROLE_ID", "1419520911471542413"))

DEFAULT_NITRADO_SERVER_ID = int(os.environ.get("NITRADO_SERVER_ID", "17649304"))
NITRADO_API_BASE = "https://api.nitrado.net"

bot_start_time = datetime.utcnow()

# -------------------------------------------------
# Discord / Flask setup
# -------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

app = Flask(__name__, static_folder="static", static_url_path="/static")

# -------------------------------------------------
# Database helpers
# -------------------------------------------------
def get_db():
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


@app.route("/initdb")
def initdb():
    conn = get_db()
    cur = conn.cursor()

    # Zones
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS zones (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            action TEXT NOT NULL,
            points JSON NOT NULL
        );
        """
    )

    # Bot settings
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_settings (
            id INTEGER PRIMARY KEY,
            kill_feed_channel BIGINT,
            explosive_feed_channel BIGINT,
            connection_feed_channel BIGINT,
            zone_alert_channel BIGINT,
            general_log_channel BIGINT,
            admin_alert_channel BIGINT
        );
        """
    )

    # Nitrado settings v2 (with server_id)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS nitrado_settings_v2 (
            id INTEGER PRIMARY KEY,
            api_token TEXT,
            server_id BIGINT
        );
        """
    )

    # Seed bot_settings
    cur.execute("SELECT id FROM bot_settings WHERE id = 1")
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO bot_settings VALUES (1, 0, 0, 0, 0, 0, 0)"
        )

    # Seed nitrado_settings_v2
    cur.execute("SELECT id FROM nitrado_settings_v2 WHERE id = 1")
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO nitrado_settings_v2 VALUES (1, NULL, %s)",
            (DEFAULT_NITRADO_SERVER_ID,),
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


def update_bot_settings(d: Dict[str, Any]) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE bot_settings
        SET
            kill_feed_channel = %s,
            explosive_feed_channel = %s,
            connection_feed_channel = %s,
            zone_alert_channel = %s,
            general_log_channel = %s,
            admin_alert_channel = %s
        WHERE id = 1
        """,
        (
            int(d.get("kill_feed_channel", 0) or 0),
            int(d.get("explosive_feed_channel", 0) or 0),
            int(d.get("connection_feed_channel", 0) or 0),
            int(d.get("zone_alert_channel", 0) or 0),
            int(d.get("general_log_channel", 0) or 0),
            int(d.get("admin_alert_channel", 0) or 0),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_nitrado_server_id() -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT server_id FROM nitrado_settings_v2 WHERE id = 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row and row.get("server_id"):
        return int(row["server_id"])
    return DEFAULT_NITRADO_SERVER_ID


def set_nitrado_server_id(sid: int) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE nitrado_settings_v2 SET server_id = %s WHERE id = 1",
        (sid,),
    )
    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Nitrado server ID updated to {sid}")


def get_all_zones() -> List[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM zones ORDER BY id ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def add_zone(name: str, action: str, points: Any) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO zones (name, action, points) VALUES (%s, %s, %s)",
        (name, action, points),
    )
    conn.commit()
    cur.close()
    conn.close()


def delete_zone(zone_id: int) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM zones WHERE id = %s", (zone_id,))
    conn.commit()
    cur.close()
    conn.close()

# -------------------------------------------------
# Channel cache helpers
# -------------------------------------------------
def get_channel_by_id(cid: int) -> Optional[discord.TextChannel]:
    if cid == 0:
        return None
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return None
    return guild.get_channel(cid)


def reload_channel_settings() -> Dict[str, Optional[discord.TextChannel]]:
    s = get_bot_settings()
    return {
        "kill_feed": get_channel_by_id(s["kill_feed_channel"]),
        "explosive_feed": get_channel_by_id(s["explosive_feed_channel"]),
        "connection_feed": get_channel_by_id(s["connection_feed_channel"]),
        "zone_alert": get_channel_by_id(s["zone_alert_channel"]),
        "general_log": get_channel_by_id(s["general_log_channel"]),
        "admin_alert": get_channel_by_id(s["admin_alert_channel"]),
    }


CHANNELS_CACHE: Dict[str, Optional[discord.TextChannel]] = {}

# -------------------------------------------------
# Nitrado API wrapper
# -------------------------------------------------
class NitradoAPI:
    def __init__(self):
        self.base_url = NITRADO_API_BASE

    @property
    def server_id(self) -> int:
        return get_nitrado_server_id()

    @property
    def token(self) -> Optional[str]:
        return NITRADO_TOKEN

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
            return None
        try:
            r = requests.post(
                self._url(path),
                headers=self.headers,
                json=payload,
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
            logger.warning(f"Nitrado POST {path} failed: {r.status_code} {r.text}")
            return None
        except Exception as e:
            logger.exception(e)
            return None

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        if not self.token:
            return None
        try:
            r = requests.get(
                self._url(path),
                headers=self.headers,
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
            logger.warning(f"Nitrado GET {path} failed: {r.status_code} {r.text}")
            return None
        except Exception as e:
            logger.exception(e)
            return None

    def get_server_info(self) -> Optional[Dict[str, Any]]:
        return self._get("/gameservers")

    def get_online_players(self) -> Optional[List[Dict[str, Any]]]:
        data = self._get("/gameservers/games/players")
        if not data:
            return None
        return data.get("data", {}).get("players", [])

    def restart_server(self) -> bool:
        return self._post("/gameservers/games/commands/server/restart", {}) is not None

    def stop_server(self) -> bool:
        return self._post("/gameservers/games/commands/server/stop", {}) is not None

    def start_server(self) -> bool:
        return self._post("/gameservers/games/commands/server/start", {}) is not None

    def ban_player(self, name: str) -> bool:
        return (
            self._post("/gameservers/games/commands/players/ban", {"player": name})
            is not None
        )

    def unban_player(self, name: str) -> bool:
        return (
            self._post("/gameservers/games/commands/players/unban", {"player": name})
            is not None
        )

    def kick_player(self, name: str) -> bool:
        return (
            self._post("/gameservers/games/commands/players/kick", {"player": name})
            is not None
        )

    def whitelist_add(self, name: str) -> bool:
        return (
            self._post(
                "/gameservers/games/commands/players/whitelist/add", {"player": name}
            )
            is not None
        )

    def whitelist_remove(self, name: str) -> bool:
        return (
            self._post(
                "/gameservers/games/commands/players/whitelist/remove",
                {"player": name},
            )
            is not None
        )


nitrado_api = NitradoAPI()

# -------------------------------------------------
# Utility
# -------------------------------------------------
def user_is_admin(member: discord.Member) -> bool:
    return any(r.id == ADMIN_ROLE_ID for r in member.roles)


def make_embed(
    title: str,
    description: str = "",
    color: discord.Color = discord.Color.blue(),
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.timestamp = datetime.utcnow()
    return embed


# -------------------------------------------------
# Discord events
# -------------------------------------------------
@bot.event
async def on_ready():
    global CHANNELS_CACHE
    CHANNELS_CACHE = reload_channel_settings()
    guild = bot.get_guild(GUILD_ID)
    if guild:
        try:
            await tree.sync(guild=guild)
            logger.info(f"Commands synced safely to guild {guild.id}")
        except Exception as e:
            logger.exception(e)
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")


# -------------------------------------------------
# Slash commands
# -------------------------------------------------
@tree.command(name="checktoken", description="Check Nitrado token")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def checktoken(interaction: discord.Interaction):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    if not nitrado_api.token:
        embed = make_embed(
            "Nitrado Token Check",
            "❌ No Nitrado token set in environment.",
            discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    info = nitrado_api.get_server_info()
    if info:
        embed = make_embed(
            "Nitrado Token Check",
            "✅ Token OK. Successfully contacted Nitrado API.",
            discord.Color.green(),
        )
    else:
        embed = make_embed(
            "Nitrado Token Check",
            "❌ Token appears invalid or Nitrado API unreachable.",
            discord.Color.red(),
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="setserverid", description="Set the Nitrado server ID")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def setserverid(interaction: discord.Interaction, server_id: int):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    set_nitrado_server_id(server_id)
    embed = make_embed(
        "Server ID Updated",
        f"Nitrado server ID has been updated to `{server_id}`.",
        discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="status", description="Show Nitrado server status")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def status(interaction: discord.Interaction):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    info = nitrado_api.get_server_info()
    players = nitrado_api.get_online_players()

    if not info:
        embed = make_embed(
            "Server Status",
            "❌ Failed to retrieve server info from Nitrado.",
            discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed)
        return

    gs = info.get("data", {}).get("gameserver", {})
    name = gs.get("name", "Unknown")
    status_str = gs.get("status", "Unknown")
    slots = gs.get("slots", "Unknown")
    region = gs.get("location", "Unknown")
    ip = gs.get("ip", "Unknown")
    port = gs.get("port", "Unknown")
    player_count = len(players) if players else 0

    embed = make_embed(
        "Server Status",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Name", value=f"`{name}`", inline=False)
    embed.add_field(name="Status", value=f"`{status_str}`", inline=True)
    embed.add_field(name="Slots", value=f"`{slots}`", inline=True)
    embed.add_field(name="Region", value=f"`{region}`", inline=True)
    embed.add_field(name="IP", value=f"`{ip}`", inline=True)
    embed.add_field(name="Port", value=f"`{port}`", inline=True)
    embed.add_field(name="Players Online", value=f"`{player_count}`", inline=True)
    embed.add_field(
        name="Server ID",
        value=f"`{nitrado_api.server_id}`",
        inline=True,
    )

    await interaction.response.send_message(embed=embed)


@tree.command(name="restartserver", description="Restart the Nitrado server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def restartserver(interaction: discord.Interaction):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    ok = nitrado_api.restart_server()

    if ok:
        embed = make_embed(
            "Server Restart",
            "🟠 Restart command sent to Nitrado.",
            discord.Color.orange(),
        )
    else:
        embed = make_embed(
            "Server Restart",
            "❌ Failed to send restart command to Nitrado.",
            discord.Color.red(),
        )

    await interaction.followup.send(embed=embed)


@tree.command(name="stopserver", description="Stop the Nitrado server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def stopserver(interaction: discord.Interaction):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    ok = nitrado_api.stop_server()

    if ok:
        embed = make_embed(
            "Server Stop",
            "🔴 Stop command sent to Nitrado.",
            discord.Color.red(),
        )
    else:
        embed = make_embed(
            "Server Stop",
            "❌ Failed to send stop command to Nitrado.",
            discord.Color.red(),
        )

    await interaction.followup.send(embed=embed)


@tree.command(name="startserver", description="Start the Nitrado server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def startserver(interaction: discord.Interaction):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    ok = nitrado_api.start_server()

    if ok:
        embed = make_embed(
            "Server Start",
            "🟢 Start command sent to Nitrado.",
            discord.Color.green(),
        )
    else:
        embed = make_embed(
            "Server Start",
            "❌ Failed to send start command to Nitrado.",
            discord.Color.red(),
        )

    await interaction.followup.send(embed=embed)


@tree.command(name="kick", description="Kick a player from the server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def kick_player(interaction: discord.Interaction, player_name: str):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    ok = nitrado_api.kick_player(player_name)

    if ok:
        embed = make_embed(
            "Kick Player",
            f"🔴 Kick command sent for player `{player_name}`.",
            discord.Color.red(),
        )
    else:
        embed = make_embed(
            "Kick Player",
            f"❌ Failed to kick player `{player_name}`.",
            discord.Color.red(),
        )

    await interaction.followup.send(embed=embed)


@tree.command(name="ban", description="Ban a player from the server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ban_player(interaction: discord.Interaction, player_name: str):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    ok = nitrado_api.ban_player(player_name)

    if ok:
        embed = make_embed(
            "Ban Player",
            f"🔴 Ban command sent for player `{player_name}`.",
            discord.Color.red(),
        )
    else:
        embed = make_embed(
            "Ban Player",
            f"❌ Failed to ban player `{player_name}`.",
            discord.Color.red(),
        )

    await interaction.followup.send(embed=embed)


@tree.command(name="unban", description="Unban a player from the server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def unban_player(interaction: discord.Interaction, player_name: str):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    ok = nitrado_api.unban_player(player_name)

    if ok:
        embed = make_embed(
            "Unban Player",
            f"🟢 Unban command sent for player `{player_name}`.",
            discord.Color.green(),
        )
    else:
        embed = make_embed(
            "Unban Player",
            f"❌ Failed to unban player `{player_name}`.",
            discord.Color.red(),
        )

    await interaction.followup.send(embed=embed)


@tree.command(name="whitelist_add", description="Add a player to the whitelist")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def whitelist_add(interaction: discord.Interaction, player_name: str):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    ok = nitrado_api.whitelist_add(player_name)

    if ok:
        embed = make_embed(
            "Whitelist Add",
            f"🟢 Player `{player_name}` added to whitelist.",
            discord.Color.green(),
        )
    else:
        embed = make_embed(
            "Whitelist Add",
            f"❌ Failed to add `{player_name}` to whitelist.",
            discord.Color.red(),
        )

    await interaction.followup.send(embed=embed)


@tree.command(name="whitelist_remove", description="Remove a player from the whitelist")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def whitelist_remove(interaction: discord.Interaction, player_name: str):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    ok = nitrado_api.whitelist_remove(player_name)

    if ok:
        embed = make_embed(
            "Whitelist Remove",
            f"🟠 Player `{player_name}` removed from whitelist.",
            discord.Color.orange(),
        )
    else:
        embed = make_embed(
            "Whitelist Remove",
            f"❌ Failed to remove `{player_name}` from whitelist.",
            discord.Color.red(),
        )

    await interaction.followup.send(embed=embed)


@tree.command(name="online", description="List online players")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def online(interaction: discord.Interaction):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    players = nitrado_api.get_online_players()
    if not players:
        embed = make_embed(
            "Online Players",
            "No players are currently online.",
            discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed)
        return

    lines = []
    for p in players:
        name = p.get("name", "Unknown")
        ping = p.get("ping", "N/A")
        lines.append(f"- `{name}` (ping: {ping})")

    embed = make_embed(
        "Online Players",
        "\n".join(lines),
        discord.Color.blue(),
    )
    await interaction.response.send_message(embed=embed)


# -------------------------------------------------
# Flask routes
# -------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# -------------------------------------------------
# Run
# -------------------------------------------------
def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


def run_bot():
    bot.run(TOKEN)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    run_bot()

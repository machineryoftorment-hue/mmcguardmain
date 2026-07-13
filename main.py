import os, json, threading, logging, asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
from flask import Flask, render_template
import discord
from discord.ext import commands
from discord import app_commands
import psycopg2, psycopg2.extras, requests
from ftplib import FTP
from io import BytesIO

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("mmcguard")

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

# -------------------------
# JSON SETTINGS (NO DB FOR LOCATION FEED)
# -------------------------
SETTINGS_FILE = "settings.json"

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {"location_feed_channel": 0}
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)

# -------------------------
# DISCORD BOT SETUP
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

app = Flask(__name__, static_folder="static", static_url_path="/static")

# -------------------------
# DATABASE FUNCTIONS
# -------------------------
def get_db():
    return psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=psycopg2.extras.RealDictCursor)

@app.route("/initdb")
def initdb():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS zones (id SERIAL PRIMARY KEY, name TEXT NOT NULL, action TEXT NOT NULL, points JSON NOT NULL);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS bot_settings (id INTEGER PRIMARY KEY, kill_feed_channel BIGINT, explosive_feed_channel BIGINT, connection_feed_channel BIGINT, zone_alert_channel BIGINT, general_log_channel BIGINT, admin_alert_channel BIGINT);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS nitrado_settings_v2 (id INTEGER PRIMARY KEY, api_token TEXT, server_id BIGINT);""")
    cur.execute("SELECT id FROM bot_settings WHERE id = 1")
    if cur.fetchone() is None: cur.execute("INSERT INTO bot_settings VALUES (1,0,0,0,0,0,0)")
    cur.execute("SELECT id FROM nitrado_settings_v2 WHERE id = 1")
    if cur.fetchone() is None: cur.execute("INSERT INTO nitrado_settings_v2 VALUES (1,NULL,%s)", (DEFAULT_NITRADO_SERVER_ID,))
    conn.commit(); cur.close(); conn.close()
    return "Database initialized!"

def get_bot_settings() -> Dict[str, int]:
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM bot_settings WHERE id = 1")
    row = cur.fetchone(); cur.close(); conn.close()
    if not row:
        return {"kill_feed_channel": 0, "explosive_feed_channel": 0, "connection_feed_channel": 0,
                "zone_alert_channel": 0, "general_log_channel": 0, "admin_alert_channel": 0}
    return {
        "kill_feed_channel": row["kill_feed_channel"] or 0,
        "explosive_feed_channel": row["explosive_feed_channel"] or 0,
        "connection_feed_channel": row["connection_feed_channel"] or 0,
        "zone_alert_channel": row["zone_alert_channel"] or 0,
        "general_log_channel": row["general_log_channel"] or 0,
        "admin_alert_channel": row["admin_alert_channel"] or 0,
    }

def update_bot_settings(d: Dict[str, Any]) -> None:
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        """UPDATE bot_settings SET
           kill_feed_channel=%s, explosive_feed_channel=%s, connection_feed_channel=%s,
           zone_alert_channel=%s, general_log_channel=%s, admin_alert_channel=%s
           WHERE id=1""",
        (
            int(d.get("kill_feed_channel", 0) or 0),
            int(d.get("explosive_feed_channel", 0) or 0),
            int(d.get("connection_feed_channel", 0) or 0),
            int(d.get("zone_alert_channel", 0) or 0),
            int(d.get("general_log_channel", 0) or 0),
            int(d.get("admin_alert_channel", 0) or 0),
        ),
    )
    conn.commit(); cur.close(); conn.close()

def get_nitrado_server_id() -> int:
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT server_id FROM nitrado_settings_v2 WHERE id = 1")
    row = cur.fetchone(); cur.close(); conn.close()
    return int(row["server_id"]) if row and row.get("server_id") else DEFAULT_NITRADO_SERVER_ID

def set_nitrado_server_id(sid: int) -> None:
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE nitrado_settings_v2 SET server_id=%s WHERE id=1", (sid,))
    conn.commit(); cur.close(); conn.close()
    logger.info(f"Nitrado server ID updated to {sid}")

def get_all_zones() -> List[Dict[str, Any]]:
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM zones ORDER BY id ASC")
    rows = cur.fetchall(); cur.close(); conn.close()
    return rows

def add_zone(name: str, action: str, points: Any) -> None:
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO zones (name, action, points) VALUES (%s, %s, %s)", (name, action, points))
    conn.commit(); cur.close(); conn.close()

def delete_zone(zone_id: int) -> None:
    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM zones WHERE id=%s", (zone_id,))
    conn.commit(); cur.close(); conn.close()

# -------------------------
# CHANNEL LOADER
# -------------------------
def get_channel_by_id(cid: int) -> Optional[discord.TextChannel]:
    if cid == 0: return None
    guild = bot.get_guild(GUILD_ID)
    if not guild: return None
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

# -------------------------
# NITRADO API
# -------------------------
class NitradoAPI:
    def __init__(self): self.base_url = NITRADO_API_BASE

    @property
    def server_id(self) -> int: return get_nitrado_server_id()

    @property
    def token(self) -> Optional[str]: return NITRADO_TOKEN

    @property
    def headers(self) -> Dict[str, str]:
        return {} if not self.token else {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}/services/{self.server_id}{path}"

    def _post(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.token: return None
        try:
            r = requests.post(self._url(path), headers=self.headers, json=payload, timeout=10)
            if r.status_code == 200: return r.json()
            logger.warning(f"Nitrado POST {path} failed: {r.status_code} {r.text}")
            return None
        except Exception as e:
            logger.exception(e); return None

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        if not self.token: return None
        try:
            r = requests.get(self._url(path), headers=self.headers, timeout=10)
            if r.status_code == 200: return r.json()
            logger.warning(f"Nitrado GET {path} failed: {r.status_code} {r.text}")
            return None
        except Exception as e:
            logger.exception(e); return None

    def get_server_info(self) -> Optional[Dict[str, Any]]:
        return self._get("/gameservers")

    def get_online_players(self) -> Optional[List[Dict[str, Any]]]:
        data = self._get("/gameservers/players")
        return None if not data else data.get("data", {}).get("players", [])

    # ⭐ NEW: Correct player position API
    def get_player_positions(self):
        info = self.get_server_info()
        if not info:
            return []
        gs = info.get("data", {}).get("gameserver", {})
        return gs.get("players", [])

    def restart_server(self) -> bool: return self._post("/gameservers/restart", {}) is not None
    def stop_server(self) -> bool: return self._post("/gameservers/stop", {}) is not None
    def start_server(self) -> bool: return self._post("/gameservers/start", {}) is not None

    def ban_player(self, name: str) -> bool:
        data = self._post("/gameservers/games/players/ban", {"name": name})
        return data is not None

    def unban_player(self, name: str) -> bool:
        data = self._post("/gameservers/games/players/unban", {"name": name})
        return data is not None

nitrado_api = NitradoAPI()

# -------------------------
# FTP
# -------------------------
class DayZFTP:
    def __init__(self):
        self.host = FTP_HOST
        self.port = FTP_PORT
        self.user = FTP_USER
        self.password = FTP_PASS

    def _connect(self) -> FTP:
        if not self.password:
            raise RuntimeError("FTP_PASS not set in environment")
        ftp = FTP()
        ftp.connect(self.host, self.port, timeout=10)
        ftp.login(self.user, self.password)
        return ftp

    def read_file(self, path: str) -> str:
        ftp = self._connect()
        lines = []
        try:
            ftp.retrlines(f"RETR {path}", lambda line: lines.append(line))
        finally:
            ftp.quit()
        return "\n".join(lines) + "\n" if lines else ""

    def write_file(self, path: str, content: str) -> None:
        ftp = self._connect()
        try:
            bio = BytesIO(content.encode("utf-8"))
            ftp.storbinary(f"STOR {path}", bio)
        finally:
            ftp.quit()

ftp_client = DayZFTP()

def update_list_file(path: str, name: str, mode: str) -> bool:
    try:
        try:
            content = ftp_client.read_file(path)
        except Exception:
            content = ""
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        lname = name.strip()
        if mode == "add":
            if lname not in lines: lines.append(lname)
        elif mode == "remove":
            lines = [l for l in lines if l.lower() != lname.lower()]
        new_content = "\n".join(lines) + "\n" if lines else ""
        ftp_client.write_file(path, new_content)
        return True
    except Exception as e:
        logger.exception(f"FTP update failed for {path}: {e}")
        return False

def whitelist_add_via_ftp(name: str) -> bool:
    return update_list_file(FTP_WHITELIST_PATH, name, "add")

def whitelist_remove_via_ftp(name: str) -> bool:
    return update_list_file(FTP_WHITELIST_PATH, name, "remove")

# -------------------------
# DISCORD HELPERS
# -------------------------
def user_is_admin(member: discord.Member) -> bool:
    return any(r.id == ADMIN_ROLE_ID for r in member.roles)

def make_embed(title: str, description: str = "", color: discord.Color = discord.Color.blue()) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color)
    e.timestamp = datetime.utcnow()
    return e

# -------------------------
# BOT READY
# -------------------------
@bot.event
async def on_ready():
    guild = bot.get_guild(GUILD_ID)

    # Sync commands normally
    await tree.sync(guild=guild)

    # Start location feed
    bot.loop.create_task(location_feed_task())

    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")


# -------------------------
# COMMANDS
# -------------------------

@tree.command(name="setlocationfeed", description="Set the channel for player location logs")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def setlocationfeed(interaction: discord.Interaction, channel: discord.TextChannel):
    user = interaction.user
    if not isinstance(user, discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission.", ephemeral=True)
        return

    settings = load_settings()
    settings["location_feed_channel"] = channel.id
    save_settings(settings)

    await interaction.response.send_message(
        embed=make_embed("Location Feed Enabled", f"Logs will be sent to {channel.mention}.", discord.Color.green())
    )

# -------------------------
# LOCATION FEED TASK
# -------------------------
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
                            f"**{name}** (`{steam_id}`)\n**X:** `{x}`\n**Y:** `{y}`",
                            discord.Color.blue()
                        )
                        await channel.send(embed=embed)

        await asyncio.sleep(5)

# -------------------------
# FLASK + BOT RUNNERS
# -------------------------
@app.route("/")
def index():
    return render_template("index.html")

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

def run_bot():
    bot.run(TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    run_bot()

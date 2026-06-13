import os,json,threading,logging,time
from datetime import datetime,timedelta
from typing import Dict,Any,Optional,List
from flask import Flask,request,jsonify,render_template,redirect,url_for
import discord
from discord.ext import commands
from discord import app_commands
import psycopg2,psycopg2.extras,requests

logging.basicConfig(level=logging.INFO,format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
logger=logging.getLogger("mmcguard")

TOKEN=os.environ.get("DISCORD_BOT_TOKEN","")
NITRADO_TOKEN=os.environ.get("NITRADO_TOKEN",None)
GUILD_ID=1404279040893911103
ADMIN_ROLE_ID=1419520911471542413
DEFAULT_NITRADO_SERVER_ID=int(os.environ.get("NITRADO_SERVER_ID","17649304"))
NITRADO_API_BASE="https://api.nitrado.net"
bot_start_time=datetime.utcnow()

intents=discord.Intents.default()
intents.message_content=True
intents.guilds=True
intents.members=True
bot=commands.Bot(command_prefix="!",intents=intents)
tree=bot.tree
app=Flask(__name__,static_folder="static",static_url_path="/static")

def get_db():
    return psycopg2.connect(os.getenv("DATABASE_URL"),cursor_factory=psycopg2.extras.RealDictCursor)

@app.route("/initdb")
def initdb():
    conn=get_db();cur=conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS zones (id SERIAL PRIMARY KEY,name TEXT NOT NULL,action TEXT NOT NULL,points JSON NOT NULL);")
    cur.execute("CREATE TABLE IF NOT EXISTS bot_settings (id INTEGER PRIMARY KEY,kill_feed_channel BIGINT,explosive_feed_channel BIGINT,connection_feed_channel BIGINT,zone_alert_channel BIGINT,general_log_channel BIGINT,admin_alert_channel BIGINT);")
    cur.execute("CREATE TABLE IF NOT EXISTS nitrado_settings_v2 (id INTEGER PRIMARY KEY,api_token TEXT,server_id BIGINT);")
    cur.execute("SELECT id FROM bot_settings WHERE id=1")
    if cur.fetchone() is None:
        cur.execute("INSERT INTO bot_settings VALUES (1,0,0,0,0,0,0)")
    cur.execute("SELECT id FROM nitrado_settings_v2 WHERE id=1")
    if cur.fetchone() is None:
        cur.execute("INSERT INTO nitrado_settings_v2 VALUES (1,NULL,%s)",(DEFAULT_NITRADO_SERVER_ID,))
    conn.commit();cur.close();conn.close()
    return "Database initialized!"

def get_bot_settings():
    conn=get_db();cur=conn.cursor()
    cur.execute("SELECT * FROM bot_settings WHERE id=1")
    row=cur.fetchone();cur.close();conn.close()
    if not row:return {"kill_feed_channel":0,"explosive_feed_channel":0,"connection_feed_channel":0,"zone_alert_channel":0,"general_log_channel":0,"admin_alert_channel":0}
    return {k:row[k] or 0 for k in ["kill_feed_channel","explosive_feed_channel","connection_feed_channel","zone_alert_channel","general_log_channel","admin_alert_channel"]}

def update_bot_settings(d):
    conn=get_db();cur=conn.cursor()
    cur.execute("UPDATE bot_settings SET kill_feed_channel=%s,explosive_feed_channel=%s,connection_feed_channel=%s,zone_alert_channel=%s,general_log_channel=%s,admin_alert_channel=%s WHERE id=1",
        (int(d.get("kill_feed_channel",0) or 0),int(d.get("explosive_feed_channel",0) or 0),int(d.get("connection_feed_channel",0) or 0),
         int(d.get("zone_alert_channel",0) or 0),int(d.get("general_log_channel",0) or 0),int(d.get("admin_alert_channel",0) or 0)))
    conn.commit();cur.close();conn.close()

def get_nitrado_server_id():
    conn=get_db();cur=conn.cursor()
    cur.execute("SELECT server_id FROM nitrado_settings_v2 WHERE id=1")
    row=cur.fetchone();cur.close();conn.close()
    return int(row["server_id"]) if row and row.get("server_id") else DEFAULT_NITRADO_SERVER_ID

def set_nitrado_server_id(sid):
    conn=get_db();cur=conn.cursor()
    cur.execute("UPDATE nitrado_settings_v2 SET server_id=%s WHERE id=1",(sid,))
    conn.commit();cur.close();conn.close()
    logger.info(f"Nitrado server ID updated to {sid}")

def get_all_zones():
    conn=get_db();cur=conn.cursor()
    cur.execute("SELECT * FROM zones ORDER BY id ASC")
    rows=cur.fetchall();cur.close();conn.close();return rows

def add_zone(n,a,p):
    conn=get_db();cur=conn.cursor()
    cur.execute("INSERT INTO zones (name,action,points) VALUES (%s,%s,%s)",(n,a,p))
    conn.commit();cur.close();conn.close()

def delete_zone(zid):
    conn=get_db();cur=conn.cursor()
    cur.execute("DELETE FROM zones WHERE id=%s",(zid,))
    conn.commit();cur.close();conn.close()

def get_channel_by_id(cid):
    if cid==0:return None
    g=bot.get_guild(GUILD_ID)
    return g.get_channel(cid) if g else None

def reload_channel_settings():
    s=get_bot_settings()
    return {"kill_feed":get_channel_by_id(s["kill_feed_channel"]),"explosive_feed":get_channel_by_id(s["explosive_feed_channel"]),
            "connection_feed":get_channel_by_id(s["connection_feed_channel"]),"zone_alert":get_channel_by_id(s["zone_alert_channel"]),
            "general_log":get_channel_by_id(s["general_log_channel"]),"admin_alert":get_channel_by_id(s["admin_alert_channel"])}

CHANNELS_CACHE={}

class NitradoAPI:
    def __init__(self):self.base_url=NITRADO_API_BASE
    @property
    def server_id(self):return get_nitrado_server_id()
    @property
    def token(self):return NITRADO_TOKEN
    @property
    def headers(self):
        return {"Authorization":f"Bearer {self.token}","Accept":"application/json","Content-Type":"application/json"} if self.token else {}
    def _url(self,p):return f"{self.base_url}/services/{self.server_id}{p}"
    def _post(self,p,pl):
        if not self.token:return None
        try:
            r=requests.post(self._url(p),headers=self.headers,json=pl,timeout=10)
            return r.json() if r.status_code==200 else None
        except Exception as e:logger.exception(e);return None
    def _get(self,p):
        if not self.token:return None
        try:
            r=requests.get(self._url(p),headers=self.headers,timeout=10)
            return r.json() if r.status_code==200 else None
        except Exception as e:logger.exception(e);return None
    def get_server_info(self):return self._get("/gameservers")
    def get_online_players(self):
        d=self._get("/gameservers/games/players")
        return d.get("data",{}).get("players",[]) if d else None
    def restart_server(self):return self._post("/gameservers/games/commands/server/restart",{}) is not None
    def stop_server(self):return self._post("/gameservers/games/commands/server/stop",{}) is not None
    def start_server(self):return self._post("/gameservers/games/commands/server/start",{}) is not None
    def ban_player(self,n):return self._post("/gameservers/games/commands/players/ban",{"player":n}) is not None
    def unban_player(self,n):return self._post("/gameservers/games/commands/players/unban",{"player":n}) is not None
    def kick_player(self,n):return self._post("/gameservers/games/commands/players/kick",{"player":n}) is not None
    def whitelist_add(self,n):return self._post("/gameservers/games/commands/players/whitelist/add",{"player":n}) is not None
    def whitelist_remove(self,n):return self._post("/gameservers/games/commands/players/whitelist/remove",{"player":n}) is not None

nitrado_api=NitradoAPI()

def user_is_admin(m):return any(r.id==ADMIN_ROLE_ID for r in m.roles)

@bot.event
async def on_ready():
    global CHANNELS_CACHE
    CHANNELS_CACHE=reload_channel_settings()
    g=bot.get_guild(GUILD_ID)
    if g:
        try:await tree.sync(guild=g)
        except Exception as e:logger.exception(e)
    logger.info(f"Logged in as {bot.user}")

@tree.command(name="checktoken",description="Check Nitrado token")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def checktoken(i):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):
        await i.response.send_message("No permission",ephemeral=True);return
    if not nitrado_api.token:
        await i.response.send_message("❌ No Nitrado token set");return
    await i.response.send_message("✅ Token OK" if nitrado_api.get_server_info() else "❌ Token invalid")

@tree.command(name="setserverid",description="Set server ID")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def setserverid(i,sid:int):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):
        await i.response.send_message("No permission",ephemeral=True);return
    set_nitrado_server_id(sid)
    await i.response.send_message(f"Updated to {sid}")

@tree.command(name="status",description="Server status")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def status(i):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):
        await i.response.send_message("No permission",ephemeral=True);return
    info=nitrado_api.get_server_info();players=nitrado_api.get_online_players()
    if not info:
        await i.response.send_message("❌ Failed");return
    s=info.get("data",{}).get("gameserver",{})
    msg=f"Name:`{s.get('name')}` Status:`{s.get('status')}` Slots:`{s.get('slots')}` Region:`{s.get('location')}` IP:`{s.get('ip')}` Port:`{s.get('port')}` Players:`{len(players) if players else 0}`"
    await i.response.send_message(msg)

@app.route("/")
def index():return render_template("index.html")

def run_flask():app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
def run_bot():bot.run(TOKEN)

if __name__=="__main__":
    threading.Thread(target=run_flask,daemon=True).start()
    run_bot()

import os,json,threading,logging
from datetime import datetime
from typing import Dict,Any,Optional,List
from flask import Flask,render_template
import discord
from discord.ext import commands
from discord import app_commands
import psycopg2,psycopg2.extras,requests

logging.basicConfig(level=logging.INFO,format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
logger=logging.getLogger("mmcguard")

TOKEN=os.getenv("DISCORD_BOT_TOKEN","")
NITRADO_TOKEN=os.getenv("NITRADO_TOKEN",None)
GUILD_ID=int(os.getenv("DISCORD_GUILD_ID","1404279040893911103"))
ADMIN_ROLE_ID=int(os.getenv("DISCORD_ADMIN_ROLE_ID","1419520911471542413"))
DEFAULT_NITRADO_SERVER_ID=int(os.getenv("NITRADO_SERVER_ID","17649304"))
NITRADO_API_BASE="https://api.nitrado.net"

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
    cur.execute("""CREATE TABLE IF NOT EXISTS zones (id SERIAL PRIMARY KEY,name TEXT NOT NULL,action TEXT NOT NULL,points JSON NOT NULL);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS bot_settings (id INTEGER PRIMARY KEY,kill_feed_channel BIGINT,explosive_feed_channel BIGINT,connection_feed_channel BIGINT,zone_alert_channel BIGINT,general_log_channel BIGINT,admin_alert_channel BIGINT);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS nitrado_settings_v2 (id INTEGER PRIMARY KEY,api_token TEXT,server_id BIGINT);""")
    cur.execute("SELECT id FROM bot_settings WHERE id=1")
    if cur.fetchone() is None:cur.execute("INSERT INTO bot_settings VALUES (1,0,0,0,0,0,0)")
    cur.execute("SELECT id FROM nitrado_settings_v2 WHERE id=1")
    if cur.fetchone() is None:cur.execute("INSERT INTO nitrado_settings_v2 VALUES (1,NULL,%s)",(DEFAULT_NITRADO_SERVER_ID,))
    conn.commit();cur.close();conn.close()
    return "Database initialized!"

def get_bot_settings():
    conn=get_db();cur=conn.cursor()
    cur.execute("SELECT * FROM bot_settings WHERE id=1")
    row=cur.fetchone();cur.close();conn.close()
    if not row:return {k:0 for k in ["kill_feed_channel","explosive_feed_channel","connection_feed_channel","zone_alert_channel","general_log_channel","admin_alert_channel"]}
    return {k:row[k] or 0 for k in row}

def update_bot_settings(d):
    conn=get_db();cur=conn.cursor()
    cur.execute("""UPDATE bot_settings SET kill_feed_channel=%s,explosive_feed_channel=%s,connection_feed_channel=%s,zone_alert_channel=%s,general_log_channel=%s,admin_alert_channel=%s WHERE id=1""",
        (int(d.get("kill_feed_channel",0) or 0),int(d.get("explosive_feed_channel",0) or 0),
         int(d.get("connection_feed_channel",0) or 0),int(d.get("zone_alert_channel",0) or 0),
         int(d.get("general_log_channel",0) or 0),int(d.get("admin_alert_channel",0) or 0)))
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

def get_all_zones():
    conn=get_db();cur=conn.cursor()
    cur.execute("SELECT * FROM zones ORDER BY id ASC")
    rows=cur.fetchall();cur.close();conn.close()
    return rows

def add_zone(name,action,points):
    conn=get_db();cur=conn.cursor()
    cur.execute("INSERT INTO zones (name,action,points) VALUES (%s,%s,%s)",(name,action,points))
    conn.commit();cur.close();conn.close()

def delete_zone(zone_id):
    conn=get_db();cur=conn.cursor()
    cur.execute("DELETE FROM zones WHERE id=%s",(zone_id,))
    conn.commit();cur.close();conn.close()

def get_channel_by_id(cid):
    if cid==0:return None
    g=bot.get_guild(GUILD_ID)
    return g.get_channel(cid) if g else None

def reload_channel_settings():
    s=get_bot_settings()
    return {
        "kill_feed":get_channel_by_id(s["kill_feed_channel"]),
        "explosive_feed":get_channel_by_id(s["explosive_feed_channel"]),
        "connection_feed":get_channel_by_id(s["connection_feed_channel"]),
        "zone_alert":get_channel_by_id(s["zone_alert_channel"]),
        "general_log":get_channel_by_id(s["general_log_channel"]),
        "admin_alert":get_channel_by_id(s["admin_alert_channel"]),
    }

class NitradoAPI:
    def __init__(self):self.base=NITRADO_API_BASE
    @property
    def sid(self):return get_nitrado_server_id()
    @property
    def token(self):return NITRADO_TOKEN
    @property
    def headers(self):
        return {} if not self.token else {"Authorization":f"Bearer {self.token}","Accept":"application/json","Content-Type":"application/json"}
    def _url(self,p):return f"{self.base}/services/{self.sid}{p}"
    def _get(self,p):
        try:r=requests.get(self._url(p),headers=self.headers,timeout=10);return r.json() if r.status_code==200 else None
        except:return None
    def _post(self,p,data):
        try:r=requests.post(self._url(p),headers=self.headers,json=data,timeout=10);return r.json() if r.status_code==200 else None
        except:return None

    # FIXED PS4 ENDPOINTS
    def get_server_info(self):return self._get("/gameservers")
    def get_online_players(self):
        d=self._get("/gameservers/players")
        return d.get("data",{}).get("players",[]) if d else None

    def restart_server(self):return self._post("/gameservers/restart",{})!=None
    def stop_server(self):return self._post("/gameservers/stop",{})!=None
    def start_server(self):return self._post("/gameservers/start",{})!=None

    def ban_player(self,name):return self._post("/gameservers/command/players/ban",{"name":name})!=None
    def unban_player(self,name):return self._post("/gameservers/command/players/unban",{"name":name})!=None
    def kick_player(self,name):return self._post("/gameservers/command/players/kick",{"name":name})!=None
    def whitelist_add(self,name):return self._post("/gameservers/command/players/whitelist/add",{"name":name})!=None
    def whitelist_remove(self,name):return self._post("/gameservers/command/players/whitelist/remove",{"name":name})!=None

nitrado=NitradoAPI()

def user_is_admin(m):return any(r.id==ADMIN_ROLE_ID for r in m.roles)
def make_embed(t,d="",c=discord.Color.blue()):
    e=discord.Embed(title=t,description=d,color=c);e.timestamp=datetime.utcnow();return e

@bot.event
async def on_ready():
    g=bot.get_guild(GUILD_ID)
    await tree.sync(guild=g)
    for c in await tree.fetch_commands():await c.delete()
    logger.info(f"Logged in as {bot.user}")

@tree.command(name="checktoken")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def checktoken(i):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    if not nitrado.token:return await i.response.send_message(embed=make_embed("Token","❌ No token",discord.Color.red()),ephemeral=True)
    ok=nitrado.get_server_info()
    e=make_embed("Token","✅ OK",discord.Color.green()) if ok else make_embed("Token","❌ Invalid",discord.Color.red())
    await i.response.send_message(embed=e,ephemeral=True)

@tree.command(name="setserverid")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def setserverid(i,sid:int):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    set_nitrado_server_id(sid)
    await i.response.send_message(embed=make_embed("Updated",f"Server ID → `{sid}`",discord.Color.green()))

@tree.command(name="status")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def status(i):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    info=nitrado.get_server_info();players=nitrado.get_online_players()
    if not info:return await i.response.send_message(embed=make_embed("Status","❌ Failed",discord.Color.red()))
    gs=info.get("data",{}).get("gameserver",{})
    e=make_embed("Status")
    for k in ["name","status","slots","location","ip","port"]:
        e.add_field(name=k.capitalize(),value=f"`{gs.get(k,'?')}`",inline=True)
    e.add_field(name="Players",value=f"`{len(players) if players else 0}`",inline=True)
    e.add_field(name="Server ID",value=f"`{nitrado.sid}`",inline=True)
    await i.response.send_message(embed=e)

@tree.command(name="restartserver")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def restartserver(i):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    await i.response.defer()
    ok=nitrado.restart_server()
    await i.followup.send(embed=make_embed("Restart","🟠 Sent" if ok else "❌ Failed",discord.Color.orange()))

@tree.command(name="stopserver")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def stopserver(i):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    await i.response.defer()
    ok=nitrado.stop_server()
    await i.followup.send(embed=make_embed("Stop","🔴 Sent" if ok else "❌ Failed",discord.Color.red()))

@tree.command(name="startserver")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def startserver(i):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    await i.response.defer()
    ok=nitrado.start_server()
    await i.followup.send(embed=make_embed("Start","🟢 Sent" if ok else "❌ Failed",discord.Color.green()))

@tree.command(name="kick")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def kick(i,name:str):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    await i.response.defer()
    ok=nitrado.kick_player(name)
    await i.followup.send(embed=make_embed("Kick","🔴 Sent" if ok else "❌ Failed",discord.Color.red()))

@tree.command(name="ban")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ban(i,name:str):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    await i.response.defer()
    ok=nitrado.ban_player(name)
    await i.followup.send(embed=make_embed("Ban","🔴 Sent" if ok else "❌ Failed",discord.Color.red()))

@tree.command(name="unban")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def unban(i,name:str):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    await i.response.defer()
    ok=nitrado.unban_player(name)
    await i.followup.send(embed=make_embed("Unban","🟢 Sent" if ok else "❌ Failed",discord.Color.green()))

@tree.command(name="whitelist_add")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def whitelist_add(i,name:str):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    await i.response.defer()
    ok=nitrado.whitelist_add(name)
    await i.followup.send(embed=make_embed("Whitelist Add","🟢 Added" if ok else "❌ Failed",discord.Color.green()))

@tree.command(name="whitelist_remove")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def whitelist_remove(i,name:str):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    await i.response.defer()
    ok=nitrado.whitelist_remove(name)
    await i.followup.send(embed=make_embed("Whitelist Remove","🟠 Removed" if ok else "❌ Failed",discord.Color.orange()))

@tree.command(name="online")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def online(i):
    if not isinstance(i.user,discord.Member) or not user_is_admin(i.user):return await i.response.send_message("No permission",ephemeral=True)
    await i.response.defer()
    players=nitrado.get_online_players()
    if not players:return await i.followup.send(embed=make_embed("Online","No players"))
    lines=[f"- `{p.get('name','?')}` (ping {p.get('ping','?')})" for p in players]
    await i.followup.send(embed=make_embed("Online","\n".join(lines)))


@tree.command(name="debug_endpoints", description="Test all Nitrado player-action endpoints")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def debug_endpoints(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not user_is_admin(interaction.user):
        return await interaction.response.send_message("No permission", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    sid = nitrado.sid
    token = NITRADO_TOKEN
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    endpoints = {
        "ban": [
            f"/gameservers/players/ban",
            f"/gameservers/command/players/ban",
            f"/gameservers/games/players/ban"
        ],
        "kick": [
            f"/gameservers/players/kick",
            f"/gameservers/command/players/kick",
            f"/gameservers/games/players/kick"
        ],
        "unban": [
            f"/gameservers/players/unban",
            f"/gameservers/command/players/unban",
            f"/gameservers/games/players/unban"
        ],
        "whitelist_add": [
            f"/gameservers/players/whitelist/add",
            f"/gameservers/command/players/whitelist/add",
            f"/gameservers/games/players/whitelist/add"
        ]
    }

    results = []

    for action, paths in endpoints.items():
        for p in paths:
            url = f"https://api.nitrado.net/services/{sid}{p}"
            try:
                r = requests.post(url, headers=headers, json={"name": "TestName"})
                results.append(f"{action} → `{p}` → {r.status_code}")
            except Exception as e:
                results.append(f"{action} → `{p}` → ERROR: {str(e)}")

    embed = make_embed("Endpoint Debug Results", "\n".join(results), discord.Color.orange())
    await interaction.followup.send(embed=embed, ephemeral=True)


@app.route("/")
def index():return render_template("index.html")

def run_flask():app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)))
def run_bot():bot.run(TOKEN)

if __name__=="__main__":
    threading.Thread(target=run_flask,daemon=True).start()
    run_bot()

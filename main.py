import os,json,threading,logging
from datetime import datetime
from typing import Dict,Any,Optional,List
from flask import Flask,render_template
import discord
from discord.ext import commands
from discord import app_commands
import psycopg2,psycopg2.extras,requests
from ftplib import FTP
from io import BytesIO

logging.basicConfig(level=logging.INFO,format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
logger=logging.getLogger("mmcguard")

TOKEN=os.getenv("DISCORD_BOT_TOKEN","")
NITRADO_TOKEN=os.getenv("NITRADO_TOKEN",None)
GUILD_ID=int(os.getenv("DISCORD_GUILD_ID","1404279040893911103"))
ADMIN_ROLE_ID=int(os.getenv("DISCORD_ADMIN_ROLE_ID","1419520911471542413"))
DEFAULT_NITRADO_SERVER_ID=int(os.getenv("NITRADO_SERVER_ID","17649304"))
NITRADO_API_BASE="https://api.nitrado.net"

FTP_HOST=os.getenv("FTP_HOST","usmi121.gamedata.io")
FTP_PORT=int(os.getenv("FTP_PORT","21"))
FTP_USER=os.getenv("FTP_USER","ni9352260_806")
FTP_PASS=os.getenv("FTP_PASS","")
FTP_BANS_PATH=os.getenv("FTP_BANS_PATH","/dayzps/config/bans.txt")
FTP_WHITELIST_PATH=os.getenv("FTP_WHITELIST_PATH","/dayzps/config/whitelist.txt")

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
    cur.execute("SELECT id FROM bot_settings WHERE id = 1")
    if cur.fetchone() is None:cur.execute("INSERT INTO bot_settings VALUES (1,0,0,0,0,0,0)")
    cur.execute("SELECT id FROM nitrado_settings_v2 WHERE id = 1")
    if cur.fetchone() is None:cur.execute("INSERT INTO nitrado_settings_v2 VALUES (1,NULL,%s)",(DEFAULT_NITRADO_SERVER_ID,))
    conn.commit();cur.close();conn.close()
    return "Database initialized!"

def get_bot_settings()->Dict[str,int]:
    conn=get_db();cur=conn.cursor()
    cur.execute("SELECT * FROM bot_settings WHERE id = 1")
    row=cur.fetchone();cur.close();conn.close()
    if not row:
        return {"kill_feed_channel":0,"explosive_feed_channel":0,"connection_feed_channel":0,"zone_alert_channel":0,"general_log_channel":0,"admin_alert_channel":0}
    return {
        "kill_feed_channel":row["kill_feed_channel"] or 0,
        "explosive_feed_channel":row["explosive_feed_channel"] or 0,
        "connection_feed_channel":row["connection_feed_channel"] or 0,
        "zone_alert_channel":row["zone_alert_channel"] or 0,
        "general_log_channel":row["general_log_channel"] or 0,
        "admin_alert_channel":row["admin_alert_channel"] or 0,
    }

def update_bot_settings(d:Dict[str,Any])->None:
    conn=get_db();cur=conn.cursor()
    cur.execute(
        """UPDATE bot_settings SET
           kill_feed_channel=%s,explosive_feed_channel=%s,connection_feed_channel=%s,
           zone_alert_channel=%s,general_log_channel=%s,admin_alert_channel=%s
           WHERE id=1""",
        (
            int(d.get("kill_feed_channel",0) or 0),
            int(d.get("explosive_feed_channel",0) or 0),
            int(d.get("connection_feed_channel",0) or 0),
            int(d.get("zone_alert_channel",0) or 0),
            int(d.get("general_log_channel",0) or 0),
            int(d.get("admin_alert_channel",0) or 0),
        ),
    )
    conn.commit();cur.close();conn.close()

def get_nitrado_server_id()->int:
    conn=get_db();cur=conn.cursor()
    cur.execute("SELECT server_id FROM nitrado_settings_v2 WHERE id = 1")
    row=cur.fetchone();cur.close();conn.close()
    return int(row["server_id"]) if row and row.get("server_id") else DEFAULT_NITRADO_SERVER_ID

def set_nitrado_server_id(sid:int)->None:
    conn=get_db();cur=conn.cursor()
    cur.execute("UPDATE nitrado_settings_v2 SET server_id=%s WHERE id=1",(sid,))
    conn.commit();cur.close();conn.close()
    logger.info(f"Nitrado server ID updated to {sid}")

def get_all_zones()->List[Dict[str,Any]]:
    conn=get_db();cur=conn.cursor()
    cur.execute("SELECT * FROM zones ORDER BY id ASC")
    rows=cur.fetchall();cur.close();conn.close()
    return rows

def add_zone(name:str,action:str,points:Any)->None:
    conn=get_db();cur=conn.cursor()
    cur.execute("INSERT INTO zones (name,action,points) VALUES (%s,%s,%s)",(name,action,points))
    conn.commit();cur.close();conn.close()

def delete_zone(zone_id:int)->None:
    conn=get_db();cur=conn.cursor()
    cur.execute("DELETE FROM zones WHERE id=%s",(zone_id,))
    conn.commit();cur.close();conn.close()

def get_channel_by_id(cid:int)->Optional[discord.TextChannel]:
    if cid==0:return None
    guild=bot.get_guild(GUILD_ID)
    if not guild:return None
    return guild.get_channel(cid)

def reload_channel_settings()->Dict[str,Optional[discord.TextChannel]]:
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
    def __init__(self):self.base_url=NITRADO_API_BASE
    @property
    def server_id(self)->int:return get_nitrado_server_id()
    @property
    def token(self)->Optional[str]:return NITRADO_TOKEN
    @property
    def headers(self)->Dict[str,str]:
        return {} if not self.token else {
            "Authorization":f"Bearer {self.token}",
            "Accept":"application/json",
            "Content-Type":"application/json",
        }
    def _url(self,path:str)->str:return f"{self.base_url}/services/{self.server_id}{path}"
    def _post(self,path:str,payload:Dict[str,Any])->Optional[Dict[str,Any]]:
        if not self.token:return None
        try:
            r=requests.post(self._url(path),headers=self.headers,json=payload,timeout=10)
            if r.status_code==200:return r.json()
            logger.warning(f"Nitrado POST {path} failed: {r.status_code} {r.text}")
            return None
        except Exception as e:
            logger.exception(e);return None
    def _get(self,path:str)->Optional[Dict[str,Any]]:
        if not self.token:return None
        try:
            r=requests.get(self._url(path),headers=self.headers,timeout=10)
            if r.status_code==200:return r.json()
            logger.warning(f"Nitrado GET {path} failed: {r.status_code} {r.text}")
            return None
        except Exception as e:
            logger.exception(e);return None
    def get_server_info(self)->Optional[Dict[str,Any]]:return self._get("/gameservers")
    def get_online_players(self)->Optional[List[Dict[str,Any]]]:
        data=self._get("/gameservers/players")
        return None if not data else data.get("data",{}).get("players",[])
    def restart_server(self)->bool:return self._post("/gameservers/restart",{}) is not None
    def stop_server(self)->bool:return self._post("/gameservers/stop",{}) is not None
    def start_server(self)->bool:return self._post("/gameservers/start",{}) is not None

nitrado_api=NitradoAPI()

class DayZFTP:
    def __init__(self):
        self.host=FTP_HOST
        self.port=FTP_PORT
        self.user=FTP_USER
        self.password=FTP_PASS
    def _connect(self)->FTP:
        if not self.password:
            raise RuntimeError("FTP_PASS not set in environment")
        ftp=FTP()
        ftp.connect(self.host,self.port,timeout=10)
        ftp.login(self.user,self.password)
        return ftp
    def read_file(self,path:str)->str:
        ftp=self._connect()
        lines=[]
        try:
            ftp.retrlines(f"RETR {path}",lambda line:lines.append(line))
        finally:
            ftp.quit()
        return "\n".join(lines)+"\n" if lines else ""
    def write_file(self,path:str,content:str)->None:
        ftp=self._connect()
        try:
            bio=BytesIO(content.encode("utf-8"))
            ftp.storbinary(f"STOR {path}",bio)
        finally:
            ftp.quit()

ftp_client=DayZFTP()

def update_list_file(path:str,name:str,mode:str)->bool:
    try:
        try:
            content=ftp_client.read_file(path)
        except Exception:
            content=""
        lines=[l.strip() for l in content.splitlines() if l.strip()]
        lname=name.strip()
        if mode=="add":
            if lname not in lines:lines.append(lname)
        elif mode=="remove":
            lines=[l for l in lines if l.lower()!=lname.lower()]
        new_content="\n".join(lines)+"\n" if lines else ""
        ftp_client.write_file(path,new_content)
        return True
    except Exception as e:
        logger.exception(f"FTP update failed for {path}: {e}")
        return False

def ban_via_ftp(name:str)->bool:
    return update_list_file(FTP_BANS_PATH,name,"add")

def unban_via_ftp(name:str)->bool:
    return update_list_file(FTP_BANS_PATH,name,"remove")

def whitelist_add_via_ftp(name:str)->bool:
    return update_list_file(FTP_WHITELIST_PATH,name,"add")

def whitelist_remove_via_ftp(name:str)->bool:
    return update_list_file(FTP_WHITELIST_PATH,name,"remove")

def user_is_admin(member:discord.Member)->bool:
    return any(r.id==ADMIN_ROLE_ID for r in member.roles)

def make_embed(title:str,description:str="",color:discord.Color=discord.Color.blue())->discord.Embed:
    e=discord.Embed(title=title,description=description,color=color)
    e.timestamp=datetime.utcnow()
    return e

@bot.event
async def on_ready():
    guild=bot.get_guild(GUILD_ID)
    await tree.sync(guild=guild)
    cmds=await tree.fetch_commands()
    for c in cmds:await c.delete()
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")

@tree.command(name="checktoken",description="Check Nitrado token")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def checktoken(interaction:discord.Interaction):
    user=interaction.user
    if not isinstance(user,discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission to use this command.",ephemeral=True);return
    if not nitrado_api.token:
        await interaction.response.send_message(embed=make_embed("Nitrado Token Check","❌ No Nitrado token set in environment.",discord.Color.red()),ephemeral=True);return
    info=nitrado_api.get_server_info()
    embed=make_embed("Nitrado Token Check","✅ Token OK. Successfully contacted Nitrado API.",discord.Color.green()) if info else make_embed("Nitrado Token Check","❌ Token appears invalid or Nitrado API unreachable.",discord.Color.red())
    await interaction.response.send_message(embed=embed,ephemeral=True)

@tree.command(name="setserverid",description="Set the Nitrado server ID")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def setserverid(interaction:discord.Interaction,server_id:int):
    user=interaction.user
    if not isinstance(user,discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission to use this command.",ephemeral=True);return
    set_nitrado_server_id(server_id)
    await interaction.response.send_message(embed=make_embed("Server ID Updated",f"Nitrado server ID has been updated to `{server_id}`.",discord.Color.green()))

@tree.command(name="status",description="Show Nitrado server status")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def status(interaction:discord.Interaction):
    user=interaction.user
    if not isinstance(user,discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission to use this command.",ephemeral=True);return
    info=nitrado_api.get_server_info();players=nitrado_api.get_online_players()
    if not info:
        await interaction.response.send_message(embed=make_embed("Server Status","❌ Failed to retrieve server info from Nitrado.",discord.Color.red()));return
    gs=info.get("data",{}).get("gameserver",{})
    name=gs.get("name","Unknown");status_str=gs.get("status","Unknown");slots=gs.get("slots","Unknown")
    region=gs.get("location","Unknown");ip=gs.get("ip","Unknown");port=gs.get("port","Unknown")
    player_count=len(players) if players else 0
    e=make_embed("Server Status",color=discord.Color.blue())
    e.add_field(name="Name",value=f"`{name}`",inline=False)
    e.add_field(name="Status",value=f"`{status_str}`",inline=True)
    e.add_field(name="Slots",value=f"`{slots}`",inline=True)
    e.add_field(name="Region",value=f"`{region}`",inline=True)
    e.add_field(name="IP",value=f"`{ip}`",inline=True)
    e.add_field(name="Port",value=f"`{port}`",inline=True)
    e.add_field(name="Players Online",value=f"`{player_count}`",inline=True)
    e.add_field(name="Server ID",value=f"`{nitrado_api.server_id}`",inline=True)
    await interaction.response.send_message(embed=e)

@tree.command(name="restartserver",description="Restart the Nitrado server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def restartserver(interaction:discord.Interaction):
    user=interaction.user
    if not isinstance(user,discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission to use this command.",ephemeral=True);return
    await interaction.response.defer()
    ok=nitrado_api.restart_server()
    e=make_embed("Server Restart","🟠 Restart command sent to Nitrado.",discord.Color.orange()) if ok else make_embed("Server Restart","❌ Failed to send restart command to Nitrado.",discord.Color.red())
    await interaction.followup.send(embed=e)

@tree.command(name="stopserver",description="Stop the Nitrado server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def stopserver(interaction:discord.Interaction):
    user=interaction.user
    if not isinstance(user,discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission to use this command.",ephemeral=True);return
    await interaction.response.defer()
    ok=nitrado_api.stop_server()
    e=make_embed("Server Stop","🔴 Stop command sent to Nitrado.",discord.Color.red()) if ok else make_embed("Server Stop","❌ Failed to send stop command to Nitrado.",discord.Color.red())
    await interaction.followup.send(embed=e)

@tree.command(name="startserver",description="Start the Nitrado server")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def startserver(interaction:discord.Interaction):
    user=interaction.user
    if not isinstance(user,discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission to use this command.",ephemeral=True);return
    await interaction.response.defer()
    ok=nitrado_api.start_server()
    e=make_embed("Server Start","🟢 Start command sent to Nitrado.",discord.Color.green()) if ok else make_embed("Server Start","❌ Failed to send start command to Nitrado.",discord.Color.red())
    await interaction.followup.send(embed=e)

@tree.command(name="online",description="List online players")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def online(interaction:discord.Interaction):
    user=interaction.user
    if not isinstance(user,discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission to use this command.",ephemeral=True);return
    players=nitrado_api.get_online_players()
    if not players:
        await interaction.response.send_message(embed=make_embed("Online Players","No players are currently online.",discord.Color.blue()));return
    lines=[f"- `{p.get('name','Unknown')}` (ping: {p.get('ping','N/A')})" for p in players]
    await interaction.response.send_message(embed=make_embed("Online Players","\n".join(lines),discord.Color.blue()))

@tree.command(name="ban",description="Ban a player via FTP (instant)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ban(interaction:discord.Interaction,player_name:str):
    user=interaction.user
    if not isinstance(user,discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission to use this command.",ephemeral=True);return
    await interaction.response.defer(ephemeral=True)
    ok=ban_via_ftp(player_name)
    if not ok:
        await interaction.followup.send(embed=make_embed("Ban Player","❌ Failed to update bans.txt via FTP.",discord.Color.red()),ephemeral=True);return
    e=make_embed("Ban Player",f"🔴 Player `{player_name}` added to bans.txt (instant ban, no restart).",discord.Color.red())
    await interaction.followup.send(embed=e,ephemeral=True)

@tree.command(name="unban",description="Unban a player via FTP (instant)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def unban(interaction:discord.Interaction,player_name:str):
    user=interaction.user
    if not isinstance(user,discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission to use this command.",ephemeral=True);return
    await interaction.response.defer(ephemeral=True)
    ok=unban_via_ftp(player_name)
    if not ok:
        await interaction.followup.send(embed=make_embed("Unban Player","❌ Failed to update bans.txt via FTP.",discord.Color.red()),ephemeral=True);return
    e=make_embed("Unban Player",f"🟢 Player `{player_name}` removed from bans.txt (instant unban).",discord.Color.green())
    await interaction.followup.send(embed=e,ephemeral=True)

@tree.command(name="whitelist_add",description="Add a player to the whitelist via FTP (instant)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def whitelist_add_cmd(interaction:discord.Interaction,player_name:str):
    user=interaction.user
    if not isinstance(user,discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission to use this command.",ephemeral=True);return
    await interaction.response.defer(ephemeral=True)
    ok=whitelist_add_via_ftp(player_name)
    if not ok:
        await interaction.followup.send(embed=make_embed("Whitelist Add","❌ Failed to update whitelist.txt via FTP.",discord.Color.red()),ephemeral=True);return
    e=make_embed("Whitelist Add",f"🟢 Player `{player_name}` added to whitelist.txt (instant).",discord.Color.green())
    await interaction.followup.send(embed=e,ephemeral=True)

@tree.command(name="whitelist_remove",description="Remove a player from the whitelist via FTP (instant)")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def whitelist_remove_cmd(interaction:discord.Interaction,player_name:str):
    user=interaction.user
    if not isinstance(user,discord.Member) or not user_is_admin(user):
        await interaction.response.send_message("You do not have permission to use this command.",ephemeral=True);return
    await interaction.response.defer(ephemeral=True)
    ok=whitelist_remove_via_ftp(player_name)
    if not ok:
        await interaction.followup.send(embed=make_embed("Whitelist Remove","❌ Failed to update whitelist.txt via FTP.",discord.Color.red()),ephemeral=True);return
    e=make_embed("Whitelist Remove",f"🟠 Player `{player_name}` removed from whitelist.txt (instant).",discord.Color.orange())
    await interaction.followup.send(embed=e,ephemeral=True)

@app.route("/")
def index():return render_template("index.html")

def run_flask():app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
def run_bot():bot.run(TOKEN)

if __name__=="__main__":
    threading.Thread(target=run_flask,daemon=True).start()
    run_bot()

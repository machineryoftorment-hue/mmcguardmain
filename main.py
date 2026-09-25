import os
import discord
from discord.ext import commands
import requests

# ============================
# CONFIG
# ============================

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Azure / Copilot API config
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT")  # e.g. https://your-resource-name.openai.azure.com
AZURE_DEPLOYMENT = os.getenv("AZURE_DEPLOYMENT")  # e.g. copilot or gpt-4.1
AZURE_API_KEY = os.getenv("AZURE_API_KEY")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ============================
# AI REPAIR FUNCTION (COPILOT)
# ============================

def repair_file_with_ai(content: str) -> str:
    if not AZURE_ENDPOINT or not AZURE_DEPLOYMENT or not AZURE_API_KEY:
        return "AI ERROR: Azure endpoint, deployment, or API key not configured."

    url = f"{AZURE_ENDPOINT}/openai/deployments/{AZURE_DEPLOYMENT}/chat/completions?api-version=2024-02-01"

    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_API_KEY
    }

    data = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert JSON/XML repair engine. "
                    "Fix malformed JSON or XML while preserving ALL existing values and structure. "
                    "Do not invent new values. Do not remove objects. "
                    "Return ONLY the corrected file content."
                )
            },
            {
                "role": "user",
                "content": content
            }
        ],
        "temperature": 0,
        "max_tokens": 4096
    }

    resp = requests.post(url, headers=headers, json=data)
    resp.raise_for_status()
    result = resp.json()

    return result["choices"][0]["message"]["content"].strip()

# ============================
# !fixai COMMAND
# ============================

@bot.command()
async def fixai(ctx):
    if not ctx.message.attachments:
        await ctx.send("Attach a JSON or XML file to fix.")
        return

    attachment = ctx.message.attachments[0]
    raw_bytes = await attachment.read()
    raw_text = raw_bytes.decode("utf-8", errors="replace")

    await ctx.send("🛠 Sending file to Copilot for repair…")

    try:
        fixed = repair_file_with_ai(raw_text)
    except Exception as e:
        await ctx.send(f"❌ AI ERROR: {e}")
        return

    from io import BytesIO
    buf = BytesIO(fixed.encode("utf-8"))

    out_name = attachment.filename.replace(".", "_fixed.")
    await ctx.send(file=discord.File(buf, out_name))

# ============================
# BOT START
# ============================

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN is not set.")
    else:
        bot.run(BOT_TOKEN)

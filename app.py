from flask import Flask
from ftplib import FTP
from datetime import datetime, timedelta
import requests
import threading
import time
import os
import re

app = Flask(__name__)

print("VERSION = MMC TEST 999")

# Nitrado API
NITRADO_TOKEN = os.getenv("NITRADO_TOKEN")
SERVICE_ID = os.getenv("NITRADO_SERVICE_ID")

# FTP
FTP_HOST = os.getenv("FTP_HOST")
FTP_USER = os.getenv("FTP_USER")
FTP_PASS = os.getenv("FTP_PASS")

# Discord
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# Restart interval (hours)
RESTART_HOURS = 3

HEADERS = {
    "Authorization": f"Bearer {NITRADO_TOKEN}"
}


@app.route("/")
def home():
    return "DayZ Status Bot Online"


def get_server_info():
    try:

        url = f"https://api.nitrado.net/services/{SERVICE_ID}/gameservers"

        print("=" * 50)
        print("CHECKING NITRADO")
        print("SERVICE ID:", SERVICE_ID)

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15
        )

        print("HTTP STATUS:", response.status_code)

        if response.status_code != 200:
            print(response.text)
            return None

        return response.json()

    except Exception as e:
        print("NITRADO ERROR:", e)
        return None


def download_log():

    try:

        print("=" * 50)
        print("CONNECTING TO FTP")
        print("HOST:", FTP_HOST)
        print("USER:", FTP_USER)

        ftp = FTP(FTP_HOST)
        ftp.login(FTP_USER, FTP_PASS)

        print("FTP LOGIN SUCCESS")

        local_file = "restart.log"

        print("DOWNLOADING HARDCODED FILE: restart.log")

        with open(local_file, "wb") as f:
            ftp.retrbinary(
                "RETR restart.log",
                f.write
            )

        ftp.quit()

        print("LOG DOWNLOADED")

        return local_file

    except Exception as e:
        print("FTP ERROR:", e)
        return None


def get_last_restart():

    logfile = download_log()

    if not logfile:
        print("NO LOG FILE DOWNLOADED")
        return None

    with open(
        logfile,
        "r",
        encoding="utf-8",
        errors="ignore"
    ) as f:
        lines = f.readlines()

    restart_line = None

    for line in reversed(lines):

        if (
            "Server restart requested" in line
            or "Restart Command" in line
        ):
            restart_line = line.strip()
            break

    print("FOUND RESTART LINE:")
    print(restart_line)

    if not restart_line:
        return None

    match = re.search(
        r"(\d{4}-\d{2}-\d{2})\s(\d{2}:\d{2}:\d{2})",
        restart_line
    )

    if not match:
        print("FAILED TO PARSE RESTART LINE")
        return None

    try:

        dt_string = (
            f"{match.group(1)} "
            f"{match.group(2)}"
        )

        parsed = datetime.strptime(
            dt_string,
            "%Y-%m-%d %H:%M:%S"
        )

        print("LAST RESTART:", parsed)

        return parsed

    except Exception as e:

        print("PARSE ERROR:", e)
        return None


def format_timedelta(td):

    total = int(td.total_seconds())

    if total < 0:
        total = 0

    hours = total // 3600
    mins = (total % 3600) // 60

    return f"{hours}h {mins}m"


def post_status():

    print("BACKGROUND THREAD STARTED")

    while True:

        try:

            status = "🔴 Offline"
            players = 0
            max_players = 0
            server_name = "Unknown"

            server = get_server_info()

            if server:

                try:

                    gs = server["data"]["gameserver"]

                    status = "🟢 Online"

                    query = gs.get("query", {})

                    players = query.get(
                        "player_current",
                        0
                    )

                    max_players = query.get(
                        "player_max",
                        0
                    )

                    server_name = query.get(
                        "server_name",
                        "DayZ Server"
                    )

                    print(
                        f"Players: {players}/{max_players}"
                    )

                    print(
                        f"Server Name: {server_name}"
                    )

                except Exception as e:

                    print(
                        "JSON PARSE ERROR:",
                        e
                    )

            last_restart = get_last_restart()

            if last_restart:

                next_restart = (
                    last_restart +
                    timedelta(hours=RESTART_HOURS)
                )

                now = datetime.now()

                until_next = next_restart - now
                since_last = now - last_restart

                next_restart_text = format_timedelta(
                    until_next
                )

                last_restart_text = format_timedelta(
                    since_last
                )

            else:

                next_restart_text = "Unknown"
                last_restart_text = "Unknown"

            payload = {
                "embeds": [
                    {
                        "title": "🎮 MMC: Chernarus Rising",
                        "color": 3066993,
                        "fields": [
                            {
                                "name": "🟢 Server Status",
                                "value": status,
                                "inline": True
                            },
                            {
                                "name": "👥 Players",
                                "value": f"{players}/{max_players}",
                                "inline": True
                            },
                            {
                                "name": "⏰ Next Restart",
                                "value": next_restart_text,
                                "inline": True
                            },
                            {
                                "name": "🔄 Last Restart",
                                "value": f"{last_restart_text} ago",
                                "inline": True
                            },
                            {
                                "name": "📡 Server Name",
                                "value": server_name,
                                "inline": False
                            }
                        ],
                        "footer": {
                            "text": datetime.now().strftime(
                                "%d/%m/%Y %H:%M:%S"
                            )
                        }
                    }
                ]
            }

            requests.post(
                WEBHOOK_URL,
                json=payload,
                timeout=15
            )

            print("STATUS UPDATED")

        except Exception as e:

            print("UPDATE ERROR:", e)

        time.sleep(300)


print("STARTING DAYZ STATUS THREAD")

threading.Thread(
    target=post_status,
    daemon=True
).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )

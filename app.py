from flask import Flask
from ftplib import FTP
from datetime import datetime, timedelta
import requests
import threading
import time
import os
import re

app = Flask(__name__)

# Nitrado API
NITRADO_TOKEN = os.getenv("NITRADO_TOKEN")
SERVICE_ID = os.getenv("NITRADO_SERVICE_ID")

# FTP
FTP_HOST = os.getenv("FTP_HOST")
FTP_USER = os.getenv("FTP_USER")
FTP_PASS = os.getenv("FTP_PASS")
FTP_LOG_PATH = os.getenv(
    "FTP_LOG_PATH",
    "/games/dayzps/config/dayzserver.log"
)

# Discord
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# Restart interval
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

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15
        )

        if response.status_code != 200:
            return None

        return response.json()

    except Exception as e:
        print("Nitrado Error:", e)
        return None


def download_log():

    try:
        ftp = FTP(FTP_HOST)
        ftp.login(FTP_USER, FTP_PASS)

        local_file = "server.log"

        with open(local_file, "wb") as f:
            ftp.retrbinary(
                f"RETR {FTP_LOG_PATH}",
                f.write
            )

        ftp.quit()

        return local_file

    except Exception as e:
        print("FTP Error:", e)
        return None


def get_last_restart():

    logfile = download_log()

    if not logfile:
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

        if "Server restart requested" in line:
            restart_line = line.strip()
            break

    if restart_line is None:
        return None

    # Example:
    # Thu, 29 Oct 2026 23:17:47 +0100 Server restart requested (WINDOWS)

    match = re.search(
        r"(\d{1,2}\s\w+\s\d{4}\s\d{2}:\d{2}:\d{2})",
        restart_line
    )

    if not match:
        return None

    dt_string = match.group(1)

    try:
        return datetime.strptime(
            dt_string,
            "%d %b %Y %H:%M:%S"
        )
    except:
        return None


def format_timedelta(td):

    total = int(td.total_seconds())

    if total < 0:
        total = 0

    hours = total // 3600
    mins = (total % 3600) // 60

    return f"{hours}h {mins}m"


def post_status():

    while True:

        try:

            status = "🔴 Offline"
            players = 0
            max_players = 0
            server_name = "Unknown"

            server = get_server_info()

            if server:

                gs = server["data"]["gameserver"]

                status = "🟢 Online"

                players = gs.get(
                    "player_current",
                    0
                )

                max_players = gs.get(
                    "player_limit",
                    0
                )

                server_name = gs.get(
                    "hostname",
                    "DayZ Server"
                )

            last_restart = get_last_restart()

            if last_restart:

                next_restart = (
                    last_restart +
                    timedelta(
                        hours=RESTART_HOURS
                    )
                )

                now = datetime.now()

                until_next = (
                    next_restart - now
                )

                since_last = (
                    now - last_restart
                )

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
                        "title":
                        "🎮 UNITY PVE Nasdara",
                        "color": 3066993,
                        "fields": [
                            {
                                "name":
                                "Server Status",
                                "value":
                                status,
                                "inline":
                                True
                            },
                            {
                                "name":
                                "Players Online",
                                "value":
                                f"{players}/{max_players}",
                                "inline":
                                True
                            },
                            {
                                "name":
                                "Next Restart",
                                "value":
                                next_restart_text,
                                "inline":
                                True
                            },
                            {
                                "name":
                                "Last Restart",
                                "value":
                                f"{last_restart_text} ago",
                                "inline":
                                True
                            },
                            {
                                "name":
                                "Server",
                                "value":
                                server_name,
                                "inline":
                                False
                            }
                        ],
                        "footer": {
                            "text":
                            datetime.now().strftime(
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

            print("Status Updated")

        except Exception as e:
            print("Update Error:", e)

        time.sleep(300)


threading.Thread(
    target=post_status,
    daemon=True
).start()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000
    )

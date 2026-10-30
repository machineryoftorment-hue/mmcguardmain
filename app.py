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
    "/games/ni9352260_806/ftproot/restart.log"
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

        print("=" * 50)
        print("CHECKING NITRADO")
        print("SERVICE ID:", SERVICE_ID)
        print("TOKEN EXISTS:", bool(NITRADO_TOKEN))

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15
        )

        print("HTTP STATUS:", response.status_code)
        print("RAW RESPONSE:")
        print(response.text[:5000])

        if response.status_code != 200:
            print("NITRADO REQUEST FAILED")
            return None

        data = response.json()

        print("JSON RESPONSE:")
        print(data)

        return data

    except Exception as e:
        print("NITRADO ERROR:", e)
        return None


def download_log():

    try:

        print("=" * 50)
        print("CONNECTING TO FTP")
        print("HOST:", FTP_HOST)
        print("USER:", FTP_USER)
        print("PATH:", FTP_LOG_PATH)

        ftp = FTP(FTP_HOST)
        ftp.login(FTP_USER, FTP_PASS)

        print("FTP LOGIN SUCCESS")

        local_file = "server.log"

        with open(local_file, "wb") as f:
            ftp.retrbinary(
                f"RETR {FTP_LOG_PATH}",
                f.write
            )

        print("LOG DOWNLOADED")

        ftp.quit()

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

    print("LOG LINES:", len(lines))

    print("LAST 20 LINES")

    for line in lines[-20:]:
        print(line.strip())

    restart_line = None

    for line in reversed(lines):

        if "Server restart requested" in line:
            restart_line = line.strip()
            break

    print("FOUND RESTART LINE:")
    print(restart_line)

    if restart_line is None:
        return None

    match = re.search(
        r"(\d{1,2}\s\w+\s\d{4}\s\d{2}:\d{2}:\d{2})",
        restart_line
    )

    if not match:
        print("REGEX FAILED")
        return None

    dt_string = match.group(1)

    print("PARSED DATE:", dt_string)

    try:

        parsed = datetime.strptime(
            dt_string,
            "%d %b %Y %H:%M:%S"
        )

        print("LAST RESTART:", parsed)

        return parsed

    except Exception as e:

        print("DATE PARSE FAILED:", e)
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

                print("SERVER DATA FOUND")

                try:
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

                except Exception as e:
                    print("JSON PARSE ERROR:", e)

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
                        "title": "🎮 MMC Chernarus Rising",
                        "color": 3066993,
                        "fields": [
                            {
                                "name": "Server Status",
                                "value": status,
                                "inline": True
                            },
                            {
                                "name": "Players Online",
                                "value": f"{players}/{max_players}",
                                "inline": True
                            },
                            {
                                "name": "Next Restart",
                                "value": next_restart_text,
                                "inline": True
                            },
                            {
                                "name": "Last Restart",
                                "value": f"{last_restart_text} ago",
                                "inline": True
                            },
                            {
                                "name": "Server",
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
    app.run(
        host="0.0.0.0",
        port=5000
    )

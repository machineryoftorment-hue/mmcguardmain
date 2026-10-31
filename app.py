from flask import Flask
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
import threading
import time
import os

app = Flask(__name__)

print("MMC FIXED SCHEDULE VERSION")

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# Frankfurt timezone
FRANKFURT = ZoneInfo("Europe/Berlin")

# Based on your log pattern
RESTART_INTERVAL = timedelta(minutes=197)

# Last known automatic restart from logs
BASE_RESTART = datetime(
    2026,
    10,
    31,
    1,
    35,
    43,
    tzinfo=FRANKFURT
)


@app.route("/")
def home():
    return "MMC Restart Tracker Online"


def format_timedelta(td):

    total = int(td.total_seconds())

    if total < 0:
        total = 0

    hours = total // 3600
    minutes = (total % 3600) // 60

    return f"{hours}h {minutes}m"


def get_restart_times():

    now = datetime.now(FRANKFURT)

    last_restart = BASE_RESTART

    while (last_restart + RESTART_INTERVAL) <= now:
        last_restart += RESTART_INTERVAL

    next_restart = last_restart + RESTART_INTERVAL

    return last_restart, next_restart


def post_status():

    print("BACKGROUND THREAD STARTED")

    while True:

        try:

            now = datetime.now(FRANKFURT)

            last_restart, next_restart = get_restart_times()

            time_since = now - last_restart
            time_until = next_restart - now

            next_timestamp = int(next_restart.timestamp())
            last_timestamp = int(last_restart.timestamp())

            payload = {
                "embeds": [
                    {
                        "title": "🎮 UNITY PVE Nasdara",
                        "color": 3066993,
                        "fields": [
                            {
                                "name": "🟢 Server Status",
                                "value": "Online",
                                "inline": True
                            },
                            {
                                "name": "⏰ Next Restart",
                                "value": (
                                    f"<t:{next_timestamp}:R>\n"
                                    f"<t:{next_timestamp}:F>"
                                ),
                                "inline": False
                            },
                            {
                                "name": "🔄 Last Restart",
                                "value": (
                                    f"<t:{last_timestamp}:R>\n"
                                    f"<t:{last_timestamp}:F>"
                                ),
                                "inline": False
                            },
                            {
                                "name": "📊 Time Until Restart",
                                "value": format_timedelta(time_until),
                                "inline": True
                            },
                            {
                                "name": "📈 Time Since Restart",
                                "value": format_timedelta(time_since),
                                "inline": True
                            },
                            {
                                "name": "🌍 Schedule Timezone",
                                "value": "Europe/Berlin (Frankfurt)",
                                "inline": False
                            }
                        ],
                        "footer": {
                            "text":
                            f"Updated {now.strftime('%d/%m/%Y %H:%M:%S')}"
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


print("STARTING THREAD")

threading.Thread(
    target=post_status,
    daemon=True
).start()


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )

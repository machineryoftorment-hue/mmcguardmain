from flask import Flask
from datetime import datetime, timedelta
import requests
import threading
import time
import os

app = Flask(__name__)

print("MMC RESTART TIMER VERSION")

# Discord Webhook
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

# Average restart interval from logs
RESTART_INTERVAL = timedelta(minutes=197)

# Last known startup from logs
BASE_RESTART = datetime(
    2026, 10, 31,
    1, 35, 43
)


@app.route("/")
def home():
    return "DayZ Status Bot Online"


def format_timedelta(td):

    total = int(td.total_seconds())

    if total < 0:
        total = 0

    hours = total // 3600
    minutes = (total % 3600) // 60

    return f"{hours}h {minutes}m"


def get_restart_times():

    now = datetime.now()

    last_restart = BASE_RESTART

    while (
        last_restart + RESTART_INTERVAL
    ) <= now:

        last_restart += RESTART_INTERVAL

    next_restart = (
        last_restart + RESTART_INTERVAL
    )

    return (
        last_restart,
        next_restart
    )


def post_status():

    print(
        "BACKGROUND THREAD STARTED"
    )

    while True:

        try:

            now = datetime.now()

            (
                last_restart,
                next_restart
            ) = get_restart_times()

            time_since = (
                now - last_restart
            )

            time_until = (
                next_restart - now
            )

            payload = {
                "embeds": [
                    {
                        "title":
                        "🎮 MMC: Chernarus Rising",

                        "color":
                        3066993,

                        "fields": [
                            {
                                "name":
                                "🟢 Server Status",

                                "value":
                                "Online",

                                "inline":
                                True
                            },
                            {
                                "name":
                                "👥 Players",

                                "value":
                                "Unknown",

                                "inline":
                                True
                            },
                            {
                                "name":
                                "⏰ Next Restart",

                                "value":
                                format_timedelta(
                                    time_until
                                ),

                                "inline":
                                True
                            },
                            {
                                "name":
                                "🔄 Last Restart",

                                "value":
                                f"{format_timedelta(time_since)} ago",

                                "inline":
                                True
                            },
                            {
                                "name":
                                "🕒 Restart Interval",

                                "value":
                                "3h 17m",

                                "inline":
                                True
                            }
                        ],

                        "footer": {
                            "text":
                            now.strftime(
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

            print(
                "STATUS UPDATED"
            )

        except Exception as e:

            print(
                "UPDATE ERROR:",
                e
            )

        time.sleep(300)


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

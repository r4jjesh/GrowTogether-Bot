# keep_alive.py
import os
import threading
import time
import requests

def keep_alive():
    """
    Ping the public Render URL every 10 minutes so the instance never sleeps.
    Uses RENDER_EXTERNAL_URL (set in Render → Environment).
    """
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        print("Warning: RENDER_EXTERNAL_URL not set – keep-alive disabled")
        return

    def _ping():
        while True:
            try:
                r = requests.get(url, timeout=5)
                print(f"[{time.strftime('%X')}] Keep-alive ping → {url} [{r.status_code}]")
            except Exception as e:
                print(f"Keep-alive ping failed: {e}")
            time.sleep(600)   # 10 min

    t = threading.Thread(target=_ping, daemon=True)
    t.start()

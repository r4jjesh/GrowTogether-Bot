# keep_alive.py
from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route("/")
def home():
    return "✅ GrowTogether Bot is alive and running!"

def run():
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

def keep_alive():
    thread = Thread(target=run)
    thread.daemon = True
    thread.start()

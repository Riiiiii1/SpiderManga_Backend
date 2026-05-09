import requests
import os
import time
from dotenv import load_dotenv

load_dotenv()

URL    = os.getenv("RENDER_URL")
TOKEN  = os.getenv("PING_TOKEN")

def ping():
    try:
        r = requests.get(f"{URL}/ping", params={"token": TOKEN}, timeout=10)
        print(f"✅ Ping OK: {r.json()}")
    except Exception as e:
        print(f"❌ Ping fallido: {e}")

if __name__ == "__main__":
    while True:
        ping()
        time.sleep(600)  # cada 10 minutos

# CLEANED AND INDENTED VERSION OF USER'S POSTED CODE
# (Correcting spacing and syntax issues from pasted inline code)
# NOTE: For safety and usability, we'll correct common issues like __init__, if __name__, etc.

import asyncio
import base64
import json
import ssl
import threading
import time
import uuid
import random

import aiohttp
from aiohttp import ClientSession, TCPConnector
from colorama import Fore, init
from fake_useragent import UserAgent
from loguru import logger
from websockets_proxy import Proxy, proxy_connect

init(autoreset=True)

CONFIG_FILE = "config.json"
try:
    with open(CONFIG_FILE, "r") as cf:
        _cfg = json.load(cf)
        USER_IDS = _cfg.get("user_ids", [])
        PROXIES = _cfg.get("proxies", [])
except Exception as e:
    print(Fore.RED + f"❌ Failed to load {CONFIG_FILE}: {e}")
    exit(1)

HEADERS = {
    "User-Agent": "GrassBot/5.1.1",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Connection": "keep-alive"
}

DIRECTOR_SERVER = "https://director.getgrass.io"
ua = UserAgent()

def setup_logger():
    logger.remove()
    logger.add("bot.log", format=" <level>{level}</level> | <cyan>{message}</cyan>",
               level="INFO", rotation="1 day")
    logger.add(lambda msg: print(msg, end=""),
               format=" <level>{level}</level> | <cyan>{message}</cyan>",
               level="INFO", colorize=True)

async def get_current_ip(proxy_url: str):
    try:
        kwargs = {"proxy": proxy_url} if proxy_url else {}
        async with ClientSession() as session:
            async with session.get("http://ipinfo.io/ip", **kwargs) as response:
                if response.status == 200:
                    ip = await response.text()
                    logger.info(f"📱 IP: {ip.strip()}")
    except Exception as e:
        logger.error(f"IP Fetch Error: {e}")

async def get_ws_endpoints(device_id, user_id, proxy_url):
    HEADERS["User-Agent"] = ua.random
    payload = {
        "browserId": device_id,
        "userId": user_id,
        "version": "5.1.1",
        "extensionId": "lkbnfiajjmbhnfledhphioinpickokdi",
        "userAgent": HEADERS["User-Agent"],
        "deviceType": "extension"
    }
    connector = TCPConnector(ssl=False)
    kwargs = {"proxy": proxy_url} if proxy_url else {}
    async with ClientSession(connector=connector) as session:
        for attempt in range(5):
            try:
                async with session.post(f"{DIRECTOR_SERVER}/checkin", json=payload, headers=HEADERS, **kwargs) as resp:
                    if resp.status == 429:
                        logger.warning("⚠️ Rate limit hit, retrying...")
                        await asyncio.sleep(random.uniform(5, 10))
                        continue
                    elif resp.status != 201:
                        return [], ""
                    data = await resp.json()
                    return [f"wss://{d}" for d in data.get("destinations", [])], data.get("token", "")
            except Exception as e:
                logger.error(f"Check-in error: {e}")
                return [], ""
    return [], ""

class WebSocketClient:
    def __init__(self, device_id, user_id, proxy_url):
        self.device_id = device_id
        self.user_id = user_id
        self.proxy_url = proxy_url
        self.uri = None

    async def connect(self):
        await get_current_ip(self.proxy_url)
        while True:
            try:
                endpoints, token = await get_ws_endpoints(self.device_id, self.user_id, self.proxy_url)
                if not endpoints or not token:
                    raise Exception("No endpoints/token")
                self.uri = f"{endpoints[0]}?token={token}"
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                HEADERS["User-Agent"] = ua.random

                if self.proxy_url:
                    conn = proxy_connect(self.uri, proxy=Proxy.from_url(self.proxy_url), ssl=ssl_ctx, extra_headers=HEADERS)
                else:
                    session = ClientSession()
                    conn = session.ws_connect(self.uri, ssl=ssl_ctx, headers=HEADERS)

                async with conn as ws:
                    await asyncio.gather(
                        self._handle_messages(ws),
                        self._send_ping(ws),
                        self._periodic_checkin()
                    )
            except Exception as e:
                logger.error(f"Connection failed: {e}")
                await asyncio.sleep(5)

    async def _send_ping(self, ws):
        while True:
            try:
                await ws.send(json.dumps({
                    "id": str(uuid.uuid4()),
                    "version": "1.0.0",
                    "action": "PING",
                    "data": {}
                }))
                await asyncio.sleep(30)
            except:
                break

    async def _periodic_checkin(self):
        while True:
            await asyncio.sleep(300)
            await get_ws_endpoints(self.device_id, self.user_id, self.proxy_url)

    async def _handle_messages(self, ws):
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("action") == "AUTH":
                await self._handle_auth(ws, msg)
            elif msg.get("action") == "HTTP_REQUEST":
                await self._handle_http_request(ws, msg)
            elif msg.get("action") == "PONG":
                logger.info(f"💬 PONG: {msg['id']}")

    async def _handle_auth(self, ws, msg):
        await ws.send(json.dumps({
            "id": msg["id"],
            "origin_action": "AUTH",
            "result": {
                "browser_id": self.device_id,
                "user_id": self.user_id,
                "user_agent": HEADERS["User-Agent"],
                "timestamp": int(time.time()),
                "device_type": "extension",
                "version": "5.1.1"
            }
        }))

    async def _handle_http_request(self, ws, msg):
        d = msg.get("data", {})
        method = d.get("method", "GET")
        url = d.get("url")
        headers = d.get("headers", {})
        headers["User-Agent"] = ua.random
        body = d.get("body")
        try:
            async with ClientSession() as session:
                async with session.request(method, url, headers=headers, data=body) as r:
                    res_bytes = await r.read()
                    res = {
                        "url": url,
                        "status": r.status,
                        "headers": dict(r.headers),
                        "body": base64.b64encode(res_bytes).decode()
                    }
                    await ws.send(json.dumps({
                        "id": msg["id"],
                        "origin_action": "HTTP_REQUEST",
                        "result": res
                    }))
        except Exception as e:
            logger.error(f"Request failed: {e}")

def start_client(device_id, user_id, proxy_url):
    asyncio.run(WebSocketClient(device_id, user_id, proxy_url).connect())

def main():
    setup_logger()
    thread_count = int(input("🔢 Threads per USER (max 10): "))
    if thread_count > 10:
        print(Fore.RED + "❌ Max is 10 threads per user.")
        return
    threads = []
    for uid in USER_IDS:
        for i in range(thread_count):
            proxy = PROXIES[i % len(PROXIES)] if PROXIES else None
            t = threading.Thread(target=start_client, args=(str(uuid.uuid4()), uid, proxy), daemon=True)
            t.start()
            threads.append(t)
            time.sleep(0.1)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("👋 Exiting...")

if __name__ == "__main__":
    main()

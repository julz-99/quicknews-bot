"""
MACRO INTELLIGENCE BOT — QuickNews Channel
Fixed version with proper async handling for Railway
"""

import json
import logging
import hashlib
import schedule
import time
import requests
import feedparser
import threading
from datetime import datetime, timezone
from flask import Flask
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN  = "8588761754:AAGQM5I8eISCNfpz351S9ObWE1dIYLjWfjI"
TELEGRAM_CHANNEL_ID = "-1003956815615"
ANTHROPIC_API_KEY   = "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"
MIN_SCORE = 7

RSS_FEEDS = [
    {"name": "Reuters Markets",  "url": "https://feeds.reuters.com/reuters/marketsNews"},
    {"name": "Reuters Business", "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "ForexLive",        "url": "https://www.forexlive.com/feed/news"},
    {"name": "Investing.com",    "url": "https://www.investing.com/rss/news.rss"},
    {"name": "MarketWatch",      "url": "https://feeds.content.dowjones.io/public/rss/mktw_realtimeheadlines"},
    {"name": "FX Street",        "url": "https://www.fxstreet.com/rss/news"},
]

def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHANNEL_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info("Telegram message sent")
        else:
            log.error(f"Telegram error {resp.status_code}: {resp.text}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

claude = Anthropic(api_key=ANTHROPIC_API_KEY)

PROMPT = """You are a macro trading analyst. Analyse this news and respond ONLY with valid JSON, no markdown.

News: "{text}"
Source: {source}

{{"score": <1-10>, "headline": "<max 8 words>", "reason": "<one sentence>", "affected_pairs": ["EURUSD"], "direction": {{"EURUSD": "bearish"}}, "trader_action": "<exit_longs|exit_shorts|avoid_entry|stay_flat|monitor>"}}

Score: 1-3=noise, 4-6=minor, 7-8=significant 0.3-1% move, 9-10=extreme (tariffs/rate decisions/war)"""

def score_news(text, source):
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": PROMPT.format(text=text[:400], source=source)}]
        )
        raw = response.content[0].text.strip().replace("```json","").replace("```","").strip()
        return json.loads(raw)
    except Exception as e:
        log.error(f"AI error: {e}")
        return None

def format_alert(text, source, impact):
    score = impact.get("score", 0)
    headline = impact.get("headline", text[:80])
    reason = impact.get("reason", "")
    pairs = impact.get("affected_pairs", [])
    direction = impact.get("direction", {})
    action = impact.get("trader_action", "monitor")
    timestamp = datetime.now(timezone.utc).strftime("%H:%M UTC  %d %b")
    if score >= 9:
        header = "🔴🔴 <b>CRITICAL — HIGH IMPACT NEWS</b> 🔴🔴"
    elif score >= 8:
        header = "🔴 <b>HIGH IMPACT NEWS</b>"
    else:
        header = "🟠 <b>MARKET MOVING NEWS</b>"
    pairs_text = ""
    for pair in pairs:
        d = direction.get(pair, "")
        emoji = "📈" if "bull" in d.lower() else "📉" if "bear" in d.lower() else "↔️"
        pairs_text += f"  {emoji} <b>{pair}</b> — {d}\n"
    action_map = {"exit_longs": "🚪 Exit your long positions now", "exit_shorts": "🚪 Exit your short positions now", "avoid_entry": "⛔ Do not enter any new trades", "stay_flat": "🔲 Stay flat — no trades", "monitor": "👀 Monitor price reaction"}
    return (f"{header}\n━━━━━━━━━━━━━━━━━━━━\n📰 <b>{headline}</b>\n\n<b>Why it matters:</b>\n{reason}\n\n<b>Pairs affected:</b>\n{pairs_text}\n<b>What to do:</b>\n{action_map.get(action,'👀 Monitor')}\n\n<b>Impact:</b> {score}/10  |  <b>Source:</b> {source}\n━━━━━━━━━━━━━━━━━━━━\n<i>{timestamp}</i>")

sent_cache = set()

def already_sent(text):
    h = hashlib.md5(text[:120].encode()).hexdigest()
    if h in sent_cache:
        return True
    sent_cache.add(h)
    if len(sent_cache) > 2000:
        for x in list(sent_cache)[:500]:
            sent_cache.discard(x)
    return False

def process(text, source):
    if already_sent(text):
        return
    log.info(f"Scoring [{source}]: {text[:80]}")
    impact = score_news(text, source)
    if not impact:
        return
    score = impact.get("score", 0)
    log.info(f"Score {score}/10")
    if score < MIN_SCORE:
        return
    send_telegram(format_alert(text, source, impact))

rss_seen = set()

def poll_feeds():
    log.info("Polling RSS feeds...")
    for feed in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed["url"])
            for entry in parsed.entries[:5]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                text = f"{title}. {summary[:200]}" if summary else title
                uid = entry.get("id") or entry.get("link") or title
                h = hashlib.md5(uid.encode()).hexdigest()
                if h in rss_seen:
                    continue
                rss_seen.add(h)
                process(text, feed["name"])
        except Exception as e:
            log.error(f"RSS error ({feed['name']}): {e}")

def fetch_events():
    try:
        resp = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return [e for e in resp.json() if e.get("date","")[:10] == today and e.get("impact") == "High"]
    except:
        return []

def check_calendar():
    now = datetime.now(timezone.utc)
    for ev in fetch_events():
        try:
            et = datetime.strptime(f"{now.strftime('%Y-%m-%d')} {ev['time']}", "%Y-%m-%d %I:%M%p").replace(tzinfo=timezone.utc)
            diff = (et - now).total_seconds() / 60
            if 28 <= diff <= 32:
                send_telegram(f"⚠️ <b>RED NEWS IN 30 MINUTES</b>\n━━━━━━━━━━━━━━━━━━━━\n📅 <b>{ev.get('title','')}</b>\n💱 <b>Currency:</b> {ev.get('country','')}\n⏰ <b>Releases at:</b> {ev.get('time','')} UTC\n\nClose positions or move to BE before this drops.")
        except:
            continue

def daily_briefing():
    events = fetch_events()
    lines = "\n".join([f"  🔴 {e.get('time','')} {e.get('country','')} — <b>{e.get('title','')}</b>" for e in events]) or "  ✅ No high-impact events today"
    send_telegram(f"☀️ <b>DAILY BRIEFING — {datetime.now(timezone.utc).strftime('%A %d %B %Y')}</b>\n━━━━━━━━━━━━━━━━━━━━\n<b>High impact events today:</b>\n{lines}\n\n<b>Sessions (UK time):</b>\n  🟢 Frankfurt  07:00 – 07:40\n  🟢 London     08:00 – 10:00\n  ⛔ Lunch      10:00 – 12:00\n  🟢 New York   13:00 – 15:00\n\n<i>Only news scoring {MIN_SCORE}+/10 will be sent today.</i>")

def run_scheduler():
    schedule.every(3).minutes.do(poll_feeds)
    schedule.every(1).minutes.do(check_calendar)
    schedule.every().day.at("06:30").do(daily_briefing)
    while True:
        schedule.run_pending()
        time.sleep(20)

app = Flask(__name__)

@app.route("/")
def home():
    return "Macro Intelligence Bot is running", 200

if __name__ == "__main__":
    log.info("Starting Macro Intelligence Bot...")
    send_telegram("✅ <b>Macro Intelligence Bot — ONLINE</b>\n━━━━━━━━━━━━━━━━━━━━\nMonitoring <b>6</b> live news feeds\nAI filter: only <b>7+/10</b> impact gets through\nEconomic calendar warnings: <b>ON</b>\n━━━━━━━━━━━━━━━━━━━━\n<i>Reuters · ForexLive · MarketWatch · FX Street</i>")
    daily_briefing()
    threading.Thread(target=run_scheduler, daemon=True).start()
    poll_feeds()
    app.run(host="0.0.0.0", port=8080, debug=False)

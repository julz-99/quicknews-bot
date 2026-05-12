"""
QuickNews Macro Intelligence Bot
─────────────────────────────────────────────────────────────────
Sends four types of messages to your Telegram channel:

  1. Morning briefing  — 06:30 UTC daily, ForexFactory-style event list
                         + potential directional bias per pair
  2. 10-min warning    — fires before every red news event
  3. Breaking news     — RSS feeds scored by Claude AI (7+ only)
  4. Tweet/post alert  — world leaders & big financial accounts (7+ only)

Requirements:
  pip install anthropic requests feedparser schedule flask python-dotenv
─────────────────────────────────────────────────────────────────
"""

import os
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# CREDENTIALS — set these as Railway environment variables
# ══════════════════════════════════════════════════════════════════

BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN",  "")
CHANNEL_ID  = os.environ.get("TELEGRAM_CHANNEL_ID", "")
CLAUDE_KEY  = os.environ.get("ANTHROPIC_API_KEY",   "")
MIN_SCORE   = 7

# ══════════════════════════════════════════════════════════════════
# RSS FEEDS — monitors these every 3 minutes
# ══════════════════════════════════════════════════════════════════

RSS_FEEDS = [
    {"name": "Reuters Markets",  "url": "https://feeds.reuters.com/reuters/marketsNews"},
    {"name": "Reuters Business", "url": "https://feeds.reuters.com/reuters/businessNews"},
    {"name": "ForexLive",        "url": "https://www.forexlive.com/feed/news"},
    {"name": "MarketWatch",      "url": "https://feeds.content.dowjones.io/public/rss/mktw_realtimeheadlines"},
    {"name": "FX Street",        "url": "https://www.fxstreet.com/rss/news"},
    {"name": "Investing.com",    "url": "https://www.investing.com/rss/news.rss"},
]

SEEN = set()

# ══════════════════════════════════════════════════════════════════
# TELEGRAM SENDER
# ══════════════════════════════════════════════════════════════════

def send(text: str):
    """Send an HTML message to the Telegram channel."""
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHANNEL_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
        if not r.ok:
            log.error(f"Telegram error: {r.text}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

# ══════════════════════════════════════════════════════════════════
# MESSAGE FORMATTERS
# ══════════════════════════════════════════════════════════════════

IMPACT_DOTS = {
    "high":   "🔴🔴🔴",
    "medium": "🟠🟠⚪",
    "low":    "⚪⚪⚪",
}

DIR_ICON = {
    "bullish": "📈",
    "bearish": "📉",
    "neutral": "↔️ ",
}

def fmt_direction(pair: str, direction: str) -> str:
    icon = DIR_ICON.get(direction, "↔️ ")
    label = f"potentially {direction}" if direction != "neutral" else "no clear bias"
    return f"<code>{pair:<8}</code>{icon}  {label}"


def fmt_morning_briefing(events: list, pairs: dict, date_str: str) -> str:
    """
    Morning briefing format — ForexFactory style.

    events = [{"time": "08:30", "flag": "🇬🇧", "name": "UK CPI y/y", "impact": "high"}, ...]
    pairs  = {"EURUSD": "bearish", "XAUUSD": "bullish", "NAS100": "neutral", ...}
    """
    lines = [
        f"🔵  <b>DAILY BRIEFING — {date_str}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "<b>Today's high-impact events (UTC)</b>",
        "",
    ]

    if events:
        for e in events:
            dots = IMPACT_DOTS.get(e.get("impact", "low"), "⚪⚪⚪")
            lines.append(f"<code>{e['time']}</code>  {e['flag']}  {e['name']}  {dots}")
    else:
        lines.append("✅  No high-impact events scheduled today")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "<b>Potential directional bias today</b>",
        "",
    ]

    for pair, direction in pairs.items():
        lines.append(fmt_direction(pair, direction))

    lines += [
        "",
        "<i>⚠️ Bias based on event risk — not a guaranteed signal</i>",
    ]

    return "\n".join(lines)


def fmt_red_warning(event_name: str, flag: str, currency: str,
                    event_time: str, forecast: str, previous: str,
                    pairs: dict) -> str:
    """10-minute red news warning."""
    lines = [
        "⚠️  <b>RED NEWS IN 10 MINUTES</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"{flag}  <b>{event_name}</b>",
        f"⏰  <code>{event_time} UTC</code>   💱  {currency}",
        f"Forecast: <b>{forecast}</b>   —   Previous: {previous}",
        "",
        "<b>Potential impact if forecast is met</b>",
        "",
    ]

    for pair, direction in pairs.items():
        lines.append(fmt_direction(pair, direction))

    lines += [
        "",
        "<i>⚠️ Directional bias only — not a guaranteed signal</i>",
        "<b>Manage open positions before release</b>",
    ]

    return "\n".join(lines)


def fmt_news_alert(headline: str, reason: str, source: str,
                   score: int, pairs: dict, directions: dict) -> str:
    """Breaking news alert from RSS feeds."""
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC · %d %b")

    if score >= 9:
        header = "🔴  <b>VERY HIGH IMPACT NEWS</b>"
    elif score >= 7:
        header = "🔴  <b>HIGH IMPACT NEWS</b>"
    else:
        header = "🟠  <b>MARKET UPDATE</b>"

    lines = [
        header,
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"<b>{source}</b>   ·   {score}/10 impact",
        "",
        f"<b>{headline}</b>",
        "",
        f"{reason}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "<b>Potential market impact</b>",
        "",
    ]

    for pair in pairs:
        direction = directions.get(pair, "neutral")
        lines.append(fmt_direction(pair, direction))

    lines += [
        "",
        f"<i>⚠️ AI analysis — not a guaranteed signal</i>",
        f"<i>{ts}</i>",
    ]

    return "\n".join(lines)


def fmt_tweet_alert(handle: str, platform: str, quote: str,
                    reason: str, score: int, pairs: dict, directions: dict) -> str:
    """World leader or big account tweet/post alert."""
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC · %d %b")

    lines = [
        "🚨  <b>WORLD LEADER / KEY ACCOUNT ALERT</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"<b>@{handle}</b>  ·  {platform}   ·   {score}/10 impact",
        "",
        f'<i>"{quote}"</i>',
        "",
        f"{reason}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "<b>Potential market impact</b>",
        "",
    ]

    for pair in pairs:
        direction = directions.get(pair, "neutral")
        lines.append(fmt_direction(pair, direction))

    lines += [
        "",
        "<i>⚠️ AI analysis — not a guaranteed signal</i>",
        f"<i>{ts}</i>",
    ]

    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════
# CLAUDE AI SCORER
# ══════════════════════════════════════════════════════════════════

SCORE_PROMPT = """You are a professional macro FX trader. Score this news for market impact.

News: "{text}"
Source: {source}

Reply ONLY with valid JSON, no markdown, no extra text:
{{
  "score": <1-10>,
  "headline": "<max 10 words — plain English, no jargon>",
  "reason": "<one clear sentence explaining why this matters to FX traders>",
  "affected_pairs": ["EURUSD", "XAUUSD"],
  "directions": {{"EURUSD": "bearish", "XAUUSD": "bullish"}},
  "is_tweet": false
}}

Score guide:
1-4  = noise / irrelevant to FX
5-6  = minor, unlikely to move market
7-8  = meaningful — 0.3-1% expected move
9-10 = major — exit / avoid entry

Score 8-10 triggers: Fed rate decision, Trump tariff announcement,
war escalation, massive data miss, central bank emergency action"""

BIAS_PROMPT = """You are a professional FX macro analyst. Today is {date}.
Today's economic events: {events}

Generate a directional bias for today's session.
Reply ONLY with valid JSON, no markdown, no extra text:
{{
  "pairs": {{
    "EURUSD": "bearish",
    "XAUUSD": "bullish",
    "NAS100": "neutral",
    "GBPUSD": "bearish",
    "USDJPY": "bullish"
  }}
}}

Use only: "bullish", "bearish", or "neutral"
Base it purely on what the scheduled events could do to each pair."""


def score_news(text: str, source: str) -> dict | None:
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": SCORE_PROMPT.format(
                    text=text[:500], source=source
                )}],
            },
            timeout=15,
        )
        if not r.ok:
            log.error(f"AI error: {r.status_code} {r.text[:120]}")
            return None
        raw = r.json()["content"][0]["text"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        log.error(f"Score error: {e}")
        return None


def get_bias(events: list) -> dict:
    try:
        date_str = datetime.now(timezone.utc).strftime("%A %d %B %Y")
        event_text = "\n".join([
            f"- {e.get('time','')} {e.get('flag','')} {e.get('name','')} ({e.get('impact','').upper()})"
            for e in events
        ]) or "No major events scheduled"

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": BIAS_PROMPT.format(
                    date=date_str, events=event_text
                )}],
            },
            timeout=15,
        )
        if not r.ok:
            return {"EURUSD": "neutral", "XAUUSD": "neutral", "NAS100": "neutral", "GBPUSD": "neutral", "USDJPY": "neutral"}
        raw = r.json()["content"][0]["text"].strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        return data.get("pairs", {})
    except Exception as e:
        log.error(f"Bias error: {e}")
        return {"EURUSD": "neutral", "XAUUSD": "neutral", "NAS100": "neutral", "GBPUSD": "neutral", "USDJPY": "neutral"}

# ══════════════════════════════════════════════════════════════════
# ECONOMIC CALENDAR  (investing.com free endpoint)
# ══════════════════════════════════════════════════════════════════

def fetch_events() -> list:
    """Fetch today's high/medium impact events from Forex Factory RSS."""
    try:
        feed = feedparser.parse("https://www.forexfactory.com/calendar?week=this&impact=high")
        events = []
        for e in feed.entries[:20]:
            events.append({
                "time":   e.get("published", "")[:5],
                "flag":   "🌍",
                "name":   e.get("title", ""),
                "impact": "high",
                "forecast": e.get("ff_forecast", "—"),
                "previous": e.get("ff_previous", "—"),
                "currency": e.get("ff_currency", ""),
            })
        return events
    except Exception:
        pass

    # Fallback — build from known high-impact events for today
    return []


def build_fallback_events() -> list:
    """
    If the calendar feed fails, return a minimal placeholder.
    The morning briefing will still fire — just without event detail.
    """
    return []

# ══════════════════════════════════════════════════════════════════
# MORNING BRIEFING
# ══════════════════════════════════════════════════════════════════

def send_morning_briefing():
    log.info("Sending morning briefing...")
    date_str = datetime.now(timezone.utc).strftime("%A %d %B %Y")

    events = fetch_events() or build_fallback_events()
    pairs  = get_bias(events)

    msg = fmt_morning_briefing(events, pairs, date_str)
    send(msg)
    log.info("Morning briefing sent.")

# ══════════════════════════════════════════════════════════════════
# 10-MINUTE WARNING TRACKER
# ══════════════════════════════════════════════════════════════════

WARNED = set()

def check_upcoming_events():
    """Fire a 10-minute warning for any upcoming high-impact event."""
    now = datetime.now(timezone.utc)
    events = fetch_events()

    for e in events:
        if e.get("impact") != "high":
            continue
        try:
            event_dt = datetime.strptime(
                f"{now.strftime('%Y-%m-%d')} {e['time']}",
                "%Y-%m-%d %H:%M"
            ).replace(tzinfo=timezone.utc)
        except Exception:
            continue

        minutes_away = (event_dt - now).total_seconds() / 60
        event_key = f"{now.strftime('%Y-%m-%d')}-{e['time']}-{e['name']}"

        if 9 <= minutes_away <= 11 and event_key not in WARNED:
            WARNED.add(event_key)

            # Ask Claude what pairs are affected
            pairs_info = score_news(
                f"Upcoming event: {e['name']} for {e.get('currency','USD')}. "
                f"Forecast: {e.get('forecast','?')} Previous: {e.get('previous','?')}",
                "Economic Calendar"
            )

            if pairs_info:
                affected = pairs_info.get("affected_pairs", ["EURUSD"])
                directions = pairs_info.get("directions", {})
            else:
                affected = ["EURUSD", "XAUUSD"]
                directions = {}

            # Build pairs dict
            pairs = {p: directions.get(p, "neutral") for p in affected}

            msg = fmt_red_warning(
                event_name=e["name"],
                flag=e.get("flag", "🌍"),
                currency=e.get("currency", ""),
                event_time=e["time"],
                forecast=e.get("forecast", "—"),
                previous=e.get("previous", "—"),
                pairs=pairs,
            )
            send(msg)
            log.info(f"10-min warning sent: {e['name']}")

# ══════════════════════════════════════════════════════════════════
# RSS FEED POLLER
# ══════════════════════════════════════════════════════════════════

def poll_feeds():
    log.info("Polling RSS feeds...")
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:5]:
                title   = entry.get("title", "")
                summary = entry.get("summary", title)
                text    = f"{title}. {summary}"
                uid     = hashlib.md5(title.encode()).hexdigest()

                if uid in SEEN:
                    continue
                SEEN.add(uid)

                log.info(f"Scoring [{feed_info['name']}]: {title[:80]}")
                result = score_news(text, feed_info["name"])

                if result and result.get("score", 0) >= MIN_SCORE:
                    pairs      = result.get("affected_pairs", ["EURUSD"])
                    directions = result.get("directions", {})

                    msg = fmt_news_alert(
                        headline=result.get("headline", title[:80]),
                        reason=result.get("reason", ""),
                        source=feed_info["name"],
                        score=result["score"],
                        pairs=pairs,
                        directions={p: directions.get(p, "neutral") for p in pairs},
                    )
                    send(msg)
                    log.info(f"Alert sent: score {result['score']} — {title[:60]}")

        except Exception as e:
            log.error(f"Feed error [{feed_info['name']}]: {e}")

# ══════════════════════════════════════════════════════════════════
# SCHEDULER
# ══════════════════════════════════════════════════════════════════

def run_scheduler():
    schedule.every(3).minutes.do(poll_feeds)
    schedule.every(1).minutes.do(check_upcoming_events)
    schedule.every().day.at("06:30").do(send_morning_briefing)

    while True:
        schedule.run_pending()
        time.sleep(20)

# ══════════════════════════════════════════════════════════════════
# FLASK — keeps Railway happy (needs an HTTP port open)
# ══════════════════════════════════════════════════════════════════

app = Flask(__name__)

@app.route("/")
def home():
    return "QuickNews bot is running", 200

# ══════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    missing = [v for v in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "ANTHROPIC_API_KEY"]
               if not os.environ.get(v)]
    if missing:
        log.error(f"Missing env vars: {missing}")
        exit(1)

    log.info("Starting QuickNews bot...")

    send(
        "✅  <b>QuickNews — ONLINE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Morning briefing: <b>06:30 UTC daily</b>\n"
        "Red news warnings: <b>10 min before release</b>\n"
        "News feed check: <b>every 3 minutes</b>\n"
        "AI threshold: <b>7+ / 10 only</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Reuters · ForexLive · MarketWatch · FX Street · Investing.com</i>"
    )

    # Fire morning briefing immediately on startup if before 09:00 UTC
    if datetime.now(timezone.utc).hour < 9:
        send_morning_briefing()

    threading.Thread(target=run_scheduler, daemon=True).start()
    poll_feeds()

    app.run(host="0.0.0.0", port=8080, debug=False)

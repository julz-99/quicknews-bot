"""
QuickNews Meta-Agent
────────────────────────────────────────────────────────────────────
Runs alongside the bot as a second Railway service.

Every 6 hours:
  1. Pulls the latest deploy logs from Railway
  2. Reads the current bot code from GitHub
  3. Sends both to Claude — asks it to find errors and fix them
  4. If a fix is found → pushes updated code to GitHub automatically
  5. Railway sees the new commit and redeploys the bot
  6. Sends you a Telegram report of what was changed and why

Every 24 hours:
  - Sends a separate improvement suggestion report to Telegram
  - Lists things that could be better but doesn't auto-push them
  - You can reply "build it" in a future session to implement them

────────────────────────────────────────────────────────────────────
Add these as Railway environment variables for this service:
  ANTHROPIC_API_KEY     → same key as your bot
  TELEGRAM_BOT_TOKEN    → same as your bot
  TELEGRAM_CHANNEL_ID   → same as your bot
  GITHUB_TOKEN          → github_pat_11BF3JPXQ0S4L35y6gO2zd_...
  RAILWAY_TOKEN         → bca8bf48-e718-4e9b-a301-ba862c0e8989
  RAILWAY_PROJECT_ID    → get from Railway URL: railway.app/project/XXXX-XXXX
  RAILWAY_SERVICE_ID    → get from Railway: click your bot service → Settings → copy Service ID
  GITHUB_REPO           → julz-99/quicknews-bot
  GITHUB_FILE           → quicknews_bot.py
────────────────────────────────────────────────────────────────────
"""

import os
import json
import base64
import logging
import schedule
import time
import requests
import threading
from datetime import datetime, timezone
from flask import Flask

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# CREDENTIALS
# ══════════════════════════════════════════════════════════════════

ANTHROPIC_KEY      = os.environ.get("ANTHROPIC_API_KEY", "")
BOT_TOKEN          = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID         = os.environ.get("TELEGRAM_CHANNEL_ID", "")
GITHUB_TOKEN       = os.environ.get("GITHUB_TOKEN", "")
RAILWAY_TOKEN      = os.environ.get("RAILWAY_TOKEN", "")
RAILWAY_PROJECT_ID = os.environ.get("RAILWAY_PROJECT_ID", "")
RAILWAY_SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "")
GITHUB_REPO        = os.environ.get("GITHUB_REPO", "julz-99/quicknews-bot")
GITHUB_FILE        = os.environ.get("GITHUB_FILE", "quicknews_bot.py")

RAILWAY_GQL = "https://backboard.railway.app/graphql/v2"

# ══════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════

def send(text: str):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHANNEL_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if not r.ok:
            log.error(f"Telegram error: {r.text}")
    except Exception as e:
        log.error(f"Telegram failed: {e}")

# ══════════════════════════════════════════════════════════════════
# RAILWAY — fetch recent deploy logs
# ══════════════════════════════════════════════════════════════════

def get_latest_deployment_id() -> str | None:
    query = """
    query getDeployments($serviceId: String!, $projectId: String!) {
      deployments(input: { serviceId: $serviceId, projectId: $projectId }, first: 1) {
        edges {
          node {
            id
            status
            createdAt
          }
        }
      }
    }
    """
    try:
        r = requests.post(
            RAILWAY_GQL,
            json={"query": query, "variables": {
                "serviceId": RAILWAY_SERVICE_ID,
                "projectId": RAILWAY_PROJECT_ID,
            }},
            headers={"Authorization": f"Bearer {RAILWAY_TOKEN}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        data = r.json()
        edges = data.get("data", {}).get("deployments", {}).get("edges", [])
        if edges:
            return edges[0]["node"]["id"]
    except Exception as e:
        log.error(f"Railway deployment fetch failed: {e}")
    return None


def get_deploy_logs(deployment_id: str) -> str:
    query = """
    query getLogs($deploymentId: String!) {
      deploymentLogs(deploymentId: $deploymentId) {
        message
        severity
        timestamp
      }
    }
    """
    try:
        r = requests.post(
            RAILWAY_GQL,
            json={"query": query, "variables": {"deploymentId": deployment_id}},
            headers={"Authorization": f"Bearer {RAILWAY_TOKEN}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        data = r.json()
        logs = data.get("data", {}).get("deploymentLogs", [])
        lines = [f"[{l.get('severity','INFO')}] {l.get('message','')}" for l in logs[-150:]]
        return "\n".join(lines)
    except Exception as e:
        log.error(f"Railway log fetch failed: {e}")
        return ""

# ══════════════════════════════════════════════════════════════════
# GITHUB — read and write bot code
# ══════════════════════════════════════════════════════════════════

def github_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def read_bot_code() -> tuple[str, str]:
    """Returns (code_content, file_sha)"""
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}",
            headers=github_headers(),
            timeout=15,
        )
        if r.ok:
            data = r.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data["sha"]
    except Exception as e:
        log.error(f"GitHub read failed: {e}")
    return "", ""


def push_bot_code(new_code: str, sha: str, commit_msg: str) -> bool:
    try:
        encoded = base64.b64encode(new_code.encode("utf-8")).decode("utf-8")
        r = requests.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}",
            headers=github_headers(),
            json={
                "message": commit_msg,
                "content": encoded,
                "sha": sha,
            },
            timeout=20,
        )
        if r.ok:
            log.info(f"Pushed to GitHub: {commit_msg}")
            return True
        else:
            log.error(f"GitHub push failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        log.error(f"GitHub push exception: {e}")
    return False

# ══════════════════════════════════════════════════════════════════
# CLAUDE — error fixer
# ══════════════════════════════════════════════════════════════════

FIX_PROMPT = """You are an expert Python developer maintaining a Telegram macro news bot.

Here are the recent deploy logs from Railway:
<logs>
{logs}
</logs>

Here is the current bot code:
<code>
{code}
</code>

Your job:
1. Identify any errors, crashes, or recurring failures in the logs
2. If you find fixable issues, write the complete corrected Python file
3. Only fix real errors — do not refactor or add features

Respond ONLY with valid JSON, no markdown:
{{
  "has_fix": true,
  "error_summary": "One sentence describing what was wrong",
  "fix_description": "One sentence describing what you changed",
  "fixed_code": "the complete corrected Python file as a string, or empty string if no fix needed"
}}

If there are no errors or nothing fixable, set has_fix to false and fixed_code to empty string."""


IMPROVE_PROMPT = """You are a product manager and Python developer reviewing a Telegram macro news bot.

Here are the recent logs:
<logs>
{logs}
</logs>

Here is the current code:
<code>
{code}
</code>

Suggest 3 concrete improvements that would make this bot more useful or reliable.
Focus on things that would genuinely help FX traders.

Respond ONLY with valid JSON, no markdown:
{{
  "suggestions": [
    {{
      "title": "Short title",
      "why": "One sentence on why this matters to traders",
      "effort": "easy|medium|hard"
    }}
  ]
}}"""


def ask_claude(prompt: str) -> dict | None:
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 4000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        if not r.ok:
            log.error(f"Claude error: {r.status_code} {r.text[:200]}")
            return None
        raw = r.json()["content"][0]["text"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        log.error(f"Claude failed: {e}")
        return None

# ══════════════════════════════════════════════════════════════════
# MAIN AGENT LOOPS
# ══════════════════════════════════════════════════════════════════

def fix_loop():
    """Runs every 6 hours — auto-fixes errors found in logs."""
    log.info("Fix loop starting...")
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC · %d %b")

    deployment_id = get_latest_deployment_id()
    if not deployment_id:
        log.warning("Could not fetch deployment ID — skipping fix loop")
        return

    logs = get_deploy_logs(deployment_id)
    if not logs:
        log.info("No logs retrieved — skipping fix loop")
        return

    # Check if there are any errors worth fixing
    error_keywords = ["ERROR", "Traceback", "Exception", "401", "403", "500", "failed", "crash"]
    has_errors = any(kw.lower() in logs.lower() for kw in error_keywords)
    if not has_errors:
        log.info("No errors found in logs — nothing to fix")
        return

    log.info("Errors detected — asking Claude to fix...")
    code, sha = read_bot_code()
    if not code:
        log.error("Could not read bot code from GitHub")
        return

    result = ask_claude(FIX_PROMPT.format(logs=logs[:3000], code=code[:6000]))
    if not result:
        return

    if result.get("has_fix") and result.get("fixed_code", "").strip():
        fixed_code = result["fixed_code"]
        commit_msg = f"Agent auto-fix: {result.get('fix_description', 'fix errors from logs')}"

        pushed = push_bot_code(fixed_code, sha, commit_msg)
        if pushed:
            send(
                "🔧  <b>Agent auto-fix deployed</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Problem found:</b> {result.get('error_summary', '—')}\n"
                f"<b>Fix applied:</b> {result.get('fix_description', '—')}\n\n"
                "<i>Code pushed to GitHub → Railway is redeploying now</i>\n"
                f"<i>{ts}</i>"
            )
            log.info("Auto-fix pushed and reported to Telegram")
        else:
            send(
                "⚠️  <b>Agent found an error but could not push fix</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Problem:</b> {result.get('error_summary', '—')}\n\n"
                "<i>Check Railway logs manually</i>\n"
                f"<i>{ts}</i>"
            )
    else:
        log.info("Claude found no fixable errors")


def improve_loop():
    """Runs every 24 hours — sends improvement suggestions to Telegram."""
    log.info("Improvement loop starting...")
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC · %d %b")

    deployment_id = get_latest_deployment_id()
    logs = get_deploy_logs(deployment_id) if deployment_id else ""
    code, _ = read_bot_code()

    if not code:
        return

    result = ask_claude(IMPROVE_PROMPT.format(
        logs=logs[:2000] if logs else "No logs available",
        code=code[:6000]
    ))
    if not result:
        return

    suggestions = result.get("suggestions", [])
    if not suggestions:
        return

    effort_emoji = {"easy": "🟢", "medium": "🟠", "hard": "🔴"}
    lines = [
        "💡  <b>Agent improvement suggestions</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for i, s in enumerate(suggestions, 1):
        effort = s.get("effort", "medium")
        lines.append(f"{i}. <b>{s.get('title', '')}</b>  {effort_emoji.get(effort, '🟠')} {effort}")
        lines.append(f"   {s.get('why', '')}")
        lines.append("")

    lines += [
        "<i>Reply to this message with which one to build and it will be added next update</i>",
        f"<i>{ts}</i>",
    ]

    send("\n".join(lines))
    log.info("Improvement suggestions sent to Telegram")

# ══════════════════════════════════════════════════════════════════
# SCHEDULER
# ══════════════════════════════════════════════════════════════════

def run_scheduler():
    schedule.every(6).hours.do(fix_loop)
    schedule.every(24).hours.do(improve_loop)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ══════════════════════════════════════════════════════════════════
# FLASK — keeps Railway happy
# ══════════════════════════════════════════════════════════════════

app = Flask(__name__)

@app.route("/")
def home():
    return "QuickNews Meta-Agent is running", 200

# ══════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    missing = [v for v in [
        "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID",
        "GITHUB_TOKEN", "RAILWAY_TOKEN", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"
    ] if not os.environ.get(v)]

    if missing:
        log.error(f"Missing env vars: {missing}")
        exit(1)

    log.info("Starting QuickNews Meta-Agent...")

    send(
        "🤖  <b>Meta-Agent — ONLINE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Auto-fix loop: <b>every 6 hours</b>\n"
        "Improvement suggestions: <b>every 24 hours</b>\n"
        "Watching: <b>Railway logs + GitHub code</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Will auto-push fixes and report to this channel</i>"
    )

    # Run first checks immediately on startup
    threading.Thread(target=fix_loop, daemon=True).start()

    threading.Thread(target=run_scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=8081, debug=False)

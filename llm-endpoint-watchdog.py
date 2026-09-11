#!/usr/bin/env python3
"""
LLM Endpoint Watchdog - OpenAI-Compatible Model Endpoint Monitor
Continuously monitors OpenAI-compatible LLM endpoints (any host:port)
and serves a real-time web dashboard.
"""

import http.server
import json
import socket
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional
import os
import base64

# --- Config ---
DEFAULT_ENDPOINTS = [{"endpoint": "127.0.0.1:8000", "name": "Local"}]
CHECK_INTERVAL = 10  # seconds
DASHBOARD_PORT = 9090
TIMEOUT = 5  # seconds per check
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog_endpoints.json")

# --- Token Usage Config ---
TOKEN_CHECK_INTERVAL = 120  # seconds between paid-agent usage refreshes
CODEX_AUTH_FILE = os.path.expanduser("~/.codex/auth.json")
CODEX_INSTALLATION_ID_FILE = os.path.expanduser("~/.codex/installation_id")
CLAUDE_CRED_FILE = os.path.expanduser("~/.claude/.credentials.json")
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"
CLAUDE_MESSAGES_URL = "https://api.anthropic.com/v1/messages"

# --- Favicon (SVG -> base64) ---
FAVICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#0a0a0f"/>
  <rect x="4" y="4" width="56" height="56" rx="11" fill="#12121a" stroke="#76b900" stroke-width="2"/>
  <text x="32" y="46" text-anchor="middle" font-size="34" font-family="monospace" font-weight="bold" fill="#76b900">W</text>
  <circle cx="18" cy="20" r="4" fill="#76b900" opacity="0.7"/>
  <circle cx="46" cy="20" r="4" fill="#76b900" opacity="0.7"/>
</svg>'''
FAVICON_B64 = base64.b64encode(FAVICON_SVG.encode()).decode()

# --- State ---
@dataclass
class EndpointStatus:
    endpoint: str  # host:port
    host: str
    port: int
    name: str = ""  # user-friendly label
    online: bool = False
    models: list = field(default_factory=list)
    response_time_ms: Optional[float] = None
    last_check: Optional[str] = None
    last_online: Optional[str] = None
    error: Optional[str] = None
    uptime_pct: float = 0.0
    total_checks: int = 0
    successful_checks: int = 0

def load_endpoints():
    """Load endpoint list from state file, fallback to defaults.
    Supports both new format [{endpoint, name}, ...] and old format [str, ...].
    """
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                raw = data.get("endpoints", DEFAULT_ENDPOINTS)
                normalized = []
                seen = set()
                for item in raw:
                    if isinstance(item, dict):
                        ep = item.get("endpoint", "").strip()
                        nm = item.get("name", "").strip()
                    elif isinstance(item, str):
                        ep = item.strip()
                        nm = ""
                    else:
                        continue
                    if ep and ep not in seen:
                        seen.add(ep)
                        normalized.append({"endpoint": ep, "name": nm})
                return normalized
    except Exception:
        pass
    return list(DEFAULT_ENDPOINTS)

def save_endpoints(endpoints):
    """Persist endpoint list to state file."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump({"endpoints": endpoints}, f, indent=2)
    except Exception:
        pass

def parse_endpoint(ep: str):
    """Parse 'host:port' string, return (host, port)."""
    if ":" in ep:
        h, p = ep.rsplit(":", 1)
        return h, int(p)
    return ep, 80

ENDPOINTS = load_endpoints()
statuses = {}
for item in ENDPOINTS:
    ep = item["endpoint"]
    nm = item.get("name", "")
    h, p = parse_endpoint(ep)
    statuses[ep] = EndpointStatus(endpoint=ep, host=h, port=p, name=nm)
status_lock = threading.Lock()
start_time = datetime.now()

# --- Token usage state (Claude Code + Codex CLI) ---
token_status = {
    "codex": {"online": False, "last_check": None, "error": None, "plan": None,
             "primary_pct": None, "primary_reset": None,
             "secondary_pct": None, "secondary_reset": None,
             "credits_balance": None, "credits_unlimited": None, "email": None, "rate_reached": False},
    "claude": {"online": False, "last_check": None, "error": None, "plan": None, "tier": None,
              "h5_pct": None, "h5_reset": None, "h5_status": None,
              "d7_pct": None, "d7_reset": None, "d7_status": None,
              "retry_after": None, "email": None},
    "last_check": None,
}
token_lock = threading.Lock()


def check_endpoint(ep: str) -> EndpointStatus:
    """Check a single OpenAI-compatible LLM endpoint and return its status."""
    host, port = parse_endpoint(ep)
    url = f"http://{host}:{port}/v1/models"
    h, p = parse_endpoint(ep)
    status = EndpointStatus(endpoint=ep, host=h, port=p)

    with status_lock:
        old = statuses.get(ep)
        if old:
            status.total_checks = old.total_checks + 1
            status.successful_checks = old.successful_checks
            status.last_online = old.last_online
        else:
            status.total_checks = 1

    start = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "llm-endpoint-watchdog/1.0"})
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        elapsed = (time.monotonic() - start) * 1000

        body = resp.read().decode()
        if not body.strip():
            raise ValueError("Empty response body")
        data = json.loads(body)
        if data.get("object") != "list" or "data" not in data:
            raise ValueError("Not an OpenAI-compatible /v1/models response")
        models = [m.get("id", "unknown") for m in data.get("data", [])]

        status.online = True
        status.models = models
        status.response_time_ms = round(elapsed, 1)
        status.last_check = datetime.now().strftime("%H:%M:%S")
        status.last_online = status.last_check
        status.successful_checks += 1
        status.error = None

    except urllib.error.HTTPError as e:
        elapsed = (time.monotonic() - start) * 1000
        status.online = True  # port open, just HTTP error
        status.response_time_ms = round(elapsed, 1)
        status.last_check = datetime.now().strftime("%H:%M:%S")
        status.error = f"HTTP {e.code}"
        status.successful_checks += 1

    except (urllib.error.URLError, socket.timeout, ConnectionRefusedError, OSError, ValueError, json.JSONDecodeError) as e:
        status.online = False
        status.response_time_ms = None
        status.last_check = datetime.now().strftime("%H:%M:%S")
        status.error = str(e)[:80]

    status.uptime_pct = round(status.successful_checks / status.total_checks * 100, 1) if status.total_checks > 0 else 0

    with status_lock:
        statuses[ep] = status

    return status


def monitor_loop():
    """Background thread that continuously checks all endpoints."""
    while True:
        current = [item["endpoint"] for item in ENDPOINTS]
        threads = []
        for ep in current:
            t = threading.Thread(target=check_endpoint, args=(ep,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        time.sleep(CHECK_INTERVAL)


# --- Token usage collectors (Claude Code + Codex CLI) ---
def _fmt_countdown(seconds):
    """Format seconds remaining into a short human string."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return "now"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m"
    return f"{s}s"


def _utc_to_local(ts):
    """Convert a unix-seconds timestamp to a local HH:MM string."""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return None


def fetch_codex_usage():
    """Query the ChatGPT backend for Codex CLI rate-limit / usage state."""
    if not os.path.exists(CODEX_AUTH_FILE):
        with token_lock:
            token_status["codex"].update({"online": False, "error": "auth.json bulunamadi", "last_check": datetime.now().strftime("%H:%M:%S")})
        return
    try:
        auth = json.load(open(CODEX_AUTH_FILE))
        access = (auth.get("tokens") or {}).get("access_token")
        if not access:
            raise ValueError("access_token yok")
        inst = ""
        if os.path.exists(CODEX_INSTALLATION_ID_FILE):
            try:
                inst = open(CODEX_INSTALLATION_ID_FILE).read().strip()
            except Exception:
                inst = ""
        headers = {
            "Authorization": f"Bearer {access}",
            "Accept": "application/json",
            "User-Agent": "codex-cli/0.154.0",
        }
        if inst:
            headers["x-codex-installation-id"] = inst
        req = urllib.request.Request(CODEX_USAGE_URL, headers=headers, method="GET")
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        rl = data.get("rate_limit") or {}
        pw = rl.get("primary_window") or {}
        sw = rl.get("secondary_window") or {}
        credits = data.get("credits") or {}
        with token_lock:
            token_status["codex"].update({
                "online": True,
                "last_check": datetime.now().strftime("%H:%M:%S"),
                "error": None,
                "email": data.get("email"),
                "plan": data.get("plan_type"),
                "primary_pct": pw.get("used_percent"),
                "primary_reset": _fmt_countdown(pw.get("reset_after_seconds")),
                "primary_reset_at": _utc_to_local(pw.get("reset_at")),
                "secondary_pct": sw.get("used_percent"),
                "secondary_reset": _fmt_countdown(sw.get("reset_after_seconds")),
                "secondary_reset_at": _utc_to_local(sw.get("reset_at")),
                "credits_balance": credits.get("balance"),
                "credits_unlimited": credits.get("unlimited"),
                "rate_reached": bool(rl.get("limit_reached")),
            })
    except Exception as e:
        with token_lock:
            token_status["codex"].update({"online": False, "error": str(e)[:100], "last_check": datetime.now().strftime("%H:%M:%S")})


def fetch_claude_usage():
    """Probe Anthropic messages endpoint to read Claude Code rate-limit headers.
    A 429 response still carries the rate-limit headers, so this works even
    when the account is throttled (no tokens consumed on rejection)."""
    if not os.path.exists(CLAUDE_CRED_FILE):
        with token_lock:
            token_status["claude"].update({"online": False, "error": "credentials.json bulunamadi", "last_check": datetime.now().strftime("%H:%M:%S")})
        return
    try:
        creds = json.load(open(CLAUDE_CRED_FILE))
        oauth = creds.get("claudeAiOauth") or {}
        tok = oauth.get("accessToken")
        if not tok:
            raise ValueError("accessToken yok")
        body = json.dumps({"model": "claude-haiku-4-5", "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}]}).encode()
        headers = {
            "Authorization": f"Bearer {tok}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
            "content-type": "application/json",
            "User-Agent": "claude-code/1.0",
        }
        req = urllib.request.Request(CLAUDE_MESSAGES_URL, data=body, headers=headers, method="POST")
        status = "ok"
        h = None
        try:
            resp = urllib.request.urlopen(req, timeout=12)
            h = resp.headers
        except urllib.error.HTTPError as e:
            h = e.headers
            status = "limit" if e.code == 429 else ("err" + str(e.code))
        # parse unified rate-limit headers
        def _hdr(name):
            return h.get(name) if h else None
        def _pct(v):
            try:
                return round(float(v) * 100, 1) if v is not None else None
            except (TypeError, ValueError):
                return None
        def _reset_local(v):
            try:
                return _utc_to_local(int(v))
            except (TypeError, ValueError):
                return None
        h5_pct = _pct(_hdr("anthropic-ratelimit-unified-5h-utilization"))
        d7_pct = _pct(_hdr("anthropic-ratelimit-unified-7d-utilization"))
        retry_after = _hdr("retry-after")
        with token_lock:
            token_status["claude"].update({
                "online": True,
                "last_check": datetime.now().strftime("%H:%M:%S"),
                "error": None,
                "email": creds.get("claudeAiOauth", {}).get("subscriptionType") and None,
                "plan": oauth.get("subscriptionType"),
                "tier": oauth.get("rateLimitTier"),
                "h5_pct": h5_pct,
                "h5_status": _hdr("anthropic-ratelimit-unified-5h-status"),
                "h5_reset": _reset_local(_hdr("anthropic-ratelimit-unified-5h-reset")),
                "d7_pct": d7_pct,
                "d7_status": _hdr("anthropic-ratelimit-unified-7d-status"),
                "d7_reset": _reset_local(_hdr("anthropic-ratelimit-unified-7d-reset")),
                "retry_after": _fmt_countdown(retry_after) if retry_after else None,
                "http_status": status,
            })
    except Exception as e:
        with token_lock:
            token_status["claude"].update({"online": False, "error": str(e)[:100], "last_check": datetime.now().strftime("%H:%M:%S")})


def token_loop():
    """Background thread that periodically refreshes paid-agent token usage."""
    while True:
        tc = threading.Thread(target=fetch_codex_usage)
        tl = threading.Thread(target=fetch_claude_usage)
        tc.start(); tl.start()
        tc.join(timeout=15); tl.join(timeout=15)
        with token_lock:
            token_status["last_check"] = datetime.now().strftime("%H:%M:%S")
        time.sleep(TOKEN_CHECK_INTERVAL)


# --- Dashboard HTML ---
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LLM Endpoint Watchdog</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,__FAVICON__" />
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', monospace;
    background: #0a0a0f;
    color: #e0e0e0;
    min-height: 100vh;
    padding: 24px 24px 70px 24px;
    font-size: 16px;
    line-height: 1.5;
}
.header {
    text-align: center;
    margin-bottom: 30px;
}
.header h1 {
    font-size: 2.4em;
    color: #76b900;
    letter-spacing: 2px;
}
.header .subtitle {
    color: #777;
    font-size: 1em;
    margin-top: 8px;
}
.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
    gap: 24px;
    max-width: 1600px;
    margin: 0 auto;
}
.card {
    background: #12121a;
    border: 1px solid #222;
    border-radius: 14px;
    padding: 28px 28px 24px 28px;
    position: relative;
    overflow: hidden;
    transition: border-color 0.3s, transform 0.2s, box-shadow 0.2s;
}
.card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
}
.card.online { border-color: #76b900; }
.card.offline { border-color: #e03131; }
.card.error { border-color: #f59f00; }
.card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 4px;
}
.card.online::before { background: #76b900; }
.card.offline::before { background: #e03131; }
.card.error::before { background: #f59f00; }
.port-label {
    font-size: 1.7em;
    font-weight: 700;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
}
.ep-name {
    color: #e0e0e0;
}
.ep-addr {
    color: #777;
    font-size: 0.75em;
    font-weight: 400;
}
.status-dot {
    width: 16px; height: 16px;
    border-radius: 50%;
    display: inline-block;
    animation: pulse 2s ease-in-out infinite;
    flex-shrink: 0;
}
.online .status-dot { background: #76b900; box-shadow: 0 0 14px #76b90088; }
.offline .status-dot { background: #e03131; box-shadow: 0 0 14px #e0313188; }
.error .status-dot { background: #f59f00; box-shadow: 0 0 14px #f59f0088; }
@keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.6; transform: scale(0.85); }
}
.url {
    color: #888;
    font-size: 0.9em;
    margin-bottom: 18px;
    word-break: break-all;
}
.models {
    margin-bottom: 16px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}
.model-tag {
    display: inline-block;
    background: #1a1a2e;
    color: #a0d911;
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 0.9em;
    border: 1px solid #2a2a3e;
}
.stats {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 12px;
    margin-top: 18px;
    padding-top: 18px;
    border-top: 1px solid #1f1f2e;
}
.stat {
    text-align: center;
}
.stat-value {
    font-size: 1.5em;
    font-weight: 700;
}
.stat-label {
    font-size: 0.75em;
    color: #777;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-top: 4px;
}
.online .stat-value { color: #76b900; }
.offline .stat-value { color: #e03131; }
.error .stat-value { color: #f59f00; }
.error-msg {
    color: #f59f00;
    font-size: 0.9em;
    margin-top: 12px;
    padding: 10px 14px;
    background: #1a1a0a;
    border-radius: 8px;
    border: 1px solid #f59f0033;
}
.footer {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    text-align: center;
    color: #666;
    font-size: 0.85em;
    padding: 14px 0;
    background: #0a0a0f;
    border-top: 1px solid #1a1a1a;
    z-index: 50;
}
.summary {
    text-align: center;
    margin-bottom: 24px;
    padding: 18px;
    background: #12121a;
    border-radius: 12px;
    border: 1px solid #222;
    font-size: 1.05em;
    color: #999;
}
.summary .count {
    font-size: 2em;
    font-weight: 700;
    margin-right: 8px;
}
.summary .count.good { color: #76b900; }
.summary .count.bad { color: #e03131; }
/* Token usage cards (Claude + Codex) */
.token-section {
    max-width: 1600px;
    margin: 0 auto 28px auto;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
    gap: 24px;
}
.token-card {
    background: #12121a;
    border: 1px solid #222;
    border-radius: 14px;
    padding: 24px 26px;
    position: relative;
    overflow: hidden;
    transition: border-color 0.3s, box-shadow 0.2s;
}
.token-card.codex { border-color: #10a37f44; }
.token-card.claude { border-color: #d9775744; }
.token-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 4px;
}
.token-card.codex::before { background: #10a37f; }
.token-card.claude::before { background: #d97757; }
.token-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
}
.token-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 1.35em;
    font-weight: 700;
}
.token-brand .dot { width: 12px; height: 12px; border-radius: 50%; }
.token-card.codex .token-brand .dot { background: #10a37f; box-shadow: 0 0 10px #10a37f88; }
.token-card.claude .token-brand .dot { background: #d97757; box-shadow: 0 0 10px #d9775788; }
.token-brand .sub { color: #777; font-size: 0.7em; font-weight: 400; }
.token-badge {
    font-size: 0.8em;
    padding: 4px 10px;
    border-radius: 6px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.token-badge.ok { background: #10a37f22; color: #10a37f; border: 1px solid #10a37f55; }
.token-badge.limit { background: #e0313122; color: #e03131; border: 1px solid #e0313155; }
.token-badge.off { background: #4443; color: #888; border: 1px solid #4445; }
.usage-row {
    margin-bottom: 14px;
}
.usage-row:last-child { margin-bottom: 0; }
.usage-label {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-size: 0.85em;
    color: #999;
    margin-bottom: 6px;
}
.usage-label .name { text-transform: uppercase; letter-spacing: 1px; }
.usage-label .val { color: #e0e0e0; font-weight: 700; font-size: 1.15em; }
.usage-label .reset { color: #777; font-size: 0.9em; }
.usage-bar {
    height: 10px;
    background: #1a1a2e;
    border-radius: 6px;
    overflow: hidden;
    position: relative;
}
.usage-bar .fill {
    height: 100%;
    border-radius: 6px;
    transition: width 0.6s ease;
}
.fill.good { background: linear-gradient(90deg, #76b900, #a0d911); }
.fill.mid { background: linear-gradient(90deg, #f59f00, #fab005); }
.fill.bad { background: linear-gradient(90deg, #e03131, #ff6b6b); }
.token-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 16px;
    margin-top: 16px;
    padding-top: 14px;
    border-top: 1px solid #1f1f2e;
    font-size: 0.82em;
    color: #777;
}
.token-meta span b { color: #aaa; font-weight: 600; }
.token-error {
    color: #f59f00;
    font-size: 0.85em;
    margin-top: 10px;
    padding: 8px 12px;
    background: #1a1a0a;
    border-radius: 8px;
    border: 1px solid #f59f0033;
}
/* Add Port Button */
.header-row {
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    margin-bottom: 36px;
}
.header-row h1 {
    font-size: 2.4em;
    color: #76b900;
    letter-spacing: 2px;
}
.header-row .subtitle {
    position: absolute;
    bottom: -22px;
    left: 50%;
    transform: translateX(-50%);
    color: #777;
    font-size: 1em;
    white-space: nowrap;
}
.add-port-btn {
    position: fixed;
    top: 24px;
    right: 24px;
    z-index: 100;
    background: #1a1a2e;
    color: #76b900;
    border: 1px solid #76b90055;
    border-radius: 12px;
    padding: 14px 22px;
    font-family: inherit;
    font-size: 1.1em;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.2s;
    display: flex;
    align-items: center;
    gap: 8px;
}
.add-port-btn:hover {
    background: #76b90022;
    border-color: #76b900;
    transform: scale(1.05);
    box-shadow: 0 4px 16px #76b90033;
}
.add-port-modal {
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.75);
    z-index: 200;
    justify-content: center;
    align-items: center;
}
.add-port-modal.open { display: flex; }
.add-port-modal .modal-box {
    background: #12121a;
    border: 1px solid #333;
    border-radius: 16px;
    padding: 36px;
    min-width: 460px;
    text-align: center;
    box-shadow: 0 12px 40px rgba(0,0,0,0.6);
}
.add-port-modal h3 {
    color: #76b900;
    margin-bottom: 22px;
    font-size: 1.5em;
}
.add-port-modal input {
    background: #0a0a0f;
    border: 1px solid #333;
    color: #e0e0e0;
    border-radius: 10px;
    padding: 14px 16px;
    font-family: inherit;
    font-size: 1.1em;
    width: 100%;
    margin-bottom: 14px;
    text-align: center;
    letter-spacing: 1px;
}
.add-port-modal input:focus {
    outline: none;
    border-color: #76b900;
    box-shadow: 0 0 0 3px #76b90022;
}
.add-port-modal input::placeholder { color: #555; }
.add-port-modal .modal-btns {
    display: flex;
    gap: 12px;
    justify-content: center;
    margin-top: 8px;
}
.add-port-modal button {
    padding: 12px 28px;
    border-radius: 10px;
    border: 1px solid #333;
    font-family: inherit;
    font-size: 1.05em;
    cursor: pointer;
    font-weight: 600;
    transition: all 0.2s;
}
.add-port-modal .btn-ok {
    background: #76b900;
    color: #000;
    border-color: #76b900;
}
.add-port-modal .btn-ok:hover { background: #8cd400; transform: scale(1.03); }
.add-port-modal .btn-cancel {
    background: #1a1a2e;
    color: #999;
}
.add-port-modal .btn-cancel:hover { background: #222; color: #ccc; }
.add-port-modal .modal-error {
    color: #e03131;
    font-size: 0.95em;
    margin-top: 10px;
    min-height: 1.2em;
}
/* Card action buttons (remove + edit) */
.card-remove, .card-edit {
    position: absolute;
    top: 12px;
    background: none;
    border: 1px solid #333;
    border-radius: 8px;
    width: 32px;
    height: 32px;
    cursor: pointer;
    font-size: 1.05em;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
    padding: 0;
    line-height: 1;
}
.card-remove {
    right: 14px;
    color: #666;
}
.card-edit {
    right: 52px;
    color: #666;
}
.card-remove:hover {
    color: #e03131;
    border-color: #e03131;
    background: #e0313122;
}
.card-edit:hover {
    color: #76b900;
    border-color: #76b900;
    background: #76b90022;
}
.card-drag-handle {
    position: absolute;
    top: 12px;
    left: 14px;
    color: #666;
    cursor: grab;
    font-size: 1.4em;
    line-height: 1;
    user-select: none;
    padding: 2px 4px;
    border-radius: 6px;
    transition: color 0.2s, background 0.2s;
}
.card-drag-handle:hover { color: #76b900; background: #76b90022; }
.card-drag-handle:active { cursor: grabbing; }
.card.dragging { opacity: 0.4; border-style: dashed; }
.card.drag-over { border-color: #76b900; }
.card.drag-over::after {
    content: '';
    position: absolute;
    left: 0; right: 0;
    height: 3px;
    background: #76b900;
    box-shadow: 0 0 10px #76b900;
}
</style>
</head>
<body>
<button class="add-port-btn" onclick="openModal()">+ Endpoint Ekle</button>
<div class="add-port-modal" id="addModal">
    <div class="modal-box">
        <h3 id="modalTitle">Yeni Endpoint Ekle</h3>
        <input type="text" id="newNameInput" placeholder="Isim (orn: vLLM Sunucu)" />
        <input type="text" id="newEndpointInput" placeholder="host:port (orn: 10.0.0.5:8000)" />
        <div class="modal-btns">
            <button class="btn-ok" id="modalOkBtn" onclick="submitModal()">Ekle</button>
            <button class="btn-cancel" onclick="closeModal()">Iptal</button>
        </div>
        <div class="modal-error" id="modalError"></div>
    </div>
</div>
<div class="header-row">
    <h1>⚡ LLM ENDPOINT WATCHDOG</h1>
    <div class="subtitle">__INTERVAL__s interval · since __START__</div>
</div>
<div class="summary" id="summary"></div>
<div class="token-section" id="tokenSection"></div>
<div class="grid" id="grid"></div>
<div class="footer">LLM Endpoint Watchdog v1.0 · Dashboard port __DPORT__ · Auto-refresh 5s</div>
<script>
const ENDPOINTS = __ENDPOINTS__;
let refreshTimer = null;
let tokenRefreshTimer = null;
let isDragging = false;
let draggedEp = null;
let dropHandled = false;
let lastStatusData = {};
let editingEp = null;

function openModal(epToEdit) {
    const modal = document.getElementById('addModal');
    const titleEl = document.getElementById('modalTitle');
    const okBtn = document.getElementById('modalOkBtn');
    const nameInput = document.getElementById('newNameInput');
    const epInput = document.getElementById('newEndpointInput');
    const errEl = document.getElementById('modalError');
    errEl.textContent = '';
    if (epToEdit) {
        editingEp = epToEdit;
        const item = ENDPOINTS.find(e => e.endpoint === epToEdit) || {};
        titleEl.textContent = 'Endpoint Düzenle';
        okBtn.textContent = 'Kaydet';
        nameInput.value = item.name || '';
        epInput.value = item.endpoint || '';
        epInput.disabled = false;
    } else {
        editingEp = null;
        titleEl.textContent = 'Yeni Endpoint Ekle';
        okBtn.textContent = 'Ekle';
        nameInput.value = '';
        epInput.value = '';
        epInput.disabled = false;
    }
    modal.classList.add('open');
    setTimeout(() => nameInput.focus(), 100);
}
function closeModal() {
    document.getElementById('addModal').classList.remove('open');
    editingEp = null;
}
function escapeAttr(s) {
    return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;');
}
async function submitModal() {
    const epInput = document.getElementById('newEndpointInput');
    const nameInput = document.getElementById('newNameInput');
    const ep = epInput.value.trim();
    const name = nameInput.value.trim();
    const errEl = document.getElementById('modalError');
    errEl.textContent = '';
    if (!ep || !/^.+:\\d+$/.test(ep)) {
        errEl.textContent = 'Gecerli format: host:port (orn: 10.0.0.5:8000)';
        return;
    }
    if (editingEp) {
        if (ep !== editingEp && ENDPOINTS.some(e => e.endpoint === ep)) {
            errEl.textContent = `${ep} zaten izleniyor`;
            return;
        }
        try {
            const resp = await fetch('/api/endpoints', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({old_endpoint: editingEp, endpoint: ep, name: name})
            });
            const data = await resp.json();
            if (data.ok) {
                ENDPOINTS.length = 0;
                ENDPOINTS.push(...data.endpoints);
                closeModal();
                refresh();
            } else {
                errEl.textContent = data.error || 'Hata olustu';
            }
        } catch(e) {
            errEl.textContent = 'Sunucu hatasi';
        }
    } else {
        if (ENDPOINTS.some(e => e.endpoint === ep)) {
            errEl.textContent = `${ep} zaten izleniyor`;
            return;
        }
        try {
            const resp = await fetch('/api/endpoints', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({endpoint: ep, name: name})
            });
            const data = await resp.json();
            if (data.ok) {
                ENDPOINTS.length = 0;
                ENDPOINTS.push(...data.endpoints);
                closeModal();
                refresh();
            } else {
                errEl.textContent = data.error || 'Hata olustu';
            }
        } catch(e) {
            errEl.textContent = 'Sunucu hatasi';
        }
    }
}
async function removeEndpoint(ep) {
    if (!confirm(`${ep} endpoint'ini izleme listesinden cikarmak istediginize emin misiniz?`)) return;
    try {
        const resp = await fetch('/api/endpoints', {
            method: 'DELETE',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({endpoint: ep})
        });
        const data = await resp.json();
        if (data.ok) {
            ENDPOINTS.length = 0;
            ENDPOINTS.push(...data.endpoints);
            refresh();
        }
    } catch(e) {
        console.error('Remove endpoint error:', e);
    }
}

function render(data) {
    const grid = document.getElementById('grid');
    const summary = document.getElementById('summary');

    let onlineCount = 0;
    let html = '';

    for (const item of ENDPOINTS) {
        const ep = item.endpoint;
        const displayName = item.name || ep;
        const s = data[ep] || {};
        const isOnline = s.online;
        const hasError = s.error && isOnline;
        const cls = isOnline ? (hasError ? 'error' : 'online') : 'offline';
        if (isOnline && !hasError) onlineCount++;

        const modelsHtml = (s.models || []).map(m =>
            `<span class="model-tag">${m}</span>`
        ).join('') || '<span style="color:#666">No models loaded</span>';

        const nameHtml = item.name
            ? `<span class="ep-name">${escapeAttr(item.name)}</span><span class="ep-addr">${escapeAttr(ep)}</span>`
            : `<span class="ep-addr">${escapeAttr(ep)}</span>`;

        html += `
        <div class="card ${cls}" ondragover="onDragOver(event, '${escapeAttr(ep)}')" ondrop="onDrop(event, '${escapeAttr(ep)}')">
            <span class="card-drag-handle" title="Sürükleyerek taşı" draggable="true" ondragstart="onDragStart(event, '${escapeAttr(ep)}')" ondragend="onDragEnd(event)">&#8942;&#8942;</span>
            <button class="card-edit" onclick="openModal('${escapeAttr(ep)}')" title="Düzenle">&#9998;</button>
            <button class="card-remove" onclick="removeEndpoint('${escapeAttr(ep)}')" title="Kaldir">&#10005;</button>
            <div class="port-label">
                <span class="status-dot"></span>
                ${nameHtml}
            </div>
            <div class="url">http://${escapeAttr(ep)}/v1/models</div>
            <div class="models">${modelsHtml}</div>
            <div class="stats">
                <div class="stat">
                    <div class="stat-value">${s.response_time_ms != null ? s.response_time_ms + 'ms' : '—'}</div>
                    <div class="stat-label">Latency</div>
                </div>
                <div class="stat">
                    <div class="stat-value">${s.uptime_pct != null ? s.uptime_pct + '%' : '—'}</div>
                    <div class="stat-label">Uptime</div>
                </div>
                <div class="stat">
                    <div class="stat-value">${s.last_check || '—'}</div>
                    <div class="stat-label">Last Check</div>
                </div>
            </div>
            ${s.error ? `<div class="error-msg">⚠ ${s.error}</div>` : ''}
        </div>`;
    }

    grid.innerHTML = html;

    const total = ENDPOINTS.length;
    const color = onlineCount === total ? 'good' : onlineCount === 0 ? 'bad' : 'bad';
    summary.innerHTML = `<span class="count ${color}">${onlineCount}/${total}</span> endpoints online`;
}

async function refresh() {
    if (isDragging) return;
    try {
        const resp = await fetch('/api/status');
        const data = await resp.json();
        lastStatusData = data;
        render(data);
    } catch(e) {
        console.error('Fetch error:', e);
    }
}

function renderTokens(t) {
    const sec = document.getElementById('tokenSection');
    if (!t) { sec.innerHTML = ''; return; }
    const cx = t.codex || {};
    const cl = t.claude || {};

    function bar(pct) {
        if (pct == null) return '<div class="usage-bar"><div class="fill" style="width:0%"></div></div>';
        const cls = pct >= 90 ? 'bad' : (pct >= 70 ? 'mid' : 'good');
        return `<div class="usage-bar"><div class="fill ${cls}" style="width:${Math.min(pct,100)}%"></div></div>`;
    }
    function pctStr(v){ return v != null ? `%${v}` : '—'; }

    let cxBadge = cx.online ? (cx.rate_reached ? 'limit' : 'ok') : 'off';
    let cxBadgeTxt = cx.online ? (cx.rate_reached ? 'Limit Dolu' : 'Aktif') : 'Offline';
    let cxHtml = `
    <div class="token-card codex">
      <div class="token-head">
        <div class="token-brand"><span class="dot"></span> Codex CLI <span class="sub">${cx.plan||''}</span></div>
        <span class="token-badge ${cxBadge}">${cxBadgeTxt}</span>
      </div>
      <div class="usage-row">
        <div class="usage-label"><span class="name">Primary (5h)</span><span><span class="val">${pctStr(cx.primary_pct)}</span> <span class="reset">${cx.primary_reset?('↺ '+cx.primary_reset):''}${cx.primary_reset_at?(' '+cx.primary_reset_at):''}</span></span></div>
        ${bar(cx.primary_pct)}
      </div>
      <div class="usage-row">
        <div class="usage-label"><span class="name">Secondary (weekly)</span><span><span class="val">${pctStr(cx.secondary_pct)}</span> <span class="reset">${cx.secondary_reset?('↺ '+cx.secondary_reset):''}${cx.secondary_reset_at?(' '+cx.secondary_reset_at):''}</span></span></div>
        ${bar(cx.secondary_pct)}
      </div>
      <div class="token-meta">
        <span><b>Credits:</b> ${cx.credits_unlimited?'∞':(cx.credits_balance!=null?cx.credits_balance:'—')}</span>
        <span><b>Last:</b> ${cx.last_check||'—'}</span>
      </div>
      ${cx.error?`<div class="token-error">⚠ ${cx.error}</div>`:''}
    </div>`;

    let clBadge = cl.online ? (cl.h5_status==='rejected' || cl.http_status==='limit' ? 'limit' : 'ok') : 'off';
    let clBadgeTxt = cl.online ? (cl.h5_status==='rejected' || cl.http_status==='limit' ? 'Limit Dolu' : 'Aktif') : 'Offline';
    let clHtml = `
    <div class="token-card claude">
      <div class="token-head">
        <div class="token-brand"><span class="dot"></span> Claude Code <span class="sub">${cl.plan||''}${cl.tier?(' · '+cl.tier):''}</span></div>
        <span class="token-badge ${clBadge}">${clBadgeTxt}</span>
      </div>
      <div class="usage-row">
        <div class="usage-label"><span class="name">5h Window</span><span><span class="val">${pctStr(cl.h5_pct)}</span> <span class="reset">${cl.h5_reset?('↺ '+cl.h5_reset):''}${cl.retry_after?(' · retry '+cl.retry_after):''}</span></span></div>
        ${bar(cl.h5_pct)}
      </div>
      <div class="usage-row">
        <div class="usage-label"><span class="name">7d Window</span><span><span class="val">${pctStr(cl.d7_pct)}</span> <span class="reset">${cl.d7_reset?('↺ '+cl.d7_reset):''}</span></span></div>
        ${bar(cl.d7_pct)}
      </div>
      <div class="token-meta">
        <span><b>Last:</b> ${cl.last_check||'—'}</span>
        ${cl.h5_status?`<span><b>5h:</b> ${cl.h5_status}</span>`:''}
        ${cl.d7_status?`<span><b>7d:</b> ${cl.d7_status}</span>`:''}
      </div>
      ${cl.error?`<div class="token-error">⚠ ${cl.error}</div>`:''}
    </div>`;

    sec.innerHTML = cxHtml + clHtml;
}

async function refreshTokens() {
    if (isDragging) return;
    try {
        const resp = await fetch('/api/token-usage');
        const data = await resp.json();
        renderTokens(data);
    } catch(e) { console.error('Token fetch error:', e); }
}

function startRefreshTimer() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(refresh, 5000);
    if (tokenRefreshTimer) clearInterval(tokenRefreshTimer);
    tokenRefreshTimer = setInterval(refreshTokens, 10000);
}

function stopRefreshTimer() {
    if (refreshTimer) {
        clearInterval(refreshTimer);
        refreshTimer = null;
    }
    if (tokenRefreshTimer) {
        clearInterval(tokenRefreshTimer);
        tokenRefreshTimer = null;
    }
}

function onDragStart(event, ep) {
    isDragging = true;
    draggedEp = ep;
    dropHandled = false;
    stopRefreshTimer();
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', ep);
    const card = event.target.closest('.card');
    if (card) card.classList.add('dragging');
}

function onDragOver(event, targetEp) {
    if (!isDragging) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    document.querySelectorAll('.card.drag-over').forEach(c => c.classList.remove('drag-over'));
    if (targetEp !== draggedEp) {
        event.currentTarget.classList.add('drag-over');
    }
}

function onDrop(event, targetEp) {
    event.preventDefault();
    if (!isDragging || !draggedEp) return;
    dropHandled = true;
    const srcIdx = ENDPOINTS.findIndex(e => e.endpoint === draggedEp);
    const tgtIdx = ENDPOINTS.findIndex(e => e.endpoint === targetEp);
    document.querySelectorAll('.card.dragging, .card.drag-over').forEach(c => c.classList.remove('dragging', 'drag-over'));
    if (srcIdx === -1 || tgtIdx === -1 || srcIdx === tgtIdx) {
        finishDrag();
        return;
    }
    const [moved] = ENDPOINTS.splice(srcIdx, 1);
    ENDPOINTS.splice(tgtIdx, 0, moved);
    render(lastStatusData);
    fetch('/api/endpoints/reorder', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({order: ENDPOINTS.map(e => e.endpoint)})
    })
        .then(resp => resp.json())
        .then(data => {
            if (data.ok) {
                ENDPOINTS.length = 0;
                ENDPOINTS.push(...data.endpoints);
            } else {
                console.warn('Reorder failed, syncing from server:', data.error);
                return fetch('/api/endpoints').then(r => r.json()).then(d => {
                    ENDPOINTS.length = 0;
                    ENDPOINTS.push(...d.endpoints);
                });
            }
        })
        .catch(e => {
            console.warn('Reorder network error, syncing from server:', e);
            return fetch('/api/endpoints').then(r => r.json()).then(d => {
                ENDPOINTS.length = 0;
                ENDPOINTS.push(...d.endpoints);
            });
        })
        .finally(() => {
            finishDrag();
            refresh();
        });
}

function onDragEnd(event) {
    if (dropHandled) return;
    document.querySelectorAll('.card.dragging, .card.drag-over').forEach(c => c.classList.remove('dragging', 'drag-over'));
    finishDrag();
    refresh();
}

function finishDrag() {
    isDragging = false;
    draggedEp = null;
    startRefreshTimer();
}

refresh();
refreshTokens();
startRefreshTimer();
</script>
</body>
</html>"""


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for the dashboard and API."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/api/status':
            with status_lock:
                data = {item["endpoint"]: asdict(statuses[item["endpoint"]]) for item in ENDPOINTS if item["endpoint"] in statuses}
            self._json_response(200, data)
        elif self.path == '/api/token-usage':
            with token_lock:
                data = json.loads(json.dumps(token_status))  # deep copy
            self._json_response(200, data)
        elif self.path == '/api/endpoints':
            self._json_response(200, {"endpoints": ENDPOINTS})
        elif self.path == '/favicon.svg':
            self.send_response(200)
            self.send_header('Content-Type', 'image/svg+xml')
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers()
            self.wfile.write(FAVICON_SVG.encode())
        elif self.path == '/' or self.path == '/index.html':
            html = DASHBOARD_HTML
            html = html.replace('__INTERVAL__', str(CHECK_INTERVAL))
            html = html.replace('__START__', start_time.strftime("%Y-%m-%d %H:%M"))
            html = html.replace('__DPORT__', str(DASHBOARD_PORT))
            html = html.replace('__ENDPOINTS__', json.dumps(ENDPOINTS))
            html = html.replace('__FAVICON__', FAVICON_B64)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/api/endpoints':
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len).decode()
            try:
                data = json.loads(body)
                ep = str(data.get('endpoint', '')).strip()
                name = str(data.get('name', '')).strip()
                # Validate host:port format
                if not ep or ':' not in ep:
                    self._json_response(400, {"ok": False, "error": "Format: host:port"})
                    return
                try:
                    host, port = parse_endpoint(ep)
                    if port < 1 or port > 65535 or not host:
                        raise ValueError()
                except (ValueError, IndexError):
                    self._json_response(400, {"ok": False, "error": "Gecersiz host:port"})
                    return
                if any(e["endpoint"] == ep for e in ENDPOINTS):
                    self._json_response(409, {"ok": False, "error": f"{ep} zaten izleniyor"})
                    return
                ENDPOINTS.append({"endpoint": ep, "name": name})
                with status_lock:
                    h, p = parse_endpoint(ep)
                    statuses[ep] = EndpointStatus(endpoint=ep, host=h, port=p, name=name)
                save_endpoints(ENDPOINTS)
                print(f"   + Endpoint {ep} ({name or 'isimsiz'}) eklendi (toplam: {len(ENDPOINTS)})")
                self._json_response(200, {"ok": True, "endpoints": ENDPOINTS})
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                self._json_response(400, {"ok": False, "error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        if self.path == '/api/endpoints':
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len).decode()
            try:
                data = json.loads(body)
                ep = str(data.get('endpoint', '')).strip()
                if not any(e["endpoint"] == ep for e in ENDPOINTS):
                    self._json_response(404, {"ok": False, "error": f"{ep} bulunamadi"})
                    return
                ENDPOINTS[:] = [e for e in ENDPOINTS if e["endpoint"] != ep]
                with status_lock:
                    statuses.pop(ep, None)
                save_endpoints(ENDPOINTS)
                print(f"   - Endpoint {ep} kaldirildi (toplam: {len(ENDPOINTS)})")
                self._json_response(200, {"ok": True, "endpoints": ENDPOINTS})
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                self._json_response(400, {"ok": False, "error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        if self.path == '/api/endpoints':
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len).decode()
            try:
                data = json.loads(body)
                old_ep = str(data.get('old_endpoint', '')).strip()
                new_ep = str(data.get('endpoint', '')).strip()
                name = str(data.get('name', '')).strip()
                if not old_ep or ':' not in old_ep:
                    self._json_response(400, {"ok": False, "error": "Format: host:port"})
                    return
                if not new_ep or ':' not in new_ep:
                    self._json_response(400, {"ok": False, "error": "Gecersiz host:port"})
                    return
                try:
                    host, port = parse_endpoint(new_ep)
                    if port < 1 or port > 65535 or not host:
                        raise ValueError()
                except (ValueError, IndexError):
                    self._json_response(400, {"ok": False, "error": "Gecersiz host:port"})
                    return
                idx = next((i for i, e in enumerate(ENDPOINTS) if e["endpoint"] == old_ep), -1)
                if idx == -1:
                    self._json_response(404, {"ok": False, "error": f"{old_ep} bulunamadi"})
                    return
                if new_ep != old_ep and any(e["endpoint"] == new_ep for e in ENDPOINTS):
                    self._json_response(409, {"ok": False, "error": f"{new_ep} zaten izleniyor"})
                    return
                ENDPOINTS[idx] = {"endpoint": new_ep, "name": name}
                with status_lock:
                    if new_ep != old_ep and old_ep in statuses:
                        old_status = statuses.pop(old_ep)
                        old_status.endpoint = new_ep
                        old_status.host, old_status.port = parse_endpoint(new_ep)
                        old_status.name = name
                        statuses[new_ep] = old_status
                    elif new_ep == old_ep and new_ep in statuses:
                        statuses[new_ep].name = name
                save_endpoints(ENDPOINTS)
                print(f"   ✎ Endpoint güncellendi: {old_ep} -> {new_ep} ({name or 'isimsiz'})")
                self._json_response(200, {"ok": True, "endpoints": ENDPOINTS})
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                self._json_response(400, {"ok": False, "error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_PATCH(self):
        if self.path == '/api/endpoints/reorder':
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len).decode()
            try:
                data = json.loads(body)
                order = data.get('order')
                if not isinstance(order, list) or not order:
                    self._json_response(400, {"ok": False, "error": "order listesi gerekli"})
                    return
                current_set = {e["endpoint"] for e in ENDPOINTS}
                order_set = set(order)
                if order_set != current_set or len(order) != len(ENDPOINTS):
                    self._json_response(400, {"ok": False, "error": "Order listesi mevcut endpoint'lerle uyumsuz"})
                    return
                item_by_ep = {e["endpoint"]: e for e in ENDPOINTS}
                ENDPOINTS[:] = [item_by_ep[ep] for ep in order]
                save_endpoints(ENDPOINTS)
                print(f"   ↕ Endpoint sıralaması güncellendi: {' -> '.join(order)}")
                self._json_response(200, {"ok": True, "endpoints": ENDPOINTS})
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                self._json_response(400, {"ok": False, "error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _json_response(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()


def main():
    print(f"🐕 LLM Endpoint Watchdog starting...")
    ep_list = [f'{item["endpoint"]} ({item["name"]})' if item.get("name") else item["endpoint"] for item in ENDPOINTS]
    print(f"   Monitoring: {', '.join(ep_list)}")
    print(f"   Check interval: {CHECK_INTERVAL}s")
    print(f"   Dashboard: http://0.0.0.0:{DASHBOARD_PORT}")
    print(f"   Endpoints saved to: {STATE_FILE}")

    # Start monitor thread
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()

    # Start token usage thread (Claude + Codex)
    print("   Running initial token usage fetch (Claude + Codex)...")
    token_thread = threading.Thread(target=token_loop, daemon=True)
    token_thread.start()

    # Initial check
    print("   Running initial check...")
    for item in ENDPOINTS:
        ep = item["endpoint"]
        check_endpoint(ep)
        s = statuses[ep]
        status_icon = "✅" if s.online else "❌"
        name_str = f" [{item.get('name', '')}]" if item.get("name") else ""
        models_str = f" ({', '.join(s.models)})" if s.models else ""
        print(f"   {status_icon} {ep}{name_str}{models_str}")

    # Start dashboard server
    server = http.server.HTTPServer(('0.0.0.0', DASHBOARD_PORT), DashboardHandler)
    print(f"\n⚡ Dashboard live at http://localhost:{DASHBOARD_PORT}")
    print("   Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🐕 Watchdog stopped.")


if __name__ == '__main__':
    main()

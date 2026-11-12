"""Push channels other than ServerChan: ntfy, Bark, Telegram, plus ServerChan.

All are optional and zero-monthly-cost. `send_push` auto-selects the first
configured channel; explicit `--channel` overrides. Keys/tokens come from the
environment (GitHub Secrets in the workflow) and are never logged.
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

NTFY_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TELEGRAM_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")


def _post(url: str, payload: dict, opener, *, headers=None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", **(headers or {})})
    try:
        with opener(request, timeout=15) as response:
            status = getattr(response, "status", 200)
            return {"sent": 200 <= status < 300, "status": status}
    except Exception as error:  # noqa: BLE001 - reported, never fatal to report generation
        return {"sent": False, "reason": type(error).__name__}


def send_ntfy(topic: str, title: str, body: str, *, opener=urllib.request.urlopen) -> dict:
    if not isinstance(topic, str) or not NTFY_TOPIC_RE.fullmatch(topic):
        return {"sent": False, "reason": "NTFY_TOPIC 非法（只允许字母/数字/_/-，1–64 位）"}
    result = _post("https://ntfy.sh/", {"topic": topic, "title": title[:100],
                                        "message": body[:4000], "priority": 3}, opener)
    result["channel"] = "ntfy"
    return result


def send_bark(key: str, title: str, body: str, *, opener=urllib.request.urlopen) -> dict:
    if not isinstance(key, str) or not key.strip():
        return {"sent": False, "reason": "未配置 BARK_KEY"}
    safe_key = urllib.parse.quote(key.strip(), safe="")
    result = _post(f"https://api.day.app/{safe_key}",
                   {"title": title[:100], "body": body[:4000]}, opener)
    result["channel"] = "bark"
    return result


def send_telegram(token: str, chat_id: str, text: str, *, opener=urllib.request.urlopen) -> dict:
    if not isinstance(token, str) or not TELEGRAM_TOKEN_RE.fullmatch(token):
        return {"sent": False, "reason": "TELEGRAM_BOT_TOKEN 格式不正确"}
    if not isinstance(chat_id, str) or not chat_id.strip():
        return {"sent": False, "reason": "未配置 TELEGRAM_CHAT_ID"}
    result = _post(f"https://api.telegram.org/bot{token}/sendMessage",
                   {"chat_id": chat_id.strip(), "text": text[:4000],
                    "disable_web_page_preview": True}, opener)
    result["channel"] = "telegram"
    return result


def send_serverchan(key: str, title: str, desp: str, *, opener=urllib.request.urlopen) -> dict:
    if not key:
        return {"sent": False, "reason": "未配置 SERVERCHAN_KEY"}
    payload = urllib.parse.urlencode({"title": title[:100], "desp": desp[:30000]}).encode("utf-8")
    request = urllib.request.Request(
        f"https://sctapi.ftqq.com/{key}.send", data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with opener(request, timeout=15) as response:
            status = getattr(response, "status", 200)
            return {"sent": 200 <= status < 300, "status": status, "channel": "serverchan"}
    except Exception as error:  # noqa: BLE001
        return {"sent": False, "reason": type(error).__name__, "channel": "serverchan"}


def send_email(host: str, port: int, username: str, password: str, to: str,
               subject: str, body: str, *, html: str | None = None,
               filename: str = "今日事报.html", tls: str = "ssl",
               from_addr: str = "") -> dict:
    if not host or not to:
        return {"sent": False, "reason": "未配置 SMTP_HOST / EMAIL_TO"}
    try:
        port = int(port or (465 if tls != "starttls" else 587))
    except (TypeError, ValueError):
        return {"sent": False, "reason": "SMTP_PORT 非法"}
    message = EmailMessage()
    message["Subject"] = subject[:200]
    message["From"] = from_addr or username or "causal-daily@localhost"
    message["To"] = to
    message.set_content(body[:100_000])
    if html:
        message.add_attachment(html.encode("utf-8"), maintype="text", subtype="html",
                               filename=filename)
    try:
        if tls == "starttls":
            client = smtplib.SMTP(host, port, timeout=25)
            client.ehlo()
            client.starttls()
            client.ehlo()
        else:
            client = smtplib.SMTP_SSL(host, port, timeout=25)
        with client:
            if username:
                client.login(username, password)
            client.send_message(message)
        return {"sent": True, "channel": "email"}
    except Exception as error:  # noqa: BLE001 - never log credentials
        return {"sent": False, "reason": type(error).__name__, "channel": "email"}


def _configured(environ: dict) -> str:
    if environ.get("SMTP_HOST") and environ.get("EMAIL_TO"):
        return "email"
    if environ.get("SERVERCHAN_KEY"):
        return "serverchan"
    if environ.get("NTFY_TOPIC"):
        return "ntfy"
    if environ.get("BARK_KEY"):
        return "bark"
    if environ.get("TELEGRAM_BOT_TOKEN") and environ.get("TELEGRAM_CHAT_ID"):
        return "telegram"
    return ""


def send_push(channel: str, title: str, body: str, *, html: str | None = None,
              filename: str = "今日事报.html", environ=None,
              opener=urllib.request.urlopen) -> dict:
    """channel: auto / email / serverchan / ntfy / bark / telegram / none."""
    environ = environ if environ is not None else os.environ
    if channel not in ("auto", "email", "serverchan", "ntfy", "bark", "telegram", "none"):
        return {"sent": False, "reason": f"未知 channel：{channel}"}
    if channel == "none":
        return {"sent": False, "reason": "已选择不推送"}
    selected = _configured(environ) if channel == "auto" else channel
    if not selected:
        return {"sent": False, "reason": "没有配置任何推送通道"}
    if selected == "email":
        return send_email(environ.get("SMTP_HOST", ""), environ.get("SMTP_PORT", ""),
                          environ.get("SMTP_USER", ""), environ.get("SMTP_PASSWORD", ""),
                          environ.get("EMAIL_TO", ""), title, body, html=html,
                          filename=filename, tls=environ.get("SMTP_TLS", "ssl"),
                          from_addr=environ.get("SMTP_FROM", ""))
    if selected == "serverchan":
        return send_serverchan(environ.get("SERVERCHAN_KEY", ""), title, body, opener=opener)
    if selected == "ntfy":
        return send_ntfy(environ.get("NTFY_TOPIC", ""), title, body, opener=opener)
    if selected == "bark":
        return send_bark(environ.get("BARK_KEY", ""), title, body, opener=opener)
    return send_telegram(environ.get("TELEGRAM_BOT_TOKEN", ""),
                         environ.get("TELEGRAM_CHAT_ID", ""), body, opener=opener)

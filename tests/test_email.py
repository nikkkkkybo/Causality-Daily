"""Email push: no new app, HTML attachment plus a short body/link."""
from __future__ import annotations

import pytest

from causal_ledger import push


class FakeSMTP:
    instances = []
    def __init__(self, host, port, timeout=25):
        self.host, self.port, self.timeout = host, port, timeout
        self.started = self.logged_in = self.sent = False
        FakeSMTP.instances.append(self)
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def ehlo(self): return None
    def starttls(self): self.started = True
    def login(self, user, password): self.logged_in = (user, password)
    def send_message(self, message): self.sent = message


def test_send_email_ssl_attaches_html(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(push.smtplib, "SMTP_SSL", FakeSMTP)
    result = push.send_email("smtp.example.org", 465, "me@example.org", "pw",
                             "target@example.org", "标题", "正文",
                             html="<html>报告</html>", filename="今日事报.html")
    assert result == {"sent": True, "channel": "email"}
    client = FakeSMTP.instances[-1]
    assert client.host == "smtp.example.org" and client.port == 465
    assert client.logged_in == ("me@example.org", "pw")
    message = client.sent
    assert message["To"] == "target@example.org"
    assert message["Subject"] == "标题"
    attachments = [part for part in message.walk() if part.get_filename()]
    assert attachments and attachments[0].get_filename() == "今日事报.html"


def test_send_email_starttls(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(push.smtplib, "SMTP", FakeSMTP)
    result = push.send_email("smtp.example.org", 587, "u", "p", "to@example.org",
                             "t", "b", tls="starttls")
    assert result["sent"] is True
    assert FakeSMTP.instances[-1].started is True


def test_send_email_missing_config():
    assert push.send_email("", 465, "", "", "", "t", "b")["sent"] is False


def test_send_push_auto_prefers_email():
    environ = {"SMTP_HOST": "smtp.example.org", "EMAIL_TO": "to@example.org"}
    seen = {}
    def fake_send_email(*args, **kwargs):
        seen["html"] = kwargs.get("html")
        return {"sent": True, "channel": "email"}
    import causal_ledger.push as module
    original = module.send_email
    module.send_email = fake_send_email
    try:
        result = push.send_push("auto", "t", "b", html="<html>x</html>", environ=environ)
        assert result["channel"] == "email" and seen["html"] == "<html>x</html>"
    finally:
        module.send_email = original

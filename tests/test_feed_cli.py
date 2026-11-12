from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def cli(db, *args):
    env = {k: v for k, v in os.environ.items()
           if k not in ('DASHSCOPE_API_KEY', 'OPENAI_API_KEY', 'CL_LLM_API_KEY')}
    result = subprocess.run([sys.executable, '-m', 'causal_ledger', '--db', str(db), *args],
                            cwd=ROOT, env={**env, 'PYTHONIOENCODING': 'utf-8'},
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    return result


def rss_file(tmp_path):
    path = tmp_path / 'synthetic.xml'
    stamp = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
    path.write_text(f'''<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>虚构演练订阅</title>
<item><guid>demo-1</guid><title>虚构电池研究公告</title>
<link>https://example.org/demo-1</link><pubDate>{stamp}</pubDate>
<description>完全虚构，只测试订阅与日报，不是真实科学证据。</description></item>
<item><guid>demo-2</guid><title>虚构无日期条目</title><description>不要把抓取日当发布日期。</description></item>
</channel></rss>''', encoding='utf-8')
    return path


def setup_feed(tmp_path):
    db = tmp_path / 'feeds.sqlite3'
    added = cli(db, 'feed', 'add', '--name', '虚构演练订阅', '--url', 'https://example.org/feed.xml')
    assert added.returncode == 0, added.stderr
    imported = cli(db, 'feed', 'import-file', '1', str(rss_file(tmp_path)))
    assert imported.returncode == 0, imported.stderr
    return db


def test_cli_feed_import_recommend_read_and_offline_daily(tmp_path):
    db = setup_feed(tmp_path)
    listing = cli(db, 'feed', 'list')
    assert listing.returncode == 0 and '虚构演练订阅' in listing.stdout
    repeated = cli(db, 'feed', 'import-file', '1', str(tmp_path / 'synthetic.xml'))
    assert repeated.returncode == 0 and '新增 0' in repeated.stdout
    inbox = cli(db, 'inbox', '--topic', '电池')
    assert inbox.returncode == 0, inbox.stderr
    assert '虚构电池研究公告' in inbox.stdout
    assert '未核验' in inbox.stdout
    assert '首次采集' in inbox.stdout
    assert '虚构无日期条目' not in inbox.stdout
    item = cli(db, 'item', '1')
    assert item.returncode == 0 and '完全虚构' in item.stdout
    report_path = tmp_path / 'daily.txt'
    daily = cli(db, 'daily', '--no-fetch', '--output', str(report_path))
    assert daily.returncode == 0, daily.stderr
    report = report_path.read_text(encoding='utf-8')
    assert '虚构电池研究公告' in report and '发布时间未知' in report
    assert '未联网采集' in report and '因果簿日报' in report
    refused = cli(db, 'daily', '--no-fetch', '--output', str(report_path))
    assert refused.returncode != 0 and 'Traceback' not in refused.stderr
    assert report_path.read_text(encoding='utf-8') == report
    marked = cli(db, 'read', '1')
    assert marked.returncode == 0
    assert '虚构电池研究公告' not in cli(db, 'inbox').stdout
    assert cli(db, 'read', '999').returncode != 0
    assert cli(db, 'feed', 'disable', '1').returncode == 0
    assert '虚构无日期条目' not in cli(db, 'inbox').stdout
    assert cli(db, 'feed', 'enable', '1').returncode == 0
    assert '虚构无日期条目' in cli(db, 'inbox').stdout
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM claims').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM feed_items').fetchone()[0] == 2


def test_daily_requires_no_feed_defaults_or_paid_api(tmp_path):
    db = tmp_path / 'empty.sqlite3'
    result = cli(db, 'daily', '--no-fetch')
    assert result.returncode == 0, result.stderr
    assert '暂无' in result.stdout
    assert 'LLM' not in result.stderr


@pytest.mark.parametrize('args', [('inbox', '--days', '0'), ('daily', '--limit', '0'),
                                 ('inbox', '--per-source', '0')])
def test_invalid_queue_options_fail_cleanly(tmp_path, args):
    result = cli(tmp_path / 'demo.sqlite3', *args)
    assert result.returncode != 0
    assert 'Traceback' not in result.stderr


def test_subscription_rejects_private_url_without_network(tmp_path):
    result = cli(tmp_path / 'demo.sqlite3', 'feed', 'add', '--name', 'bad', '--url', 'http://127.0.0.1/feed')
    assert result.returncode != 0 and 'Traceback' not in result.stderr


def test_daily_existing_output_rejected_before_fetch(tmp_path, monkeypatch):
    from argparse import Namespace
    from causal_ledger import cli as module
    from causal_ledger.db import connect
    path = tmp_path / 'keep.txt'
    path.write_text('keep', encoding='utf-8')
    calls = []
    monkeypatch.setattr(module, 'collect_feeds', lambda *a, **k: calls.append(True), raising=False)
    args = Namespace(no_fetch=False, feed=None, output=str(path), days=7, limit=10,
                     topic=None, per_source=2, brief=False, html=False,
                     html_dir=None, fetch_details=False)
    conn = connect(':memory:')
    try:
        with pytest.raises(FileExistsError):
            module.cmd_daily(args, conn)
        assert not calls
        assert path.read_text(encoding='utf-8') == 'keep'
    finally:
        conn.close()


def test_daily_records_partial_fetch_failures_and_returns_nonzero(tmp_path, monkeypatch, capsys):
    from causal_ledger import cli as module
    from causal_ledger.db import connect
    from causal_ledger.feeds import add_subscription
    db = tmp_path / 'feeds.sqlite3'
    conn = connect(db)
    add_subscription(conn, '虚构', 'https://example.org/feed.xml')
    conn.close()
    monkeypatch.setattr(module, 'collect_feeds', lambda *a, **k: [
        {'feed_id': 1, 'added': 0, 'duplicates': 0, 'error': '模拟采集失败'}])
    with pytest.raises(SystemExit) as caught:
        module.main(['--db', str(db), 'daily', '--output', str(tmp_path / 'partial.txt')])
    assert caught.value.code
    text = (tmp_path / 'partial.txt').read_text(encoding='utf-8')
    assert '模拟采集失败' in text
    assert '因果簿日报' in text

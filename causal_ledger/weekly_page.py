"""Weekly rollup page: same chain grammar as 今日事报, but over a 7-day window.

Daily pages are temporary; the weekly page becomes the retained archive. The
weekly page therefore keeps a full "本周信号清单（保留出处）" so deleting the
daily pages does not lose the sources.
"""
from __future__ import annotations

from .daily_page import render_daily_page


def render_weekly_page(items: list[dict], start: str, end: str) -> str:
    if not start or not end:
        raise ValueError("weekly page needs start and end")
    return render_daily_page(items, end, title="本周事报",
                             period_start=start, period_end=end, weekly=True)

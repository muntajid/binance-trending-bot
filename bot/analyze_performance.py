"""Offline performance feedback loop for published Binance Square posts.

The bot cannot read Square view/like/reply counters through the public post
endpoint it uses, so the operator pastes metrics into ``data/performance/posts.csv``
(one row per published post) and this analyzer turns the log into decisions:

* median/mean views overall and per post type / hook family / coin;
* per-post and pooled engagement rate, using the same formula as the creator
  research report: (likes + replies) / views * 100;
* top and bottom performers with cautious, sample-size-aware language;
* one concrete "next test" suggestion at a time.

No strategy is ever labelled a guarantee of reach; small samples are flagged.

CSV columns (extras ignored, blanks tolerated)::

    published_utc,url,type,coin,hook_family,views,likes,replies,shares,followers_gain,notes

Usage::

    python -m bot.analyze_performance                      # default CSV location
    python -m bot.analyze_performance data/performance/posts.csv --days 30
"""

from __future__ import annotations

import argparse
import csv
import datetime
import statistics
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT_DIR / "data" / "performance" / "posts.csv"

MIN_SAMPLE_FOR_CONFIDENCE = 5


def _number(value: Any) -> float | None:
    try:
        number = float(str(value or "").strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def load_rows(path: Path, *, days: int | None = None, now: datetime.datetime | None = None) -> list[dict[str, Any]]:
    """Load metric rows, skipping blanks and rows without a view count."""

    if not path.is_file():
        raise FileNotFoundError(
            f"Performance log not found: {path}. Create it from the header in "
            "data/performance/posts.csv and paste one row per published post."
        )

    cutoff = None
    if days:
        moment = now or datetime.datetime.now(datetime.timezone.utc)
        cutoff = moment - datetime.timedelta(days=int(days))

    rows: list[dict[str, Any]] = []
    for raw in csv.DictReader(path.read_text(encoding="utf-8").splitlines()):
        if not isinstance(raw, dict):
            continue
        views = _number(raw.get("views"))
        if views is None or views <= 0:
            continue

        published: datetime.datetime | None = None
        stamp = str(raw.get("published_utc", "")).strip().replace("Z", "+00:00")
        if stamp:
            try:
                published = datetime.datetime.fromisoformat(stamp)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                published = None
        if cutoff is not None and (published is None or published < cutoff):
            continue

        likes = _number(raw.get("likes")) or 0.0
        replies = _number(raw.get("replies")) or 0.0
        shares = _number(raw.get("shares")) or 0.0
        rows.append(
            {
                "published_utc": stamp,
                "url": str(raw.get("url", "")).strip(),
                "type": str(raw.get("type", "unknown")).strip() or "unknown",
                "coin": str(raw.get("coin", "")).upper().strip(),
                "hook_family": str(raw.get("hook_family", "unknown")).strip() or "unknown",
                "views": views,
                "likes": likes,
                "replies": replies,
                "shares": shares,
                "followers_gain": _number(raw.get("followers_gain")),
                "engagement_rate": (likes + replies) / views * 100.0,
            }
        )
    return rows


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "unknown"), []).append(row)
    return groups


def _fmt(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    if digits == 0:
        return f"{value:,.0f}"
    return f"{value:,.{digits}f}"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the headline comparison numbers for a set of rows."""

    views = [row["views"] for row in rows]
    rates = [row["engagement_rate"] for row in rows]
    pooled = (
        (sum(row["likes"] + row["replies"] for row in rows) / sum(views)) * 100.0
        if views
        else None
    )
    return {
        "posts": len(rows),
        "median_views": _median(views),
        "mean_views": (sum(views) / len(views)) if views else None,
        "median_engagement": _median(rates),
        "pooled_engagement": pooled,
    }


def render_report(rows: list[dict[str, Any]]) -> str:
    """Human-readable comparison report with sample-size cautions."""

    if not rows:
        return "No performance rows with a positive view count found."

    overall = summarize(rows)
    lines = [
        "# Binance Square performance report",
        "",
        f"Posts analysed: {overall['posts']} | Median views: {_fmt(overall['median_views'])} | "
        f"Mean views: {_fmt(overall['mean_views'])} | "
        f"Median engagement: {_fmt(overall['median_engagement'], 3)}% | "
        f"Pooled engagement: {_fmt(overall['pooled_engagement'], 3)}%",
        "",
        "Formula: (likes + replies) / views x 100 — same as the creator research report.",
        "Reach and engagement rate are different goals; judge both before changing anything.",
        "",
    ]

    for key, title in (("type", "By post type"), ("hook_family", "By hook family"), ("coin", "By coin")):
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| " + key + " | posts | median views | median eng. % | verdict |")
        lines.append("| --- | --- | --- | --- | --- |")
        groups = _group(rows, key)
        ordered = sorted(
            groups.items(),
            key=lambda item: _median([row["views"] for row in item[1]]) or 0.0,
            reverse=True,
        )
        for name, group_rows in ordered[:15]:
            stats = summarize(group_rows)
            verdict = "low sample" if stats["posts"] < MIN_SAMPLE_FOR_CONFIDENCE else (
                "above median" if (stats["median_views"] or 0) >= (overall["median_views"] or 0) else "below median"
            )
            lines.append(
                f"| {name} | {stats['posts']} | {_fmt(stats['median_views'])} | "
                f"{_fmt(stats['median_engagement'], 3)} | {verdict} |"
            )
        lines.append("")

    ranked = sorted(rows, key=lambda row: row["views"], reverse=True)
    lines.append("## Top 3 by views")
    for row in ranked[:3]:
        lines.append(
            f"- {_fmt(row['views'])} views | {row['type']} | {row['hook_family']} | "
            f"{row['coin'] or '-'} | eng {row['engagement_rate']:.3f}% | {row['published_utc']} {row['url']}"
        )
    lines.append("")
    lines.append("## Bottom 3 by views")
    for row in ranked[-3:]:
        lines.append(
            f"- {_fmt(row['views'])} views | {row['type']} | {row['hook_family']} | "
            f"{row['coin'] or '-'} | eng {row['engagement_rate']:.3f}% | {row['published_utc']} {row['url']}"
        )
    lines.append("")

    suggestion = suggest_next_test(rows)
    lines.append("## Suggested next test")
    lines.append(suggestion)
    lines.append("")
    lines.append(
        "_Cautions: post age at capture differs, Square rounds public counters, "
        "and medians below 5 posts are directional hints, not conclusions. "
        "Nothing here guarantees reach; it only improves the odds of learning fast._"
    )
    return "\n".join(lines)


def suggest_next_test(rows: list[dict[str, Any]]) -> str:
    """One concrete experiment, chosen from the largest observed gap."""

    by_family = _group(rows, "hook_family")
    candidates = [
        (name, summarize(group_rows))
        for name, group_rows in by_family.items()
        if len(group_rows) >= MIN_SAMPLE_FOR_CONFIDENCE and name != "unknown"
    ]
    if len(candidates) >= 2:
        candidates.sort(key=lambda item: item[1]["median_views"] or 0, reverse=True)
        best, worst = candidates[0], candidates[-1]
        return (
            f"Hook family '{best[0]}' leads '{worst[0]}' on median views "
            f"({ _fmt(best[1]['median_views']) } vs { _fmt(worst[1]['median_views']) }). "
            f"Next: run {MIN_SAMPLE_FOR_CONFIDENCE} more posts using '{best[0]}' hooks on similar "
            "post types at similar hours, then re-measure before changing anything else."
        )

    by_type = _group(rows, "type")
    typed = [
        (name, summarize(group_rows))
        for name, group_rows in by_type.items()
        if len(group_rows) >= MIN_SAMPLE_FOR_CONFIDENCE
    ]
    if len(typed) >= 2:
        typed.sort(key=lambda item: item[1]["median_views"] or 0, reverse=True)
        return (
            f"'{typed[0][0]}' posts lead on median views ({_fmt(typed[0][1]['median_views'])}). "
            "Next: one more weekly slot in that shape, then re-measure."
        )

    return (
        f"Log at least {MIN_SAMPLE_FOR_CONFIDENCE} posts per hook family and type before "
        "drawing conclusions; correlation within a handful of posts is not signal."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyse the Binance Square performance log")
    parser.add_argument("csv", nargs="?", default=str(DEFAULT_CSV), help="Path to posts.csv")
    parser.add_argument("--days", type=int, default=None, help="Only include posts newer than N days")
    args = parser.parse_args(argv)

    rows = load_rows(Path(args.csv), days=args.days)
    print(render_report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

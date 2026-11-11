"""Topic-to-visual routing and deterministic quality checks for Square assets.

Research basis: AI_Researcher_Visual_Intelligence_Report.pdf (31 pages; 499 posts,
661 inspected images, Sep 16–Nov 11 2026). Observations are used as principles,
not as a template to imitate. Market-data cards stay code-rendered; never ask an
image model to draw numerical market content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VisualPlan:
    format: str
    purpose: str
    ratio: str
    rendering: str
    evidence: str


# Rules are ordered by specific intent; unknown material defaults to editorial
# restraint rather than inventing a data visualization without evidence.
_ROUTES = (
    ("receipt", ("pnl", "profit", "loss", "result", "target hit", "outcome"), VisualPlan("RECEIPT", "Show a verified outcome and its originating thesis", "4:5", "code + verified ledger", "ledger/result")),
    ("versus", ("versus", "compare", "comparison", "vs ", "etf flows", "inflows", "outflows"), VisualPlan("VERSUS", "Compare like-for-like values on a shared scale", "4:5", "code + verified data", "matched data")),
    ("data", ("whale", "transaction", "on-chain", "onchain", "funding", "open interest", "liquidation", "order book", "flow"), VisualPlan("DATA", "Make one sourced metric or flow the focal point", "1:1", "code + verified data", "source data")),
    ("thesis", ("setup", "trade", "price analysis", "technical", "breakout", "support", "resistance", "bitcoin news"), VisualPlan("THESIS", "Show real price structure, thesis and invalidation", "1:1", "code-rendered verified candles", "exchange candles and levels")),
    ("scenario", ("prediction", "forecast", "scenario", "bull", "bear", "outlook"), VisualPlan("SCENARIO", "Show distinct conditional paths and their triggers", "1:1", "code + verified levels", "explicit conditions")),
    ("explainer", ("education", "educational", "explain", "how ", "guide", "security", "blockchain", "regulation", "timeline"), VisualPlan("EXPLAINER", "Explain a concept or sequence in a few steps", "4:5", "code-built diagram", "validated source")),
)


def select_visual_plan(topic: str, intent: str = "") -> VisualPlan:
    """Choose an evidence-appropriate visual format from topic and post intent."""
    text = re.sub(r"\s+", " ", f"{intent} {topic}".lower())
    for _, terms, plan in _ROUTES:
        if any(term in text for term in terms):
            return plan
    return VisualPlan("EDITORIAL", "Illustrate the narrative without fabricated market facts", "1:1", "scene-only art + code text overlay if needed", "no numeric data required")


def validate_visual_asset(path: str | Path, *, minimum_bytes: int = 1000) -> tuple[bool, list[str]]:
    """Check image integrity/basic mobile format; this does not claim human inspection."""
    from PIL import Image

    file_path = Path(path)
    issues: list[str] = []
    if not file_path.is_file() or file_path.stat().st_size < minimum_bytes:
        return False, ["missing or undersized image"]
    try:
        with Image.open(file_path) as image:
            image.verify()
        with Image.open(file_path) as image:
            width, height = image.size
            if width < 720:
                issues.append("width below 720px; mobile detail may be weak")
            ratio = width / height
            if not (0.68 <= ratio <= 1.02):
                issues.append("aspect ratio outside recommended square/4:5 range")
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                issues.append("unsupported image format")
    except (OSError, ValueError):
        issues.append("image cannot be decoded")
    return not issues, issues


def quality_checklist(plan: VisualPlan, *, numeric_claims: bool = False) -> list[str]:
    """Return required human/automated review points; no visual inspection implied."""
    checks = [
        "caption-image relevance and one clear claim",
        "main message readable at 360px preview; adequate contrast and safe margins",
        "balanced hierarchy, restrained annotation, no irrelevant decoration",
        "project branding and layout vary appropriately without losing recognition",
    ]
    if numeric_claims or plan.format != "EDITORIAL":
        checks += ["verify every displayed number/label against source data and caption", "show accurate source, pair/timeframe and UTC timestamp where applicable"]
    if plan.format == "THESIS":
        checks.append("confirm real candles, entry/target/invalidation, and distinct direction semantics")
    return checks

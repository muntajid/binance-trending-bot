"""Patch the downloaded official Square helper so media uploads stop timing out.

Binance Square processes images asynchronously: after the file is PUT to S3 the
official skill polls ``/image/imageStatus`` with a *hard-coded* budget of
``10`` retries at ``3000`` ms, i.e. roughly 30 seconds. Under load the media
pipeline regularly needs longer than that, so a perfectly good upload is
abandoned with ``Poll timed out after 10 retries`` and the scheduled post is
never created (run 39250309711 is the reference failure).

This patch keeps the official workflow and only hardens the transport:

* the poll budget becomes environment-configurable and much larger
  (default 20 retries x 4000 ms = 80 s per image);
* every API call gets a fetch timeout so a stalled socket cannot hang the job;
* ``status`` payloads are compared numerically, because a ``"1"`` string would
  otherwise be treated as "still processing" forever.

Markers are looked up defensively. An upstream refactor degrades to a warning
instead of failing the run, because :mod:`bot.square_publisher` retries the
media upload and falls back to a text post on its own. Pass ``--strict`` to
turn a missing marker into an error.
"""

from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_LIB_PATH = Path(
    "/tmp/binance-skills-hub/skills/binance/square-post/scripts/lib.mjs"
)

POLL_INTERVAL_OLD = "const POLL_INTERVAL_MS = 3000;"
POLL_INTERVAL_NEW = (
    "const POLL_INTERVAL_MS = positiveNumber(process.env.SQUARE_POLL_INTERVAL_MS, 4000);"
)
POLL_RETRIES_OLD = "const MAX_POLL_RETRIES = 10;"
POLL_RETRIES_NEW = (
    "const MAX_POLL_RETRIES = positiveNumber(process.env.SQUARE_POLL_MAX_RETRIES, 20);\n"
    "const API_TIMEOUT_MS = positiveNumber(process.env.SQUARE_API_TIMEOUT_MS, 30000);"
)

HELPER_OLD = "export async function api("
HELPER_NEW = """function positiveNumber(raw, fallback) {
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

export async function api("""

FETCH_OLD = """  const res = await fetch(`${baseUrl}${endpoint}`, {
    method: "POST",
"""
FETCH_NEW = """  const res = await fetch(`${baseUrl}${endpoint}`, {
    method: "POST",
    signal: AbortSignal.timeout(API_TIMEOUT_MS),
"""

POLL_START = "export async function pollImageStatus(apiKey, fileTicket) {"
POLL_MARKER = "last status ${lastStatus}"
POLL_NEW = """export async function pollImageStatus(apiKey, fileTicket) {
  const startedAt = Date.now();
  let lastStatus = "unknown";
  for (let i = 0; i < MAX_POLL_RETRIES; i++) {
    const data = await api("/image/imageStatus", apiKey, { fileTicket });
    const status = Number(data?.status);
    lastStatus = Number.isFinite(status) ? String(status) : String(data?.status ?? "missing");
    if (status === 1) return data;
    if (status === 2) {
      throw new Error(`Processing failed: ${data?.failedReason ?? "unknown reason"}`);
    }
    const waited = Math.round((Date.now() - startedAt) / 1000);
    console.log(`  Processing... (${i + 1}/${MAX_POLL_RETRIES}, status ${lastStatus}, ${waited}s waited)`);
    if (i + 1 < MAX_POLL_RETRIES) await sleep(POLL_INTERVAL_MS);
  }
  const waited = Math.round((Date.now() - startedAt) / 1000);
  throw new Error(
    `Poll timed out after ${MAX_POLL_RETRIES} retries (~${waited}s, last status ${lastStatus})`
  );
}"""

REQUIRED_FRAGMENTS = (
    POLL_INTERVAL_NEW,
    "positiveNumber(process.env.SQUARE_POLL_MAX_RETRIES, 20)",
    "signal: AbortSignal.timeout(API_TIMEOUT_MS)",
    POLL_MARKER,
)


def _replace_once(text: str, old: str, new: str, label: str) -> tuple[str, str | None]:
    """Replace ``old`` with ``new`` exactly once, or report why it was skipped."""

    if new in text:
        return text, None  # Already patched; running the patch twice is safe.

    occurrences = text.count(old)
    if occurrences == 0:
        return text, f"{label}: marker not found (official helper changed shape)"
    if occurrences > 1:
        return text, f"{label}: marker is ambiguous ({occurrences} matches)"

    return text.replace(old, new, 1), None


def _replace_poll_function(text: str) -> tuple[str, str | None]:
    """Swap the official poll loop for the budget-aware implementation."""

    if POLL_MARKER in text:
        return text, None

    start = text.find(POLL_START)
    if start == -1:
        return text, "poll loop: function signature not found (official helper changed shape)"

    end = text.find("\n}\n", start)
    if end == -1:
        return text, "poll loop: closing brace not found (official helper changed shape)"

    # ``text[end:end + 3]`` is the official function's "\n}\n"; drop it entirely so
    # the replacement keeps exactly one closing brace.
    return text[:start] + POLL_NEW + "\n" + text[end + 3 :], None


def patch_library(path: Path) -> list[str]:
    """Apply the upload-hardening patch and return a list of warnings."""

    if not path.is_file():
        raise FileNotFoundError(f"Square helper library not found: {path}")

    text = path.read_text(encoding="utf-8")
    warnings: list[str] = []

    replacements = (
        (POLL_INTERVAL_OLD, POLL_INTERVAL_NEW, "poll interval"),
        (POLL_RETRIES_OLD, POLL_RETRIES_NEW, "poll budget"),
        (HELPER_OLD, HELPER_NEW, "number helper"),
        (FETCH_OLD, FETCH_NEW, "fetch timeout"),
    )
    for old, new, label in replacements:
        text, warning = _replace_once(text, old, new, label)
        if warning:
            warnings.append(warning)

    text, warning = _replace_poll_function(text)
    if warning:
        warnings.append(warning)

    for fragment in REQUIRED_FRAGMENTS:
        if fragment not in text:
            warnings.append(f"validation: expected fragment missing after patch: {fragment!r}")

    path.write_text(text, encoding="utf-8")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_LIB_PATH),
        help="Path to the downloaded square-post scripts/lib.mjs file",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when an official marker cannot be found instead of warning",
    )
    args = parser.parse_args()

    warnings = patch_library(Path(args.path))

    for warning in warnings:
        print(f"[Upload Patch] WARNING {warning}")

    if warnings and args.strict:
        print("[Upload Patch] Strict mode: refusing to continue with an unpatched helper")
        return 1

    if not warnings:
        print(f"[Upload Patch] Applied and validated: {args.path}")
    else:
        print("[Upload Patch] Partial patch; publisher retry/fallback still protects the post")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

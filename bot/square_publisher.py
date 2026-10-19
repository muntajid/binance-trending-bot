"""Retry-aware Binance Square publishing on top of the official skill scripts.

The official ``square-post`` helpers are single-shot: any hiccup in the
asynchronous image pipeline (``Poll timed out after 10 retries``, a stalled S3
handshake, a transient API 5xx) makes the script exit non-zero, which used to
take the whole scheduled workflow down with it.

This wrapper keeps the official scripts as the only thing that talks to Binance
and adds the operational safety net around them:

* the media upload is retried with backoff, because uploading an image that is
  never attached to a post has no side effects;
* once the official script reaches its ``Publishing...`` step the outcome is
  unknown, so neither retries nor the text fallback run - that guardrail is what
  keeps a flaky network from producing duplicate posts;
* if images never become ready, the post still ships as a text-only Square post
  instead of being lost for that slot;
* degraded runs are annotated in the GitHub UI (``::warning::`` + step summary)
  so a fallback is visible without failing the schedule.

``bot.monitor_runner`` and ``.github/workflows/signal.yml`` both publish through
:func:`publish_to_square`. Only the CLI (``python -m bot.square_publisher``, i.e.
the signal workflow) reports the verdict to :mod:`bot.schedule`: success marks
the day's slot published, a retryable failure hands it back to the queue, and a
failure whose request may already have landed consumes it without a retry. The
monitor publishes event-driven proof cards rather than slots, so it owns no
queue state.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from bot import schedule as schedule_state

ROOT_DIR = Path(__file__).resolve().parents[1]
SKILLS_DIR = Path(os.getenv("BINANCE_SKILLS_DIR", "/tmp/binance-skills-hub"))
SCRIPTS_DIR = SKILLS_DIR / "skills" / "binance" / "square-post" / "scripts"
POST_IMAGE_SCRIPT = SCRIPTS_DIR / "post-image.mjs"
POST_TEXT_SCRIPT = SCRIPTS_DIR / "post-text.mjs"

# Printed by the official scripts immediately before the create-post call.
PUBLISH_MARKER = "Publishing..."

MAX_IMAGES = 4
DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = (20, 40)
DEFAULT_DEADLINE_SECONDS = 300
SCRIPT_TIMEOUT_SECONDS = 420

Runner = Callable[..., tuple[int, str]]


class PublishError(RuntimeError):
    """Raised when no post could be created and no fallback survived.

    ``duplicate_risk`` marks the case where the create-post request had already
    been issued, so the slot must not be re-queued and retried - a retry could
    publish the same content twice.
    """

    def __init__(self, message: str, *, output: str = "", duplicate_risk: bool = False) -> None:
        super().__init__(message)
        self.output = output
        self.duplicate_risk = duplicate_risk


@dataclass
class PublishOutcome:
    """What actually reached Binance Square."""

    mode: str  # "image", "text", or "none"
    published: bool
    attempts: int = 0
    degraded: bool = False
    detail: str = ""


def default_runner(command: Sequence[str], timeout: float | None = None) -> tuple[int, str]:
    """Run a Square helper script, returning ``(returncode, combined_output)``."""

    limit = SCRIPT_TIMEOUT_SECONDS if timeout is None else max(30.0, float(timeout))
    limit = min(SCRIPT_TIMEOUT_SECONDS, limit)
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=int(limit),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return 124, f"{partial}\nError: script timed out after {int(limit)}s"

    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return completed.returncode, output.strip()


def classify_attempt(returncode: int, output: str) -> str:
    """Map an official-script exit to ``published`` / ``unknown`` / ``retryable``."""

    if returncode == 0:
        return "published"
    if PUBLISH_MARKER in output:
        # The create-post call was already issued; a retry could duplicate the post.
        return "unknown"
    return "retryable"


def build_command(
    script: Path, text: str, images: Sequence[str] = ()
) -> list[str]:
    parts = ["node", str(script), "--text", text]
    if images:
        parts += ["--images", ",".join(str(image) for image in images)]
    return parts


def resolve_images(images: Iterable[str] | str | None) -> list[str]:
    """Keep existing image paths only, capped at the Square maximum."""

    if isinstance(images, str):
        candidates: list[str] = [part.strip() for part in images.split(",")]
    else:
        candidates = [str(part).strip() for part in (images or [])]

    resolved: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_file():
            print(f"[Publish] Skipping missing image: {candidate}")
            continue
        resolved.append(str(path.resolve()))
        if len(resolved) >= MAX_IMAGES:
            break
    return resolved


def _annotate(level: str, message: str, title: str = "Binance Square publish") -> None:
    """Emit a GitHub Actions annotation when running inside Actions."""

    if not os.getenv("GITHUB_ACTIONS"):
        return
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} title={title}::{escaped}")


def _append_summary(markdown: str) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown.rstrip() + "\n")
    except OSError as exc:  # pragma: no cover - never fail a post over a summary
        print(f"[Publish] Could not write step summary: {exc}")


def _script_or_raise(script: Path) -> Path:
    if not script.is_file():
        raise FileNotFoundError(
            f"Square posting script not found: {script}. "
            "Did the 'Download official Binance Square posting scripts' step run?"
        )
    return script


def publish_to_square(
    text: str,
    images: Iterable[str] | str | None = (),
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: Sequence[int] = DEFAULT_BACKOFF_SECONDS,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    fallback_to_text: bool = True,
    runner: Runner | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> PublishOutcome:
    """Publish to Binance Square with retries for the media stage only.

    Returns the :class:`PublishOutcome`; raises :class:`PublishError` when
    nothing was published or when a post request was already in flight.
    """

    from bot.poster import assert_clean_post_text  # imported late to keep CLI startup cheap

    body = assert_clean_post_text(str(text).strip())
    valid_images = resolve_images(images)
    run = runner or default_runner

    started = clock()
    attempts_used = 0
    last_output = ""

    if valid_images:
        command = build_command(_script_or_raise(POST_IMAGE_SCRIPT), body, valid_images)
        for attempt in range(1, max(1, int(attempts)) + 1):
            attempts_used += 1
            remaining = deadline_seconds - (clock() - started)
            if attempt > 1 and remaining <= 30:
                print("[Publish] Publish deadline reached; skipping further image attempts")
                break
            print(
                f"[Publish] Sending image post with {len(valid_images)} image(s) "
                f"(attempt {attempt}/{max(1, int(attempts))}, {max(0, remaining):.0f}s left)"
            )
            returncode, output = run(command, max(30.0, remaining))
            last_output = output
            if output:
                print(output)

            status = classify_attempt(returncode, output)
            if status == "published":
                degraded = attempt > 1
                detail = f"image post published on attempt {attempt}"
                if degraded:
                    _annotate("warning", f"Image post only succeeded after retry (attempt {attempt}).")
                    _append_summary(
                        "### ⚠️ Binance Square publish\n"
                        f"- Image post needed a retry: attempt {attempt} of {attempts} succeeded."
                    )
                print(f"[Publish] {detail}")
                return PublishOutcome("image", True, attempts_used, degraded, detail)

            if status == "unknown":
                raise PublishError(
                    "Binance Square create-post request failed after it was already sent. "
                    "Not retrying to avoid a duplicate post - check the Square profile "
                    "before re-running this workflow.",
                    output=output,
                    duplicate_risk=True,
                )

            remaining = deadline_seconds - (clock() - started)
            if attempt >= max(1, int(attempts)) or remaining <= 30:
                break

            schedule = list(backoff_seconds) or [20]
            delay = float(schedule[min(attempt - 1, len(schedule) - 1)])
            delay = max(0.0, min(delay, remaining - 10))
            print(
                f"[Publish] Image stage failed (returncode {returncode}); "
                f"re-uploading in {delay:.0f}s"
            )
            if delay > 0:
                sleep(delay)

        if not fallback_to_text:
            raise PublishError(
                "Image post failed after "
                f"{attempts_used} attempt(s); text fallback disabled",
                output=last_output,
            )
        print("[Publish] Image uploads are still not ready; falling back to a text post")
        _annotate(
            "warning",
            "Charts could not be attached; this slot was published as a text-only post.",
        )
        _append_summary(
            "### ⚠️ Binance Square publish\n"
            f"- Image post failed after {attempts_used} attempt(s); "
            "published as a text-only post instead."
        )
    else:
        print("[Publish] No valid image supplied; sending text-only post")

    text_command = build_command(_script_or_raise(POST_TEXT_SCRIPT), body)
    returncode, output = run(text_command, deadline_seconds - (clock() - started))
    last_output = output
    if output:
        print(output)

    if returncode == 0:
        detail = "text-only post published"
        print(f"[Publish] {detail}")
        return PublishOutcome(
            "text",
            True,
            attempts_used + 1,
            degraded=bool(valid_images),
            detail=detail,
        )

    raise PublishError(
        "Binance Square publishing failed after every attempt",
        output=last_output,
        duplicate_risk=classify_attempt(returncode, output) == "unknown",
    )


def _read_text(argument_text: str | None, argument_file: str | None) -> str:
    if argument_text:
        return argument_text
    if not argument_file:
        raise SystemExit("Error: --text or --text-file is required")
    path = Path(argument_file)
    if not path.is_file():
        raise SystemExit(f"Error: post text file not found: {path}")
    return path.read_text(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish a Binance Square post with media-stage retries and a text fallback."
    )
    parser.add_argument("--text", help="Post body")
    parser.add_argument("--text-file", help="Read the post body from this file")
    parser.add_argument("--images", default="", help="Comma-separated image paths (max 4)")
    parser.add_argument(
        "--attempts",
        type=int,
        default=int(os.getenv("SQUARE_PUBLISH_ATTEMPTS", DEFAULT_ATTEMPTS)),
        help="Image upload attempts before the text fallback runs",
    )
    parser.add_argument(
        "--deadline-seconds",
        type=float,
        default=float(
            os.getenv("SQUARE_PUBLISH_DEADLINE_SECONDS", DEFAULT_DEADLINE_SECONDS)
        ),
        help="Total wall-clock budget for the image stage",
    )
    parser.add_argument(
        "--no-text-fallback",
        action="store_true",
        help="Fail instead of publishing a text-only post when images never become ready",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="Do not touch the daily posting queue (used for standalone or test publishes)",
    )
    args = parser.parse_args(argv)

    def update_schedule(outcome: PublishOutcome | None, error: PublishError | None) -> None:
        """Hand the queue the verdict so a failed slot is retried, not lost.

        Without this the run that could not publish still consumed a slot, which
        is how a Square outage silently turns "12 posts a day" into nine.
        """

        if args.no_state or schedule_state.state_disabled():
            return
        try:
            if outcome is not None:
                record = schedule_state.publish_succeeded(
                    mode=outcome.mode,
                    attempts=outcome.attempts,
                    degraded=outcome.degraded,
                )
                if record:
                    print(
                        f"[Schedule] Slot {record['slot']} ({record['type']}) marked published"
                    )
                next_slot = (schedule_state.status() or {}).get("next_slot") or {}
                if next_slot:
                    print(
                        f"[Schedule] Next queued slot: {next_slot['slot']} ({next_slot['type']})"
                    )
            elif error is not None and error.duplicate_risk:
                schedule_state.publish_uncertain(str(error)[:300])
                print("[Schedule] Slot consumed without retry (possible duplicate post risk)")
            elif error is not None:
                schedule_state.abort_run(str(error)[:300])
        except Exception as state_exc:  # noqa: BLE001 - never mask the publish result
            print(f"[Schedule] Warning: queue state not updated ({state_exc})", file=sys.stderr)

    text = _read_text(args.text, args.text_file)
    backoff = tuple(
        int(part)
        for part in os.getenv(
            "SQUARE_PUBLISH_BACKOFF_SECONDS", ",".join(str(v) for v in DEFAULT_BACKOFF_SECONDS)
        ).split(",")
        if part.strip()
    ) or DEFAULT_BACKOFF_SECONDS

    try:
        outcome = publish_to_square(
            text,
            args.images,
            attempts=args.attempts,
            backoff_seconds=backoff,
            deadline_seconds=args.deadline_seconds,
            fallback_to_text=not args.no_text_fallback,
        )
    except PublishError as exc:
        print(f"[Publish] FAILED: {exc}", file=sys.stderr)
        _annotate("error", str(exc))
        _append_summary(
            "### ❌ Binance Square publish\n"
            f"- {exc}\n\n```\n{exc.output[-1500:]}\n```"
        )
        update_schedule(None, exc)
        return 1

    print(
        f"[Publish] Done: {outcome.mode} post published "
        f"({outcome.attempts} attempt(s){', degraded' if outcome.degraded else ''})"
    )
    _append_summary(
        "### ✅ Binance Square publish\n"
        f"- Mode: {outcome.mode}\n- Attempts: {outcome.attempts}\n- {outcome.detail}"
    )
    update_schedule(outcome, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

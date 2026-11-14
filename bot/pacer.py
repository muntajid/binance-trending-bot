"""Hourly pacer: one automatic Square post per UTC hour (24 per day).

Why this exists
---------------
GitHub cron is best effort. With ``0 */2 * * *`` the signal workflow targeted
12 runs a day but the publish ledger shows only 2-5 posts per day actually
went out (Nov 3-14, 2026): most scheduled events were delayed or dropped.
An hourly cron alone would therefore never reach 24 posts.

The pacer is a long-running workflow job that acts as a reliable clock:

* every ``check_every_minutes`` it reads ``data/schedule_state.json`` from the
  branch through the GitHub API and asks :func:`bot.schedule.due_status`
  whether the current UTC hour still needs its post;
* if so, and no signal run is already queued/running, it dispatches
  ``signal.yml`` with ``paced=true``. A run that failed (the slot goes back to
  the queue) or was cancelled by the shared concurrency group is retried on a
  later tick, up to ``max_dispatches_per_hour`` times per hour;
* GitHub caps a job at 6 hours, so after ``handoff_after_minutes`` the pacer
  dispatches a fresh copy of itself and exits. An hourly cron on the pacer
  workflow revives the chain if a handoff is ever lost, and the leader check
  keeps exactly one pacer active.

Dispatching ``workflow_dispatch`` with the job's ``GITHUB_TOKEN`` is allowed
by GitHub (it is the documented exception to the "token events don't trigger
workflows" rule) and needs ``permissions: actions: write``. Public
repositories pay nothing for these runner minutes.

Only the standard library is used, so the job needs no ``pip install``.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from bot import schedule

SIGNAL_WORKFLOW = "signal.yml"
PACER_WORKFLOW = "pacer.yml"
STATE_PATH = "data/schedule_state.json"
DEFAULT_PACER_SETTINGS = {
    "check_every_minutes": 5,
    "max_dispatches_per_hour": 3,
    "handoff_after_minutes": 330,
}
# A finishing pacer can stay "in_progress" for a short while after it
# dispatched its successor; the successor waits this long before giving up.
LEADER_WAIT_SECONDS = 240
LEADER_POLL_SECONDS = 30
TICK_OFFSET_SECONDS = 20


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _log(message: str) -> None:
    print(f"[Pacer {_utc_now().strftime('%H:%M:%S')}] {message}", flush=True)


# ---------------------------------------------------------------------------
# Settings and pure decision helpers (unit-tested)
# ---------------------------------------------------------------------------

def pacer_settings(config: dict[str, Any] | None = None) -> dict[str, float]:
    settings = config if config is not None else schedule.load_config()
    raw = settings.get("schedule", {}).get("pacer", {})
    raw = raw if isinstance(raw, dict) else {}
    merged: dict[str, float] = {}
    for key, default in DEFAULT_PACER_SETTINGS.items():
        try:
            merged[key] = max(1.0, float(raw.get(key, default)))
        except (TypeError, ValueError):
            merged[key] = float(default)
    # A job is killed at 360 minutes; leave room to hand off cleanly.
    merged["handoff_after_minutes"] = min(merged["handoff_after_minutes"], 345.0)
    return merged


def next_tick(now: datetime.datetime, every_minutes: float) -> datetime.datetime:
    """Next check time aligned to the clock (e.g. :00:20, :05:20, ...)."""

    step = max(60, int(every_minutes * 60))
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (now - midnight).total_seconds() - TICK_OFFSET_SECONDS
    slots = int(elapsed // step) + 1
    return midnight + datetime.timedelta(seconds=slots * step + TICK_OFFSET_SECONDS)


def active_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs that are queued, waiting on the concurrency group, or running."""

    return [run for run in runs if str(run.get("status", "")).lower() != "completed"]


def decide(
    *,
    now: datetime.datetime,
    state: dict[str, Any] | None,
    signal_active: bool,
    dispatches_this_hour: int,
    max_dispatches: int,
    min_gap: float,
) -> tuple[bool, str]:
    """Return ``(dispatch?, reason)`` for one pacer tick."""

    if signal_active:
        return False, "a signal run is already queued or running"
    verdict = schedule.due_status(state, now=now, min_gap=min_gap)
    if not verdict["due"]:
        return False, verdict["reason"]
    if dispatches_this_hour >= max_dispatches:
        return False, (
            f"hour still unserved but {dispatches_this_hour} dispatch(es) already tried; "
            "waiting for the next hour"
        )
    return True, verdict["reason"]


def leadership(my_run_id: int, pacer_runs: list[dict[str, Any]]) -> str:
    """``lead`` unless an older pacer run is still active (then ``yield``)."""

    for run in active_runs(pacer_runs):
        try:
            run_id = int(run.get("id"))
        except (TypeError, ValueError):
            continue
        if run_id != my_run_id and run_id < my_run_id and run.get("status") == "in_progress":
            return "yield"
    return "lead"


# ---------------------------------------------------------------------------
# GitHub REST client (stdlib only)
# ---------------------------------------------------------------------------

class GitHubClient:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com") -> None:
        self.repo = repo
        self.token = token
        self.api = api.rstrip("/")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, *, raw: bool = False) -> Any:
        url = f"{self.api}/repos/{self.repo}/{path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("Accept", "application/vnd.github.raw+json" if raw else "application/vnd.github+json")
        request.add_header("User-Agent", "binance-trending-bot-pacer")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))

    def workflow_runs(self, workflow: str, per_page: int = 15) -> list[dict[str, Any]]:
        result = self._request("GET", f"actions/workflows/{workflow}/runs?per_page={per_page}")
        return list((result or {}).get("workflow_runs") or [])

    def dispatch(self, workflow: str, ref: str, inputs: dict[str, str] | None = None) -> None:
        self._request("POST", f"actions/workflows/{workflow}/dispatches", {"ref": ref, "inputs": inputs or {}})

    def read_state(self, ref: str) -> dict[str, Any] | None:
        quoted = urllib.parse.quote(ref, safe="")
        try:
            value = self._request("GET", f"contents/{STATE_PATH}?ref={quoted}", raw=True)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        return value if isinstance(value, dict) else None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _claim_leadership(client: GitHubClient, my_run_id: int, sleep: Callable[[float], None]) -> bool:
    waited = 0
    while True:
        verdict = leadership(my_run_id, client.workflow_runs(PACER_WORKFLOW))
        if verdict == "lead":
            return True
        if waited >= LEADER_WAIT_SECONDS:
            return False
        _log("An older pacer is still running; waiting in case it is handing off")
        sleep(LEADER_POLL_SECONDS)
        waited += LEADER_POLL_SECONDS


def run(
    *,
    client: GitHubClient,
    ref: str,
    my_run_id: int,
    now: Callable[[], datetime.datetime] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
    dry_run: bool = False,
) -> dict[str, Any]:
    config = schedule.load_config()
    settings = pacer_settings(config)
    min_gap = schedule.min_gap_minutes(config)
    started = now()
    deadline = started + datetime.timedelta(minutes=settings["handoff_after_minutes"])
    report: dict[str, Any] = {"dispatched": [], "handoff": False, "led": False}

    if not _claim_leadership(client, my_run_id, sleep):
        _log("Another pacer is active; exiting so exactly one pacer drives the schedule")
        return report
    report["led"] = True
    _log(
        f"Leading on ref {ref}: checks every {settings['check_every_minutes']:g} min, "
        f"min gap {min_gap:g} min, handoff at {deadline.strftime('%H:%M')} UTC"
    )

    dispatches: dict[str, int] = {}
    while now() < deadline:
        current = now()
        hour_key = current.strftime("%Y-%m-%dT%H")
        try:
            signal_active = bool(active_runs(client.workflow_runs(SIGNAL_WORKFLOW)))
            state = None if signal_active else client.read_state(ref)
            go, reason = decide(
                now=current,
                state=state,
                signal_active=signal_active,
                dispatches_this_hour=dispatches.get(hour_key, 0),
                max_dispatches=int(settings["max_dispatches_per_hour"]),
                min_gap=min_gap,
            )
            if go:
                _log(f"Dispatching {SIGNAL_WORKFLOW} ({reason})")
                if not dry_run:
                    client.dispatch(SIGNAL_WORKFLOW, ref, {"paced": "true"})
                dispatches[hour_key] = dispatches.get(hour_key, 0) + 1
                report["dispatched"].append(current.isoformat())
            else:
                _log(f"No dispatch: {reason}")
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            # One bad API call must not end the chain; the next tick retries.
            _log(f"GitHub API problem, retrying next tick: {exc}")

        wake = next_tick(now(), settings["check_every_minutes"])
        if wake >= deadline:
            break
        sleep(max(1.0, (wake - now()).total_seconds()))

    _log(f"Handing off to a fresh pacer run ({PACER_WORKFLOW})")
    if not dry_run:
        for attempt in range(1, 4):
            try:
                client.dispatch(PACER_WORKFLOW, ref)
                report["handoff"] = True
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                _log(f"Handoff attempt {attempt} failed: {exc}")
                sleep(10 * attempt)
        if not report["handoff"]:
            _log("Handoff failed; the hourly cron on the pacer workflow will revive the chain")
    return report


def main() -> int:
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not repo or not token:
        print("[Pacer] GITHUB_REPOSITORY and GITHUB_TOKEN are required", file=sys.stderr)
        return 2
    ref = os.getenv("PACER_REF", "").strip() or os.getenv("GITHUB_REF_NAME", "").strip() or "main"
    try:
        my_run_id = int(os.getenv("GITHUB_RUN_ID", "0"))
    except ValueError:
        my_run_id = 0
    dry_run = os.getenv("PACER_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}

    report = run(client=GitHubClient(repo, token), ref=ref, my_run_id=my_run_id, dry_run=dry_run)

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### ⏱️ Hourly pacer\n")
            if not report["led"]:
                handle.write("- Another pacer was already active; this run exited.\n")
            else:
                handle.write(f"- Signal runs dispatched: **{len(report['dispatched'])}**\n")
                handle.write(f"- Handed off to a fresh pacer: **{'yes' if report['handoff'] else 'no'}**\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

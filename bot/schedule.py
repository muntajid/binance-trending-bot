"""Clock-independent queue for the 12-slot daily posting plan.

Why this module exists
----------------------
The post type used to be derived from the wall clock inside ``main_signal``::

    slot_index = (utc_hour // 2) % 12

That tied *what* gets posted to *when* the runner happened to start. Three
manual ``workflow_dispatch`` runs at 12:27, 12:29 and 12:31 UTC therefore all
resolved to slot 6 and pushed three identical meme posts, while the slots no
run landed on (and every slot whose run crashed) were silently skipped. The
schedule could both repeat and lose posts, and there was no way to ask for a
specific type by hand.

This module makes the plan durable state instead of a clock lookup:

* one rotation per UTC day, loaded from ``bot/config.json`` (single source of
  truth - the workflow and the engine can no longer drift apart);
* every run takes the **next pending slot** in plan order, so clicking
  "Run workflow" five times yields five *different* posts;
* a slot counts as published only once Binance Square accepted the post. A
  generation or publish failure hands the slot back to the front of the queue
  and rolls back the coin cooldown / open trade that the run created, so the
  retry is not poisoned by state nobody saw;
* a run killed between generation and publish (cancelled job, workflow
  timeout) is healed on the next load once its in-flight entry goes stale;
* when the day's queue is exhausted the rotation wraps, so a manual run is
  never a silent no-op - the 24h coin cooldown and the caption similarity
  guard are what keep a wrap from becoming duplicate content.

State files (all under ``data/``, committed by the workflow):

``schedule_state.json``   the day's plan: pending / in-flight / published
``pending_run.json``      the effects of the run that is currently publishing
``publish_receipt.json``  the last publish outcome, for auditability
``slot_state.json``       legacy mirror, kept in sync for existing tooling
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT_DIR / "bot" / "config.json"
STATE_FILE = ROOT_DIR / "data" / "schedule_state.json"
PENDING_RUN_FILE = ROOT_DIR / "data" / "pending_run.json"
PUBLISH_RECEIPT_FILE = ROOT_DIR / "data" / "publish_receipt.json"
LEGACY_SLOT_STATE_FILE = ROOT_DIR / "data" / "slot_state.json"

ACTIVE_TRADES_FILE = ROOT_DIR / "data" / "active_trades.json"
POSTED_COINS_FILE = ROOT_DIR / "data" / "posted_coins.json"

STATE_VERSION = 1
POST_TYPES = ("market_overview", "rapid_alert", "explained_setup", "meme_community")
DEFAULT_IN_FLIGHT_TIMEOUT_MINUTES = 45


class ScheduleError(RuntimeError):
    """Raised when the persisted schedule cannot be understood."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def state_disabled() -> bool:
    """Return True when state writes must be skipped (unit tests, local dry runs)."""

    return _env_flag("SCHEDULE_STATE_DISABLED")


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path, default: Any, *, tolerant: bool = False) -> Any:
    """Read a JSON file.

    ``tolerant`` is used for the queue's own state files: a half-written or
    hand-edited ``schedule_state.json`` must not stop an autonomous bot forever,
    so the day simply starts from a fresh queue with a loud warning.
    """

    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if tolerant:
            print(f"[Schedule] Warning: ignoring unreadable {path.name} ({exc})", file=sys.stderr)
            return default
        raise ScheduleError(f"Unable to read JSON from {path}: {exc}") from exc


def _read_list(path: Path) -> list[Any]:
    """Best-effort list read: a broken state file must not stop a run."""

    value = _read_json(path, [], tolerant=True)
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Plan loading
# ---------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    config = _read_json(CONFIG_FILE, {})
    return config if isinstance(config, dict) else {}


def load_plan(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return the ordered slots that make up one daily rotation.

    ``bot/config.json`` is the only definition of the plan.
    """

    settings = config if config is not None else load_config()
    raw_slots = settings.get("schedule", {}).get("slots")
    if not isinstance(raw_slots, list) or not raw_slots:
        raise ScheduleError("bot/config.json must define a non-empty schedule.slots list.")

    plan: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, entry in enumerate(raw_slots):
        if not isinstance(entry, dict):
            raise ScheduleError(f"schedule.slots[{index}] is not an object")

        post_type = str(entry.get("type", "")).strip()
        if post_type not in POST_TYPES:
            raise ScheduleError(
                f"schedule.slots[{index}] has unknown type {post_type!r}; "
                f"expected one of {', '.join(POST_TYPES)}"
            )

        try:
            slot = int(entry.get("slot", index))
        except (TypeError, ValueError) as exc:
            raise ScheduleError(f"schedule.slots[{index}] has a non-numeric slot") from exc

        if slot in seen:
            raise ScheduleError(f"Duplicate schedule slot {slot} in bot/config.json")
        seen.add(slot)

        plan.append(
            {
                "slot": slot,
                "type": post_type,
                "name": str(entry.get("name") or f"{post_type} #{slot}"),
                "hour_utc": entry.get("hour_utc"),
            }
        )

    plan.sort(key=lambda item: item["slot"])
    return plan


def type_counts(plan: list[dict[str, Any]] | None = None) -> dict[str, int]:
    entries = plan if plan is not None else load_plan()
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["type"]] = counts.get(entry["type"], 0) + 1
    return counts


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _fresh_state(day: str, plan: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "day_utc": day,
        "pending": [entry["slot"] for entry in plan],
        "in_flight": None,
        "published": [],
        "rotations": 0,
        "reset_reason": reason,
        "updated_utc": _iso(_utc_now()),
    }


def _in_flight_timeout_minutes() -> int:
    try:
        return max(
            5,
            int(os.getenv("SLOT_IN_FLIGHT_TIMEOUT_MINUTES", str(DEFAULT_IN_FLIGHT_TIMEOUT_MINUTES))),
        )
    except (TypeError, ValueError):
        return DEFAULT_IN_FLIGHT_TIMEOUT_MINUTES


def _save_state(state: dict[str, Any]) -> None:
    if state_disabled():
        return
    state["updated_utc"] = _iso(_utc_now())
    _atomic_write_json(STATE_FILE, state)
    _write_legacy_view(state)


def _write_legacy_view(state: dict[str, Any]) -> None:
    """Keep ``data/slot_state.json`` in sync for anyone reading the old file."""

    published = state.get("published") or []
    last = published[-1] if published else {}
    _atomic_write_json(
        LEGACY_SLOT_STATE_FILE,
        {
            "last_slot": last.get("slot"),
            "last_type": last.get("type"),
            "last_run_utc": last.get("published_utc") or state.get("updated_utc"),
            "day_utc": state.get("day_utc"),
            "pending_slots": state.get("pending", []),
            "in_flight": state.get("in_flight"),
        },
    )


def _day_summary(state: dict[str, Any]) -> dict[str, Any]:
    published = [entry for entry in state.get("published", []) if isinstance(entry, dict)]
    counts: dict[str, int] = {}
    for entry in published:
        key = str(entry.get("type", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return {
        "day_utc": state.get("day_utc"),
        "published": len(published),
        "by_type": counts,
        "unpublished": [slot for slot in state.get("pending", []) if isinstance(slot, int)],
    }


def _plan_by_slot(plan: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(entry["slot"]): entry for entry in plan}


def _release_in_flight(state: dict[str, Any], in_flight: dict[str, Any], *, reason: str) -> None:
    """Put an unfinished slot back at the head of the queue."""

    slot = in_flight.get("slot")
    pending = [item for item in state.get("pending", []) if isinstance(item, int)]
    if slot is not None and not in_flight.get("forced") and slot not in pending:
        pending = [slot] + pending
    state["pending"] = pending
    state["last_error"] = {
        "reason": reason,
        "slot": slot,
        "type": in_flight.get("type"),
        "at_utc": _iso(_utc_now()),
    }


def _heal_stale_run(state: dict[str, Any]) -> bool:
    """Give a crashed run's slot back to the queue.

    A run killed between generation and publish never reaches a rollback hook,
    so its slot would stay in flight forever. Once the record is older than the
    publish budget it is released here instead.
    """

    in_flight = state.get("in_flight")
    if not isinstance(in_flight, dict):
        return False

    started = _parse_iso(in_flight.get("started_utc")) or _utc_now()
    age_minutes = (_utc_now() - started).total_seconds() / 60.0
    if age_minutes < _in_flight_timeout_minutes():
        return False

    print(
        f"[Schedule] Healing stale in-flight slot {in_flight.get('slot')} "
        f"({in_flight.get('type')}) from run {in_flight.get('run_id', '?')} after "
        f"{age_minutes:.0f} min; the slot is back in the queue",
        file=sys.stderr,
    )
    _release_in_flight(state, in_flight, reason="stale in-flight run healed")
    state["in_flight"] = None
    _clear_pending_run()
    return True


def load_state(*, reset: bool = False, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return today's schedule, rolling the day over when the UTC date changed.

    A rebuilt or healed state is persisted immediately, so ``select_slot`` and
    ``begin_run`` always agree on what "today" still needs.
    """

    settings = config if config is not None else load_config()
    plan = load_plan(settings)
    today = _utc_now().strftime("%Y-%m-%d")

    stored = None if reset else _read_json(STATE_FILE, None, tolerant=True)

    if not isinstance(stored, dict) or stored.get("version") != STATE_VERSION:
        state = _fresh_state(today, plan, "manual reset" if reset else "no usable state file yet")
        state["previous_day"] = _day_summary(stored) if isinstance(stored, dict) else None
        _save_state(state)
        return state

    if str(stored.get("day_utc")) != today or reset:
        state = _fresh_state(today, plan, "manual reset" if reset else "new UTC day")
        state["previous_day"] = _day_summary(stored)
        _save_state(state)
        return state

    state = stored
    known = set(_plan_by_slot(plan))
    cleaned: list[int] = []
    for slot in state.get("pending", []) or []:
        if isinstance(slot, int) and slot in known and slot not in cleaned:
            cleaned.append(slot)

    published = [entry for entry in state.get("published", []) if isinstance(entry, dict)]
    mutated = cleaned != (state.get("pending") or []) or len(published) != len(state.get("published", []) or [])
    state["pending"] = cleaned
    state["published"] = published
    state.setdefault("rotations", 0)

    if _heal_stale_run(state):
        mutated = True

    if mutated:
        _save_state(state)

    return state


# ---------------------------------------------------------------------------
# Selecting the next slot
# ---------------------------------------------------------------------------

def _normalise_request(request: str | None) -> tuple[str, Any]:
    """Return ``(kind, value)`` for a manual slot request.

    ``kind`` is one of ``auto``, ``reset``, ``type`` or ``slot``.
    """

    raw = str(request or "").strip().lower()
    if raw in {"", "auto", "next", "queue"}:
        return "auto", None
    if raw in {"reset", "new_day", "fresh"}:
        return "reset", None
    if raw in POST_TYPES:
        return "type", raw
    stripped = raw[4:].strip() if raw.startswith("slot") else raw
    try:
        return "slot", int(stripped)
    except (TypeError, ValueError):
        raise ScheduleError(
            f"Unknown slot request {request!r}. Use auto, reset, a slot number, "
            f"or one of: {', '.join(POST_TYPES)}."
        ) from None


def _wrap_if_exhausted(state: dict[str, Any], plan: list[dict[str, Any]]) -> bool:
    """Restart the rotation once a day's twelve slots are all published."""

    if state["pending"]:
        return False

    state["pending"] = [entry["slot"] for entry in plan]
    state["rotations"] = int(state.get("rotations", 0) or 0) + 1
    print(
        f"[Schedule] All {len(plan)} slots for {state['day_utc']} are published; "
        f"continuing with rotation #{state['rotations'] + 1}"
    )
    _save_state(state)
    return True


def select_slot(request: str | None = None, *, reset: bool = False) -> dict[str, Any]:
    """Choose the slot this run should publish, without reserving it yet.

    ``request`` comes from the ``SLOT`` environment variable / the
    ``workflow_dispatch`` input. With no request the next pending slot of the
    day is returned - which is what makes repeated manual runs produce a
    sequence instead of the same post twice.
    """

    config = load_config()
    plan = load_plan(config)
    state = load_state(reset=reset, config=config)
    kind, value = _normalise_request(request)
    by_slot = _plan_by_slot(plan)

    wrapped = False
    forced = False
    if kind in {"auto", "reset"}:
        wrapped = _wrap_if_exhausted(state, plan)
        if not state["pending"]:
            raise ScheduleError("No pending slot is available and the queue did not refill")
        chosen_slot = state["pending"][0]
    elif kind == "type":
        match = next((slot for slot in state["pending"] if by_slot[slot]["type"] == value), None)
        if match is None:
            # Every slot of this type is already published today: honour the
            # manual request anyway without consuming another slot's turn.
            match = next(slot for slot in sorted(by_slot) if by_slot[slot]["type"] == value)
            forced = True
        chosen_slot = match
    else:
        if value not in by_slot:
            raise ScheduleError(
                f"Slot {value} is not part of the plan "
                f"(valid: {', '.join(str(slot) for slot in sorted(by_slot))})"
            )
        forced = value not in state["pending"]
        chosen_slot = value

    entry = dict(by_slot[chosen_slot])
    entry.update(
        {
            "forced": forced,
            "wrapped": wrapped,
            "day_utc": state["day_utc"],
            "pending_before": len(state["pending"]),
            "rotation": int(state.get("rotations", 0) or 0) + 1,
            "total_slots": len(plan),
            "published_before": len(state.get("published") or []),
        }
    )
    return entry


def begin_run(slot: dict[str, Any]) -> None:
    """Reserve the slot until the publish step finalises it."""

    if state_disabled():
        return

    state = load_state()
    chosen = int(slot["slot"])
    if not slot.get("forced") and chosen in state["pending"]:
        state["pending"] = [item for item in state["pending"] if item != chosen]

    state["in_flight"] = {
        "slot": chosen,
        "type": str(slot["type"]),
        "name": str(slot.get("name", "")),
        "forced": bool(slot.get("forced")),
        "started_utc": _iso(_utc_now()),
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
    }
    state["last_error"] = None
    _save_state(state)
    _atomic_write_json(
        PENDING_RUN_FILE,
        {
            "run_id": state["in_flight"]["run_id"],
            "slot": chosen,
            "type": str(slot["type"]),
            "name": str(slot.get("name", "")),
            "forced": bool(slot.get("forced")),
            "started_utc": state["in_flight"]["started_utc"],
            "coin": None,
            "trade_id": None,
            "posted_time": None,
        },
    )


def record_run_effects(*, coin: str | None = None, trade_id: str | None = None, posted_time: str | None = None) -> None:
    """Remember what this run mutated, so a failed publish can undo it."""

    if state_disabled():
        return

    pending = _read_json(PENDING_RUN_FILE, None, tolerant=True)
    if not isinstance(pending, dict):
        return

    if coin:
        pending["coin"] = str(coin).upper().strip()
    if trade_id:
        pending["trade_id"] = str(trade_id)
    if posted_time:
        pending["posted_time"] = str(posted_time)
    _atomic_write_json(PENDING_RUN_FILE, pending)


def _clear_pending_run() -> None:
    try:
        PENDING_RUN_FILE.unlink()
    except (FileNotFoundError, OSError):
        pass


def _rollback_run_effects(pending: dict[str, Any]) -> list[str]:
    """Undo the open trade / coin cooldown this run created, entry by entry."""

    undone: list[str] = []
    coin = str(pending.get("coin") or "").upper().strip()
    trade_id = str(pending.get("trade_id") or "").strip()
    posted_time = str(pending.get("posted_time") or "").strip()

    if trade_id and ACTIVE_TRADES_FILE.exists():
        trades = _read_list(ACTIVE_TRADES_FILE)
        kept = [
            entry
            for entry in trades
            if not (
                isinstance(entry, dict)
                and str(entry.get("trade_id", "")) == trade_id
                and str(entry.get("status", "ACTIVE")).upper() == "ACTIVE"
            )
        ]
        if len(kept) != len(trades):
            _atomic_write_json(ACTIVE_TRADES_FILE, kept)
            undone.append(f"active trade {trade_id}")

    if coin and POSTED_COINS_FILE.exists():
        posted = _read_list(POSTED_COINS_FILE)
        drop_index: int | None = None
        for index in range(len(posted) - 1, -1, -1):
            entry = posted[index]
            if not isinstance(entry, dict):
                continue
            if str(entry.get("coin", "")).upper().strip() != coin:
                continue
            if posted_time and str(entry.get("time")) != posted_time:
                continue
            drop_index = index
            break
        if drop_index is not None:
            del posted[drop_index]
            _atomic_write_json(POSTED_COINS_FILE, posted)
            undone.append(f"cooldown for {coin}")

    return undone


def publish_succeeded(*, mode: str = "unknown", attempts: int = 0, degraded: bool = False) -> dict[str, Any] | None:
    """Mark the in-flight slot published; call once Square accepted the post."""

    if state_disabled():
        return None

    pending = _read_json(PENDING_RUN_FILE, None, tolerant=True)
    state = load_state()
    in_flight = state.get("in_flight")
    if not isinstance(in_flight, dict):
        _clear_pending_run()
        return None

    record = {
        "slot": in_flight.get("slot"),
        "type": in_flight.get("type"),
        "name": in_flight.get("name"),
        "forced": in_flight.get("forced", False),
        "published_utc": _iso(_utc_now()),
        "run_id": in_flight.get("run_id"),
        "mode": mode,
        "attempts": attempts,
        "degraded": degraded,
        "coin": pending.get("coin") if isinstance(pending, dict) else None,
    }
    state["published"] = (state.get("published") or []) + [record]
    state["in_flight"] = None
    state["last_error"] = None
    _save_state(state)
    _clear_pending_run()
    _atomic_write_json(
        PUBLISH_RECEIPT_FILE,
        {**record, "day_utc": state["day_utc"], "pending_after": state["pending"]},
    )
    return record


def publish_uncertain(reason: str) -> dict[str, Any] | None:
    """Consume the slot without claiming it was published.

    Used when the create-post request had already been issued: re-queueing it
    risks publishing the same content twice, so the slot is given up with a
    note asking for a manual check of the Square profile instead.
    """

    if state_disabled():
        return None

    state = load_state()
    in_flight = state.get("in_flight")
    if not isinstance(in_flight, dict):
        _clear_pending_run()
        return None

    record = {
        **in_flight,
        "published_utc": None,
        "resolved_utc": _iso(_utc_now()),
        "mode": "uncertain",
        "attempts": 0,
        "degraded": True,
        "note": reason,
    }
    state["published"] = (state.get("published") or []) + [record]
    state["in_flight"] = None
    state["last_error"] = {
        "reason": reason,
        "slot": in_flight.get("slot"),
        "type": in_flight.get("type"),
        "at_utc": _iso(_utc_now()),
        "action": "slot consumed without a retry; verify the Square profile",
    }
    _save_state(state)
    _clear_pending_run()
    _atomic_write_json(
        PUBLISH_RECEIPT_FILE,
        {**record, "day_utc": state["day_utc"], "pending_after": state["pending"]},
    )
    return record


def abort_run(reason: str) -> dict[str, Any] | None:
    """Return the slot to the queue and undo the effects of a failed run."""

    if state_disabled():
        return None

    pending = _read_json(PENDING_RUN_FILE, None, tolerant=True)
    state = load_state()
    in_flight = state.get("in_flight")
    if not isinstance(in_flight, dict):
        _clear_pending_run()
        return None

    released = dict(in_flight)
    _release_in_flight(state, released, reason=reason)
    state["in_flight"] = None
    _save_state(state)

    if isinstance(pending, dict):
        undone = _rollback_run_effects(pending)
        if undone:
            print(f"[Schedule] Rolled back: {', '.join(undone)}")
    _clear_pending_run()

    print(
        f"[Schedule] Slot {released.get('slot')} ({released.get('type')}) is back in "
        "the queue and will be retried by the next run"
    )
    return released


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def status(*, config: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = config if config is not None else load_config()
    plan = load_plan(settings)
    state = load_state(config=settings)
    by_slot = _plan_by_slot(plan)

    def describe(slot: int) -> dict[str, Any]:
        return {"slot": slot, "type": by_slot[slot]["type"], "name": by_slot[slot]["name"]}

    pending = [slot for slot in state.get("pending", []) if slot in by_slot]
    return {
        "day_utc": state["day_utc"],
        "rotation": int(state.get("rotations", 0) or 0) + 1,
        "total_slots": len(plan),
        "published_today": len(state.get("published") or []),
        "next_slot": describe(pending[0]) if pending else None,
        "in_flight": state.get("in_flight"),
        "pending": [describe(slot) for slot in pending],
        "published": state.get("published") or [],
        "last_error": state.get("last_error"),
        "previous_day": state.get("previous_day"),
    }


def render_summary(payload: dict[str, Any]) -> str:
    lines = [
        "### 🗓️ Posting schedule",
        f"- Day (UTC): **{payload['day_utc']}** · rotation #{payload['rotation']}",
        f"- Published today: **{payload['published_today']}/{payload['total_slots']}**",
    ]
    if payload.get("next_slot"):
        nxt = payload["next_slot"]
        lines.append(f"- Next run publishes: slot {nxt['slot']} · `{nxt['type']}`")
    else:
        lines.append("- Next run publishes: *queue empty → starts a fresh rotation*")
    if payload.get("in_flight"):
        in_flight = payload["in_flight"]
        lines.append(
            f"- In flight: slot {in_flight.get('slot')} ({in_flight.get('type')}) "
            f"from run {in_flight.get('run_id')}"
        )
    pending = payload.get("pending") or []
    if pending:
        lines.append("- Still queued: " + ", ".join(f"#{item['slot']} {item['type']}" for item in pending))
    if payload.get("last_error"):
        lines.append(f"- ⚠️ Last failure: {payload['last_error'].get('reason')}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or drive the daily posting queue.")
    parser.add_argument("--status", action="store_true", help="Print today's queue as JSON")
    parser.add_argument("--summary", action="store_true", help="Print a GitHub step summary block")
    parser.add_argument("--reset", action="store_true", help="Rebuild today's queue from the plan")
    parser.add_argument("--peek", action="store_true", help="Show the slot the next run would take")
    parser.add_argument("--slot", default="", help="Slot number or type to preview")
    args = parser.parse_args(argv)

    if args.reset:
        load_state(reset=True)
        print("[Schedule] Queue rebuilt from bot/config.json")

    if args.peek:
        try:
            slot = select_slot(args.slot or None)
        except ScheduleError as exc:
            print(f"[Schedule] {exc}", file=sys.stderr)
            return 2
        print(
            f"slot={slot['slot']} type={slot['type']} name={slot['name']} "
            f"forced={slot['forced']} wrapped={slot['wrapped']}"
        )
        return 0

    if args.summary:
        print(render_summary(status()))
        return 0

    print(json.dumps(status(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

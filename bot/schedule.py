"""Clock-independent, continuous queue for the 24-slot hourly posting plan.

Why this module exists
----------------------
The post type used to be derived from the wall clock inside ``main_signal``::

    slot_index = (utc_hour // 2) % 12

That tied *what* gets posted to *when* the runner happened to start. Repeated
manual runs could therefore publish the same type while other slots were lost.
Replacing the lookup with a queue that reset at midnight fixed only half of the
problem: GitHub delivered a late slot 0 at 20:04 UTC on 2026-11-02, then the
midnight reset put slot 0 at the head again for the next run at 03:23 UTC on
2026-11-03. Two BTC outlooks appeared consecutively.

This module makes the rotation durable, including across calendar days:

* the plan comes only from ``bot/config.json``: normally 11 rapid alerts,
  11 explained setups, 1 BTC outlook and 1 meme per complete rotation;
* every automatic run takes the **next pending slot**, never a clock lookup;
* midnight changes the reporting date, not the queue. Delayed runs can span
  several UTC days without restarting or skipping the remaining content;
* editing the slot list in ``bot/config.json`` is detected through a plan
  signature and starts a clean rotation, so an old queue is never replayed
  against a new plan with different slot types;
* a retryable generation/publish failure returns its slot and rolls back the
  coin cooldown / open trade created by that run;
* a killed run's in-flight reservation is healed after the configured timeout;
* only an exhausted queue starts the next rotation. Explicit manual requests
  can publish extras, but those extras do not consume another queued slot;
* ``reset`` deliberately starts a new rotation and can repeat content. It is
  never called automatically just because the UTC date changed.

The pacer workflow (``bot/pacer.py``) dispatches one automatic run per UTC
hour, with an hourly cron as a backup. :func:`due_status` is the shared guard:
an automatic run is skipped when the current UTC hour already has a queued
post or the previous one is less than ``schedule.min_gap_minutes`` old, so
cron, the pacer and retries cannot double-post. The invariant is the content
mix per resolved rotation; a day receives 24 posts when every hour is served.

State files (all under ``data/``, committed by the workflow):

``schedule_state.json``   pending / in-flight slots and a rotation-tagged ledger
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
# Manual-only dispatch types: never part of the automatic rotation plan, but
# valid as explicit ``slot:`` requests that run beside the queue as forced
# extras. The scorecard is the public, verified outcomes ledger (wins and
# losses alike); the explainer is a data-driven educational post.
MANUAL_ONLY_TYPES = ("scorecard", "educational_explainer")
MANUAL_SLOT_NUMBERS = {"scorecard": 90, "educational_explainer": 91}
MANUAL_SLOT_NAMES = {
    "scorecard": "Verified Setup Scorecard (manual)",
    "educational_explainer": "Educational Explainer (manual)",
}
DEFAULT_IN_FLIGHT_TIMEOUT_MINUTES = 45
DEFAULT_MIN_GAP_MINUTES = 20
# This is an audit ledger, not the queue itself. Keep it bounded even when the
# bot runs for months; pending/published counts for the active rotation are
# derived from rotation tags, never from the length of this retained history.
MAX_PUBLISH_HISTORY = 240


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
    """Return the ordered slots that make up one complete rotation.

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


def plan_signature(plan: list[dict[str, Any]]) -> str:
    """Identify a plan by its ordered ``slot:type`` pairs (names are cosmetic)."""

    return ",".join(f"{entry['slot']}:{entry['type']}" for entry in plan)


def _rotation_matches_plan(state: dict[str, Any], plan: list[dict[str, Any]]) -> bool:
    """Best-effort check for state written before plan signatures existed.

    Every queued slot already resolved in the active rotation (and an in-flight
    one) must still have the same type in the current plan.
    """

    by_slot = _plan_by_slot(plan)
    records = list(_rotation_records(state))
    if isinstance(state.get("in_flight"), dict) and not state["in_flight"].get("forced"):
        records.append(state["in_flight"])
    for entry in records:
        slot = entry.get("slot")
        if not isinstance(slot, int) or slot not in by_slot:
            return False
        if entry.get("type") and by_slot[slot]["type"] != entry.get("type"):
            return False
    return True


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _fresh_state(
    day: str,
    plan: list[dict[str, Any]],
    reason: str,
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a rotation, retaining the audit ledger on an explicit reset."""

    now = _iso(_utc_now())
    return {
        "version": STATE_VERSION,
        "day_utc": day,
        "rotation_started_utc": now,
        "pending": [entry["slot"] for entry in plan],
        "in_flight": None,
        "published": list((previous or {}).get("published") or [])[-MAX_PUBLISH_HISTORY:],
        "rotations": _active_rotation(previous) if previous is not None else 0,
        "reset_reason": reason,
        "updated_utc": now,
        "previous_day": _day_summary(previous) if previous is not None else None,
        "previous_rotation": _rotation_summary(previous) if previous is not None else None,
        "plan_signature": plan_signature(plan),
        "last_error": None,
    }


def _active_rotation(state: dict[str, Any]) -> int:
    try:
        return max(0, int(state.get("rotations", 0) or 0)) + 1
    except (TypeError, ValueError):
        return 1


def _rotation_records(
    state: dict[str, Any], *, include_forced: bool = False
) -> list[dict[str, Any]]:
    """Queued slots resolved in this rotation; explicit extras don't count."""

    rotation = _active_rotation(state)
    return [
        entry
        for entry in state.get("published", [])
        if isinstance(entry, dict)
        and entry.get("rotation") == rotation
        and (include_forced or not entry.get("forced", False))
    ]


def _record_day(entry: dict[str, Any]) -> str | None:
    if entry.get("day_utc"):
        return str(entry["day_utc"])
    timestamp = _parse_iso(entry.get("published_utc") or entry.get("resolved_utc"))
    return timestamp.strftime("%Y-%m-%d") if timestamp else None


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
            "last_run_utc": last.get("published_utc") or last.get("resolved_utc") or state.get("updated_utc"),
            "day_utc": state.get("day_utc"),
            "rotation": _active_rotation(state),
            "rotation_started_utc": state.get("rotation_started_utc"),
            "published_this_rotation": len(_rotation_records(state)),
            "pending_slots": state.get("pending", []),
            "in_flight": state.get("in_flight"),
        },
    )


def _summary(
    state: dict[str, Any], published: list[dict[str, Any]], **identity: Any
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for entry in published:
        key = str(entry.get("type", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return {
        **identity,
        "published": len(published),
        "by_type": counts,
        "unpublished": [slot for slot in state.get("pending", []) if isinstance(slot, int)],
    }


def _day_summary(state: dict[str, Any]) -> dict[str, Any]:
    day = str(state.get("day_utc"))
    published = [
        entry
        for entry in state.get("published", [])
        if isinstance(entry, dict) and (_record_day(entry) or day) == day
    ]
    return _summary(state, published, day_utc=day)


def _rotation_summary(state: dict[str, Any]) -> dict[str, Any]:
    queued = _rotation_records(state)
    return _summary(
        state,
        queued,
        rotation=_active_rotation(state),
        started_utc=state.get("rotation_started_utc"),
        extras=len(_rotation_records(state, include_forced=True)) - len(queued),
    )


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
    """Load the continuous rotation; a UTC date change never refills its queue.

    Older state files have no rotation tags. Migrate them in place, preserving
    pending and in-flight slots, so deploying this fix cannot itself cause the
    repeat it is meant to prevent. Only an explicit reset starts over.
    """

    settings = config if config is not None else load_config()
    plan = load_plan(settings)
    today = _utc_now().strftime("%Y-%m-%d")
    stored = _read_json(STATE_FILE, None, tolerant=True)

    if not isinstance(stored, dict) or stored.get("version") != STATE_VERSION:
        state = _fresh_state(today, plan, "no usable state file yet")
        _save_state(state)
        return state

    state = stored
    migrated = (
        "rotation_started_utc" not in state
        or any("rotation" not in entry for entry in (state.get("published") or []) if isinstance(entry, dict))
        or len(state.get("published") or []) > MAX_PUBLISH_HISTORY
    )
    state.setdefault("rotations", 0)
    rotation = _active_rotation(state)
    published = [entry for entry in (state.get("published") or []) if isinstance(entry, dict)]
    for entry in published:
        entry.setdefault("rotation", rotation)
    state["published"] = published[-MAX_PUBLISH_HISTORY:]

    if not _parse_iso(state.get("rotation_started_utc")):
        timestamps = [
            _parse_iso(entry.get("published_utc") or entry.get("resolved_utc"))
            for entry in _rotation_records(state)
        ]
        started = min((value for value in timestamps if value is not None), default=_utc_now())
        state["rotation_started_utc"] = _iso(started)

    if isinstance(state.get("in_flight"), dict):
        state["in_flight"].setdefault("rotation", rotation)

    if reset:
        state = _fresh_state(today, plan, "manual reset", previous=state)
        _save_state(state)
        return state

    signature = plan_signature(plan)
    stored_signature = state.get("plan_signature")
    if stored_signature != signature:
        if stored_signature is None and _rotation_matches_plan(state, plan):
            # Pre-signature state that is still consistent with the plan.
            state["plan_signature"] = signature
            migrated = True
        elif isinstance(state.get("in_flight"), dict):
            # Never pull the rug from under a run that is publishing right
            # now; its publish/abort hook resolves it and the next load resets.
            print("[Schedule] Plan changed; waiting for the in-flight run before starting the new rotation")
        else:
            print(
                f"[Schedule] bot/config.json now defines a different {len(plan)}-slot plan; "
                "starting a fresh rotation instead of replaying the old queue"
            )
            state = _fresh_state(today, plan, "plan changed in bot/config.json", previous=state)
            _save_state(state)
            return state

    known = set(_plan_by_slot(plan))
    cleaned: list[int] = []
    for slot in state.get("pending", []) or []:
        if isinstance(slot, int) and slot in known and slot not in cleaned:
            cleaned.append(slot)

    mutated = migrated or cleaned != (state.get("pending") or [])
    state["pending"] = cleaned

    if str(state.get("day_utc")) != today:
        state["previous_day"] = _day_summary(state)
        state["day_utc"] = today
        mutated = True
        print("[Schedule] New UTC day; preserving the unfinished rotation and its pending slots")

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
    if raw in POST_TYPES or raw in MANUAL_ONLY_TYPES:
        return "type", raw
    stripped = raw[4:].strip() if raw.startswith("slot") else raw
    try:
        return "slot", int(stripped)
    except (TypeError, ValueError):
        raise ScheduleError(
            f"Unknown slot request {request!r}. Use auto, reset, a slot number, "
            f"or one of: {', '.join(POST_TYPES + MANUAL_ONLY_TYPES)}."
        ) from None


def _wrap_if_exhausted(state: dict[str, Any], plan: list[dict[str, Any]]) -> bool:
    """Start the next rotation only after every queued slot was resolved."""

    if state["pending"]:
        return False

    state["previous_rotation"] = _rotation_summary(state)
    state["rotations"] = _active_rotation(state)
    state["rotation_started_utc"] = _iso(_utc_now())
    state["pending"] = [entry["slot"] for entry in plan]
    state["plan_signature"] = plan_signature(plan)
    state["reset_reason"] = "completed rotation"
    # Keep the tagged ledger for daily reporting and auditability. The new
    # rotation's progress is derived from its tag, not from len(published).
    print(
        f"[Schedule] All {len(plan)} slots in rotation #{state['rotations']} are resolved; "
        f"continuing with rotation #{_active_rotation(state)}"
    )
    _save_state(state)
    return True


def select_slot(request: str | None = None, *, reset: bool = False) -> dict[str, Any]:
    """Choose the slot this run should publish, without reserving it yet.

    ``request`` comes from the ``SLOT`` environment variable / the
    ``workflow_dispatch`` input. With no request the next pending slot of the
    continuous rotation is returned, even across midnight. Explicit type/slot
    requests retain their documented override behaviour.
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
    elif kind == "type" and value in MANUAL_ONLY_TYPES:
        # Manual-only types are not in the rotation plan, so they synthesise a
        # forced extra slot that leaves the automatic queue untouched.
        entry = {
            "slot": MANUAL_SLOT_NUMBERS[value],
            "type": value,
            "name": MANUAL_SLOT_NAMES[value],
            "hour_utc": None,
        }
        entry.update(
            {
                "forced": True,
                "wrapped": False,
                "day_utc": state["day_utc"],
                "pending_before": len(state["pending"]),
                "rotation": _active_rotation(state),
                "total_slots": len(plan),
                "published_before": len(_rotation_records(state)),
            }
        )
        return entry
    elif kind == "type":
        match = next((slot for slot in state["pending"] if by_slot[slot]["type"] == value), None)
        if match is None:
            # Every slot of this type is already resolved in this rotation:
            # honour the manual request without consuming another slot's turn.
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
            "rotation": _active_rotation(state),
            "total_slots": len(plan),
            "published_before": len(_rotation_records(state)),
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
        "rotation": _active_rotation(state),
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

    now = _utc_now()
    record = {
        "slot": in_flight.get("slot"),
        "type": in_flight.get("type"),
        "name": in_flight.get("name"),
        "forced": in_flight.get("forced", False),
        "published_utc": _iso(now),
        "day_utc": now.strftime("%Y-%m-%d"),
        "run_id": in_flight.get("run_id"),
        "rotation": in_flight.get("rotation", _active_rotation(state)),
        "mode": mode,
        "attempts": attempts,
        "degraded": degraded,
        "coin": pending.get("coin") if isinstance(pending, dict) else None,
    }
    state["published"] = ((state.get("published") or []) + [record])[-MAX_PUBLISH_HISTORY:]
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

    now = _utc_now()
    record = {
        **in_flight,
        "published_utc": None,
        "resolved_utc": _iso(now),
        "day_utc": now.strftime("%Y-%m-%d"),
        "rotation": in_flight.get("rotation", _active_rotation(state)),
        "mode": "uncertain",
        "attempts": 0,
        "degraded": True,
        "note": reason,
    }
    state["published"] = ((state.get("published") or []) + [record])[-MAX_PUBLISH_HISTORY:]
    state["in_flight"] = None
    state["last_error"] = {
        "reason": reason,
        "slot": in_flight.get("slot"),
        "type": in_flight.get("type"),
        "at_utc": _iso(now),
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
# Hourly cadence guard (shared by the pacer and automatic workflow runs)
# ---------------------------------------------------------------------------

def min_gap_minutes(config: dict[str, Any] | None = None) -> float:
    settings = config if config is not None else load_config()
    try:
        return max(0.0, float(settings.get("schedule", {}).get("min_gap_minutes", DEFAULT_MIN_GAP_MINUTES)))
    except (TypeError, ValueError):
        return float(DEFAULT_MIN_GAP_MINUTES)


def last_automatic_post(state: dict[str, Any] | None) -> datetime.datetime | None:
    """When the queue last resolved a slot (published or uncertain).

    Explicit manual extras are ignored: they never consume a queued slot, so
    they must not cost the rotation its hourly post either.
    """

    latest: datetime.datetime | None = None
    for entry in (state or {}).get("published") or []:
        if not isinstance(entry, dict) or entry.get("forced"):
            continue
        stamp = _parse_iso(entry.get("published_utc") or entry.get("resolved_utc"))
        if stamp and (latest is None or stamp > latest):
            latest = stamp
    return latest


def due_status(
    state: dict[str, Any] | None,
    *,
    now: datetime.datetime | None = None,
    min_gap: float = DEFAULT_MIN_GAP_MINUTES,
    in_flight_timeout: float | None = None,
) -> dict[str, Any]:
    """Decide whether an automatic run should publish the next slot now.

    One queued post per UTC clock hour, and never two within ``min_gap``
    minutes (a run delayed to 10:58 must not be followed by one at 11:00).
    Pure function of the state dict so the pacer can evaluate the state it
    fetched from GitHub without touching local files.
    """

    current = (now or _utc_now()).astimezone(datetime.timezone.utc)
    timeout = float(in_flight_timeout if in_flight_timeout is not None else _in_flight_timeout_minutes())
    in_flight = (state or {}).get("in_flight")
    if isinstance(in_flight, dict) and not in_flight.get("forced"):
        started = _parse_iso(in_flight.get("started_utc"))
        if started and (current - started).total_seconds() / 60.0 < timeout:
            return {"due": False, "reason": f"slot {in_flight.get('slot')} is already in flight", "last_post_utc": None}

    last = last_automatic_post(state)
    if last is None:
        return {"due": True, "reason": "no automatic post on record", "last_post_utc": None}

    age_minutes = (current - last).total_seconds() / 60.0
    stamp = _iso(last)
    if last.strftime("%Y-%m-%dT%H") == current.strftime("%Y-%m-%dT%H"):
        return {"due": False, "reason": f"this UTC hour already has its post ({stamp})", "last_post_utc": stamp}
    if age_minutes < min_gap:
        return {
            "due": False,
            "reason": f"last post is only {age_minutes:.0f} min old (min gap {min_gap:.0f} min)",
            "last_post_utc": stamp,
        }
    return {"due": True, "reason": f"last post {age_minutes:.0f} min ago", "last_post_utc": stamp}


def local_due_status(*, now: datetime.datetime | None = None) -> dict[str, Any]:
    """:func:`due_status` for the checked-out ``data/schedule_state.json`` (read-only)."""

    state = _read_json(STATE_FILE, None, tolerant=True)
    return due_status(
        state if isinstance(state, dict) else None,
        now=now,
        min_gap=min_gap_minutes(),
    )


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
    current = _rotation_records(state, include_forced=True)
    return {
        "day_utc": state["day_utc"],
        "rotation": _active_rotation(state),
        "rotation_started_utc": state.get("rotation_started_utc"),
        "total_slots": len(plan),
        "published_this_rotation": len(_rotation_records(state)),
        "published_today": sum(_record_day(entry) == state["day_utc"] for entry in state.get("published", [])),
        "next_slot": describe(pending[0]) if pending else None,
        "in_flight": state.get("in_flight"),
        "pending": [describe(slot) for slot in pending],
        "published": current,
        "last_error": state.get("last_error"),
        "previous_day": state.get("previous_day"),
        "previous_rotation": state.get("previous_rotation"),
    }


def render_summary(payload: dict[str, Any]) -> str:
    lines = [
        "### 🗓️ Posting schedule",
        f"- Day (UTC): **{payload['day_utc']}** · rotation #{payload['rotation']}",
        f"- Queued slots completed this rotation: **{payload.get('published_this_rotation', 0)}/{payload['total_slots']}**",
        f"- Published today (including explicit extras): **{payload['published_today']}**",
        "- The queue continues across midnight; only a completed rotation starts over.",
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
    parser = argparse.ArgumentParser(description="Inspect or drive the continuous posting rotation.")
    parser.add_argument("--status", action="store_true", help="Print the current rotation as JSON")
    parser.add_argument("--summary", action="store_true", help="Print a GitHub step summary block")
    parser.add_argument("--reset", action="store_true", help="Explicitly start a new rotation (can repeat content)")
    parser.add_argument("--peek", action="store_true", help="Show the slot the next run would take")
    parser.add_argument("--slot", default="", help="Slot number or type to preview")
    parser.add_argument(
        "--due",
        action="store_true",
        help="Report whether an automatic run should post this UTC hour (writes due=true/false to $GITHUB_OUTPUT)",
    )
    args = parser.parse_args(argv)

    if args.due:
        verdict = local_due_status()
        print(json.dumps(verdict, ensure_ascii=False))
        output = os.getenv("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as handle:
                handle.write(f"due={'true' if verdict['due'] else 'false'}\n")
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary and not verdict["due"]:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(f"### ⏭️ Automatic run skipped\n- {verdict['reason']}\n")
        return 0

    if args.reset:
        load_state(reset=True)
        print("[Schedule] New rotation started from bot/config.json; earlier history retained")

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

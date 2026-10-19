# Binance Trending Bot

Autonomous [Binance Square](https://www.binance.com/en/square) content engine. It scans Binance
spot movers, builds a verified trade setup with [Groq](https://console.groq.com) LLMs, renders a
dark-mode chart card, and publishes the result — then a separate monitor tracks the open setups and
posts proof when a target is hit.

```
GitHub Actions cron ──> bot.main_signal (pick next queued slot → research → post text + chart)
                     └─> bot.square_publisher (retry-aware publish via the official Square scripts)
GitHub Actions cron ──> bot.monitor_runner  (1m candle scan → TP1/TP2 proof cards → close trades)
                                  │
                                  └──> data/*.json committed back to this repo (that IS the database)
```

## The 12-post daily plan

`bot/config.json` → `schedule.slots` is the single source of truth (top-100-creator benchmark mix):

| Count | Type | What it is |
| --- | --- | --- |
| 5 | `rapid_alert` | Verified levels + a concise institutional read |
| 5 | `explained_setup` | 15-point evidence, *Why now* + *What invalidates it* |
| 1 | `market_overview` | Macro & Bitcoin structure, flows, key levels |
| 1 | `meme_community` | Trader-psychology meme (highest median views, +49% paired uplift) |

**The type is chosen by a queue, not by the clock.** Each run takes the next pending slot of the
current UTC day, so:

- the 12 scheduled runs (`0 */2 * * *`) walk the whole plan in order;
- **manual runs keep that property** — click *Run workflow* five times and you get five different
  posts, never five memes (the old code computed `slot = (utc_hour // 2) % 12`, so three runs inside
  12:00–13:59 UTC all resolved to the meme slot);
- a slot whose run failed goes back to the **front** of the queue and is retried by the next run,
  instead of being lost (a failed slot used to silently turn "12 posts a day" into nine);
- once all 12 are published, the rotation wraps (guarded by the 24 h coin cooldown and the caption
  similarity check, so a wrap cannot become duplicate content).

## Running it by hand

*Actions → Signal Poster - 12-Slot Daily Rotation → Run workflow*

| Input | Effect |
| --- | --- |
| `slot: auto` (default) | Next pending slot of today's queue |
| `slot: market_overview` / `rapid_alert` / `explained_setup` / `meme_community` | Publish that type now (takes its pending slot if it still has one; otherwise a forced extra run that leaves the queue untouched) |
| `reset` | Rebuild today's queue from `bot/config.json` first |
| `preview` | Generate the post and chart, publish nothing, and roll the slot + its side effects back |

The workflow prints today's queue (`python -m bot.schedule --status`) and a summary card with the
next queued slot, so a run explains itself in the GitHub UI. Locally:

```bash
python -m bot.schedule --status          # today's queue as JSON
python -m bot.schedule --peek --slot 7   # what "slot 7" / a type would resolve to
SLOT=rapid_alert PREVIEW_ONLY=1 python -m bot.main_signal   # generate without publishing
```

## State files (committed by CI)

| File | Purpose |
| --- | --- |
| `data/schedule_state.json` | The day's plan: `pending`, `in_flight`, `published`, wrap count, last error |
| `data/pending_run.json` | What the current run mutated (slot, coin, `trade_id`) so a failed publish can undo exactly that |
| `data/publish_receipt.json` | Last publish outcome (mode, attempts, degraded flag) |
| `data/active_trades.json` | Open setups the monitor watches |
| `data/closed_trades.json` | Resolved or expired setups (proof log) |
| `data/posted_coins.json` | 24 h per-coin cooldown |
| `data/posted_captions.json` | Recent captions, for the near-duplicate guard |

Writes are atomic, and both workflows share the `binance-bot-state-writer` concurrency group so they
never race on the same file or the same push.

## Reliability rules the code enforces

- **No unverified numbers.** Every price comes from Binance REST through `bot/market_data.py`
  (cross-checked klines), and a signal whose price drifts >0.5% between selection and generation is
  rejected. Targets/stops are recomputed from the verified entry with an RR guard.
- **Publish is retried, never blindly.** Media uploads retry with backoff inside a wall-clock budget;
  if the create-post request was *already sent* the publisher stops (no retry, no re-queue) to avoid
  double-posting, and marks the slot `uncertain` for a human to verify.
- **Text fallback.** If images never become ready, the post still ships text-only instead of losing
  the slot, with a `::warning::` annotation and a step-summary note.
- **Duplicate guard.** A caption too similar to a recent one triggers a regeneration (up to
  `caption_regeneration_attempts`) rather than being published anyway.
- **Stale trade TTL.** An `ACTIVE` setup nobody can resolve any more is archived as `EXPIRED` after
  `trading.active_trade_ttl_hours` (72 h) so it stops blocking its coin.
- **Quiet-market degradation.** If nothing clears the +5% momentum filter, the threshold relaxes
  through `trending.min_change_fallbacks` before the run gives up — a softer verified setup still
  keeps the day's cadence intact.

## Required secrets and variables

| Name | Where | Used for |
| --- | --- | --- |
| `BINANCE_SQUARE_OPENAPI_KEY` | Secret | Posting via the official `binance-skills-hub` scripts (cloned at runtime, patched by `bot/patch_square_*.py`) |
| `GROQ_API_KEY` | Secret | Post copy, signal narrative, market overview, meme |
| `GROQ_MODEL` | Variable | Preferred model (default `openai/gpt-oss-120b`); the engine probes the account and falls back |
| `SQUARE_PUBLISH_ATTEMPTS`, `SQUARE_POLL_*`, `SQUARE_PUBLISH_DEADLINE_SECONDS` | Variable | Media-upload retry tuning |
| `CARD_USERNAME`, `CARD_TIMEZONE`, `BINANCE_REFERRAL_CODE`, `TARGET_CARD_*`, `MONITOR_*` | Variable | Proof-card look & scan window |
| `ACTIVE_TRADE_TTL_HOURS` | Variable | Stale-trade expiry override (`0` disables) |

## Workflows

| Workflow | Schedule | Does |
| --- | --- | --- |
| `signal.yml` — Signal Poster | `0 */2 * * *` (12×/day) | Publishes the next queued slot |
| `monitor.yml` — Live Monitor | `*/10 * * * *` | Candle-aware TP/SL detection, proof cards, state expiry |
| `tests.yml` — Tests | push / PR | Offline unit suites + config validation + compile check |
| `test-card.yml`, `binance-data-test.yml`, `price-consistency-test.yml` | manual | Card rendering check, live-data sanity checks |

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest test_schedule_rotation test_trade_state test_slot_cadence \
    test_signal_quality_pipeline test_square_publish_resilience
```

`bot/schedule.py`, `bot/trade_state.py` and `bot/poster.py` are dependency-light on purpose, so the
state machine can be unit-tested without matplotlib or network access.

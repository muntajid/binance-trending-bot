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

## The 12-post rotation

`bot/config.json` → `schedule.slots` is the single source of truth (top-100-creator benchmark mix):

| Count | Type | What it is |
| --- | --- | --- |
| 5 | `rapid_alert` | Verified levels + a concise institutional read |
| 5 | `explained_setup` | 15-point evidence, *Why now* + *What invalidates it* |
| 1 | `market_overview` | Macro & Bitcoin structure, flows, key levels |
| 1 | `meme_community` | Trader-psychology meme (highest median views, +49% paired uplift) |

**The type is chosen by a continuous queue, not by the clock or calendar date.** Each automatic run
takes the next pending slot, so:

- cron (`0 */2 * * *`) targets 12 runs per day and walks the plan in order;
- **midnight does not restart an unfinished rotation.** GitHub can delay or omit cron events; if the
  runs arrive late, the 12-post mix simply spans multiple UTC days. This prevents a delayed BTC
  outlook from being followed immediately by the next day's outlook;
- **manual runs with `slot: auto` advance the same queue**, rather than repeating the current hour's
  post type;
- a retryable failure returns its slot to the **front** of the queue for the next run;
- only after all 12 queued slots are resolved does the next rotation begin. An uncertain publish
  consumes its slot without a retry because the post may already have reached Square.

Explicit type/slot requests and `reset` are overrides, not part of the automatic mix. They can add
extras or repeat content; leave `slot: auto` and `reset: false` for normal posting. The coin cooldown
and caption similarity guard still apply, but they check wording/coins, not the daily content mix.

## Running it by hand

*Actions → Signal Poster - 12-Slot Daily Rotation → Run workflow*

| Input | Effect |
| --- | --- |
| `slot: auto` (default) | Next pending slot of the continuous rotation |
| `slot: market_overview` / `rapid_alert` / `explained_setup` / `meme_community` | Publish that type now (takes its pending slot if it still has one; otherwise a forced extra run that leaves the queue untouched) |
| `reset` | Deliberately start a new rotation from `bot/config.json`; may repeat content. Never enabled automatically at midnight. |
| `preview` | Generate the post and chart, publish nothing, and roll the slot + its side effects back |

The workflow prints the current rotation (`python -m bot.schedule --status`) and a summary card with
the next queued slot, so a run explains itself in the GitHub UI. Locally:

```bash
python -m bot.schedule --status          # current rotation and daily publish count as JSON
python -m bot.schedule --peek --slot 7   # what "slot 7" / a type would resolve to
SLOT=rapid_alert PREVIEW_ONLY=1 python -m bot.main_signal   # generate without publishing
```

## State files (committed by CI)

| File | Purpose |
| --- | --- |
| `data/schedule_state.json` | Continuous queue: `pending`, `in_flight`, rotation-tagged publish ledger, completed rotation count, last error. Older untagged state migrates without discarding pending slots. |
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

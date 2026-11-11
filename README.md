# Binance Trending Bot

Autonomous [Binance Square](https://www.binance.com/en/square) content engine. It scans Binance
spot movers, builds a verified trade setup with [Groq](https://console.groq.com) LLMs, renders a
dark-mode chart card, and publishes the result — then a separate monitor tracks the open setups and
posts proof when a target is hit.

```
GitHub Actions cron ──> bot.main_signal (topic score → research → hook + verified levels → post + chart)
                     └─> bot.square_publisher (retry-aware publish via the official Square scripts)
GitHub Actions cron ──> bot.monitor_runner  (1m candle scan → TP1/TP2 proof cards; SL resolutions and
                                  │           one-shot 'under pressure' journal updates within daily caps)
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

## Content quality layer (hook, angle, structure)

Posts now lead with a *verified narrative hook* instead of a raw levels line:

- Every signal payload asks the model for a `hook_line` (≤90 chars, one curiosity
  device, **every number must exist in the research snapshot**) and an
  `angle_family` (`contrarian_conflict`, `binary_scenario`, `data_finding`,
  `event_risk`, `outcome_update`, `structure_break`). The formatter puts the hook
  on line 1, the verified Entry/TP1/TP2/SL line on line 2, and a compact human
  evidence read (structure/indicators/participation/liquidity/trend stack/context)
  after that. Consecutive posts avoid repeating the same angle family when the
  data supports variety.
- `bot/poster.py: assert_post_quality` is the editorial gate: banned outcome
  promises (`guaranteed`, `can't lose`, …), at most one hook question plus one
  CTA question, and a cross-check that every numeral in the hook exists in the
  verified payload (0.5% tolerance, same as the price-drift guard). A failed
  signal post falls back to the data-first header; overview/meme/explainer
  variants are regenerated through the duplicate-guard loop.
- CTAs are contextual: the discussion question is built from the post's own
  scenario levels (`Which hits first for $X: $up or $down?`), with the mix in
  `bot/config.json → content.cta_mix`.
- Topic selection is a composite **topic score** (momentum, intraday volatility,
  liquidity, participation, 7-day novelty; weights in `trending.score_weights`)
  instead of "highest 24h gainer wins". Score components are printed in the run
  log and step summary.

## Running it by hand

*Actions → Signal Poster - 12-Slot Daily Rotation → Run workflow*

| Input | Effect |
| --- | --- |
| `slot: auto` (default) | Next pending slot of the continuous rotation |
| `slot: market_overview` / `rapid_alert` / `explained_setup` / `meme_community` | Publish that type now (takes its pending slot if it still has one; otherwise a forced extra run that leaves the queue untouched) |
| `slot: scorecard` | **Manual extra:** weekly verified outcomes ledger — wins and losses alike, counted from `data/closed_trades.json`, text-only, never part of the rotation |
| `slot: educational_explainer` | **Manual extra:** data-driven teaching post (one market concept explained through today's verified numbers) + BTC chart |
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
| `data/monitor_posts.json` | Today's outcome-narration counters (SL/pressure posts), reset every UTC day |
| `data/posted_coins.json` | 24 h per-coin cooldown |
| `data/posted_captions.json` | Recent captions, for the near-duplicate guard |
| `data/performance/posts.csv` | Manual Square metrics log; see `data/performance/README.md` for the feedback loop |

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
- **Stale trade TTL.** An `ACTIVE` setup unresolved for more than
  `trading.active_trade_ttl_hours` (72 h) **from its creation time** is archived as `EXPIRED` so it
  stops blocking its coin and its story closes. (The TTL used to measure from
  `last_checked_at`, which let perpetually-scannable trades live for months.)
- **Editorial quality gate.** `assert_post_quality` rejects banned outcome-promising language,
  excessive question marks, and hooks quoting numbers that are not in the verified payload.
  Signal posts fall back to the verified-levels header; overview/meme/explainer posts regenerate
  through the duplicate-guard loop before a slot is at risk.
- **Outcome honesty.** The monitor publishes stop-loss resolutions (with a red STOPPED card, no
  referral block) and one-shot "under pressure" journal updates, capped per UTC day via
  `bot/config.json → monitor` (`sl_posts_max_per_day` default 4, `pressure_posts_max_per_day`
  default 2). The manual `slot: scorecard` post counts **every** outcome in the window.
- **Quiet-market degradation.** If nothing clears the +5% momentum filter, the threshold relaxes
  through `trending.min_change_fallbacks` before the run gives up — a softer verified setup can still
  use the run's assigned slot when conditions are quiet.

## Required secrets and variables

| Name | Where | Used for |
| --- | --- | --- |
| `BINANCE_SQUARE_OPENAPI_KEY` | Secret | Posting via the official `binance-skills-hub` scripts (cloned at runtime, patched by `bot/patch_square_*.py`) |
| `GROQ_API_KEY` | Secret | Post copy, signal narrative, market overview, meme |
| `GROQ_MODEL` | Variable | Preferred model (default `openai/gpt-oss-120b`); the engine probes the account and falls back |
| `SQUARE_PUBLISH_ATTEMPTS`, `SQUARE_POLL_*`, `SQUARE_PUBLISH_DEADLINE_SECONDS` | Variable | Media-upload retry tuning |
| `CARD_USERNAME`, `CARD_TIMEZONE`, `BINANCE_REFERRAL_CODE`, `CARD_*` | Variable | Proof-card look; `CARD_USERNAME` also watermarks charts. Target-hit cards omit the referral/QR block on STOPPED outcomes |
| `TARGET_CARD_LEVERAGE` | Variable | Optional override; empty (default) means each card shows the trade's own configured leverage label |
| `MONITOR_CANDLE_INTERVAL`, `MONITOR_MAX_LOOKBACK_HOURS`, `MONITOR_MAX_KLINE_PAGES` | Variable | Scan window tuning |
| `MONITOR_SL_POSTS_ENABLED`, `MONITOR_SL_POST_MAX_PER_DAY`, `MONITOR_PRESSURE_POSTS_ENABLED`, `MONITOR_PRESSURE_POST_MAX_PER_DAY` | Variable | Optional overrides for the outcome-narration knobs in `bot/config.json → monitor` |
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

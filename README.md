# Binance Trending Bot

Autonomous [Binance Square](https://www.binance.com/en/square) content engine. It scans Binance
spot movers, builds a verified trade setup with [Groq](https://console.groq.com) LLMs, renders a
dark-mode chart card, and publishes the result — then a separate monitor tracks the open setups and
posts proof when a target is hit.

```
Square Pacer        ──> checks every 5m; dispatches after gap + jitter (pacer.yml → bot.pacer)
signal.yml          ──> bot.main_signal (topic score → research → hook + verified levels → post + chart)
                     └─> bot.square_publisher (retry-aware publish via the official Square scripts)
GitHub Actions cron ──> bot.monitor_runner  (1m candle scan → TP1/TP2 proof cards only;
                                  │           SL/near-SL events are recorded silently, never posted)
                                  └──> data/*.json committed back to this repo (that IS the database)
```

## The six-slot rotation

`bot/config.json` → `schedule.slots` is the single source of truth. The cadence targets
**roughly six scheduled opportunities a day**, not 24 hourly posts:

| Count | Type | What it is |
| --- | --- | --- |
| 4 | `explained_setup` | Hook, verified levels, two short reasons, computed risk and one relevant question |
| 1 | `market_overview` | Bitcoin structure, market context and key levels |
| 1 | `meme_community` | Explicitly labelled fictional trading humor |

The example UTC hours are 00, 04, 08, 12, 16 and 20. They describe the plan's order, not
fixed publish appointments: the continuous queue, minimum gap and jitter decide when a run is due.
A `plan_signature` detects the change from the old plan and starts a fresh rotation while retaining
the historical ledger. Midnight never resets an unfinished queue.

**The user's monitoring exceptions are preserved:** position cards are unchanged; every existing
TP1/TP2-hit posting path and its text remain unchanged; SL hits close silently. There are **no new
TP-post limits**. TP-hit posts, explicit manual runs and the weekly aggregate scorecard are additional
to the six scheduled opportunities, so total account posts can exceed six.

### Cadence and posting-time variation

- `schedule.min_gap_minutes: 230` actually controls cadence; shortening the slot list alone would
  merely repeat that list four times a day.
- `post_jitter_min_minutes: 3` / `post_jitter_max_minutes: 40` add a stable pseudo-random delay to
  each opportunity. The next release is the last automatic resolution + 230 minutes + that delay.
  Selection is stable across pacer ticks/retries, using the prior timestamp, rotation and pending
  slot. The first post waits for a randomized minute within the current UTC hour.
- A single `pacer.yml` job checks state through the GitHub API every five minutes. Both it and the
  hourly `signal.yml` backup use the same `bot.schedule.due_status` gate. The one-post-per-UTC-hour
  safety guard remains, but an empty hour by itself is **not** permission to post.
- Failed API/generation/publication runs return their slot to the queue and can retry; the pacer
  allows at most three dispatches per hour. After ~330 minutes it hands off to a new pacer; cron
  revives a lost chain and a leader check suppresses duplicate pacers.
- A normal quality rejection is different from an outage: after up to five candidates, **no strong
  setup means no post**. The opportunity is recorded as `mode: skipped`, the queue advances and the
  cadence waits before trying again. No chart, active trade or coin cooldown is created for it.
  The generation step reports `ready=false`, so the workflow cannot accidentally publish the old
  `latest_post.txt`. Previews abort rather than consume a skipped opportunity.

Explicit manual type/slot requests and `reset` bypass automatic cadence. `rapid_alert` remains a
manual extra even though it is absent from the six-slot plan. The full 24-hour per-coin cooldown
is retained; legacy configurations can explicitly set a shorter cooldown fallback.

### Weekly outcomes recap

`signal.yml` also schedules a text-only **seven-day scorecard every Sunday at 18:17 UTC**. It uses
`build_scorecard_post` and the existing closed/active trade ledgers, counts stopped setups as well
as TP2 results, and runs as a non-consuming extra. It is an aggregate recap, **not an SL-hit post**;
it does not touch monitor behavior or position-card rendering. `slot: scorecard` still works manually.

## Content and setup quality

- `why_now` and `rapid_reason` must each have at least six words and cannot be copied schema
  instructions, empty strings or fragments like `Trigger.` / `1H trigger.`. A bad draft is regenerated
  once per model; persistent failures reject the candidate instead of publishing placeholders.
- Hooks are requested at **70 characters or fewer**. The drafter and formatter share a whole-word,
  whole-price clipper; clipped hooks drop trailing numeric tokens and dangling prepositions. Short
  or unusable hooks cannot become a malformed first line.
- Direction is rule-checked using 1H/4H trends, RSI (>=55 bullish, <=45 bearish), and price versus
  VWAP (>0.1% away). At least three inputs must agree and the 4H trend must not oppose the side.
  Missing/neutral readings do not vote. A model direction that disagrees is rejected.
- Rounded levels must be positive and correctly ordered, with **TP1 RR >=1.0**, **TP2 RR >=2.0**,
  and **TP1 distance >=0.6 of measured 1H ATR**. Invalid/weak model levels get one deterministic
  ATR/support-resistance derivation; derived levels must pass the same gates. Missing ATR or failed
  derivation skips the coin, never stretches the targets just to fill a slot.
- Topic scoring still combines momentum, volatility, liquidity, participation and novelty. Rejected
  candidates are excluded from subsequent attempts. Tokenized stocks and gold-linked markets in
  `trending.exclude_coins` are filtered in `get_trending_coins`, including calls used for context.
  `min_change_fallbacks: []` means a quiet market does not relax the +5% filter.
- Explained posts are at most **1,000 characters**, normally much shorter, with no 15-point indicator
  wall. Public `Conf` and validation numbers are removed. Both the header and risk line calculate
  RR directly from entry, TP1 and SL; model-written `risk_management` claims are ignored.
- Missing derivatives/macro/context data are omitted, not repeated as `OI n/a` or feed warnings.
  Available measurements still appear. This is a formatting fix, **not** a claim that missing futures
  feeds have been restored; diagnostics remain in the run logs.
- Signals use only the setup coin and BTC/ETH as discovery context, with at most **two relevant,
  non-duplicated cashtag occurrences**. Hashtags describe the subject instead of reward campaigns.
  This editorial tag policy deliberately does not alter the existing TP-hit post templates.
- `assert_post_quality` rejects banned outcome promises, excessive questions, placeholders, public
  confidence scores and hooks quoting figures absent from the payload. A bad hook can be dropped;
  no direction, RR or explanation gate is relaxed to force publication.

### One-week measurement loop

For the first week after deployment, keep this configuration stable. Record each published post's
views, likes and replies at a consistent age (for example 24 hours) in
[`data/performance/posts.csv`](data/performance/README.md), then run:

```bash
python -m bot.analyze_performance --days 7
```

Views are not available from the posting endpoint and must not be invented. Compare like-for-like
posts, change one thing at a time, and keep all accuracy/risk gates. These changes address the report's
code defects; increased reach is a hypothesis to measure, not a guarantee.

## Visual strategy and image quality

The existing chart/card pipeline now logs a topic-to-visual plan for signal, overview, meme, and explainer assets. Routing rules, evidence requirements, research observations and limits, mobile-first design guidance, and human QC are documented in [`docs/visual-strategy.md`](docs/visual-strategy.md) and implemented in `bot/visual_strategy.py`. Signal and BTC charts use a near-square canvas; market values remain rendered from verified data in code, not generated by image models. Position-card rendering and TP-hit publication remain unchanged by this update. Automated image checks are integrity/aspect warnings, not a substitute for human visual inspection.

## Running it by hand

*Actions → Signal Poster - Six-Slot Rotation → Run workflow*

| Input | Effect |
| --- | --- |
| `slot: auto` (default) | Next pending slot of the continuous rotation |
| `slot: market_overview` / `rapid_alert` / `explained_setup` / `meme_community` | Publish that type now (takes its pending slot if it still has one; otherwise a forced extra run that leaves the queue untouched) |
| `slot: scorecard` | **Extra:** seven-day outcomes ledger including losses; text-only, outside the queue; also scheduled weekly |
| `slot: educational_explainer` | **Manual extra:** data-driven teaching post (one market concept explained through today's verified numbers) + BTC chart |
| `reset` | Deliberately start a new rotation from `bot/config.json`; may repeat content. Never enabled automatically at midnight. |
| `preview` | Generate the post and chart, publish nothing, and roll the slot + its side effects back |

The workflow prints the current rotation (`python -m bot.schedule --status`) and a summary card with
the next queued slot, so a run explains itself in the GitHub UI. Locally:

```bash
python -m bot.schedule --status          # current rotation and daily publish count as JSON
python -m bot.schedule --peek --slot 4   # what "slot 4" / a type would resolve to
python -m bot.schedule --due             # would an automatic run post right now? (gap + jitter gate)
SLOT=rapid_alert PREVIEW_ONLY=1 python -m bot.main_signal   # generate without publishing
```

## State files (committed by CI)

| File | Purpose |
| --- | --- |
| `data/schedule_state.json` | Continuous queue: `pending`, `in_flight`, rotation-tagged publish ledger, completed rotation count, last error. Older untagged state migrates without discarding pending slots. |
| `data/pending_run.json` | What the current run mutated (slot, coin, `trade_id`) so a failed publish can undo exactly that |
| `data/publish_receipt.json` | Last publish or quality-skip outcome (mode, attempts, reason) |
| `data/active_trades.json` | Open setups the monitor watches |
| `data/closed_trades.json` | Resolved or expired setups (proof log) |
| `data/monitor_posts.json` | Legacy SL/pressure-post counters; retained but no longer read or updated |
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
- **TP-only monitor publishing.** The monitor posts verified TP1/TP2 hits only. Stop-loss hits are
  still closed and saved to `data/closed_trades.json`, but are silent on Binance Square; near-stop
  "under pressure" updates are disabled. The weekly aggregate scorecard separately summarizes wins
  and losses; individual SL-hit posts remain disabled. Position cards/TP1/TP2 posting are unchanged.
- **Quiet-market skip.** No momentum fallbacks are enabled. When no eligible candidate passes the
  strict momentum/direction/RR/ATR gates, nothing is published and the skipped opportunity is logged.

## Required secrets and variables

| Name | Where | Used for |
| --- | --- | --- |
| `BINANCE_SQUARE_OPENAPI_KEY` | Secret | Posting via the official `binance-skills-hub` scripts (cloned at runtime, patched by `bot/patch_square_*.py`) |
| `GROQ_API_KEY` | Secret | Post copy, signal narrative, market overview, meme |
| `GROQ_MODEL` | Variable | Preferred model (default `openai/gpt-oss-120b`); the engine probes the account and falls back |
| `SQUARE_PUBLISH_ATTEMPTS`, `SQUARE_POLL_*`, `SQUARE_PUBLISH_DEADLINE_SECONDS` | Variable | Media-upload retry tuning |
| `CARD_USERNAME`, `CARD_TIMEZONE`, `BINANCE_REFERRAL_CODE`, `CARD_*` | Variable | Proof-card look; `CARD_USERNAME` also watermarks charts |
| `TARGET_CARD_LEVERAGE` | Variable | Optional override; empty (default) means each card shows the trade's own configured leverage label |
| `MONITOR_CANDLE_INTERVAL`, `MONITOR_MAX_LOOKBACK_HOURS`, `MONITOR_MAX_KLINE_PAGES` | Variable | Scan window tuning |
| `ACTIVE_TRADE_TTL_HOURS` | Variable | Stale-trade expiry override (`0` disables) |

## Workflows

| Workflow | Schedule | Does |
| --- | --- | --- |
| `pacer.yml` — Square Pacer | long-running, self-renewing; `23 * * * *` revive cron | Dispatches only after the 230-minute gap + per-opportunity jitter; retries outages |
| `signal.yml` — Signal Poster | pacer + `0 * * * *` backup; Sunday `17 18 * * 0` recap | Six-slot rotation with quality skips, plus weekly text-only scorecard |
| `monitor.yml` — Live Monitor | `*/10 * * * *` | Candle-aware TP/SL detection, proof cards, state expiry |
| `tests.yml` — Tests | push / PR | Offline unit suites + config validation + compile check |
| `test-card.yml`, `binance-data-test.yml`, `price-consistency-test.yml` | manual | Card rendering check, live-data sanity checks |

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest test_schedule_rotation test_trade_state test_slot_cadence test_pacer \
    test_signal_quality_pipeline test_report_updates test_square_publish_resilience test_monitor_narration
```

`bot/schedule.py`, `bot/trade_state.py` and `bot/poster.py` are dependency-light on purpose, so the
state machine can be unit-tested without matplotlib or network access.

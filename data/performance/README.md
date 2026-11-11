# Performance log (the Phase 7 feedback loop)

The bot cannot read Binance Square view/like/reply counters through the posting
endpoint, so this file is filled by hand after publication. It is the raw
material the analyzer turns into prompt, structure, and topic-score reviews.

## How to log a post

Append one row per published post to `posts.csv` (right after publication is
too early — views keep moving; ~24 hours after publication is the fairest
capture time):

```
published_utc,url,type,coin,hook_family,views,likes,replies,shares,followers_gain,notes
2026-11-12T08:05:00Z,https://.../post/123,explained_setup,ERA,contrarian_conflict,18100,32,14,,,"hook compared price vs funding"
```

- `type`: `rapid_alert`, `explained_setup`, `market_overview`, `meme_community`,
  `scorecard`, `educational_explainer`, or the monitor's `target_hit`,
  `stop_resolution`, `pressure_update`.
- `hook_family`: the `angle_family` the post used (`contrarian_conflict`,
  `binary_scenario`, `data_finding`, `event_risk`, `outcome_update`,
  `structure_break`). The engine logs the chosen angle in its step summary;
  outcome posts use `outcome_update`.
- Leave `shares` / `followers_gain` blank when Square does not show them.

## How to read it

```bash
python -m bot.analyze_performance                    # whole log
python -m bot.analyze_performance --days 30          # recent window only
```

The report computes medians per type/hook family/coin, per-post and pooled
engagement ((likes + replies) / views x 100 — the same formula the creator
research used), top/bottom performers, and exactly one suggested next test.

Rules of the loop:

1. **Small samples are hints, not verdicts.** Groups under 5 posts are labelled
   "low sample" on purpose.
2. **One change at a time.** Approve a change, give it ~5 posts, re-measure.
3. **Accuracy gates are never tuned by engagement.** The feedback loop adjusts
   topics, hooks, structure, CTAs and visuals only.
4. **No viral guarantees.** The loop raises learning speed, nothing more.

# Binance Square visual strategy

## Evidence and limits

The supplied 31-page Visual Intelligence Research Report examined 499 posts and inspected 661 images from one creator (16 Sep–11 Nov 2026). The report observes that 89.7% were native-app captures; cropped annotated charts were 414/661 (62.6%), PnL cards 123/661 (18.6%), and full-screen app charts 56/661 (8.5%). One designed infographic was found. In this specific sample, new setups usually used one annotated chart; outcomes used a PnL receipt plus chart; liquidity arguments used liquidation/order-book evidence; community proof used comment captures; personal stories used photos or no image. Chart annotations and card colors encoded direction/risk, and images generally avoided editorial headlines. The report also records the highest median views for single-chart posts among common formats, but explicitly warns that topic, timing, audience growth, and post purpose confound performance; no visual treatment is proven to cause reach.

The implementation adopts the supported principles—visual evidence, one central claim, clear semantic hierarchy, consistency, mobile cropping, and setup/update/result continuity—without copying that creator's imagery, interface, palette, watermark, or identity. Local project branding remains authoritative.

## Routing and rendering policy

`bot.visual_strategy.select_visual_plan(topic, intent)` supplies the live routing vocabulary. More specific result/comparison/data intents take precedence over general analysis:

| Intent/topic | Plan | Evidence and treatment |
| --- | --- | --- |
| Signal / price thesis / Bitcoin analysis | THESIS, square | Real exchange candles and verified levels; show thesis plus invalidation, not invented candles |
| PnL, target hit, loss/result | RECEIPT, 4:5 | Verified ledger/result; pair receipt with the original reasoning when available; losses use the same treatment |
| ETF / asset comparison | VERSUS, 4:5 | Comparable units and a shared scale from sourced values |
| Whale, on-chain, funding, liquidation, order book | DATA, square | One sourced metric/flow and a concise meaning; no decorative pseudo-data |
| Conditional forecast | SCENARIO, square | Explicit bull/base/bear branches with conditional triggers, never a certainty claim |
| Education, security, regulation | EXPLAINER, 4:5 | Short, ordered diagram/timeline from validated information |
| Narrative without numeric evidence | EDITORIAL, square | Scene-only art; add any headline as code-rendered text, never ask the art model to draw text, logos, charts or numbers |

Current production remains the existing `main_signal` workflow: signal slots generate verified 1H charts from `market_data` rendered with mobile-first (4:5 portrait) TradingView and Binance Mobile styling across 5 specialized visual formats (`setup_box_arrow`, `trendline_retest`, `support_demand_zone`, `scenario_projection`, `breakout_expansion`); overview and educational slots use the styled 1H BTC chart; the community slot uses the existing Pillow meme card; scorecards remain text-only. Routing is advisory metadata for future specialized renderers and an explicit guardrail on what evidence is appropriate—it does not fabricate missing market data or change slot/publishing behavior.

## Shared design rules

- One image, one claim, one dominant focal point. The caption carries detailed reasoning.
- Data visuals are flat, code-rendered and sourced; no image model for charts, price labels, tickers, statistics or PnL.
- Existing dark analyst-card identity, bundled Inter typography, and project watermark/branding remain intact. Up/down colors retain semantic meaning; accent colors are for structure, not direction.
- Prefer square analysis and 4:5 receipts/explainers; retain generous safe margins, high contrast, legible price tags and limited annotation. Avoid forcing headlines into data plots.
- Use a distinct format when the topic needs it; avoid decorative coins, glow, rockets, excessive panels, or a carousel without an information purpose.
- For recurring stories, preserve continuity from thesis to update to verified result where existing state/data permit.

## Quality gate

Automated `validate_visual_asset` checks image decode, minimum dimensions, and square/4:5 ratio. It is not a visual inspection. Before a human accepts an asset, review the checklist returned by `quality_checklist`: relevance and one claim; readability at 360px; hierarchy/contrast/safe margins; project identity and non-repetitive composition; and exact number/source/timestamp validation. For thesis cards, verify actual candles, correct levels and invalidation. Current pipeline validation only checks file existence/size, so this is a baseline image-integrity helper, not an automatic regeneration or publication gate. A failed asset must be revised in the owning renderer; do not claim human review occurred unless a person actually inspected it.

## Research-to-repository comparison

- **Research observation:** evidence-first single chart, outcome receipt, format follows topic, little in-image copy. **Repository:** already generates verified charts and account-branded cards, but all signals share one chart renderer and community meme is the only other image type. **Opportunity:** a clear topic router and exact evidence rules; future specialized data/comparison/explainer cards should be code-rendered rather than AI-painted.
- **Research observation:** no causal proof that annotations/format increase reach. **Repository:** no image performance loop. Do not hard-code claims of engagement uplift; evaluate outcomes with the existing performance log.
- **Research observation:** authenticity and legibility are key, data values rendered by code. **Repository:** existing chart values come from verified exchange candles; general file validation does not inspect aspect, legibility, source line, or human relevance. Added deterministic image checks plus explicit human QC protocol.

## Representative routing tests

Examples: “Bitcoin news update” → THESIS; “ETF inflows and outflows” → VERSUS; “whale transaction report” → DATA; “crypto security educational post” → EXPLAINER; “BTC vs ETH market comparison” → VERSUS. `tests/test_visual_strategy.py` asserts these mappings and fallback behavior.

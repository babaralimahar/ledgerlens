# Costs and latency

Every cost number below comes from logged token counts × the prices in
[`config/pricing.yaml`](../config/pricing.yaml), copied by hand from Google's pricing page on
2026-11-15. Runs on Gemini's **free tier** still log the paid-tier price, so these are the costs
the same run would have had at paid rates.

## Provider choice (Day 2)

| Field | Value |
|---|---|
| Provider | Google Gemini, through its OpenAI-compatible endpoint (`/v1beta/openai/`) |
| Model | `gemini-3.1-flash-lite` |
| Why | Free tier (zero-spend mode), structured JSON output, tool calling; works with the existing client unchanged |
| Price (paid tier) | $0.25 per 1M input tokens · $1.50 per 1M output tokens |
| Compatibility note | The endpoint rejects our nested strict schema as too complex (bare HTTP 400). The model fills a flat "wire" schema instead, which code converts and validates strictly. See `domain/schemas.py`. |

## Measured (sequential runs, one invoice at a time)

| Run | Invoices | Total | Mean per invoice | p95 per invoice | Latency p50 | Latency p95 |
|---|---|---|---|---|---|---|
| dev, prompt v1 | 98 (+2 scans, no call) | $0.1896 | $0.00193 | $0.00256 | 4.1 s | 35.4 s |
| dev, prompt v2 | 98 | $0.1922 | $0.00196 | $0.00259 | 3.4 s | 16.2 s |
| **held-out test, prompt v2** | 49 (+1 scan) | **$0.0933** | **$0.00190** | $0.00259 | **3.4 s** | 34.7 s |
| smoke + probes + 20-invoice sample | — | ≈ $0.04 | — | — | — | — |

**P1 spend to date: about $0.52** against the manual's $8 cap for P1. All of it ran on the free
tier, so the actual charge was $0.

A typical invoice is about 640 input and 1,300 output tokens. Output dominates the cost, because
every field carries a quote and a confidence.

The p95 latency (16–35 s) comes from free-tier rate limits and retries, not from the model's
speed: the median is 3.4 s. Re-measure on the paid tier before quoting any p95 to a customer.

## Projection (estimate, not measured)

At 2,000 invoices a month: 2,000 × $0.0019 ≈ **$3.80/month** in model cost on the paid tier,
plus hosting (free tier for the demo).

## Where time goes

- **Model call:** ~3.4 s median, nearly all of an invoice's processing time.
- **PDF text extraction:** tens of milliseconds per page.
- **Rules, 3-way match, routing:** pure Python, sub-millisecond.
- **System overhead without a model** (upload, queue, parse, persist): see the load test below.

## Load test (fake model: system overhead only)

151 invoices/min with one embedded worker (20 closed-loop users, 5 min, 0 errors); upload p50 0.18 s.
Details and caveats: README, *Load test*; raw: `evals/reports/loadtest-fake-20u-300s-poll1s.json`.

## How to cut cost further

1. **Shorter output:** drop the confidence for fields that never vary (currency), or quote only
   the money fields. Measure field accuracy before and after on dev.
2. **Model tiering:** `gemini-2.5-flash-lite` costs 40% of this model's input price and 27% of
   its output price. Run `evals system --split dev` with `LLM_MODEL=gemini-2.5-flash-lite` and
   keep it only if the gate still holds.
3. **Skip the model for known layouts:** a template per top-20 vendor, if their volume justifies it.

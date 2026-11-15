# Costs

All cost numbers come from logged token counts × `config/pricing.yaml`. Prices are copied by hand
from the provider's pricing page, with the date. They are never typed from memory.

## Provider choice (Day 2)

| Field | Value |
|---|---|
| Provider | _to choose_ |
| Model id (exact) | |
| Why (structured output, tool calling, price, rate limits, can pay from Pakistan) | |
| Prices copied on (date) / source URL | |
| Monthly spend limit set in the provider console | USD ___ |

## Budget (from the manual, 60 days)

| Project | Cap (USD) | Spent | Notes |
|---|---|---|---|
| P1 LedgerLens | 8 | | |
| P2 PolicyPilot | 10 | | |
| P3 TicketPilot | 15 | | |
| P4 QueryWise | 10 | | |
| P5 ToolGate | 5 | | |
| Buffer | 10 | | |

## Smoke call

Paste the output of `uv run python -m app.cli llm-smoke "Say hello in five words"` here (tokens,
cost and latency).

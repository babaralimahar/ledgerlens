"""Closed-loop load test: N users, each uploads an invoice, waits until it is routed, repeats.

The number in flight never exceeds N, so the results are end-to-end times under concurrency
and the system's real throughput, not a backlog. Run it against a stack using the FAKE model,
so it measures LedgerLens's own overhead (upload, queue, PDF parsing, persistence), not the
provider's latency:

    $env:LLM_PROVIDER="fake"; $env:UPLOAD_RATE_PER_MINUTE="100000"; docker compose up -d --build
    uv run python scripts/loadtest.py --key <clerk key> --users 20 --seconds 300

Each upload is made unique with a trailing PDF comment, so idempotency does not short-circuit it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))] if s else float("nan")


def summary(xs: list[float], unit: str) -> dict[str, Any]:
    return {
        f"p50_{unit}": round(pct(xs, 0.5), 3),
        f"p95_{unit}": round(pct(xs, 0.95), 3),
        f"max_{unit}": round(max(xs, default=0.0), 3),
        "n": len(xs),
    }


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8002")
    p.add_argument("--key", required=True)
    p.add_argument("--users", type=int, default=20)
    p.add_argument("--seconds", type=int, default=300)
    p.add_argument("--pdfs", default="evals/datasets/invoices/pdfs")
    p.add_argument("--poll-s", type=float, default=0.1)
    p.add_argument("--out", default="evals/reports/loadtest.json")
    args = p.parse_args()

    pdfs = await asyncio.to_thread(
        lambda: [f.read_bytes() for f in sorted(Path(args.pdfs).glob("*.pdf"))[:40]]
    )
    upload_s: list[float] = []
    e2e_s: list[float] = []
    statuses: dict[str, int] = {}
    errors: dict[str, int] = {}
    seq = 0
    deadline = time.monotonic() + args.seconds

    async with httpx.AsyncClient(
        base_url=args.base, headers={"X-API-Key": args.key}, timeout=60
    ) as c:

        async def user(u: int) -> None:
            nonlocal seq
            while time.monotonic() < deadline:
                seq += 1
                body = pdfs[seq % len(pdfs)] + f"\n% loadtest {u}-{seq}\n".encode()
                t0 = time.perf_counter()
                try:
                    r = await c.post(
                        "/v1/invoices", files={"file": (f"{seq}.pdf", body, "application/pdf")}
                    )
                except httpx.HTTPError as e:
                    errors[type(e).__name__] = errors.get(type(e).__name__, 0) + 1
                    continue
                upload_s.append(time.perf_counter() - t0)
                if r.status_code != 202:
                    errors[str(r.status_code)] = errors.get(str(r.status_code), 0) + 1
                    continue
                inv = r.json()["invoice_id"]
                while (
                    True
                ):  # wait for this invoice to be routed (bounded by the run deadline + 2 min)
                    if time.monotonic() > deadline + 120:
                        errors["not_routed"] = errors.get("not_routed", 0) + 1
                        return
                    v = (await c.get(f"/v1/invoices/{inv}")).json()
                    if v["status"] not in ("queued", "processing"):
                        e2e_s.append(time.perf_counter() - t0)
                        statuses[v["status"]] = statuses.get(v["status"], 0) + 1
                        break
                    await asyncio.sleep(args.poll_s)

        started = time.monotonic()
        await asyncio.gather(*(user(i) for i in range(args.users)))
        elapsed = time.monotonic() - started

    result = {
        "mode": "closed-loop",
        "users": args.users,
        "duration_s": round(elapsed, 1),
        "invoices_routed": len(e2e_s),
        "throughput_per_min": round(len(e2e_s) / (elapsed / 60), 1),
        "upload_latency": summary(upload_s, "s"),
        "end_to_end_latency_incl_queue": summary(e2e_s, "s"),
        "final_statuses": statuses,
        "errors": errors,
        "note": "fake LLM (system overhead only), one embedded worker, same machine as the client",
    }
    out = Path(args.out)
    await asyncio.to_thread(out.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(out.write_text, json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

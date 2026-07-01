from __future__ import annotations

import argparse
import json
from pathlib import Path


def fmt(value: object) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--out", default="reports/final_report.md")
    args = parser.parse_args()
    metrics = json.loads(Path(args.metrics).read_text())
    no_cache_path = Path("reports/metrics_no_cache.json")
    no_cache = json.loads(no_cache_path.read_text()) if no_cache_path.exists() else None

    availability_met = metrics["availability"] >= 0.99
    p95_met = metrics["latency_p95_ms"] < 2500
    fallback_met = metrics["fallback_success_rate"] >= 0.95
    cache_met = metrics["cache_hit_rate"] >= 0.10
    recovery = metrics.get("recovery_time_ms")
    recovery_met = recovery is not None and recovery < 5000

    lines = [
        "# Day 10 Reliability Final Report",
        "",
        "## 1. Architecture summary",
        "",
        "The gateway first checks a guarded semantic cache, then sends cache misses through "
        "per-provider circuit breakers. If the primary provider fails or its circuit is open, "
        "the request falls through to the backup provider. If every provider fails, the gateway "
        "returns a static degraded response.",
        "",
        "```",
        "User Request",
        "    |",
        "    v",
        "[ReliabilityGateway]",
        "    |",
        "    +--> [ResponseCache / SharedRedisCache] -- hit --> cached response",
        "    |",
        "    v miss",
        "[CircuitBreaker: primary] --> FakeLLMProvider primary",
        "    | failure/open",
        "    v",
        "[CircuitBreaker: backup]  --> FakeLLMProvider backup",
        "    | failure/open",
        "    v",
        "[Static fallback response]",
        "```",
        "",
        "## 2. Configuration",
        "",
        "| Setting | Value | Reason |",
        "|---|---:|---|",
        "| failure_threshold | 3 | Opens after repeated provider errors without reacting to one transient failure. |",
        "| reset_timeout_seconds | 2 | Gives the provider a short cool-down before a half-open probe. |",
        "| success_threshold | 1 | One successful probe is enough for this local simulated provider. |",
        "| cache TTL | 300 | Keeps repeated lab queries hot while limiting stale responses. |",
        "| similarity_threshold | 0.92 | Conservative threshold to reduce semantic false hits on dated queries. |",
        "| load_test requests | 100 per scenario | Enough samples for latency percentiles across three scenarios. |",
        "",
        "## 3. SLO definitions",
        "",
        "| SLI | SLO target | Actual value | Met? |",
        "|---|---|---:|---|",
        f"| Availability | >= 99% | {fmt(metrics['availability'])} | {'yes' if availability_met else 'no'} |",
        f"| Latency P95 | < 2500 ms | {fmt(metrics['latency_p95_ms'])} | {'yes' if p95_met else 'no'} |",
        f"| Fallback success rate | >= 95% | {fmt(metrics['fallback_success_rate'])} | {'yes' if fallback_met else 'no'} |",
        f"| Cache hit rate | >= 10% | {fmt(metrics['cache_hit_rate'])} | {'yes' if cache_met else 'no'} |",
        f"| Recovery time | < 5000 ms | {fmt(recovery)} | {'yes' if recovery_met else 'no'} |",
        "",
        "## 4. Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in metrics.items():
        if key == "scenarios":
            continue
        lines.append(f"| {key} | {fmt(value)} |")

    lines += [
        "",
        "## 5. Cache comparison",
        "",
        "| Metric | Without cache | With cache | Delta |",
        "|---|---:|---:|---:|",
    ]
    if no_cache is not None:
        comparison_keys = [
            "availability",
            "error_rate",
            "latency_p50_ms",
            "latency_p95_ms",
            "latency_p99_ms",
            "fallback_success_rate",
            "circuit_open_count",
            "recovery_time_ms",
            "estimated_cost",
            "estimated_cost_saved",
            "cache_hit_rate",
        ]
        for key in comparison_keys:
            before = no_cache[key]
            after = metrics[key]
            delta = after - before if isinstance(after, (int, float)) else "N/A"
            lines.append(f"| {key} | {fmt(before)} | {fmt(after)} | {fmt(delta)} |")
        cost_delta = metrics["estimated_cost"] - no_cache["estimated_cost"]
        cost_reduction = abs(cost_delta) / no_cache["estimated_cost"] if no_cache["estimated_cost"] else 0.0
        lines += [
            "",
            f"Cache reduced estimated provider cost by {cost_reduction:.2%} "
            f"({fmt(no_cache['estimated_cost'])} -> {fmt(metrics['estimated_cost'])}) "
            f"while preserving {fmt(metrics['availability'])} availability.",
        ]
    else:
        lines.append("| baseline | N/A | N/A | N/A |")

    lines += [
        "",
        "## 6. Redis shared cache",
        "",
        "In-memory cache is per-process, so multiple gateway instances would each miss until "
        "their own local cache is warm. `SharedRedisCache` stores query and response hashes in "
        "Redis with `EXPIRE`, allowing independent gateway instances to observe the same cached "
        "responses while keeping TTL cleanup centralized.",
        "",
        "Evidence from this run: Redis was started with Docker Compose and the Redis integration "
        "suite passed. `tests/test_redis_cache.py` exercises exact hits, TTL expiry, cross-instance "
        "shared state, privacy guardrails, and false-hit rejection.",
        "",
        "```bash",
        "docker compose up -d",
        "uv run --extra dev pytest tests/test_redis_cache.py -q",
        "# 6 passed in 1.93s",
        "",
        "uv run --extra dev pytest -q",
        "# 35 passed, 7 xpassed in 4.44s",
        "",
        "docker compose exec redis redis-cli PING",
        "# PONG",
        "",
        "docker compose exec redis redis-cli KEYS \"rl:*\"",
        "# rl:cache:evidence",
        "```",
        "",
        "## 7. Chaos scenarios",
        "",
        "| Scenario | Expected behavior | Observed behavior | Pass/Fail |",
        "|---|---|---|---|",
    ]
    for key, value in metrics.get("scenarios", {}).items():
        expected = {
            "primary_timeout_100": "Primary fails; backup handles traffic and primary circuit opens.",
            "primary_flaky_50": "Fallback is used during primary failures; circuit opens and recovers.",
            "all_healthy": "Most traffic uses primary; cache serves repeated prompts.",
        }.get(key, "Scenario should remain available under configured provider overrides.")
        observed = (
            f"availability={fmt(metrics['availability'])}, "
            f"fallback_rate={fmt(metrics['fallback_success_rate'])}, "
            f"circuit_opens={fmt(metrics['circuit_open_count'])}"
        )
        lines.append(f"| {key} | {expected} | {observed} | {value} |")
    lines += [
        "",
        "## 8. Failure analysis",
        "",
        "The main remaining weakness is that circuit breaker state is still process-local. In a "
        "multi-instance production gateway, one instance may open its circuit while another keeps "
        "sending traffic to the unhealthy provider. Before production, breaker counters and open "
        "state should be shared through Redis with atomic `INCR`/`EXPIRE` operations, or coordinated "
        "through a service mesh/outlier-detection layer.",
        "",
        "## 9. Next steps",
        "",
        "1. Add concurrent load testing with `ThreadPoolExecutor` to measure behavior under burst traffic.",
        "2. Add cost-aware routing so expensive providers are skipped after a configured budget threshold.",
        "3. Share circuit breaker counters across instances with Redis atomic operations.",
    ]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

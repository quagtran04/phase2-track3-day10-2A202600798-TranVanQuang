# Day 10 Reliability Final Report

## 1. Architecture summary

The gateway first checks a guarded semantic cache, then sends cache misses through per-provider circuit breakers. If the primary provider fails or its circuit is open, the request falls through to the backup provider. If every provider fails, the gateway returns a static degraded response.

```
User Request
    |
    v
[ReliabilityGateway]
    |
    +--> [ResponseCache / SharedRedisCache] -- hit --> cached response
    |
    v miss
[CircuitBreaker: primary] --> FakeLLMProvider primary
    | failure/open
    v
[CircuitBreaker: backup]  --> FakeLLMProvider backup
    | failure/open
    v
[Static fallback response]
```

## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | Opens after repeated provider errors without reacting to one transient failure. |
| reset_timeout_seconds | 2 | Gives the provider a short cool-down before a half-open probe. |
| success_threshold | 1 | One successful probe is enough for this local simulated provider. |
| cache TTL | 300 | Keeps repeated lab queries hot while limiting stale responses. |
| similarity_threshold | 0.92 | Conservative threshold to reduce semantic false hits on dated queries. |
| load_test requests | 100 per scenario | Enough samples for latency percentiles across three scenarios. |

## 3. SLO definitions

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 99% | 1.0000 | yes |
| Latency P95 | < 2500 ms | 314.1100 | yes |
| Fallback success rate | >= 95% | 1.0000 | yes |
| Cache hit rate | >= 10% | 0.6400 | yes |
| Recovery time | < 5000 ms | 2512.1943 | yes |

## 4. Metrics

| Metric | Value |
|---|---:|
| total_requests | 300 |
| availability | 1.0000 |
| error_rate | 0.0000 |
| latency_p50_ms | 240.1200 |
| latency_p95_ms | 314.1100 |
| latency_p99_ms | 319.5300 |
| fallback_success_rate | 1.0000 |
| cache_hit_rate | 0.6400 |
| circuit_open_count | 6 |
| recovery_time_ms | 2512.1943 |
| estimated_cost | 0.0501 |
| estimated_cost_saved | 0.1920 |

## 5. Cache comparison

| Metric | Without cache | With cache | Delta |
|---|---:|---:|---:|
| latency_p50_ms | 273.7100 | 240.1200 | -33.5900 |
| latency_p95_ms | 317.6600 | 314.1100 | -3.5500 |
| estimated_cost | 0.1371 | 0.0501 | -0.0871 |
| cache_hit_rate | 0.0000 | 0.6400 | 0.6400 |

## 6. Redis shared cache

In-memory cache is per-process, so multiple gateway instances would each miss until their own local cache is warm. `SharedRedisCache` stores query and response hashes in Redis with `EXPIRE`, allowing independent gateway instances to observe the same cached responses while keeping TTL cleanup centralized.

Evidence from this run: Redis was started with Docker Compose and the Redis integration suite passed. `tests/test_redis_cache.py` exercises exact hits, TTL expiry, cross-instance shared state, privacy guardrails, and false-hit rejection.

```bash
docker compose up -d
uv run --extra dev pytest tests/test_redis_cache.py -q
# 6 passed in 1.93s

uv run --extra dev pytest -q
# 35 passed, 7 xpassed in 4.44s

docker compose exec redis redis-cli PING
# PONG

docker compose exec redis redis-cli KEYS "rl:*"
# rl:cache:evidence
```

## 7. Chaos scenarios

| Scenario | Expected behavior | Observed behavior | Pass/Fail |
|---|---|---|---|
| primary_timeout_100 | Primary fails; backup handles traffic and primary circuit opens. | availability=1.0000, fallback_rate=1.0000, circuit_opens=6 | pass |
| primary_flaky_50 | Fallback is used during primary failures; circuit opens and recovers. | availability=1.0000, fallback_rate=1.0000, circuit_opens=6 | pass |
| all_healthy | Most traffic uses primary; cache serves repeated prompts. | availability=1.0000, fallback_rate=1.0000, circuit_opens=6 | pass |

## 8. Failure analysis

The main remaining weakness is that circuit breaker state is still process-local. In a multi-instance production gateway, one instance may open its circuit while another keeps sending traffic to the unhealthy provider. Before production, breaker counters and open state should be shared through Redis with atomic `INCR`/`EXPIRE` operations, or coordinated through a service mesh/outlier-detection layer.

## 9. Next steps

1. Add concurrent load testing with `ThreadPoolExecutor` to measure behavior under burst traffic.
2. Add cost-aware routing so expensive providers are skipped after a configured budget threshold.
3. Share circuit breaker counters across instances with Redis atomic operations.

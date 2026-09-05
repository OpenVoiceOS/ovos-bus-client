#!/usr/bin/env python3
"""
Benchmark: sync MessageBusClient vs async AsyncMessageBusClient.

This benchmark uses in-process fake buses (no real WebSocket server)
so it measures pure Python overhead: serialization, event-emitter dispatch,
session injection, and waiter/collector coordination.

Usage:
    uv run python benchmarks/bench_async_vs_sync.py
    uv run python benchmarks/bench_async_vs_sync.py --n 5000
"""

import argparse
import asyncio
import json
import statistics
import time
from contextlib import contextmanager
from typing import List
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers for constructing pre-connected buses
# ---------------------------------------------------------------------------

def _cfg_patch():
    """Context manager that stubs out config loading."""
    mock_cfg = MagicMock()
    mock_cfg.host = "localhost"
    mock_cfg.port = 8181
    mock_cfg.route = "/core"
    mock_cfg.ssl = False
    return patch("ovos_bus_client.client.async_client.load_message_bus_config",
                 return_value=mock_cfg)


def _sync_cfg_patch():
    mock_cfg = MagicMock()
    mock_cfg.host = "localhost"
    mock_cfg.port = 8181
    mock_cfg.route = "/core"
    mock_cfg.ssl = False
    return patch("ovos_bus_client.client.client.load_message_bus_config",
                 return_value=mock_cfg)


def make_sync_bus():
    """Return a MessageBusClient with a mocked websocket, pre-connected."""
    from ovos_bus_client.client.client import MessageBusClient
    from unittest.mock import MagicMock

    with _sync_cfg_patch():
        bus = MessageBusClient()

    ws_mock = MagicMock()
    ws_mock.send = MagicMock()
    bus.client = ws_mock
    bus.connected_event.set()
    bus.started_running = True
    return bus


def make_async_bus():
    """Return an AsyncMessageBusClient with a mocked websocket, pre-connected."""
    from ovos_bus_client.client.async_client import AsyncMessageBusClient
    from unittest.mock import AsyncMock

    with _cfg_patch():
        bus = AsyncMessageBusClient()

    ws_mock = AsyncMock()
    ws_mock.send = AsyncMock()
    bus._ws = ws_mock
    bus._connected.set()
    return bus


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

@contextmanager
def timer(label: str):
    t0 = time.perf_counter()
    yield
    elapsed = time.perf_counter() - t0
    print(f"  {label}: {elapsed * 1000:.2f} ms")
    return elapsed


def _timeit(fn, n: int) -> List[float]:
    """Run fn() n times, return list of per-call times (seconds)."""
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return times


async def _atimeit(afn, n: int) -> List[float]:
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        await afn()
        times.append(time.perf_counter() - t0)
    return times


def _stats(times: List[float], label: str):
    ms = [t * 1000 for t in times]
    print(f"  {label}: "
          f"min={min(ms):.3f}ms  "
          f"mean={statistics.mean(ms):.3f}ms  "
          f"median={statistics.median(ms):.3f}ms  "
          f"max={max(ms):.3f}ms  "
          f"stdev={statistics.stdev(ms):.3f}ms")


# ---------------------------------------------------------------------------
# Benchmark 1 — emit() throughput
# ---------------------------------------------------------------------------

def bench_sync_emit(n: int):
    from ovos_bus_client.message import Message
    bus = make_sync_bus()
    msg = Message("benchmark.emit", {"payload": "x" * 128})

    def do():
        bus.emit(msg)

    times = _timeit(do, n)
    _stats(times, f"sync  emit ×{n}")
    return statistics.mean(times)


async def bench_async_emit(n: int):
    from ovos_bus_client.message import Message
    bus = make_async_bus()
    msg = Message("benchmark.emit", {"payload": "x" * 128})

    async def do():
        await bus.emit(msg)

    times = await _atimeit(do, n)
    _stats(times, f"async emit ×{n}")
    return statistics.mean(times)


# ---------------------------------------------------------------------------
# Benchmark 2 — wait_for_message  (loopback via emitter)
# ---------------------------------------------------------------------------

def bench_sync_wait_for_message(n: int):
    """Sync wait_for_message with immediate reply injected in a thread."""
    import threading
    from ovos_bus_client.message import Message
    from ovos_bus_client.client.waiter import MessageWaiter

    bus = make_sync_bus()

    def do():
        waiter = MessageWaiter(bus, "bench.reply")
        msg = Message("bench.reply")
        bus.emitter.emit("bench.reply", msg)
        return waiter.wait(timeout=1.0)

    times = _timeit(do, n)
    _stats(times, f"sync  wait_for_message ×{n}")
    return statistics.mean(times)


async def bench_async_wait_for_message(n: int):
    from ovos_bus_client.message import Message
    from ovos_bus_client.client.async_client import AsyncMessageWaiter
    bus = make_async_bus()

    async def do():
        waiter = AsyncMessageWaiter(bus, "bench.reply")
        msg = Message("bench.reply")
        bus.emitter.emit("bench.reply", msg)
        return await waiter.wait(timeout=1.0)

    times = await _atimeit(do, n)
    _stats(times, f"async wait_for_message ×{n}")
    return statistics.mean(times)


# ---------------------------------------------------------------------------
# Benchmark 3 — Message serialization (shared; baseline for both clients)
# ---------------------------------------------------------------------------

def bench_message_serialization(n: int):
    from ovos_bus_client.message import Message
    msg = Message("bench.serialize", {"data": "x" * 256,
                                       "nested": {"a": 1, "b": [1, 2, 3]}})

    def do():
        raw = msg.serialize()
        Message.deserialize(raw)

    times = _timeit(do, n)
    _stats(times, f"msg serialize+deserialize ×{n}")
    return statistics.mean(times)


# ---------------------------------------------------------------------------
# Benchmark 4 — concurrent async emit (fan-out)
# ---------------------------------------------------------------------------

async def bench_async_concurrent_emit(n: int, concurrency: int = 100):
    """Emit `concurrency` messages concurrently, measure wall-clock time."""
    from ovos_bus_client.message import Message
    bus = make_async_bus()
    messages = [Message(f"bench.concurrent.{i}", {"i": i}) for i in range(concurrency)]

    async def run_all():
        await asyncio.gather(*[bus.emit(m) for m in messages])

    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        await run_all()
        times.append(time.perf_counter() - t0)

    _stats(times, f"async concurrent emit ×{concurrency} (×{n} rounds)")
    total_msgs = n * concurrency
    total_s = sum(times)
    print(f"  => throughput: {total_msgs / total_s:.0f} msg/s")
    return statistics.mean(times)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync vs Async bus client benchmark")
    parser.add_argument("--n", type=int, default=2000,
                        help="Number of iterations for each benchmark (default: 2000)")
    parser.add_argument("--concurrency", type=int, default=200,
                        help="Fan-out size for concurrent-emit benchmark (default: 200)")
    args = parser.parse_args()

    n = args.n
    print(f"\n{'=' * 60}")
    print(f"  ovos-bus-client  sync vs async  (n={n})")
    print(f"{'=' * 60}\n")

    print("── 1. emit() ──────────────────────────────────────────────")
    sync_emit_mean = bench_sync_emit(n)
    async_emit_mean = asyncio.run(bench_async_emit(n))
    ratio = async_emit_mean / sync_emit_mean if sync_emit_mean else float("inf")
    print(f"  ratio async/sync: {ratio:.2f}x\n")

    print("── 2. wait_for_message() ───────────────────────────────────")
    sync_wfm = bench_sync_wait_for_message(n)
    async_wfm = asyncio.run(bench_async_wait_for_message(n))
    ratio = async_wfm / sync_wfm if sync_wfm else float("inf")
    print(f"  ratio async/sync: {ratio:.2f}x\n")

    print("── 3. Message serialization (baseline) ─────────────────────")
    bench_message_serialization(n)
    print()

    print("── 4. Concurrent async emit (fan-out) ──────────────────────")
    asyncio.run(bench_async_concurrent_emit(n=max(n // 10, 10),
                                             concurrency=args.concurrency))
    print()

    print(f"{'=' * 60}")
    print("  Done.\n")


if __name__ == "__main__":
    main()

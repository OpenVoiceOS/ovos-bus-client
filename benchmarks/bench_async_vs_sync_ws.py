#!/usr/bin/env python3
"""Benchmark sync vs async clients against a **real** WebSocket server.

The in-process benchmark (`bench_async_vs_sync.py`) measures library overhead
only — it stubs out the transport. This file spins up a localhost WebSocket
echo server (on a free port) and exercises both clients end-to-end so the
numbers include socket setup, JSON over the wire, and event-loop / thread
scheduling.

Both runs hit the same loopback socket, so this is not "real network" — it
is "real transport, no network." Add latency separately if you care about
remote scenarios.

Usage:
    python benchmarks/bench_async_vs_sync_ws.py
    python benchmarks/bench_async_vs_sync_ws.py --n 500
"""
import argparse
import asyncio
import json
import socket
import statistics
import threading
import time

import websockets

from ovos_bus_client import Message, MessageBusClient
from ovos_bus_client.client.async_client import AsyncMessageBusClient


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Echo server: receive a Message, send <type>.response back with the data.
# Runs on its own thread + asyncio loop so both sync and async clients can
# talk to it without interfering with each other's event loops.
# ---------------------------------------------------------------------------

class _EchoServer:
    """Localhost WebSocket server that responds to every Message with
    <msg_type>.response (the convention used by wait_for_response)."""

    def __init__(self, port: int):
        self.port = port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()

    async def _handle(self, ws):
        try:
            async for raw in ws:
                try:
                    obj = json.loads(raw)
                    msg_type = obj.get("type", "unknown")
                    data = obj.get("data", {})
                    reply = json.dumps({
                        "type": f"{msg_type}.response",
                        "data": data,
                        "context": obj.get("context", {}),
                    })
                    await ws.send(reply)
                except Exception:
                    pass
        except websockets.ConnectionClosed:
            pass

    async def _serve(self):
        async with websockets.serve(self._handle, "127.0.0.1", self.port):
            self._stop_event = asyncio.Event()
            self._ready.set()
            await self._stop_event.wait()

    def start(self):
        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._serve())
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self):
        if self._stop_event and self._loop:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stats(times: list[float], label: str):
    times_ms = [t * 1000 for t in times]
    print(f"  {label:48s} "
          f"min={min(times_ms):.2f}ms  "
          f"mean={statistics.mean(times_ms):.2f}ms  "
          f"median={statistics.median(times_ms):.2f}ms  "
          f"p95={sorted(times_ms)[int(len(times_ms) * 0.95)]:.2f}ms  "
          f"max={max(times_ms):.2f}ms")
    return statistics.mean(times)


# ---------------------------------------------------------------------------
# Sync client benchmark
# ---------------------------------------------------------------------------

def bench_sync(port: int, n: int):
    bus = MessageBusClient(host="127.0.0.1", port=port, route="/")
    bus.run_in_thread()
    bus.connected_event.wait(timeout=5)

    # 1. fire-and-forget emit
    times_emit = []
    for i in range(n):
        t0 = time.perf_counter()
        bus.emit(Message(f"bench.emit.{i}", {"i": i}))
        times_emit.append(time.perf_counter() - t0)
    _stats(times_emit, f"sync emit ×{n}")

    # 2. round-trip via wait_for_response
    times_rt = []
    for i in range(n):
        t0 = time.perf_counter()
        bus.wait_for_response(Message(f"bench.rt.{i}", {"i": i}), timeout=2.0)
        times_rt.append(time.perf_counter() - t0)
    _stats(times_rt, f"sync round-trip (wait_for_response) ×{n}")

    bus.close()


# ---------------------------------------------------------------------------
# Async client benchmark
# ---------------------------------------------------------------------------

async def bench_async(port: int, n: int):
    bus = AsyncMessageBusClient(host="127.0.0.1", port=port, route="/")
    await bus.connect()

    # 1. fire-and-forget emit
    times_emit = []
    for i in range(n):
        t0 = time.perf_counter()
        await bus.emit(Message(f"bench.emit.{i}", {"i": i}))
        times_emit.append(time.perf_counter() - t0)
    _stats(times_emit, f"async emit ×{n}")

    # 2. round-trip via wait_for_response
    times_rt = []
    for i in range(n):
        t0 = time.perf_counter()
        await bus.wait_for_response(Message(f"bench.rt.{i}", {"i": i}), timeout=2.0)
        times_rt.append(time.perf_counter() - t0)
    _stats(times_rt, f"async round-trip (wait_for_response) ×{n}")

    # 3. concurrent fan-out — the place async should pull ahead
    queries = [Message(f"bench.fan.{i}", {"i": i}) for i in range(n)]
    t0 = time.perf_counter()
    await asyncio.gather(*[bus.wait_for_response(q, timeout=5.0) for q in queries])
    fan_total = time.perf_counter() - t0
    print(f"  async fan-out × {n:<5}                              "
          f"total={fan_total * 1000:.0f}ms  throughput={n / fan_total:.0f} req/s")

    await bus.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=1000,
                        help="Number of iterations per case (default 1000)")
    args = parser.parse_args()

    port = _free_port()
    server = _EchoServer(port)
    server.start()
    print(f"\nWebSocket echo server: ws://127.0.0.1:{port}/\n")

    try:
        print("=" * 70)
        print(f"  Real-transport benchmark  (n={args.n}, port={port})")
        print("=" * 70 + "\n")

        print("── sync MessageBusClient ─────────────────────────────────────────────")
        bench_sync(port, args.n)
        print()

        print("── async AsyncMessageBusClient ───────────────────────────────────────")
        asyncio.run(bench_async(port, args.n))
        print()
    finally:
        server.stop()


if __name__ == "__main__":
    main()

"""
Benchmark for asyncio websocket server and client performance
transferring 1MB of data.

Author: Kumar Aditya
"""

import pyperf
import websockets.server
import websockets.client
import websockets.exceptions
import asyncio

CHUNK_SIZE = 1024 ** 2
DATA = b"x" * CHUNK_SIZE

stop: asyncio.Event


async def handler(websocket) -> None:
    # Signal from a finally block: if recv() raises (e.g. the peer went away
    # early) the main coroutine would otherwise wait on `stop` forever.
    try:
        for _ in range(100):
            await websocket.recv()
    finally:
        stop.set()


async def send(ws):
    try:
        await ws.send(DATA)
    except websockets.exceptions.ConnectionClosedOK:
        pass


async def main() -> None:
    global stop
    t0 = pyperf.perf_counter()
    stop = asyncio.Event()
    # Bind port 0 so the kernel picks a free port, then read it back. A
    # hardcoded port makes the benchmark fail outright if anything else on the
    # machine (including a second copy of this benchmark) already holds it.
    # Bind the loopback address explicitly: with a wildcard host, port 0 hands
    # out a *different* ephemeral port per address family.
    async with websockets.server.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with websockets.client.connect(f"ws://127.0.0.1:{port}") as ws:
            await asyncio.gather(*[send(ws) for _ in range(100)])
        await stop.wait()
    return pyperf.perf_counter() - t0


if __name__ == "__main__":
    runner = pyperf.Runner()
    runner.metadata['description'] = "Benchmark asyncio websockets"
    runner.bench_async_func('asyncio_websockets', main)

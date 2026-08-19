"""
Normalise tokens across threads, with a cache in front of the expensive part.

A shared memo cache has to be locked to be correct, which makes it something
every thread queues behind.

naive       one shared dict guarded by a lock, checked and filled per token.
optimized   a per-thread cache in threading.local, so lookups never leave the
            thread. Nothing is shared, at the cost of computing a popular key
            once per thread rather than once in total.
"""

import pyperf

import threading
from concurrent.futures import ThreadPoolExecutor, wait


VOCABULARY = ["token-%03d" % (i * 37 % 800) for i in range(800)]
TOKENS = [VOCABULARY[(i * 13) % len(VOCABULARY)] for i in range(72000)]


def normalise(token):
    # The expensive call the cache holds results for.
    folded = token.lower().replace("-", "_")
    total = 0
    for ch in folded:
        total = (total * 131 + ord(ch)) & 0xFFFFFFFF
    for _ in range(48):
        total = (total * 1103515245 + 12345) & 0xFFFFFFFF
    return "%s:%08x" % (folded, total)


def worker_naive(chunk, cache, lock):
    out = 0
    for token in chunk:
        with lock:
            value = cache.get(token)
            if value is None:
                value = normalise(token)
                cache[token] = value
        out += len(value)
    return out


def worker_optimized(chunk, local_state, _lock):
    cache = getattr(local_state, "cache", None)
    if cache is None:
        cache = local_state.cache = {}
    out = 0
    for token in chunk:
        value = cache.get(token)
        if value is None:
            value = normalise(token)
            cache[token] = value
        out += len(value)
    return out


def make_chunks(tokens_per_chunk):
    return [TOKENS[i:i + tokens_per_chunk]
            for i in range(0, len(TOKENS), tokens_per_chunk)]


_expected_total = None


def expected_total():
    """
    What the workers should add up to, computed once without a cache: a
    reference built through the same cache would agree with a broken one.
    """
    global _expected_total
    if _expected_total is None:
        _expected_total = sum(len(normalise(token)) for token in TOKENS)
    return _expected_total


def check_total(total):
    """
    Verify the combined worker output, outside the timed region. A cache that
    returns another key's value shows up as a total that differs.
    """
    if total != expected_total():
        raise AssertionError("total %d, expected %d" % (total,
                                                        expected_total()))


def run(worker, shared_factory, loops, threads, tokens_per_chunk):
    chunks = make_chunks(tokens_per_chunk)
    range_it = range(loops)
    total = 0
    with ThreadPoolExecutor(max_workers=threads) as pool:
        t0 = pyperf.perf_counter()
        for _ in range_it:
            shared = shared_factory()
            lock = threading.Lock()
            futures = [pool.submit(worker, chunk, shared, lock)
                       for chunk in chunks]
            wait(futures)
            total = 0
            for future in futures:
                total += future.result()
        dt = pyperf.perf_counter() - t0
    return dt, total


def checked(worker, shared_factory):
    def wrapper(loops, threads, tokens_per_chunk):
        dt, total = run(worker, shared_factory, loops, threads,
                        tokens_per_chunk)
        check_total(total)
        return dt
    return wrapper


BENCHMARKS = {
    'thread_memo_naive': checked(worker_naive, dict),
    'thread_memo_optimized': checked(worker_optimized, threading.local),
}


def add_cmdline_args(cmd, args):
    cmd.append(args.benchmark)
    cmd.extend(('--threads', str(args.threads)))
    cmd.extend(('--tokens-per-chunk', str(args.tokens_per_chunk)))


if __name__ == "__main__":
    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    runner.metadata['description'] = "Memo cache shared between threads"
    runner.argparser.add_argument("benchmark", choices=sorted(BENCHMARKS))
    runner.argparser.add_argument("--threads", type=int, default=4)
    runner.argparser.add_argument("--tokens-per-chunk", type=int, default=1440)

    args = runner.parse_args()
    runner.metadata['threads'] = args.threads
    runner.bench_time_func(args.benchmark, BENCHMARKS[args.benchmark],
                           args.threads, args.tokens_per_chunk)

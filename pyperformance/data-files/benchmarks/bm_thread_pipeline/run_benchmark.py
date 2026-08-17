"""
A producer/consumer pipeline over a queue.

Every put and get takes the queue's lock, so the queue is shared by
construction; what varies is how often it is touched.

naive       one item per put and one per get.
optimized   the same items moved in batches, so the queue is touched once per
            batch and the per-item work happens inside a worker.

Both move the same items and compute the same total.
"""

import pyperf

import queue
import threading
from concurrent.futures import ThreadPoolExecutor, wait


ITEMS = [("payload-%04d" % i, i * 7 % 311) for i in range(9600)]
SENTINEL = None


def transform(name, weight):
    total = weight
    for ch in name:
        total = (total * 131 + ord(ch)) & 0xFFFFFFFF
    for _ in range(24):
        total = (total * 1103515245 + 12345) & 0xFFFFFFFF
    return total


def consumer_single(q, results, lock):
    local = 0
    count = 0
    while True:
        item = q.get()
        if item is SENTINEL:
            q.task_done()
            break
        name, weight = item
        local += transform(name, weight)
        count += 1
        q.task_done()
    with lock:
        results.append((count, local))


def consumer_batched(q, results, lock):
    local = 0
    count = 0
    while True:
        batch = q.get()
        if batch is SENTINEL:
            q.task_done()
            break
        for name, weight in batch:
            local += transform(name, weight)
            count += 1
        q.task_done()
    with lock:
        results.append((count, local))


_expected_sum = None


def expected_sum():
    """The transformed total a correct run produces, computed once."""
    global _expected_sum
    if _expected_sum is None:
        _expected_sum = sum(transform(name, weight) for name, weight in ITEMS)
    return _expected_sum


def check_consumed(results, threads):
    """
    Verify the pipeline drained correctly. Called once per benchmark call,
    never inside the timed region.

    Every consumer must report back, or a sentinel went to the wrong thread
    and one is still blocked on get(). Every item must be accounted for
    exactly once, or the queue lost or duplicated one. And the sum must match
    the sequential answer, since a consumer that transformed the wrong field
    would still get the count right.
    """
    if len(results) != threads:
        raise AssertionError("expected %d consumer results, got %d"
                             % (threads, len(results)))
    seen = sum(count for count, _ in results)
    if seen != len(ITEMS):
        raise AssertionError("lost items: %d of %d" % (seen, len(ITEMS)))
    total = sum(value for _, value in results)
    if total != expected_sum():
        raise AssertionError("transformed total %d, expected %d"
                             % (total, expected_sum()))


def run(consumer, batched, loops, threads, batch_size):
    if batched:
        payloads = [ITEMS[i:i + batch_size]
                    for i in range(0, len(ITEMS), batch_size)]
    else:
        payloads = ITEMS

    range_it = range(loops)
    results = []
    with ThreadPoolExecutor(max_workers=threads) as pool:
        t0 = pyperf.perf_counter()
        for _ in range_it:
            q = queue.Queue()
            results = []
            lock = threading.Lock()
            futures = [pool.submit(consumer, q, results, lock)
                       for _ in range(threads)]
            for payload in payloads:
                q.put(payload)
            for _ in range(threads):
                q.put(SENTINEL)
            wait(futures)
            for future in futures:
                future.result()
        dt = pyperf.perf_counter() - t0
    return dt, results


def checked(consumer, batched):
    def wrapper(loops, threads, batch_size):
        dt, results = run(consumer, batched, loops, threads, batch_size)
        check_consumed(results, threads)
        return dt
    return wrapper


BENCHMARKS = {
    'thread_pipeline_naive': checked(consumer_single, False),
    'thread_pipeline_optimized': checked(consumer_batched, True),
}


def add_cmdline_args(cmd, args):
    cmd.append(args.benchmark)
    cmd.extend(('--threads', str(args.threads)))
    cmd.extend(('--batch-size', str(args.batch_size)))


if __name__ == "__main__":
    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    runner.metadata['description'] = "Producer/consumer queue between threads"
    runner.argparser.add_argument("benchmark", choices=sorted(BENCHMARKS))
    runner.argparser.add_argument("--threads", type=int, default=4)
    runner.argparser.add_argument("--batch-size", type=int, default=160)

    args = runner.parse_args()
    runner.metadata['threads'] = args.threads
    runner.bench_time_func(args.benchmark, BENCHMARKS[args.benchmark],
                           args.threads, args.batch_size)

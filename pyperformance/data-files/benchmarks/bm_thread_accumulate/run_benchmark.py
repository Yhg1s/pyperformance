"""
Score a batch of records across threads and collect the results.

Every append to a shared list costs a lock acquire/release and a mutation of
a list every other thread is also touching.

naive       each worker appends its results one at a time to a shared list,
            taking the lock once per record.
optimized   each worker fills a private list and splices it in once, taking
            the lock once per chunk.
"""

import pyperf

import threading
from concurrent.futures import ThreadPoolExecutor, wait


RECORDS = [("record-%04d" % i, i * 7919 % 1000) for i in range(9600)]


def score(name, weight):
    # Stands in for whatever real per-record work a pipeline does: enough
    # arithmetic and string handling that the sharing is not the only cost.
    total = 0
    for ch in name:
        total = (total * 31 + ord(ch)) & 0xFFFFFFFF
    for i in range(weight % 64 + 16):
        total = (total * 1103515245 + 12345) & 0xFFFFFFFF
    return total


def worker_naive(chunk, results, lock):
    for name, weight in chunk:
        value = score(name, weight)
        with lock:
            results.append(value)


def worker_optimized(chunk, results, lock):
    local = []
    for name, weight in chunk:
        local.append(score(name, weight))
    with lock:
        results.extend(local)


def make_chunks(records_per_chunk):
    return [RECORDS[i:i + records_per_chunk]
            for i in range(0, len(RECORDS), records_per_chunk)]


_expected = None


def expected_scores():
    """The multiset of scores a correct run produces, computed once."""
    global _expected
    if _expected is None:
        _expected = sorted(score(name, weight) for name, weight in RECORDS)
    return _expected


def check_results(results):
    """
    Verify the collected scores, outside the timed region. Compared as a
    sorted multiset: counting alone misses a lost append beside a duplicated
    one, and arrival order is nondeterministic.
    """
    if len(results) != len(RECORDS):
        raise AssertionError("lost results: %d of %d"
                             % (len(results), len(RECORDS)))
    if sorted(results) != expected_scores():
        raise AssertionError("scores do not match the sequential result")


def run(worker, loops, threads, records_per_chunk):
    chunks = make_chunks(records_per_chunk)
    range_it = range(loops)
    results = []
    with ThreadPoolExecutor(max_workers=threads) as pool:
        t0 = pyperf.perf_counter()
        for _ in range_it:
            results = []
            lock = threading.Lock()
            futures = [pool.submit(worker, chunk, results, lock)
                       for chunk in chunks]
            wait(futures)
            for future in futures:
                future.result()
        dt = pyperf.perf_counter() - t0
    return dt, results


def checked(worker):
    def wrapper(loops, threads, records_per_chunk):
        dt, results = run(worker, loops, threads, records_per_chunk)
        check_results(results)
        return dt
    return wrapper


BENCHMARKS = {
    'thread_accumulate_naive': checked(worker_naive),
    'thread_accumulate_optimized': checked(worker_optimized),
}


def add_cmdline_args(cmd, args):
    cmd.append(args.benchmark)
    cmd.extend(('--threads', str(args.threads)))
    cmd.extend(('--records-per-chunk', str(args.records_per_chunk)))


if __name__ == "__main__":
    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    runner.metadata['description'] = "Collect thread results into a list"
    runner.argparser.add_argument("benchmark", choices=sorted(BENCHMARKS))
    runner.argparser.add_argument("--threads", type=int, default=4)
    runner.argparser.add_argument("--records-per-chunk", type=int, default=200)

    args = runner.parse_args()
    runner.metadata['threads'] = args.threads
    runner.bench_time_func(args.benchmark, BENCHMARKS[args.benchmark],
                           args.threads, args.records_per_chunk)

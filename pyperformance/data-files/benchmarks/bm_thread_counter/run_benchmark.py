"""
Parse records across threads while tracking progress.

Incrementing one integer under one lock, once per item, puts a serialisation
point in the middle of the hot loop.

naive       a shared counter incremented under a lock once per record.
optimized   counted in a local, added to the shared total once per chunk.
"""

import pyperf

import threading
from concurrent.futures import ThreadPoolExecutor, wait


LINES = ["%d,%s,%d" % (i, "name-%03d" % (i % 250), i * 31 % 997)
         for i in range(24000)]


def parse(line):
    ident, name, weight = line.split(",")
    total = int(ident) + int(weight)
    for ch in name:
        total = (total * 131 + ord(ch)) & 0xFFFFFFFF
    return total


class Counter:
    def __init__(self):
        self.value = 0
        self.lock = threading.Lock()

    def bump(self, amount=1):
        with self.lock:
            self.value += amount


def worker_naive(chunk, counter):
    total = 0
    for line in chunk:
        total += parse(line)
        counter.bump()
    return total


def worker_optimized(chunk, counter):
    total = 0
    seen = 0
    for line in chunk:
        total += parse(line)
        seen += 1
    counter.bump(seen)
    return total


def make_chunks(lines_per_chunk):
    return [LINES[i:i + lines_per_chunk]
            for i in range(0, len(LINES), lines_per_chunk)]


_expected_total = None


def expected_total():
    """The sum a correct run produces, computed once."""
    global _expected_total
    if _expected_total is None:
        _expected_total = sum(parse(line) for line in LINES)
    return _expected_total


def check_counts(counted, total):
    """
    Verify the progress count and the parsed total, outside the timed region.
    The total matters too: a chunk done twice and another skipped keeps the
    count right and the sum wrong.
    """
    if counted != len(LINES):
        raise AssertionError("miscounted: %d of %d" % (counted, len(LINES)))
    if total != expected_total():
        raise AssertionError("parsed total %d, expected %d"
                             % (total, expected_total()))


def run(worker, loops, threads, lines_per_chunk):
    chunks = make_chunks(lines_per_chunk)
    range_it = range(loops)
    counter = Counter()
    total = 0
    with ThreadPoolExecutor(max_workers=threads) as pool:
        t0 = pyperf.perf_counter()
        for _ in range_it:
            counter = Counter()
            futures = [pool.submit(worker, chunk, counter)
                       for chunk in chunks]
            wait(futures)
            total = 0
            for future in futures:
                total += future.result()
        dt = pyperf.perf_counter() - t0
    return dt, counter.value, total


def checked(worker):
    def wrapper(loops, threads, lines_per_chunk):
        dt, counted, total = run(worker, loops, threads, lines_per_chunk)
        check_counts(counted, total)
        return dt
    return wrapper


BENCHMARKS = {
    'thread_counter_naive': checked(worker_naive),
    'thread_counter_optimized': checked(worker_optimized),
}


def add_cmdline_args(cmd, args):
    cmd.append(args.benchmark)
    cmd.extend(('--threads', str(args.threads)))
    cmd.extend(('--lines-per-chunk', str(args.lines_per_chunk)))


if __name__ == "__main__":
    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    runner.metadata['description'] = "Shared progress counter across threads"
    runner.argparser.add_argument("benchmark", choices=sorted(BENCHMARKS))
    runner.argparser.add_argument("--threads", type=int, default=4)
    runner.argparser.add_argument("--lines-per-chunk", type=int, default=480)

    args = runner.parse_args()
    runner.metadata['threads'] = args.threads
    runner.bench_time_func(args.benchmark, BENCHMARKS[args.benchmark],
                           args.threads, args.lines_per_chunk)

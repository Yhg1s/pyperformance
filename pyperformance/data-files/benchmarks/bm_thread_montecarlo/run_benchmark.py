"""
Estimate pi by sampling points in the unit square across threads.

naive       every worker draws from one shared random.Random, whose internal
            state is mutated on each draw.
optimized   every worker seeds its own random.Random. The streams differ, so
            the estimate differs run to run as any Monte Carlo does.
"""

import pyperf

import math
import random
from concurrent.futures import ThreadPoolExecutor, wait


SAMPLES_PER_CHUNK = 16000
CHUNKS = 12
SEED = 12345


def worker_shared_rng(count, rng):
    inside = 0
    for _ in range(count):
        x = rng.random()
        y = rng.random()
        if x * x + y * y <= 1.0:
            inside += 1
    return inside


def worker_own_rng(count, seed):
    rng = random.Random(seed)
    inside = 0
    random_ = rng.random
    for _ in range(count):
        x = random_()
        y = random_()
        if x * x + y * y <= 1.0:
            inside += 1
    return inside


def check_estimate(counts, chunks):
    """
    Verify the sample count and the estimate. Called once per benchmark call,
    never inside the timed region.

    Every chunk must report back, and each can only have landed between none
    and all of its samples inside the circle -- a lost future or a count
    corrupted by two threads updating it shows up as one of those. The
    estimate is then checked against pi with an eight-sigma band derived from
    the sample count.
    """
    if len(counts) != chunks:
        raise AssertionError("expected %d chunk results, got %d"
                             % (chunks, len(counts)))
    for i, inside in enumerate(counts):
        if not 0 <= inside <= SAMPLES_PER_CHUNK:
            raise AssertionError("chunk %d reported %d of %d samples inside"
                                 % (i, inside, SAMPLES_PER_CHUNK))
    samples = chunks * SAMPLES_PER_CHUNK
    estimate = 4.0 * sum(counts) / samples
    p = math.pi / 4.0
    sigma = 4.0 * math.sqrt(p * (1.0 - p) / samples)
    if abs(estimate - math.pi) > 8.0 * sigma:
        raise AssertionError("pi estimate %.4f is more than 8 sigma (%.4f) "
                             "from %.4f over %d samples"
                             % (estimate, 8.0 * sigma, math.pi, samples))


_expected_own = {}


def expected_own(chunks):
    """
    The exact count the per-thread-generator variant must produce.

    Each worker seeds its own generator deterministically, so the result is
    the same regardless of how the threads interleave. That allows an exact
    check, which catches a chunk silently computed twice where a pi estimate
    would not.
    """
    if chunks not in _expected_own:
        _expected_own[chunks] = [worker_own_rng(SAMPLES_PER_CHUNK, SEED + i)
                                 for i in range(chunks)]
    return _expected_own[chunks]


def run_shared(loops, threads, chunks):
    range_it = range(loops)
    counts = []
    with ThreadPoolExecutor(max_workers=threads) as pool:
        t0 = pyperf.perf_counter()
        for _ in range_it:
            rng = random.Random(SEED)
            futures = [pool.submit(worker_shared_rng, SAMPLES_PER_CHUNK, rng)
                       for _ in range(chunks)]
            wait(futures)
            counts = [f.result() for f in futures]
        dt = pyperf.perf_counter() - t0
    return dt, counts


def run_own(loops, threads, chunks):
    range_it = range(loops)
    counts = []
    with ThreadPoolExecutor(max_workers=threads) as pool:
        t0 = pyperf.perf_counter()
        for _ in range_it:
            futures = [pool.submit(worker_own_rng, SAMPLES_PER_CHUNK,
                                   SEED + i)
                       for i in range(chunks)]
            wait(futures)
            counts = [f.result() for f in futures]
        dt = pyperf.perf_counter() - t0
    return dt, counts


def bench_naive(loops, threads, chunks):
    # The shared generator is mutated by every thread, so which draws land in
    # which chunk is genuinely nondeterministic. Only the statistical check
    # applies.
    dt, counts = run_shared(loops, threads, chunks)
    check_estimate(counts, chunks)
    return dt


def bench_optimized(loops, threads, chunks):
    dt, counts = run_own(loops, threads, chunks)
    check_estimate(counts, chunks)
    if counts != expected_own(chunks):
        raise AssertionError("per-chunk counts do not match the sequential "
                             "result")
    return dt


BENCHMARKS = {
    'thread_montecarlo_naive': bench_naive,
    'thread_montecarlo_optimized': bench_optimized,
}


def add_cmdline_args(cmd, args):
    cmd.append(args.benchmark)
    cmd.extend(('--threads', str(args.threads)))
    cmd.extend(('--chunks', str(args.chunks)))


if __name__ == "__main__":
    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    runner.metadata['description'] = "Monte Carlo with shared vs per-thread RNG"
    runner.argparser.add_argument("benchmark", choices=sorted(BENCHMARKS))
    runner.argparser.add_argument("--threads", type=int, default=4)
    runner.argparser.add_argument("--chunks", type=int, default=CHUNKS)

    args = runner.parse_args()
    runner.metadata['threads'] = args.threads
    runner.bench_time_func(args.benchmark, BENCHMARKS[args.benchmark],
                           args.threads, args.chunks)

"""
Render the Mandelbrot set with a thread pool.

The escape-time calculation for a pixel depends on nothing but that pixel's
coordinates, so the work divides cleanly across threads. The inner loop is
complex arithmetic, and every value it produces is a fresh short-lived object
that no other thread can see.

naive       each worker reads the domain, the kernel and the output array out
            of module globals, and writes its rows into the shared array.
optimized   each worker takes what it needs as arguments, binds them to locals
            once, and fills a private row buffer that the caller stitches
            together afterwards.

The shared objects are read-mostly and the writes go to disjoint rows, so the
single-threaded difference between the two is the cost of global lookups and a
function call per pixel.
"""

import pyperf

from concurrent.futures import ThreadPoolExecutor, wait


NROWS = 160
NCOLS = 160
MAX_ITERATIONS = 300
XCENTER = -0.4601222
YCENTER = 0.570286
BOUND = 0.002


def linspace(start, stop, count):
    step = (stop - start) / (count - 1)
    return [start + i * step for i in range(count)]


X_DOMAIN = linspace(XCENTER - BOUND, XCENTER + BOUND, NCOLS)
Y_DOMAIN = linspace(YCENTER - BOUND, YCENTER + BOUND, NROWS)
ITERATION_ARRAY = [[0] * NCOLS for _ in range(NROWS)]

# Checksum of the fully rendered image, folded position-sensitively so that a
# row written to the wrong index does not pass. Regenerate with:
#   rows = [[mandelbrot(x, y) for x in X_DOMAIN] for y in Y_DOMAIN]
# then fold it the way check_image() does.
CHECKSUM = 0x2c91568f3af4


def mandelbrot(x, y):
    z = 0
    c = complex(x, y)
    for iteration_number in range(MAX_ITERATIONS):
        if abs(z) >= 2:
            return iteration_number
        z = z * z + c
    return 0


def worker_naive(rows):
    # Everything comes from a global: the domain, the kernel, the iteration
    # count inside it, and the array written to.
    for j, y in rows:
        for i, x in enumerate(X_DOMAIN):
            ITERATION_ARRAY[j][i] = mandelbrot(x, y)


def worker_optimized(rows, x_domain, max_iterations):
    # Nothing in the hot loop is looked up outside the frame, and the result
    # goes into a buffer only this call can see.
    out = []
    for j, y in rows:
        row = [0] * len(x_domain)
        for i, x in enumerate(x_domain):
            z = 0
            c = complex(x, y)
            value = 0
            for iteration_number in range(max_iterations):
                if abs(z) >= 2:
                    value = iteration_number
                    break
                z = z * z + c
            row[i] = value
        out.append((j, row))
    return out


def make_chunks(rows_per_chunk):
    numbered = list(enumerate(Y_DOMAIN))
    return [numbered[i:i + rows_per_chunk]
            for i in range(0, len(numbered), rows_per_chunk)]


def check_image(rows):
    """
    Verify a rendered image. Called once per benchmark call, never inside the
    timed region.

    A dropped chunk leaves a band of the array at its initial zero rather
    than raising, and a row handed back under the wrong index leaves every
    pixel present but in the wrong place. A plain sum would miss the second,
    so the fold below mixes the position in.
    """
    if len(rows) != NROWS:
        raise AssertionError("expected %d rows, got %d" % (NROWS, len(rows)))
    checksum = 0
    for j, row in enumerate(rows):
        if row is None:
            raise AssertionError("row %d was never written" % j)
        if len(row) != NCOLS:
            raise AssertionError("row %d has %d columns, expected %d"
                                 % (j, len(row), NCOLS))
        for value in row:
            checksum = (checksum * 31 + value) & 0xFFFFFFFFFFFF
    if checksum != CHECKSUM:
        raise AssertionError("image checksum 0x%012x, expected 0x%012x"
                             % (checksum, CHECKSUM))


def bench_naive(loops, threads, rows_per_chunk):
    chunks = make_chunks(rows_per_chunk)
    range_it = range(loops)
    # Clear the shared array before timing so that a chunk which is never
    # processed shows up as zeros rather than as a correct-looking leftover
    # from an earlier call in this process.
    for row in ITERATION_ARRAY:
        for i in range(NCOLS):
            row[i] = 0
    with ThreadPoolExecutor(max_workers=threads) as pool:
        t0 = pyperf.perf_counter()
        for _ in range_it:
            futures = [pool.submit(worker_naive, chunk) for chunk in chunks]
            wait(futures)
            for future in futures:
                future.result()
        dt = pyperf.perf_counter() - t0
    return dt, ITERATION_ARRAY


def bench_optimized(loops, threads, rows_per_chunk):
    chunks = make_chunks(rows_per_chunk)
    x_domain = X_DOMAIN
    max_iterations = MAX_ITERATIONS
    range_it = range(loops)
    result = [None] * NROWS
    with ThreadPoolExecutor(max_workers=threads) as pool:
        t0 = pyperf.perf_counter()
        for _ in range_it:
            result = [None] * NROWS
            futures = [pool.submit(worker_optimized, chunk, x_domain,
                                   max_iterations)
                       for chunk in chunks]
            wait(futures)
            for future in futures:
                for j, row in future.result():
                    result[j] = row
        dt = pyperf.perf_counter() - t0
    return dt, result


def checked(bench):
    def wrapper(loops, threads, rows_per_chunk):
        dt, rows = bench(loops, threads, rows_per_chunk)
        check_image(rows)
        return dt
    return wrapper


BENCHMARKS = {
    'thread_mandelbrot_naive': checked(bench_naive),
    'thread_mandelbrot_optimized': checked(bench_optimized),
}


def add_cmdline_args(cmd, args):
    cmd.append(args.benchmark)
    cmd.extend(('--threads', str(args.threads)))
    cmd.extend(('--rows-per-chunk', str(args.rows_per_chunk)))


if __name__ == "__main__":
    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    runner.metadata['description'] = "Mandelbrot set rendered by a thread pool"
    runner.argparser.add_argument("benchmark", choices=sorted(BENCHMARKS))
    runner.argparser.add_argument("--threads", type=int, default=4,
                                  help="worker threads (default: 4)")
    runner.argparser.add_argument("--rows-per-chunk", type=int, default=4,
                                  help="image rows per work item (default: 4)")

    args = runner.parse_args()
    runner.metadata['threads'] = args.threads
    bench = BENCHMARKS[args.benchmark]
    runner.bench_time_func(args.benchmark, bench, args.threads,
                           args.rows_per_chunk)

"""
Do-nothing benchmark for measuring baseline variance and overhead.

This benchmark measures an empty function to establish:
1. The minimum overhead of the pyperf benchmarking infrastructure
2. A baseline for variance detection (any variance here is pure measurement noise)

Any variance observed in this benchmark is entirely due to:
- Timer resolution
- pyperf overhead
- System noise (interrupts, scheduling, etc.)
- Python interpreter overhead for function calls
"""

import pyperf


def noop():
    """An empty function that does nothing."""
    pass


if __name__ == "__main__":
    runner = pyperf.Runner()
    runner.metadata["description"] = "Do-nothing benchmark for baseline variance measurement"
    runner.bench_func("noop", noop)

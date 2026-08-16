# pyperformance Benchmark Analysis

Analysis of pyperformance **1.14.0** (`bf9179b`) running on pyperf **2.10.0**.
Covers all 100 registered benchmarks (~140 pyperf result rows): what each one actually
measures, implementation quality, empirical noise, and whether the number is trustworthy.

---

## 1. Measurement framework (pyperf)

**Execution model.** pyperformance never calls pyperf in-process. Each benchmark is a separate
`python -u run_benchmark.py <extra_opts> <pyperf_opts> --output tmp.json` subprocess
(`pyperformance/_benchmark.py:209`), and the JSON is read back. pyperf itself then forks *worker*
processes; the manager only aggregates.

**Sampling.** Defaults (non-JIT): **20 processes × 3 values = 60 values**, 1 warmup per process,
`min_time` 100 ms.

| Mode | Processes | Values/proc | Total |
|---|---|---|---|
| default | 20 | 3 | 60 |
| `--fast` | 10 | 2 | 20 |
| `--rigorous` | 40 | 3 | 120 |
| `--debug-single-value` | 1 | 1 (0 warmups, loops=1) | 1 |

Multiple processes exist to average over `PYTHONHASHSEED` randomization — `--fast` deliberately
keeps ≥3 processes for that reason.

**Loop calibration.** The first worker doubles `loops` until one timed chunk ≥ `min_time`
(100 ms), capped at 2³². Later workers reuse that count. Reported value =
`raw_time / (loops × inner_loops)`. A benchmark declaring `inner_loops=N` (manual unrolling) gets
per-operation numbers; one that forgets to declare it reports inflated per-loop times.

**Statistics.** Mean, sample stdev, median, MAD, percentiles. The displayed `mean +- std dev` is
the **stdev of individual values, not the standard error of the mean** — it is not a confidence
interval on the reported mean. `pyperf compare_to` uses a Student's two-sample two-tailed t-test
at α=0.95, optionally gated by `--min-speed`.

**Isolation.** `pyperf system tune` can set the CPU governor to performance, disable turbo boost,
disable ASLR, and adjust IRQ affinity / perf_event limits. `--affinity=CPU_LIST` pins workers.
pyperformance additionally scrubs the environment down to `HOME`+`PATH` unless you pass
`--inherit-environ`.

**Known limitations.** (a) The t-test assumes equal-length samples and normality — neither holds
under preemption spikes. (b) Warmup calibration exists but is **off by default**; you get exactly
1 warmup. (c) Nothing detects non-stationary workloads. (d) `loops` calibration assumes per-loop
cost is constant — one benchmark in the suite violates this (see `sqlite_synth`).

---

## 2. pyperformance structure

Discovery is **100% manifest-driven** — a `bm_*/` directory not listed in
`pyperformance/data-files/benchmarks/MANIFEST` is invisible. Each entry maps a name to a
`pyproject.toml` (`<local>`) or a variant TOML (`<local:BASE>`). Metadata is PEP 621 `[project]`
plus `[tool.pyperformance]` (`tags`, `extra_opts`, `runscript`, `datadir`, `inherits`).

- **100 benchmarks**, 8 selectable groups. The shipped `[group …]` sections are all *empty*, so
  membership derives entirely from each benchmark's `tags`:
  `apps` 7, `asyncio` 21, `math` 3, `regex` 4, `serialize` 12, `startup` 3, `template` 3,
  **untagged 47**, plus `all`/`default` = 100.
- **Dependencies:** `[project].dependencies` is parsed but **never installed**. Only
  `bm_NAME/requirements.txt` (the pinned lockfile) is. They diverge in practice
  (`bm_xdsl` declares `0.46.0`, pins `0.54.3`).
- **Version gating is silent:** benchmarks whose `requires-python` excludes the running
  interpreter are dropped with no message.
- **5 in-tree benchmarks are unregistered** and therefore dead: `hg_startup` (commented out),
  `barnes_hut`, `yaml`, `decimal_pi`, `decimal_factorial`.
- **Manifest name ≠ pyperf result name** for 17 entries. Joining the two naively will mismatch:

  `async_tree`→`async_tree_none` · `argparse`→`many_optionals` · `argparse_subparsers`→`subparsers` ·
  `networkx`→`shortest_path` · `networkx_k_core`→`k_core` · `networkx_connected_components`→`connected_components` ·
  `fastapi`→`fastapi_http` · `gc_collect`→`create_gc_cycles` · `xdsl`→`xdsl_constant_fold` ·
  `concurrent_imap`→`bench_mp_pool`+`bench_thread_pool` · `deepcopy`→3 rows · `pprint`→2 ·
  `genshi`→2 · `base64`→11 · `logging`→3 · `scimark`→5 · `sympy`→4 · `xml_etree`→4 · `sqlglot_v2*`→prefixed

---

## 3. Noise measurements

**Setup:** AMD EPYC-Genoa VM, 96 cores, CPython 3.14.2+, pyperformance `--fast`, **5 independent
repetitions**, interleaved. **Shared dev box** (23 users, load 5–22, no `system tune`, no
`isolcpus`) — co-tenant preemption is the dominant noise source.

**Coverage:** 72 of 100 benchmarks → 95 result rows. 26 skipped (no PyPI access on the box),
`2to3` failed (needs setuptools to install its vendored lib2to3), `sqlite_synth` failed (no
`_sqlite3` in that build).

**Headline results:**

- Median run-to-run CV of the reported mean: **1.23%**. Grades: A(<1%) 35, B(1–2%) 32, C(2–5%) 20, D(>5%) 8.
- **Most noise is outlier contamination, not variance.** Switching mean → per-run minimum drops
  median CV to **0.61%** and rescues the worst rows: `logging_format` 37.4% → 0.57%,
  `logging_simple` 13.3% → 0.68%. Raw evidence: one `logging_format` rep had a 4.8 µs floor with
  seven preemption spikes up to 35 µs; the other four reps had none.
- **`--affinity` is free and fixes most of it.** Control run (18 benchmarks, 5 reps,
  `--affinity=90`): median CV 2.24% → 1.37%, throughput cost 0.98× (none).
- **Timer resolution is a non-issue.** `perf_counter` back-to-back delta 68.9 ns; smallest timed
  chunk in the suite 97.6 ms → worst-case relative timer error ~7×10⁻⁷.
- **No benchmark is "too fast to measure."** Only two rows are sub-microsecond
  (`unpack_sequence` 32.3 ns, `logging_silent` 73.0 ns) and both amortize over `inner_loops`;
  harness scaffolding is 0.07% and 1.29% of the reported value respectively.
- **One warmup is enough for 84/95 rows.** Exceptions: `asyncio_tcp` (1.90× — needs a second
  warmup for connection setup), `bench_mp_pool` 1.38×, four `async_tree_*_tg` rows 1.12–1.21×.
  `btree_gc_only` (0.86×) and `generators` (0.93×) warm up **backwards** — the workload is
  non-stationary, so more warmups make them worse.
- **pyperf's own error bar understates reality for 5 rows:** `logging_format` (10.8× too small),
  `logging_simple` (5.4×), `asyncio_tcp_ssl` (2.4×), `xml_etree_generate` (1.8×), `mdp` (1.6×).

*Stats caveat: n=5, so each CV carries ~±35% relative uncertainty. Differences below ~2× between
two CV values are not meaningful.*

---

## 4. Per-benchmark analysis

**Trust** = combined implementation quality + measured noise.
🟢 use as-is · 🟡 usable with caveats · 🔴 do not trust without fixing.
CV% = run-to-run CV of the mean; `n/a` = not measured (no PyPI on the test box).

### apps (7) — no noise data for any of them

| Benchmark | Measures | CV% | Trust | Concerns |
|---|---|---|---|---|
| `2to3` | Whole subprocess: exec + import lib2to3 + compile ~50 fixer patterns + refactor 64 KB | fail | 🔴 | Startup/import dominates the refactoring. **stdlib lib2to3 on ≤3.12 vs a pip-installed vendored copy on 3.13+** — compares different code across the version boundary |
| `chameleon` | Generated+exec'd template code; 5000 cells of str building | n/a | 🟢 | Template compiled outside the loop. Allocation/GC-heavy |
| `docutils` | reST→HTML5 over 49 files / 1.3 MB | n/a | 🟢 | File I/O explicitly excluded from the timing window (good). `contextlib.suppress` silently swallows parse failures → a regression could look like a speedup |
| `fastapi` | Client **and** server in one process over loopback, 150 concurrent reqs | n/a | 🔴 | GIL contention between httpx and uvicorn is the measurement; client-side `response.json()` counted as "fastapi"; busy-spin `while not server.started: pass`; TOCTOU port race; meaning changes entirely on free-threaded builds |
| `html5lib` | Pure-Python HTML5 parse of a 132 KB doc | n/a | 🟢 | Textbook setup (slurped to `BytesIO`, only `seek(0)` in the loop). Large share is tree allocation + teardown |
| `sphinx` | Full Sphinx build, 23 files, `open`/`os.replace` monkeypatched to memory | n/a | 🔴 | **`preloaded_files` accumulates every file Sphinx writes and is never cleared** → iteration 1 ≠ iteration N. Enormous scope; a regression is unattributable |
| `tornado_http` | Server+client on one IOLoop, ~9 MB/iteration over loopback | n/a | 🟡 | Mostly loopback syscalls, not Python. Uses the deprecated `@gen.coroutine` API, not async/await |

### asyncio (21)

| Benchmark | Measures | CV% | Trust | Concerns |
|---|---|---|---|---|
| `coroutines` | 242 k coroutine create/await/StopIteration — **no event loop at all** | 1.11 B | 🟢 | Cleanest in the group; mistagged as asyncio |
| `async_generators` | ~1.6 M `__anext__` hops through ~17 delegation levels | 0.54 A | 🟡 | **Builds a 100 k-node tree inside the timed coroutine** — ~half the measurement is construction |
| `asyncio_tcp` | 1 GB/iteration over loopback (docstring says 10 MB) | 6.06 D | 🔴 | Server+client setup inside the timing; **hardcoded port 8882**; needs 2 warmups (1.90×); noisy even on per-run minimum |
| `asyncio_tcp_ssl` | Same + TLS → effectively an OpenSSL/AES-NI throughput test | 5.95 D | 🔴 | Cert files read from disk *inside* the timed region; pyperf's error bar 2.4× too small |
| `asyncio_websockets` | 100 MB/iteration through websockets 11.0.3; dominated by frame masking | n/a | 🔴 | **Hardcoded port 8001**; close-vs-`stop.set()` race can hang; its internal timing is dead code (return value discarded) |
| `async_tree` (none) | 55,987 coroutines/iteration, pure asyncio machinery | 1.52 B | 🟢 | The only clean variants are `none`/`eager` |
| `async_tree_eager` | + eager task factory | 1.06 B | 🟢 | `set_task_factory` re-applied every iteration inside the timing |
| `async_tree_io` / `_memoization` / `_cpu_io_mixed` | Same tree + `asyncio.sleep(0.05)` at the leaves | 2.19 / 3.22 / 2.12 C | 🟡 | **Fixed ~50 ms sleep floor no interpreter change can shrink** → compressed dynamic range. `cpu_io_mixed`'s "CPU work" is `math.factorial(500)`, i.e. C bignum |
| `_memoization`, `_cpu_io_mixed` (state) | — | — | 🔴 | **`random.seed(0)` runs once at construction and `self.cache` is never cleared** → every iteration sees a different RNG stream and a warmer cache. The "reproducible" comment is false |
| 8 × `_eager_*`, 8 × `_*_tg` | eager-task and TaskGroup flavours | 0.64–5.43 | 🟡 | 16 highly correlated variants = 16% of the suite; four `_tg` rows need >1 warmup; `_eager_cpu_io_mixed_tg` is grade D (5.43%) from outliers only |

### math (3) — all three are C-bound, in different ways

| Benchmark | Measures | CV% | Trust | Concerns |
|---|---|---|---|---|
| `float` | 100 k object allocations + `__slots__` get/set + 300 k libm calls | 1.51 B | 🟡 | Not a float benchmark. **GC-dominated** (100 k tracked objects per call); a wasted `points[1:]` 100 k-list copy in the timed region |
| `nbody` | 200 k inner iterations of float arithmetic | 1.46 B | 🟢 | `** -1.5` = 200 k **libm `pow()`** calls/loop → moves with glibc, not CPython. Global `SYSTEM` integrates cumulatively across all iterations |
| `pidigits` | CPython's C `long_mul`/`long_divrem` (thousands of bits) | 0.34 A | 🟢 | Quietest row in the suite, but it measures `longobject.c`, not the interpreter |

### regex (4)

| Benchmark | Measures | CV% | Trust | Concerns |
|---|---|---|---|---|
| `regex_compile` | `sre_parse`+`sre_compile` cold, forced by `re.purge()` per pattern | 0.67 A | 🟡 | `re.purge()` (hundreds of times/loop) folds cache teardown + Pattern deallocation into "compile time". Known outlier amplifier — historically the largest-swinging row in the suite |
| `regex_dna` | 9 `findall` + 12 `sub` over 1 MB of bytes | 0.88 A | 🟢 | ~Half is bulk bytes copying, not matching. Setup and verification correctly outside the timer |
| `regex_effbot` | 1,470 `re.search` calls over 147 (pattern, string) pairs | 1.09 B | 🟡 | Calls module-level `re.search` with pre-compiled patterns → measures `_compile` dispatch too. 10× unrolled identical calls = optimistically warm |
| `regex_v8` | ~1,157 regex ops from 2009 web pages, ROT13-encoded | 3.12 C | 🟡 | Tight `range(N)` repeats of identical calls (N up to 6511) → very JIT/IC-friendly, under-represents cold paths. Noise is pure outlier (CVmin 0.15%) |

### serialize (12)

| Benchmark | Measures | CV% | Trust | Concerns |
|---|---|---|---|---|
| `json_dumps` | 4 cases summed into one number | 1.28 B | 🟡 | The **2000 × `dumps({})`** case is pure call dispatch; a regression in the HUGE case can be masked by it |
| `json_loads` | 20× unrolled `loads` on 3 small docs | 0.59 A | 🟢 | No large-document case; warm-cache unrolling caveat |
| `pickle`, `pickle_dict`, `pickle_list`, `unpickle`, `unpickle_list`, `pickle_pure_python`, `unpickle_pure_python` | C and pure-Python pickle paths | 0.54–3.27 | 🟢 | **Best-engineered family in the suite:** setup hoisted, `inner_loops` declared, protocol in metadata, and the pure-Python variants *assert* the accelerator is absent. Caveat: default protocol is `HIGHEST_PROTOCOL`, which changes with the Python version |
| `base64` (11 rows) | `_small` = call overhead, `_large` = C `binascii` throughput | 0.66–2.71 | 🟢 | The only benchmark that **deliberately separates the overhead regime from the throughput regime**. Summing the 11 rows conflates them |
| `tomli_loads` | Pure-Python TOML scan of a large file | n/a | 🟢 | Genuinely good pure-interpreter signal. Uses pinned `tomli`, so it will **not** track stdlib `tomllib` changes |
| `xml_etree_parse` | 30 `etree.parse` + 30 `fromstring` per iteration | 0.98 A | 🟡 | **Re-opens a temp file 30× per iteration** (I/O in the hot path) + 2 verification `tostring` calls inside the timing |
| `xml_etree_iterparse` | 10 file re-opens + 2 × 10,200-tuple event lists | 2.02 C | 🟡 | Full list comparison inside the timed region |
| `xml_etree_generate` / `_process` | Tree building / round-trip | 3.58 C / 4.52 C | 🟡 | Verification (`b'>LEAF<' in xml`) inside the timing. pyperf's error bar 1.8× too small on `generate` |

### startup (3) — all `bench_command`, so all include process spawn

| Benchmark | Measures | CV% | Trust | Concerns |
|---|---|---|---|---|
| `python_startup` | fork+exec+ld.so relocation+`Py_Initialize`+`site` | 3.66 C | 🟡 | Large, variable share is the **dynamic linker**, not CPython — shared vs static libpython moves this 20–30%. Runs inside the venv, so `.pth` files change the workload silently |
| `python_startup_no_site` | Same with `-S` | 2.10 C | 🟡 | Only meaningful as a **difference** against `python_startup`, which isolates `site.py` |
| `stdlib_startup` | Imports ~301 stdlib modules in a subprocess | 1.80 B | 🔴 | **The workload is defined by the interpreter under test** (`sys.stdlib_module_names`) and ImportErrors are swallowed — a build missing `_ssl`/`_sqlite3` silently becomes *faster*. Invalid for cross-version **and** cross-configure comparison |

### template (3) — no noise data

| Benchmark | Measures | CV% | Trust | Concerns |
|---|---|---|---|---|
| `django_template` | 100×100 cells through Django's engine | n/a | 🟡 | **`--table-size` never reaches workers** (`prepare_cmd` is dead code — `Runner()` built without `add_cmdline_args`). Docstring and docs claim 150×150; code does 100×100. Django 3.2.4 only imports thanks to a `legacy-cgi` shim |
| `genshi` | 10,000 cells via Genshi's event-stream pipeline | n/a | 🟢 | Emits **two** rows (`genshi_xml`, `genshi_text`) from one manifest entry |
| `mako` | 22,500 cells ×2 + escaping + 3-level inheritance | n/a | 🟢 | Best of the three. Deliberately disables markupsafe (`sys.modules['markupsafe'] = None`) to force the pure-Python path — good for measuring Python, unrepresentative of production Mako |

### untagged (47)

| Benchmark | Measures | CV% | Trust | Concerns |
|---|---|---|---|---|
| `argparse` → `many_optionals` | 1,000 `add_argument` calls + 2 parses | 0.65 A | 🟡 | **Construction dominates**, but the name/docstring promise parse cost |
| `argparse_subparsers` → `subparsers` | Parser tree build + 5 tiny parses | 0.43 A | 🟡 | Same |
| `bpe_tokeniser` | ~90% BPE *training*, deliberately quadratic encode | 0.50 A | 🟢 | Good pure-Python signal; misleadingly named |
| `btree` | 200 k-node B-tree build + gc + traversal + lookups | 5.03 D | 🔴 | **Irreducibly noisy** (CVmin 2.4%, unaffected by pinning) — depends on GC heap layout |
| `btree_gc_only` | Only the `gc.collect()` over that tree | 5.99 D | 🔴 | **Least trustworthy row in the suite.** CVmin 6.0%, pinning makes it *worse* (6.11%), warms up backwards (0.86×) — non-stationary workload |
| `chaos` | 5,000 B-spline evaluations via `GVector` dunder dispatch | 1.19 B | 🟢 | Re-seeds RNG per call (truly deterministic); params in metadata **and** propagated to workers. Read as "dunder dispatch", not float math |
| `comprehensions` | 24 widgets through ~6 comprehensions | 1.24 B | 🟡 | Too small (tens of µs) to isolate comprehensions — mostly call overhead, dataclass attribute loads, `Enum.__eq__` |
| `concurrent_imap` → `bench_mp_pool` | `Pool(2)` **constructed inside the timed function** | 2.46 C | 🔴 | Measures fork/exec + teardown, not communication. CVmin 5.2%; pinning makes it worse (3.73%); needs 2 warmups |
| → `bench_thread_pool` | `ThreadPool(2)` likewise | 3.97 C | 🔴 | Irreducible (CVmin 4.2%). Pinning changes *what it measures* (0.64× = faster serialised) |
| `coverage` | 1.2 M `sys.settrace` callbacks under coverage 7.3.2's C tracer | n/a | 🟡 | **`cov.stop()` is inside the timing window.** Tracing suppresses adaptive specialisation → structurally blind to JIT/specialisation work |
| `crypto_pyaes` | Pure-Python AES-CTR, 23 KB encrypt+decrypt | n/a | 🟢 | Good interpreter benchmark; verification after the timer |
| `dask` | **Stands up a Scheduler+Worker+Client cluster inside the timed coroutine** for 1,100 `x+1` tasks | n/a | 🔴 | Setup-dominated; real TCP, background threads, ephemeral ports. Integration smoke test, not a measurement |
| `deepcopy` / `_reduce` / `_memo` | Three distinct `copy.deepcopy` code paths | 0.73/0.45/0.65 A | 🟢 | Cleanest multi-variant design. 120 timer calls per `n` in the base variant |
| `deltablue` | Constraint solver — OO method/attribute dispatch | 1.12 B | 🟢 | Module-global `planner`, reset per call |
| `dulwich_log` | Git object walk: C zlib inflate + pure-Python parsing | n/a | 🟡 | Real file I/O in the hot loop; `head` referenced as a global while `repo` is a parameter |
| `fannkuch` | 362,880 permutations; list slicing + int ops | 2.81 C | 🟢 | **Structurally one of the best in the suite** — no allocation, no I/O, no GC. Noise is pure outlier (CVmin 0.41%) |
| `gc_collect` → `create_gc_cycles` | A `gc.collect()` after creating 2,100 garbage nodes | 0.64 A | 🟡 | The collect traverses the **entire process heap**; 2,100 nodes are a rounding error, so it tracks baseline heap size. Calibration skew (untimed setup ≫ timed part) |
| `gc_traversal` | Full gen-2 traversal over ~499,500 live references | 0.52 A | 🟢 | Focused and correct — better than `gc_collect`. Same calibration skew (real cost ≈ 2× reported) |
| `generators` | ~1.6 M `yield from` resumptions | 1.15 B | 🟢 | Tree built **outside** t0 — the correct sibling of the buggy `async_generators`. Warms up backwards (0.93×) |
| `go` | 9×9 MCTS, 200 playouts | 0.73 A | 🟢 | Re-seeds per call; deterministic |
| `hexiom` | Board solver, level 25 | 0.71 A | 🟢 | `StringIO` accumulation inside the timed loop (minor); answer check after the timer |
| `logging_silent` | Disabled `debug()` → `isEnabledFor` + call dispatch | 0.47 A | 🟢 | At 73 ns ≈ 3.3 empty function calls — will move on codegen changes unrelated to logging |
| `logging_simple` | LogRecord + format + handler + StringIO write | 13.26 D | 🟡 | **Not actually unstable** — CVmin 0.68%. Pinning: 13.26% → 0.85%. pyperf's error bar 5.4× too small |
| `logging_format` | Same with `%s` interpolation | 37.37 D | 🟡 | Same story: CVmin 0.57%, pinned 1.57%. **Worst mean-CV in the suite and entirely an artefact of co-tenant preemption.** Error bar 10.8× too small |
| `mdp` | Graph topological sort + float compare | 6.26 D | 🟡 | CVmin 1.09%; pinned 1.30%. Error bar 1.6× too small. Result verified after the timer |
| `meteor_contest` | Bit-mask puzzle search | 0.77 A | 🟢 | All precomputation hoisted; verified after the timer |
| `networkx` → `shortest_path` | BFS over a 262 k-node SNAP graph | n/a | 🟢 | Graph loaded once at import (correct). Multi-hundred-MB live graph taxes every GC pass |
| → `connected_components`, `k_core` | Same graph | n/a | 🟢 | Result names lack a `networkx_` prefix — confusing in tables |
| `nqueens` | 40,320 permutations × 2 `set()` builds | 1.38 B | 🟡 | Dominated by `set()` + genexp overhead, not N-Queens logic |
| `pathlib` | ~5,000 `stat()` syscalls + ~5,000 Path objects per iteration | 2.05 C | 🟡 | **A syscall benchmark.** Dominated by kernel/filesystem; 2× machine-to-machine variance for non-Python reasons. In-tree comment admits the warm-up block isn't understood |
| `pprint_pformat` | 100 k identical tuples → giant string | 0.62 A | 🟢 | Mostly string building; perfectly warm caches (all elements identical) |
| `pprint_safe_repr` | Private `PrettyPrinter._safe_repr` | 1.23 B | 🟡 | `hasattr`-guarded: if CPython renames it, the row **silently disappears** from comparisons instead of failing |
| `pyflate` | Pure-Python bzip2 decompress | 1.35 B | 🟢 | Checksum after the timer. Re-reads the file each iteration inside the loop |
| `raytrace` | 10,000 pixels × 9 objects | 0.71 A | 🟢 | Scene rebuilt inside the timed loop (minor). `--width/--height` correctly propagated |
| `richards` | OS task-scheduler simulation | 0.73 A | 🟢 | A wrong result returns `False` rather than raising → a broken build yields a plausible fast number |
| `richards_super` | Same via `super()` | 1.08 B | 🟢 | The **pair** isolates `super()`/MRO cost — one of the few deliberate A/B designs. Files verified otherwise identical |
| `scimark_*` (5 rows) | SciMark kernels in pure Python | 0.42–1.69 | 🟢 | Self-contained LCG (not `random`) → fully deterministic. Inconsistent setup placement: `_sor` allocates its matrix inside the loop, `_lu` does not |
| `spectral_norm` | ~338 k int+float ops in nested loops | 1.10 B | 🟢 | Avoids libm (plain `/`, not `pow`). No result verification at all |
| `sqlalchemy_declarative`, `sqlalchemy_imperative` | 200 commits + 100 full selects per iteration | n/a | 🔴 | **Module-level `session` shared across iterations**; `synchronize_session=False` doesn't expunge, so the identity map grows → iterations differ. Pinned to SQLAlchemy 1.4 `metadata.bind`, removed in 2.0 |
| `sqlglot_v2*` (4 rows) | SQL parse/transpile/optimize/normalize | n/a | 🟡 | `_optimize` and `_transpile` **include parsing in the timed region** → not independent of `_parse`. `_normalize` correctly excludes it |
| `sqlite_synth` | `connect` + N inserts + select + aggregate + `close`, all timed | fail | 🔴 | **The INSERT loop is `range(loops)`** while setup/teardown run once → per-loop cost is not constant, breaking pyperf's linearity assumption. Worst structural defect found |
| `sympy_*` (4 rows) | Symbolic algebra; allocation/GC/hashing-heavy | n/a | 🟢 | **`clear_cache()` explicitly excluded from the timing** — best handling of a library-cache confound anywhere in the suite. Consequence: every iteration runs cold, so it is hostile to warmup-dependent optimisations by design |
| `telco` | 5,000 records of decimal arithmetic | 0.98 A | 🟡 | With the default C `_decimal` this measures **libmpdec**, and the benchmark neither detects nor records that. `print(t, file=outfil)` per record puts `Decimal.__str__`+StringIO in the hot loop. `if datum == '':` compares bytes to str — dead code |
| `typing_runtime_protocols` | 65 `isinstance` calls against runtime-checkable Protocols | 0.61 A | 🟢 | Well-targeted regression test for a real CPython hot spot |
| `unpack_sequence` | 400 unrolled 10-element unpackings | 1.35 B | 🟢 | `inner_loops=400` correctly declared; scaffolding is 0.07% of the value. Read as *peak straight-line* throughput |
| `xdsl` → `xdsl_constant_fold` | Constant folding over a 1,000-op IR module | n/a | 🟢 | Clones the IR so each iteration is identical (correct); the clone is inside the timing. `pyproject.toml` declares a stale version vs the lockfile |

---

## 5. Summary

### Trust ratings

| Trust | Count | Benchmarks |
|---|---|---|
| 🔴 **Do not trust** | 14 | `sqlite_synth`, `stdlib_startup`, `2to3`, `fastapi`, `sphinx`, `dask`, `concurrent_imap` (×2), `sqlalchemy` (×2), `btree`, `btree_gc_only`, `asyncio_tcp`, `asyncio_tcp_ssl`, `asyncio_websockets` |
| 🟡 **Caveats** | ~35 | see per-benchmark table |
| 🟢 **Use as-is** | ~51 | see per-benchmark table |

### Noise grades (95 measured rows)

| Grade | CV of mean | Count | Notes |
|---|---|---|---|
| A | <1% | 35 | |
| B | 1–2% | 32 | |
| C | 2–5% | 20 | Mostly outlier-driven; pinning fixes most |
| D | >5% | 8 | `logging_format`, `logging_simple`, `mdp`, `asyncio_tcp`, `btree_gc_only`, `asyncio_tcp_ssl`, `async_tree_eager_cpu_io_mixed_tg`, `btree` |

**Irreducibly unreliable** (still noisy on the per-run minimum *and* when pinned):
`btree_gc_only`, `btree`, `bench_mp_pool`, `bench_thread_pool`, `asyncio_tcp`.
Everything else in grade D is rescued by `--affinity` or by using median/min.

### Defect classes

| Class | Benchmarks |
|---|---|
| Workload varies across iterations | `sphinx`, `async_tree_{memoization,cpu_io_mixed}` (×8), `sqlalchemy` (×2), `nbody`, `btree_gc_only` |
| Workload varies across builds/versions | `stdlib_startup`, `2to3`, `telco`, `xml_etree`, pickle protocol |
| Setup inside the timing loop | `async_generators`, `concurrent_imap` (×2), `dask`, `sqlite_synth`, `asyncio_tcp*`, `asyncio_websockets`, `argparse` (×2), `raytrace`, `scimark_sor`, `chaos`, `coverage` |
| I/O in the hot path | `xml_etree_parse`, `xml_etree_iterparse`, `pathlib`, `dulwich_log`, `pyflate`, `asyncio_tcp_ssl` |
| Verification inside the timing window | `xml_etree` (×4), `fastapi`, `tornado_http`, `asyncio_tcp`, `comprehensions` |
| Calibration skew (manual inner timing) | `gc_collect`, `gc_traversal`, `btree_gc_only`, `docutils`, `sphinx`, `deepcopy`, `sympy`, `sqlglot_v2*`, `sqlalchemy*` |
| Measures C, not Python | `pidigits` (longobject.c), `telco` (libmpdec), `asyncio_tcp_ssl` (OpenSSL), `base64_large`/`base16` (binascii), `nbody` (libm `pow`), `xml_etree` (C accelerator), `dulwich_log` (zlib), `json_*`/`pickle` non-pure variants |
| Hardcoded ports / flaky | `asyncio_tcp` (8882), `asyncio_websockets` (8001 + hang race), `fastapi` (TOCTOU) |
| Broken plumbing | `django_template` (`--table-size` never reaches workers), `pickle` (`is_accelerated_module` ignores its argument), `pprint_safe_repr` (silent disappearance), `richards` (returns `False` instead of raising) |

---

## 6. Recommendations

### For anyone *running* pyperformance

1. **Always pass `--affinity=<cpus>`.** It is free (0.98× throughput) and cut median CV from
   2.24% → 1.37% in the control experiment; it turned `logging_format` from 37% → 1.6%.
   Do **not** pin the multi-process/multi-thread benchmarks (`concurrent_imap`, `dask`,
   `fastapi`) and then compare against unpinned runs — pinning changes what they measure.
2. **Do not trust one `--fast` run to better than ~3%**, or better than ~10–15% for grade-D rows.
   For those, use `--rigorous` or repeat the whole run and compare *across* runs.
3. **Ignore pyperf's printed `+- std dev` as a confidence interval.** It is the stdev of
   individual values and understates real run-to-run error by up to 10× on the spike-prone rows.
4. **Prefer median or minimum** for spike-prone benchmarks. The real fix is a quiet machine:
   `pyperf system tune` + `isolcpus`.
5. **Exclude the irreducible five** (`btree`, `btree_gc_only`, `bench_mp_pool`,
   `bench_thread_pool`, `asyncio_tcp`) from any pass/fail gate — treat them as advisory.
6. **Pass `--inherit-environ=VAR1,VAR2`** when benchmarking an interpreter driven by env vars;
   the default environment is scrubbed to `HOME`+`PATH`.

### For anyone *interpreting* results

7. **A geometric mean over `default` is dominated by asyncio** — 21 of 100 benchmarks, 16 of them
   correlated `async_tree` variants, several with a fixed 50 ms sleep floor. Weight by category
   or drop the redundant variants.
8. **Never compare `stdlib_startup` or `2to3` across Python versions or `./configure` options** —
   the workload itself changes.
9. **Specialisation/JIT work will show ~nothing** on `coverage` (tracing suppresses
   specialisation), `sympy` (cache cleared every iteration → always cold), or `regex_compile`
   (short-lived, allocation-heavy, hostile to warmup).
10. **Join on result names, not manifest names** — 17 entries differ (§2).

### Fixes worth upstreaming, in priority order

11. `sqlite_synth`: move the INSERT count off `loops` onto a constant, and take `t0` after
    `connect`/`CREATE TABLE`. Currently its per-loop cost is not constant.
12. `sphinx`: clear `preloaded_files` (or re-run `read_all_files()`) at the top of each iteration.
13. `async_tree`: move `random.seed()` and `self.cache = {}` into `run()` so iterations are
    identical, as `btree`/`chaos`/`go` already do.
14. `asyncio_tcp` / `asyncio_websockets`: bind port 0 and read back the assigned port.
15. `django_template`: pass `add_cmdline_args=prepare_cmd` to `Runner()`, and fix the
    150×150 vs 100×100 docs discrepancy.
16. `coverage`: move `cov.stop()` after the second `perf_counter()`.
17. `async_generators`: hoist `tree(range(100000))` out of the timed coroutine (its sibling
    `generators` already does this correctly).
18. `xml_etree_parse`/`_iterparse`: read the file into a `BytesIO` once, as `html5lib` does; move
    verification out of the timed region.
19. `sqlalchemy_*`: create a fresh `Session` per iteration.
20. Register or delete the 5 dead benchmark directories (`barnes_hut`, `yaml`, `decimal_pi`,
    `decimal_factorial`, `hg_startup`).

### Reference examples of correct construction

`pickle` (accelerator asserted, `inner_loops` declared, setup hoisted) ·
`html5lib` (all I/O hoisted to `BytesIO`) ·
`sympy` (library cache explicitly excluded from timing) ·
`btree`/`chaos`/`go` (RNG re-seeded every iteration) ·
`fannkuch`/`spectral_norm`/`meteor_contest` (no allocation, no I/O, verification after the timer) ·
`base64` (deliberately separates overhead from throughput) ·
`docutils` (file I/O explicitly outside the timing window) ·
`generators` (setup outside `t0`) ·
`xdsl` (clones state so every iteration starts identical).

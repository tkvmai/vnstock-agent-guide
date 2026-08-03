"""
Run vnstock tool calls in a single, long-lived worker process with a hard
timeout that can actually kill the worker -- without ever losing the "warm"
in-process state that real-data calls apparently depend on.

Why not just spawn a fresh subprocess per call (the first version of this
module did that): on Windows, multiprocessing always uses the 'spawn' start
method, so every new Process is a brand-new interpreter that re-imports
vnstock/vnstock_data from scratch. Testing showed real-data tools (e.g.
get_foreign_trade, get_money_flow, get_industry_list) are slow/hang on the
*first* call made against a freshly-started interpreter, but return
instantly on later calls in a long-lived process that already exercised
them once (this matches how the same tools behave fine through a
long-running Claude Code MCP session). A fresh subprocess per call makes
every single call a "first call" -- i.e. the per-call isolation approach
turns the occasional slow cold-start into a permanent, every-call timeout.

So instead: keep ONE worker process alive for the life of the server (warm
state persists across calls, same as a long-running MCP session), but talk
to it over multiprocessing Queues so a stuck call can be abandoned without
blocking the caller, and a truly wedged worker can be killed and replaced
without taking down the main FastMCP process. The crash this replaced
(AssertionError: "Request already responded to") happened because a
ThreadPoolExecutor-based timeout let an abandoned thread finish later and
try to deliver a second response to an already-completed MCP request. Here,
a late/abandoned result is just discarded by the watchdog -- it never
reaches FastMCP's dispatch layer a second time.
"""
import itertools
import multiprocessing as mp
import queue
import sys
import threading
import time


def _log(msg):
    """Diagnostics go to stderr — stdout is the MCP JSON-RPC channel."""
    print(f"[vnstock-mcp] {msg}", file=sys.stderr, flush=True)


# Per-call timeouts. Normally server.py waits for the worker to warm before it
# serves any request (see wait_until_warm), so calls hit a warm worker and use
# the short _TIMEOUT. _COLD_TIMEOUT is a safety net for the rare case a call
# lands while the worker is still cold (e.g. warm-up exceeded the startup wait
# bound): such a call BLOCKS for real data instead of failing at the short
# budget. It relaxes back to _TIMEOUT once the worker signals it is warm.
_TIMEOUT = 25
_COLD_TIMEOUT = 90

_lock = threading.Lock()
_worker = None
_inq = None
_outq = None
_warm_event = None      # mp.Event the worker sets once it is warm & serving
_warmup = None          # (func, args, kwargs) the worker runs at startup
_call_counter = itertools.count()

# Result routing. Under HTTP transport a single server is shared by every
# Claude session on the machine, so several tool calls can be in flight at
# once (FastMCP already runs sync tools in a threadpool). One reader thread
# owns _outq and hands each result to the caller that asked for it, keyed by
# call_id. The previous design had every caller read _outq directly and drop
# anything that wasn't its own call_id — with concurrent callers that silently
# discards other callers' results and hangs them until timeout.
_pending_lock = threading.Lock()
_pending = {}           # call_id -> queue.SimpleQueue for that call's result
_generation = 0         # bumped whenever the worker (and its queues) is replaced


def _worker_loop(inq, outq, warmup=None, warm_event=None):
    # Pre-warm INSIDE the worker, before it serves the queue. vnstock's first
    # real fetch in a freshly-spawned interpreter is slow (~25s) but leaves the
    # process "warm" so every later call returns in ~1s. Running the warm-up
    # here — rather than pushing it onto `inq` from the parent — is deliberate:
    # on Windows, a parent-side inq.put() issued while the spawned child is
    # still bootstrapping wedges the child so it never reaches this loop.
    _log("worker process up; running pre-warm" if warmup else "worker process up")
    if warmup is not None:
        func, args, kwargs = warmup
        t0 = time.time()
        name = getattr(func, "__name__", func)
        try:
            func(*(args or ()), **(kwargs or {}))
            _log(f"pre-warm: worker warm after {time.time() - t0:.1f}s (via {name})")
        except Exception as e:
            _log(f"pre-warm: {name} raised {type(e).__name__}: {e} after "
                 f"{time.time() - t0:.1f}s; worker is up regardless")
    # Signal the parent that cold-start is over: subsequent calls can use the
    # normal (short) per-call timeout instead of the generous cold budget.
    if warm_event is not None:
        warm_event.set()
    while True:
        call_id, func, args, kwargs = inq.get()
        try:
            outq.put((call_id, "ok", func(*args, **kwargs)))
        except Exception as e:
            outq.put((call_id, "error", f"{type(e).__name__}: {e}"))


def _reader_loop(outq, generation):
    """Drain `outq` and deliver each result to the caller waiting on that
    call_id. Exits once its worker generation has been replaced.

    Results whose caller already gave up (timed out) find no pending slot and
    are simply dropped — the same "a late result never reaches FastMCP twice"
    guarantee the old inline discard gave us, but without stealing results
    belonging to other in-flight callers.
    """
    while True:
        with _lock:
            if generation != _generation:
                return
        try:
            call_id, status, value = outq.get(timeout=1.0)
        except queue.Empty:
            continue
        except (EOFError, OSError):
            return
        with _pending_lock:
            slot = _pending.pop(call_id, None)
        if slot is not None:
            slot.put((status, value))


def _await_result(slot, call_id, timeout):
    """Block until this call's result is delivered. Returns (status, value),
    or None on timeout (deregistering the slot so a late result is dropped)."""
    try:
        return slot.get(timeout=timeout)
    except queue.Empty:
        with _pending_lock:
            _pending.pop(call_id, None)
        return None


def _ensure_worker_locked():
    global _worker, _inq, _outq, _warm_event, _generation
    if _worker is not None and _worker.is_alive():
        return
    _inq = mp.Queue()
    _outq = mp.Queue()
    _warm_event = mp.Event()
    _generation += 1
    # Callers waiting on the dead worker will never be answered — fail them
    # now rather than making each one sit out its full timeout.
    with _pending_lock:
        orphaned = list(_pending.values())
        _pending.clear()
    for slot in orphaned:
        slot.put(("error", "worker process died before returning a result"))
    _worker = mp.Process(target=_worker_loop,
                         args=(_inq, _outq, _warmup, _warm_event), daemon=True)
    _worker.start()
    threading.Thread(target=_reader_loop, args=(_outq, _generation),
                     daemon=True).start()


def start_worker(warmup=None):
    """Spawn the persistent worker eagerly, before FastMCP's event loop starts.

    Call this from server.py's `if __name__ == "__main__":` block, before
    mcp.run(). Two reasons it can't just happen lazily on the first tool call
    or eagerly at module-import time:

    - Lazily, on first use: that first multiprocessing.Process().start() ends
      up running from inside FastMCP's asyncio dispatch (a worker thread
      under Windows' Proactor event loop). That combination appeared to
      deadlock completely -- not even our own graceful timeout message made
      it back to the caller.
    - Eagerly at module-import time (i.e. as a top-level statement in this
      file): on Windows, spawning a child re-imports the original entry
      script (server.py) as __mp_main__ to rebuild parent state for
      unpickling. That re-import hits this same top-level statement again,
      and multiprocessing's own bootstrap-safety check refuses with
      "attempt to start a new process before the current process has
      finished its bootstrapping phase" -- it looks like unbounded
      recursion to it.

    Doing it here -- in the main thread, after imports finish, but before
    mcp.run() creates any event loop -- avoids both.

    PRE-WARM (`warmup`): pass a (func, args, kwargs) tuple and the spawned
    worker runs it once as its very first action -- before it starts serving
    the queue (see _worker_loop) -- so vnstock's slow first-fetch-in-a-fresh-
    interpreter cost (~25s) is paid at server startup rather than on the
    client's first tool call. Without it, the first client call lands while the
    worker is still cold and blows the per-call 25s budget, even though the
    tools 'self-heal' once warm.

    The warm-up is baked into the worker process rather than queued from here
    on purpose: a parent-side inq.put() during the child's spawn bootstrap
    wedges the worker on Windows so it never starts _worker_loop at all. This
    call returns immediately; the worker warms in the background while mcp.run()
    serves the initialize handshake.
    """
    global _warmup
    with _lock:
        _warmup = warmup
        _ensure_worker_locked()


def wait_until_warm(timeout):
    """Block (up to `timeout` seconds) until the worker has finished its
    cold-start pre-warm. Returns True if it warmed in time.

    Call this in server.py AFTER start_worker() and BEFORE mcp.run(). On
    Windows, FastMCP's asyncio event loop running in the parent while the
    freshly-spawned worker does its first numpy/native-extension import slows
    that import catastrophically (~15s → 2-3 MINUTES, measured). Letting the
    worker warm while the parent is still idle keeps cold-start to ~15s; the
    bounded wait means a genuinely stuck worker can't hang server startup
    forever — mcp.run() proceeds anyway and the adaptive per-call timeout
    (cold budget) covers any residual warm-up.
    """
    with _lock:
        ev = _warm_event
    return bool(ev is not None and ev.wait(timeout))


def run_with_timeout(func, args=(), kwargs=None, timeout=None):
    kwargs = kwargs or {}

    with _lock:
        _ensure_worker_locked()
        call_id = next(_call_counter)
        slot = queue.SimpleQueue()
        # Register before dispatching, so the reader thread can never deliver
        # this result before we are listening for it.
        with _pending_lock:
            _pending[call_id] = slot
        _inq.put((call_id, func, args, kwargs))
        warm_event = _warm_event

    # Give calls that land during the worker's (slow, variable) cold-start a
    # generous budget so they return real data instead of failing; snap back to
    # the normal budget once the worker has signalled it is warm.
    if timeout is None:
        warm = warm_event is not None and warm_event.is_set()
        timeout = _TIMEOUT if warm else _COLD_TIMEOUT

    result = _await_result(slot, call_id, timeout)
    if result is None:
        return (
            f"Error: {func.__name__} timed out after {timeout}s — "
            "the underlying data source may be slow on first use. Please try again."
        )
    status, value = result
    if status == "error":
        return f"Error: {func.__name__} raised an exception: {value}"
    return value

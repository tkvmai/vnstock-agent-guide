"""Launch the Breakout Screener: start the background scheduler, then serve the
Panel dashboard.

    python breakout_app/run.py            # serve on http://localhost:5006
    python breakout_app/run.py --port 5010
"""

import argparse
import atexit
import datetime
import faulthandler
import os
import signal
import sys
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import panel as pn

import scheduler
from data import db

# ── Exit forensics (13/07: process was silently exiting mid-session twice) ────────
# Every abnormal path is logged to data/exit_trace.log so a silent shutdown leaves
# evidence: interpreter exit (atexit), uncaught main/thread exceptions, signals,
# and hard faults (faulthandler → data/fault.log).
_TRACE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "exit_trace.log")


def _trace(msg: str):
    try:
        with open(_TRACE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except Exception:
        pass


def _install_forensics():
    os.makedirs(os.path.dirname(_TRACE), exist_ok=True)
    faulthandler.enable(open(os.path.join(os.path.dirname(_TRACE), "fault.log"), "a"))
    _trace(f"startup pid={os.getpid()} argv={sys.argv}")
    atexit.register(lambda: _trace("atexit: interpreter shutting down"))
    def _main_hook(t, v, tb):
        _trace("MAIN EXCEPTION:\n" + "".join(traceback.format_exception(t, v, tb)))
        sys.__excepthook__(t, v, tb)
    sys.excepthook = _main_hook
    def _thread_hook(args):
        _trace(f"THREAD EXCEPTION ({args.thread.name if args.thread else '?'}):\n"
               + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
    threading.excepthook = _thread_hook
    for sig_name in ("SIGINT", "SIGBREAK", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        prev = signal.getsignal(sig)
        def _handler(signum, frame, _prev=prev, _name=sig_name):
            _trace(f"signal {_name} received")
            if callable(_prev):
                _prev(signum, frame)
            else:
                sys.exit(1)
        try:
            signal.signal(sig, _handler)
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Breakout Screener dashboard")
    parser.add_argument("--port", type=int, default=5006)
    parser.add_argument("--no-show", action="store_true", help="don't open a browser")
    parser.add_argument("--no-scheduler", action="store_true",
                        help="serve UI only, without the background scan loop")
    args = parser.parse_args()

    _install_forensics()
    db.init_db()
    if not args.no_scheduler:
        scheduler.start_scheduler()

    # Import here so app.py's pn.extension runs before serving.
    from app import template

    try:
        pn.serve(template, port=args.port, show=not args.no_show,
                 title="Breakout Screener", autoreload=False)
        _trace("pn.serve RETURNED normally — Tornado loop stopped without exception")
    except BaseException as e:
        _trace(f"pn.serve raised {type(e).__name__}: {e}\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()

"""
rpresolve.overlay_fresh: an optional freshness check for the $RPRESOLVE_CONFIG overlay.

An overlay is derived from records the toolkit cannot see. When
$RPRESOLVE_OVERLAY_CHECK holds a command, queue_render runs it (split with
shlex, no shell, 30 s) before it queues anything. Exit 0 means the overlay is
fresh; 1 means stale; anything else, a command that will not start or a
timeout means it could not be verified. Both refuse. With the variable unset
this is a no-op. Stdlib only.
"""

import os
import shlex
import subprocess

from .workflows import Refused

ENV = "RPRESOLVE_OVERLAY_CHECK"


def refuse_if_stale(env=None):
    cmd = (env if env is not None else os.environ).get(ENV, "").strip()
    if not cmd:
        return
    try:
        argv = shlex.split(cmd)
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (ValueError, OSError, subprocess.TimeoutExpired) as e:
        raise Refused(f"the config overlay could not be verified ({ENV} could not run: {type(e).__name__}); "
                      "nothing is queued.")
    if p.returncode == 0:
        return
    note = (p.stderr or p.stdout).strip().splitlines()
    why = f" It said: {note[-1][:200]}" if note else ""
    state = "is stale" if p.returncode == 1 else f"could not be verified (exit {p.returncode})"
    raise Refused(f"the config overlay {state}, so nothing is queued. Rebuild it with the tool "
                  f"that wrote it, then run this again.{why}")

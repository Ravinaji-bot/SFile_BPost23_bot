"""RAM guard for small hosts (e.g. a 512 MB container).

Why the bot's memory climbs while it posts for days
  * every poster / cover is a 1280x720 .. 2560x1440 RGB image, and building one needs several
    copies at once (10-60 MB). Python / glibc keep those freed pages instead of giving them
    back to the OS, so the process size (RSS) only ever goes up;
  * small in-memory caches that grow with every search.

What this module does
  1. tune_malloc()      - at start-up: tells glibc to return big buffers to the OS at once
  2. memory_cleaner()   - background task, every RAM_CLEAN_INTERVAL seconds:
                            gc + malloc_trim, drops idle locks / finished timers;
                            above 70 % of RAM_LIMIT_MB it also drops the search caches;
                            above 90 % for 2 checks in a row (and the bot is > 30 min old) it
                            restarts itself *before* the host kills it. /autopost, /ccopy and
                            /postrange continue by themselves after a restart.
  3. cleanup() / rss_mb() are used by the owner command /ram.

Environment variables (all optional)
  RAM_LIMIT_MB=512         container limit
  RAM_CLEAN_INTERVAL=120   seconds between two cleanups
  RAM_AUTO_RESTART=True    restart when RAM stays above 90 % of the limit
"""
import asyncio
import ctypes
import gc
import logging
import os
import sys
import time

logger = logging.getLogger("ram")
STARTED_AT = time.time()


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


RAM_LIMIT_MB = _env_float("RAM_LIMIT_MB", 512)
SOFT_MB = RAM_LIMIT_MB * 0.70     # drop caches above this
HARD_MB = RAM_LIMIT_MB * 0.90     # restart above this (if it stays there)
MIN_UPTIME_FOR_RESTART = 30 * 60  # never restart-loop: at most one restart per 30 minutes

_libc = None


def _libc_handle():
    global _libc
    if _libc is None:
        try:
            _libc = ctypes.CDLL("libc.so.6")
        except OSError:
            _libc = False
    return _libc or None


def tune_malloc():
    """Call once, as early as possible. Linux / glibc only, silently does nothing elsewhere."""
    libc = _libc_handle()
    if not libc:
        return False
    try:
        libc.mallopt(-8, 2)            # M_ARENA_MAX: fewer per-thread heaps
        libc.mallopt(-1, 256 * 1024)   # M_TRIM_THRESHOLD: give free memory back sooner
        libc.mallopt(-3, 256 * 1024)   # M_MMAP_THRESHOLD: big buffers (images) are returned on free()
        return True
    except Exception:
        return False


def trim():
    libc = _libc_handle()
    if libc:
        try:
            libc.malloc_trim(0)
        except Exception:
            pass


def rss_mb():
    """Resident memory of this process in MB (0.0 if it can not be read)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


def _drop_idle_state():
    """Cheap, always safe: forget idle per-title locks and finished timers of the auto-post."""
    channel = sys.modules.get("plugins.channel")      # never import here, only use what is already loaded
    if channel is None:
        return
    try:
        # a lock that somebody is still waiting for must stay (no two posts for one title at once)
        for k in [k for k, lock in channel.locks.items() if not lock.locked() and not getattr(lock, "_waiters", None)]:
            channel.locks.pop(k, None)
        now = asyncio.get_running_loop().time()
        for k in [k for k, h in channel.pending_updates.items() if h.cancelled() or h.when() <= now]:
            channel.pending_updates.pop(k, None)
    except Exception:
        logger.debug("idle state cleanup skipped", exc_info=True)


def _drop_caches():
    """Used when RAM is high: everything dropped here is rebuilt on demand."""
    try:
        ia = sys.modules.get("database.ia_filterdb")
        if ia is not None:
            ia.compile_regex.cache_clear()
        ut = sys.modules.get("utils")
        if ut is not None:
            ut.temp.GETALL.clear()
    except Exception:
        logger.debug("cache drop skipped", exc_info=True)
    try:
        import re
        import linecache
        re.purge()
        linecache.clearcache()
    except Exception:
        pass


def cleanup(deep=False):
    """Returns (rss_before_mb, rss_after_mb)."""
    before = rss_mb()
    try:
        _drop_idle_state()
    except RuntimeError:       # no running loop (called from a thread)
        pass
    if deep:
        _drop_caches()
    gc.collect()
    trim()
    return before, rss_mb()


async def memory_cleaner(restart_cb=None):
    interval = max(30.0, _env_float("RAM_CLEAN_INTERVAL", 120))
    auto_restart = os.environ.get("RAM_AUTO_RESTART", "True").strip().lower() in ("1", "true", "yes", "on")
    if not rss_mb():
        logger.warning("RAM guard: /proc is not available, only gc will be run")
    logger.info("RAM guard on: limit %.0f MB, clean at %.0f MB, restart above %.0f MB (every %.0fs)",
                RAM_LIMIT_MB, SOFT_MB, HARD_MB, interval)
    high_hits = 0
    cycle = 0
    while True:
        try:
            await asyncio.sleep(interval)
            cycle += 1
            deep = rss_mb() >= SOFT_MB
            before, after = cleanup(deep)
            if deep:
                logger.warning("RAM guard: %.0f MB -> %.0f MB after a deep clean", before, after)
            elif cycle % 15 == 0:
                logger.info("RAM guard: %.0f MB -> %.0f MB", before, after)

            if after >= HARD_MB:
                high_hits += 1
            else:
                high_hits = 0
            if (high_hits >= 2 and auto_restart and restart_cb is not None
                    and time.time() - STARTED_AT > MIN_UPTIME_FOR_RESTART):
                logger.error("RAM guard: %.0f MB is still above %.0f MB, restarting the bot", after, HARD_MB)
                await restart_cb()
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("RAM guard iteration failed")

"""Offline unit checks for ProxyPool rotation/cooldown and AntiBlock wiring.

Run: python tests/test_proxy_pool.py   (no network; monkeypatches _fetch/_check)
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.anti_block import AntiBlock
from core.config import Settings
from core.proxy_pool import ProxyPool, _normalize, _parse_sources, build_pool_from_settings

PASS = 0
FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


print("normalize:")
check("bare ip:port", _normalize("1.2.3.4:8080", "http") == "http://1.2.3.4:8080")
check("http scheme kept", _normalize("http://5.6.7.8:3128", "http") == "http://5.6.7.8:3128")
check("https downgraded to scheme", _normalize("https://5.6.7.8:3128", "http") == "http://5.6.7.8:3128")
check("socks rejected", _normalize("socks5://1.2.3.4:1080", "http") is None)
check("comment rejected", _normalize("# comment", "http") is None)
check("garbage rejected", _normalize("not-a-proxy", "http") is None)
check("trailing slash trimmed", _normalize("1.2.3.4:80/", "http") == "http://1.2.3.4:80")

print("parse_sources:")
check("newline+comma split", _parse_sources("a\nb, c") == ["a", "b", "c"])
check("empty -> []", _parse_sources("") == [])

print("refresh (mocked fetch, dedupe):")
pool = ProxyPool(sources=["src1", "src2"])
pool._fetch = lambda url: {  # type: ignore[assignment]
    "src1": ["1.1.1.1:80", "2.2.2.2:80", "# c", "junk"],
    "src2": ["2.2.2.2:80", "3.3.3.3:80"],
}[url]
n = pool.refresh()
check("dedupe across sources -> 3", n == 3)
check("size == 3", pool.size == 3)

print("rotation (round-robin):")
seq = [pool.get() for _ in range(4)]
check("cycles back after 3", seq[0] == seq[3])
check("all three distinct in first cycle", len(set(seq[:3])) == 3)

print("mark_bad cooldown + drop:")
p = pool.get()
pool.mark_bad(p)  # fail 1 -> cooldown
check("still in pool after 1 fail", pool.size == 3)
check("available drops to 2", pool.available == 2)
check("cooled proxy not returned", all(pool.get() != p for _ in range(6)))
pool.mark_bad(p)  # 2
pool.mark_bad(p)  # 3 -> drop (max_fails default 3)
check("dropped after max_fails", pool.size == 2)

print("get() on empty pool -> None:")
empty = ProxyPool(sources=[])
check("empty returns None", empty.get() is None)

print("cooldown expiry returns proxy again:")
short = ProxyPool(sources=["s"], cooldown=0.2)
short._fetch = lambda url: ["9.9.9.9:80"]  # type: ignore[assignment]
short.refresh()
q = short.get()
short.mark_bad(q)
check("unavailable during cooldown", short.get() is None)
time.sleep(0.25)
check("available after cooldown", short.get() == q)

print("AntiBlock rotates + reports failure via thread-local:")
s = Settings(proxy_enabled=True, proxy_rotate=True)
ab_pool = ProxyPool(sources=["s"])
ab_pool._fetch = lambda url: ["4.4.4.4:80", "5.5.5.5:80"]  # type: ignore[assignment]
ab_pool.refresh()
ab = AntiBlock(s, proxy_pool=ab_pool)
first = ab.proxy()
check("AntiBlock.proxy returns a pool proxy", first in ("http://4.4.4.4:80", "http://5.5.5.5:80"))
ab.report_proxy_failure()  # should cool down `first`
check("reported proxy cooled (available now 1)", ab_pool.available == 1)
check("next proxy differs from cooled one", ab.proxy() != first)

print("mark_bad drop keeps round-robin cursor (no skipped entry):")
rr = ProxyPool(sources=["s"], max_fails=1)
rr._fetch = lambda url: ["1.1.1.1:80", "2.2.2.2:80", "3.3.3.3:80"]  # type: ignore[assignment]
rr.refresh()
a = rr.get()  # A, cursor -> 1
b = rr.get()  # B, cursor -> 2
rr.mark_bad(a)  # max_fails=1 -> drop A at index 0, cursor should follow to 1
c = rr.get()
check("entry after dropped one is not skipped", c not in (a, b))
check("size is 2 after drop", rr.size == 2)

print("threaded get()+mark_bad() stress (no crash / no IndexError):")
import threading as _threading

stress = ProxyPool(sources=["s"], cooldown=0.01, max_fails=2)
stress._fetch = lambda url: [f"10.0.0.{i}:80" for i in range(1, 51)]  # type: ignore[assignment]
stress.refresh()
_errors: list = []


def _hammer() -> None:
    try:
        for _ in range(2000):
            p = stress.get()
            if p:
                stress.mark_bad(p)
    except Exception as exc:  # noqa: BLE001
        _errors.append(exc)


_workers = [_threading.Thread(target=_hammer) for _ in range(8)]
for _w in _workers:
    _w.start()
for _w in _workers:
    _w.join()
check("no exception under concurrent get/mark_bad", not _errors)
check("size stayed within bounds", 0 <= stress.size <= 50)

print("AntiBlock single-proxy fallback when pool empty/exhausted:")
s3 = Settings(proxy_enabled=True, proxy_rotate=True, proxy="http://8.8.8.8:1")
empty_pool = ProxyPool(sources=["s"])
empty_pool._fetch = lambda url: []  # type: ignore[assignment]
empty_pool.refresh()
ab3 = AntiBlock(s3, proxy_pool=empty_pool)
check("empty pool falls back to single proxy", ab3.proxy() == "http://8.8.8.8:1")
ab3.report_proxy_failure()  # thread-local must be None -> no-op, must not raise
check("fallback proxy not marked in pool", True)

print("AntiBlock single-proxy fallback (no pool):")
s2 = Settings(proxy_enabled=True, proxy="http://7.7.7.7:9")
ab2 = AntiBlock(s2, proxy_pool=None)
check("single proxy returned", ab2.proxy() == "http://7.7.7.7:9")
ab2.report_proxy_failure()  # no pool -> no-op, must not raise
check("report is a no-op without pool", True)

print("build_pool_from_settings disabled -> None:")
check("off returns None", build_pool_from_settings(Settings(proxy_enabled=False)) is None)
check("enabled-but-not-rotate returns None",
      build_pool_from_settings(Settings(proxy_enabled=True, proxy_rotate=False)) is None)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

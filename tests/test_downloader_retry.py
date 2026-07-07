"""Offline checks for Downloader._is_retryable IP-flag gating (no network).

An IP-flag / bot-challenge is retryable ONLY when proxy rotation is active, so
retrying rotates to a fresh IP. Without a pool it stays fatal.

Run: python tests/test_downloader_retry.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.anti_block import AntiBlock, ErrorClass
from core.config import Settings
from core.downloader import Downloader, _is_ip_flag
from core.proxy_pool import ProxyPool

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


BOT = "ERROR: [youtube] xxx: Sign in to confirm you're not a bot."
BOT_CURLY = "ERROR: [youtube] xxx: Sign in to confirm you’re not a bot."

print("_is_ip_flag:")
check("straight apostrophe bot msg", _is_ip_flag(BOT))
check("curly apostrophe bot msg", _is_ip_flag(BOT_CURLY))
check("unusual traffic", _is_ip_flag("Our systems have detected unusual traffic"))
check("captcha", _is_ip_flag("Please solve the CAPTCHA"))
check("plain private is not an ip-flag", not _is_ip_flag("This video is private"))

print("_is_retryable gating:")
d_nopool = Downloader(Settings(), AntiBlock(Settings(), proxy_pool=None))
check("no pool: bot-challenge stays fatal",
      d_nopool._is_retryable(ErrorClass.AUTH, BOT) is False)
check("no pool: retryable stays retryable",
      d_nopool._is_retryable(ErrorClass.RETRYABLE, "429 too many") is True)

pool = ProxyPool(sources=["s"])
pool._fetch = lambda u: ["1.1.1.1:80"]  # type: ignore[assignment]
pool.refresh()
d_pool = Downloader(Settings(), AntiBlock(Settings(), proxy_pool=pool))
check("with pool: bot-challenge becomes retryable",
      d_pool._is_retryable(ErrorClass.AUTH, BOT) is True)
check("with pool: genuine private-auth stays fatal",
      d_pool._is_retryable(ErrorClass.AUTH, "This video is private, login required") is False)
check("with pool: fatal stays fatal",
      d_pool._is_retryable(ErrorClass.FATAL, "Unsupported URL") is False)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

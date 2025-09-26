"""
Small utilities:
- Simple in-memory rate limiter
- PII redaction (basic regex masking)
"""

import time
import re
from typing import Tuple


class RateLimiter:
    def __init__(self, enabled: bool, reqs_per_min: int):
        self.enabled = enabled
        self.reqs = reqs_per_min
        self.bucket = {}
        self.window = 60.0

    def allow(self, key: str) -> Tuple[bool, float]:
        if not self.enabled:
            return True, 0
        now = time.time()
        window_start = int(now // self.window) * self.window
        k = (key, window_start)
        count = self.bucket.get(k, 0)
        if count >= self.reqs:
            # TTL: seconds left in current window
            return False, self.window - (now - window_start)
        self.bucket[k] = count + 1
        return True, 0.0


# Very simple PII masking (emails, phones). For demo only.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\b\+?\d[\d\-\s]{7,}\d\b")


def maybe_redact_pii(text: str) -> str:
    if not text:
        return text
    text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = PHONE_RE.sub("[REDACTED_PHONE]", text)
    return text

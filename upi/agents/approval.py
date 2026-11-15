"""I25：本地 UI 审批码。"""
from __future__ import annotations

import hashlib
import hmac


def approval_code(secret: str, action: str, target: str) -> str:
    raw = f"{action}|{target}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()[:4]


def verify(secret: str, action: str, target: str, code: str) -> bool:
    return hmac.compare_digest(approval_code(secret, action, target),
                               str(code or ""))

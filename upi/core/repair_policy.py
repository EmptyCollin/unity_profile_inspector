"""I25：生产修复的 blacklist 查询/提议。考试禁止调用。"""
from __future__ import annotations

from upi.core.finding_blacklist import (BlacklistFingerprint,
                                        BlacklistStore)


def signature_of(finding: dict) -> str:
    sig = finding.get("signature")
    if isinstance(sig, dict) and sig.get("mechanism"):
        return f"{sig.get('mechanism')}:{sig.get('metric') or ''}"
    sig = str(sig or "")
    if sig:
        return sig
    mech = str(finding.get("mechanism") or "")
    # 机制级：hot_function:ChromaGame.Update -> hot_function
    base = mech.split(":", 1)[0]
    return f"{base}:{finding.get('metric') or finding.get('root_metric') or ''}"


def fingerprint_of(finding: dict, *, window: str, metric: str,
                   protocol_fingerprint: str,
                   project_scope: str = "") -> BlacklistFingerprint:
    return BlacklistFingerprint(
        signature=signature_of(finding), window=window, metric=metric,
        protocol_fingerprint=protocol_fingerprint,
        project_scope=project_scope)


def should_skip(store: BlacklistStore, finding: dict, *, window: str,
                metric: str, protocol_fingerprint: str,
                project_scope: str = ""):
    return store.lookup(fingerprint_of(
        finding, window=window, metric=metric,
        protocol_fingerprint=protocol_fingerprint,
        project_scope=project_scope))


def propose_on_failure(store: BlacklistStore, finding: dict, *, window: str,
                       metric: str, protocol_fingerprint: str,
                       reason: str, attempts: int,
                       project_scope: str = "") -> str:
    fp = fingerprint_of(finding, window=window, metric=metric,
                        protocol_fingerprint=protocol_fingerprint,
                        project_scope=project_scope)
    store.upsert(fp, status="proposed", reason=reason,
                 attempts=attempts, owner="system")
    return fp.fingerprint_id()


def blacklist_failure_callback(store: BlacklistStore, finding: dict, *,
                               window: str, metric: str,
                               protocol_fingerprint: str,
                               project_scope: str = "",
                               reason: str = "root_failed"):
    """给 run_repair_loop(on_failure=...) 用的生产回调。"""
    def cb(tree) -> str:
        return propose_on_failure(
            store, finding, window=window, metric=metric,
            protocol_fingerprint=protocol_fingerprint,
            reason=reason, attempts=int(getattr(tree, "finding_failures", 0)),
            project_scope=project_scope)
    return cb

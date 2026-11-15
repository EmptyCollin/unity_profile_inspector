"""UPI 本地 Web 控制台（I0 骨架）。

为什么是本地 Web，而不是继续用 ANSI TUI：
- 产品目标是“UI 是唯一人机通道”。TUI 能表达的状态有限，工单/证据/回归矩阵
  一旦复杂起来就退化成“让人读 JSON”；Web 控制台更适合卡片、矩阵、时间线。
- 零 Node 构建：Python 标准库 `ThreadingHTTPServer` + 服务端 HTML + SSE。
  不引入 FastAPI/React，不依赖 CDN，断网可用；与仓库“可审计、可离线”的
  取向一致。后续页面复杂到标准库扛不住时，再评估 FastAPI（见 ADR-0002）。

I0 只交付骨架：状态 API、SSE 事件流、首页壳、导航占位。真正的工单/回归/
指纹库页面在 I3/I4/I5 接入；本文件不假装它们已经可用。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.request
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse

from upi.core import events as E
from upi.core.probe_events import EventLog

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _age_s(path: Path) -> Optional[float]:
    try:
        return round(time.time() - path.stat().st_mtime, 1)
    except OSError:
        return None


def _probe_status() -> dict:
    hb = E.RUNS / "_probe" / "pb_hb.txt"
    age = _age_s(hb)
    return {
        "heartbeat_age_s": age,
        "state": "unknown" if age is None else ("stale" if age > 12 else "alive"),
        "heartbeat": str(hb),
    }


def _project_status() -> dict:
    try:
        from upi.config.project_registry import ProjectRegistry
        reg = ProjectRegistry()
        if reg.active_id() is None:
            return {"id": None, "name": "（未选择工程）", "path": ""}
        from upi.config.project_profile import active_profile

        p = active_profile()
        return {
            "id": p.profile_id,
            "name": p.display_name or p.profile_id,
            "path": str(p.abs_project),
            "subjects": list(p.subjects),
        }
    except Exception as e:  # noqa: BLE001 - 状态页不得因档案坏而 500
        return {"id": None, "name": "（档案不可用）", "error": str(e)[:180]}


def _projects_payload() -> dict:
    try:
        from upi.config.project_registry import ProjectRegistry
        reg = ProjectRegistry()
        return {
            "active": reg.active_id(),
            "recent": [e.project_id for e in reg.recent()],
            "entries": [e.model_dump(mode="json") for e in reg.entries(include_archived=True)],
        }
    except Exception as e:  # noqa: BLE001
        return {"active": None, "recent": [], "entries": [],
                "error": f"{type(e).__name__}: {e}"}


def _build_candidate_ticket_latest():
    """从最近一次 I8 证据候选建工单；不读 case/oracle。"""
    from upi.config.project_profile import active_profile
    from upi.core.candidates_v2 import generate_candidates
    from upi.core.evidence_graph import EvidenceGraph
    from upi.core.evidence_v2 import EvidenceBundleV2, clean_game_evidence
    from upi.core.ticket import TicketStore, build_candidate_ticket

    prof = active_profile()
    files = sorted(E.RUNS.glob("*/evidence/evidence_v2.json"),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        raise ValueError("没有证据包；先跑 I8 采集")
    bundle = clean_game_evidence(
        EvidenceBundleV2(**json.loads(files[-1].read_text(encoding="utf-8"))))
    graph = EvidenceGraph.from_bundle(bundle, prof.abs_project)
    candidates = generate_candidates(bundle, graph, project_root=prof.abs_project,
                                     limit=8)
    if not candidates:
        raise ValueError("证据包里没有可建单的候选")
    t = build_candidate_ticket(candidates, prof.abs_project,
                               project_id=prof.profile_id)
    store = TicketStore(os.environ.get("UPI_TICKET_DIR") or None)
    store.save(t)
    return t


def _project_switch_blocked() -> Optional[str]:
    """运行中禁止切换/归档工程，避免把 A 的数据写到 B。"""
    try:
        running, _ = E.daemon_running()
        if running:
            return "守护进程运行中"
    except Exception:  # noqa: BLE001
        pass
    try:
        from upi.core.scheduler import get_scheduler
        if get_scheduler().status().get("running"):
            return "后台巡检运行中"
    except Exception:  # noqa: BLE001
        pass
    return None


def _daemon_status() -> dict:
    try:
        running, dst = E.daemon_running()
    except Exception as e:  # noqa: BLE001
        return {"running": False, "error": str(e)[:180]}
    st = E.read_status() or {}
    mode = st.get("sampling_mode") or ("steady" if st.get("sampling_enabled") else "off")
    return {
        "running": bool(running),
        "pid": (dst or {}).get("pid") or st.get("pid"),
        "phase": (dst or {}).get("phase") or st.get("phase"),
        "sampling": mode,
        "next_round_at": (dst or {}).get("next_round_at") or st.get("next_round_at"),
        "updated_at": (dst or {}).get("updated_at") or st.get("updated_at"),
    }


def _open_tickets(limit: int = 20) -> list[dict]:
    """只读扫描待批工单：有 request.md、尚无 decision.json。"""
    out: list[dict] = []
    try:
        runs = sorted(E.RUNS.glob("patrol_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return out
    for run in runs:
        for req in sorted(run.glob("approvals/*/request.md")):
            if (req.parent / "decision.json").exists():
                continue
            out.append({
                "run_id": run.name,
                "request_id": req.parent.name,
                "request_md": str(req),
            })
            if len(out) >= limit:
                return out
    return out


def _recent_events(n: int = 12) -> list[dict]:
    try:
        ev = E.tail_events(n)
    except Exception:  # noqa: BLE001
        return []
    # tail_events 按时间正序；首页要“最新在前”
    return list(reversed(ev))


def build_status() -> dict:
    plog = EventLog()
    all_events, bad = plog.read_all(compat=True)
    by_kind: dict[str, int] = {}
    for e in all_events:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
    pstats = {"total": len(all_events), "by_kind": by_kind, "bad_lines": bad,
              "last_ts": all_events[-1].ts if all_events else None}
    tail = all_events[-8:]
    pevents = [e.model_dump(exclude={"series", "ring_before", "ring_after", "a4_window"})
               for e in tail]
    for e, src in zip(pevents, tail):
        e["series_n"] = len(src.series)
        e["ring_n"] = len(src.ring_before) + len(src.ring_after)
        e["a4_n"] = len(src.a4_window)
    probe = _probe_status()
    probe.update({"events_total": pstats["total"], "by_kind": pstats["by_kind"],
                  "bad_lines": pstats["bad_lines"],
                  "last_event_ts": pstats["last_ts"]})

    from upi.core.cluster import cluster_events
    clusters = cluster_events(all_events, merge_gap=30)

    from upi.core.fingerprint import FingerprintStore
    fp_path = os.environ.get("UPI_FINGERPRINT_DB") or None
    fp = FingerprintStore(fp_path)
    fp.ensure_seed()
    fp_stats = fp.stats()
    fp_records = [r.model_dump(exclude={"features"}) for r in fp.all()[:20]]

    from upi.core.ticket import TicketStore
    tstore = TicketStore(os.environ.get("UPI_TICKET_DIR") or None)
    batch_tickets = [t.summary() for t in tstore.list(10)]
    from upi.core.regression import RegressionStore
    rstore = RegressionStore(os.environ.get("UPI_REGRESSION_DIR") or None)
    regression_suites = [rstore.summary(s.suite_id)
                         for s in rstore.list_suites()[:10]]
    from upi.tooling import probe_control as PC
    settings = {
        "probe_file": str(PC.PROBE_FILE),
        "fingerprint_db": str(fp.path),
        "ticket_dir": str(tstore.root),
        "regression_dir": str(rstore.root),
    }
    try:
        from upi.config.project_profile import active_profile
        prof = active_profile()
        settings.update({"profile_id": prof.profile_id,
                         "project": str(prof.abs_project)})
    except Exception as e:  # noqa: BLE001 — 未选择工程时状态页仍可用
        settings.update({"profile_id": None, "project": "",
                         "project_error": f"{type(e).__name__}: {str(e)[:160]}"})
    from upi.core.scheduler import get_scheduler
    try:
        from upi.core import capture_config as CC
        capture_cfg = CC.load()
    except Exception as e:  # noqa: BLE001
        capture_cfg = {"error": f"{type(e).__name__}: {e}"}
    try:
        from upi.core.knowledge import KnowledgeStore, default_path
        _ks = KnowledgeStore(default_path())
        learning = {"shadow_agreement": _ks.shadow_agreement(),
                    "ece_coverage": _ks.ece_coverage(),
                    "patterns": _ks.list_patterns(30)}
        _ks.close()
    except Exception as e:  # noqa: BLE001
        learning = {"error": f"{type(e).__name__}: {e}"}
    return {
        "ok": True,
        "time": E.now(),
        "ui": {"shell": "I5a", "single_channel": True},
        "mode": "resident",
        "daemon": _daemon_status(),
        "project": _project_status(),
        "projects": _projects_payload(),
        "probe": probe,
        "probe_events": pevents,
        "clusters": [c.summary() for c in clusters[:20]],
        "cluster_total": len(clusters),
        "fingerprints": fp_stats,
        "fingerprint_records": fp_records,
        "batch_tickets": batch_tickets,
        "regression_suites": regression_suites,
        "settings": settings,
        "capture_config": capture_cfg,
        "learning": learning,
        "resident_scheduler": get_scheduler().status(),
        "tickets": _open_tickets(),
        "events": _recent_events(),
    }


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")


_INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UPI · 性能 Agent 控制台</title>
<style>
  :root{
    --bg:#0b0f14; --panel:#111821; --panel2:#0e141c; --line:#223041;
    --text:#dbe7f3; --muted:#8aa0b6; --dim:#5f7388;
    --green:#3ddc97; --red:#ff6b6b; --amber:#f5b84b; --cyan:#54c8ff;
    --mono:"Cascadia Mono","Consolas",monospace;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;background:var(--bg);color:var(--text);
    font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;font-size:14px}
  a{color:inherit;text-decoration:none}
  .top{height:54px;display:flex;align-items:center;gap:14px;padding:0 18px;
    border-bottom:1px solid var(--line);background:linear-gradient(90deg,#0d141c,#0b1118)}
  .brand{font-weight:700;letter-spacing:.4px;display:flex;align-items:center;gap:9px}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--cyan);box-shadow:0 0 10px var(--cyan)}
  .tag{font-size:11px;color:var(--dim);border:1px solid var(--line);padding:2px 7px;border-radius:99px}
  .spacer{flex:1}
  .top .stat{font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center}
  .top .stat b{color:var(--text);font-weight:600}
  .top select{background:var(--panel2);color:var(--text);border:1px solid var(--line);
    border-radius:5px;padding:3px 6px;font-size:12px;max-width:190px}
  .wrap{display:grid;grid-template-columns:188px 1fr;min-height:calc(100vh - 54px)}
  .nav{border-right:1px solid var(--line);padding:14px 10px;background:var(--panel2)}
  .nav a{display:flex;gap:9px;align-items:center;padding:9px 11px;border-radius:8px;
    color:var(--muted);margin-bottom:3px;font-size:13px}
  .nav a:hover{background:#152130;color:var(--text)}
  .nav a.on{background:#142638;color:var(--text);box-shadow:inset 2px 0 0 var(--cyan)}
  .nav .k{width:18px;text-align:center;opacity:.85}
  .main{padding:20px 22px 34px;max-width:1500px}
  h1{font-size:19px;margin:0 0 4px;font-weight:650}
  .sub{color:var(--muted);font-size:12.5px;margin:0 0 18px}
  .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 15px}
  .card .lab{font-size:11.5px;color:var(--dim);letter-spacing:.4px}
  .card .val{font-size:22px;font-weight:700;margin-top:7px;display:flex;align-items:center;gap:8px}
  .card .meta{font-size:11.5px;color:var(--muted);margin-top:5px;line-height:1.55;word-break:break-all}
  .ok{color:var(--green)} .bad{color:var(--red)} .warn{color:var(--amber)} .cyan{color:var(--cyan)}
  .pill{font-size:11px;padding:2px 7px;border-radius:99px;border:1px solid var(--line);color:var(--muted)}
  .cols{display:grid;grid-template-columns:1.35fr 1fr;gap:12px;margin-top:12px}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
  .panel h2{font-size:13px;margin:0;padding:12px 15px;border-bottom:1px solid var(--line);
    color:var(--text);font-weight:600;display:flex;justify-content:space-between;align-items:center}
  .panel h2 span{font-size:11px;color:var(--dim);font-weight:400}
  .feed{max-height:340px;overflow:auto}
  .ev{padding:9px 15px;border-bottom:1px solid #182333;display:grid;
    grid-template-columns:72px 1fr;gap:10px;font-size:12px}
  .ev .t{color:var(--dim);font-family:var(--mono);font-size:11px}
  .ev .n{color:var(--cyan);font-family:var(--mono);font-size:11.5px}
  .ev .d{color:var(--muted);grid-column:2;margin-top:2px;word-break:break-all}
  .tickets{padding:8px 0}
  .ticket{padding:10px 15px;border-bottom:1px solid #182333;display:flex;justify-content:space-between;gap:10px}
  .ticket .id{font-family:var(--mono);font-size:11.5px;color:var(--amber)}
  .empty{padding:18px 15px;color:var(--dim);font-size:12.5px}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  th,td{text-align:left;padding:9px 12px;border-bottom:1px solid #182333;vertical-align:top}
  th{color:var(--dim);font-weight:500;font-size:11.5px;background:#0d141c}
  td.mono{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
  .badge{font-size:10.5px;padding:1px 6px;border-radius:99px;border:1px solid var(--line);color:var(--muted)}
  .badge.spike{color:var(--red);border-color:#4a2525}
  .badge.step{color:var(--amber);border-color:#4a3d22}
  .badge.periodic{color:var(--cyan);border-color:#1e4052}
  .badge.cluster{color:var(--green);border-color:#1e4a38}
  .tbtn{background:#152130;color:var(--muted);border:1px solid var(--line);border-radius:6px;
    padding:3px 8px;margin-right:4px;cursor:pointer;font-size:11px}
  .tbtn:hover{color:var(--text);border-color:var(--cyan)}
  .rbtn{background:#152130;color:var(--muted);border:1px solid var(--line);border-radius:6px;
    padding:3px 8px;margin-right:4px;cursor:pointer;font-size:11px}
  .rbtn:hover{color:var(--text);border-color:var(--cyan)}
  .vbtn{background:#152130;color:var(--muted);border:1px solid var(--line);border-radius:6px;
    padding:3px 8px;margin-right:4px;cursor:pointer;font-size:11px}
  .vbtn:hover{color:var(--text);border-color:var(--cyan)}
  .bbtn{background:#152130;color:var(--muted);border:1px solid var(--line);border-radius:6px;
    padding:3px 8px;margin-right:4px;cursor:pointer;font-size:11px}
  .bbtn:hover{color:var(--text);border-color:var(--cyan)}
  .gbtn{background:#152130;color:var(--muted);border:1px solid var(--line);border-radius:6px;
    padding:3px 8px;margin-right:4px;cursor:pointer;font-size:11px}
  .gbtn:hover{color:var(--text);border-color:var(--cyan)}
  .form{display:grid;gap:9px;padding:10px 15px 14px}
  .form label{display:grid;gap:4px;font-size:11.5px;color:var(--dim)}
  .form input,.form textarea{background:#0b1219;border:1px solid var(--line);border-radius:6px;
    color:var(--text);padding:7px 8px;font:12px var(--mono);outline:none}
  .form input:focus,.form textarea:focus{border-color:var(--cyan)}
  .form .pbtn{padding:6px 12px}
  .diff{margin:6px 0;padding:6px;border:1px solid #1b2a3a;border-radius:5px;background:#0b1219}
  .diff pre{white-space:pre-wrap;word-break:break-all;margin:4px 0;color:var(--dim);font-size:10.5px}
  .pbtn{background:#152130;color:var(--muted);border:1px solid var(--line);border-radius:6px;
    padding:4px 8px;margin-right:4px;cursor:pointer;font-size:11px}
  .pbtn:hover{color:var(--text);border-color:var(--cyan)}
  .small{font-size:11px;color:var(--dim)}
  .steps{margin-top:12px}
  .step{display:grid;grid-template-columns:22px 1fr auto;gap:10px;padding:9px 15px;
    border-bottom:1px solid #182333;align-items:center;font-size:12.5px}
  .step .s{font-family:var(--mono);color:var(--dim);font-size:11px}
  .foot{margin-top:18px;color:var(--dim);font-size:11.5px;display:flex;gap:14px;flex-wrap:wrap}
  .placeholder{padding:42px 20px;color:var(--dim);text-align:center;font-size:13px}
  .placeholder b{color:var(--text);display:block;font-size:15px;margin-bottom:6px}
  @media (max-width:1000px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}
    .cols{grid-template-columns:1fr}.wrap{grid-template-columns:1fr}.nav{display:flex;overflow:auto;border-right:0;border-bottom:1px solid var(--line)}
    .nav a{white-space:nowrap}.nav .k{display:none}}
</style>
</head>
<body>
<header class="top">
  <div class="brand"><span class="dot"></span>UPI 性能 Agent</div>
  <span class="tag">I5a 报告/设置</span>
  <span class="tag">UI = 唯一人机通道</span>
  <div class="spacer"></div>
  <div class="stat">形态 <b id="mode">常驻</b></div>
  <div class="stat">工程
    <select id="projectSelect" title="切换修复对象工程"></select>
  </div>
  <div class="stat">探针 <b id="probeTop">—</b></div>
  <div class="stat">待我处理 <b id="ticketTop">0</b></div>
</header>
<div class="wrap">
  <nav class="nav" id="nav">
    <a href="#" data-page="home" class="on"><span class="k">▣</span>首页</a>
    <a href="#" data-page="issues"><span class="k">◇</span>问题</a>
    <a href="#" data-page="tickets"><span class="k">▤</span>工单</a>
    <a href="#" data-page="regression"><span class="k">↻</span>回归</a>
    <a href="#" data-page="library"><span class="k">◈</span>指纹库</a>
    <a href="#" data-page="labels"><span class="k">◎</span>标签</a>
    <a href="#" data-page="reports"><span class="k">▥</span>报告</a>
    <a href="#" data-page="projects"><span class="k">▦</span>工程</a>
    <a href="#" data-page="settings"><span class="k">⚙</span>设置</a>
    <a href="/repair?token=__UPI_TOKEN__"><span class="k">⑃</span>修复状态</a>
  </nav>
  <main class="main">
    <section id="page-home">
      <h1>现在发生了什么</h1>
      <p class="sub">探针是唯一数据源；1Hz 轮询只做哨兵。所有人类操作都将在这里完成。</p>
      <div class="grid">
        <div class="card"><div class="lab">守护进程</div><div class="val" id="daemonVal">—</div><div class="meta" id="daemonMeta"></div></div>
        <div class="card"><div class="lab">工程</div><div class="val" id="projectVal">—</div><div class="meta" id="projectMeta"></div></div>
        <div class="card"><div class="lab">探针</div><div class="val" id="probeVal">—</div><div class="meta" id="probeMeta"></div>
          <div style="margin-top:9px" id="probeActions">
            <button class="pbtn" data-pact="install">安装</button>
            <button class="pbtn" data-pact="sample-on">采样开</button>
            <button class="pbtn" data-pact="sample-off">采样关</button>
            <button class="pbtn" data-pact="unload">卸载</button>
          </div>
          <div style="margin-top:8px" id="schedActions">
            <button class="pbtn" data-pact="resident-start">自动巡检</button>
            <button class="pbtn" data-pact="resident-tick">巡检一轮</button>
            <button class="pbtn" data-pact="resident-stop">停</button>
          </div>
          <div class="meta" id="schedMeta">巡检：—</div>
        </div>
        <div class="card"><div class="lab">待批工单</div><div class="val" id="ticketVal">—</div><div class="meta" id="ticketMeta"></div></div>
      </div>
      <div class="cols">
        <div class="panel">
          <h2>事件流 <span id="updated">等待刷新</span></h2>
          <div class="feed" id="feed"><div class="empty">暂无事件。启动守护后，这里会实时出现。</div></div>
        </div>
        <div class="panel">
          <h2>待我处理 <span>UI 内完成，不读文件</span></h2>
          <div class="tickets" id="tickets"><div class="empty">没有待批工单。</div></div>
        </div>
      </div>
      <div class="panel steps">
        <h2>迭代进度 <span>每项完成即点亮</span></h2>
        <div class="step"><span class="s">01</span><span>本地 Web 控制台 + 状态 API + SSE</span><span class="pill ok">已接入</span></div>
        <div class="step"><span class="s">02</span><span>统一事件总线：breach / sample + 1Hz 哨兵</span><span class="pill ok">已接入</span></div>
        <div class="step"><span class="s">03</span><span>单探针物理合并（一个心跳 / 一个回调）</span><span class="pill">I1b</span></div>
        <div class="step"><span class="s">04</span><span>事件 → 聚类（问题页已接入）；指纹库后端已落地</span><span class="pill ok">I2</span></div>
        <div class="step"><span class="s">05</span><span>常驻：工单/审批/执行/巡检已接；真实 plan 在 I3b-2c</span><span class="pill ok">I3b</span></div>
        <div class="step"><span class="s">06</span><span>回归：套件/3 次基线/计划已接；真实 replay 执行待 I4b2</span><span class="pill ok">I4b</span></div>
        <div class="step"><span class="s">07</span><span>报告/设置已接；正式审批/执行时间线待 I5b</span><span class="pill ok">I5a</span></div>
      </div>
      <div class="foot"><span>本地 127.0.0.1</span><span>所有状态来自 runs/ 与守护进程</span><span>UI 不可用时用 break-glass 停止/回滚</span></div>
    </section>
    <section id="page-issues" style="display:none">
      <h1>问题聚类</h1>
      <p class="sub">一个 replay/巡检里可能有多个相同或不同问题：先聚成 cluster，再决定修哪个。cluster 是修复/复现率/验证的单位。</p>
      <div class="panel">
        <h2>当前 cluster <span id="clusterMeta">—</span></h2>
        <table>
          <thead><tr><th>指标</th><th>波形</th><th>相对帧</th><th>次数</th><th>数据档</th><th>值</th></tr></thead>
          <tbody id="clusterBody"><tr><td colspan="6" class="empty">暂无 cluster。</td></tr></tbody>
        </table>
      </div>
    </section>
    <section id="page-library" style="display:none">
      <h1>指纹库</h1>
      <p class="sub">cluster 特征命中已知 pattern 时跳过 LLM；指纹是路由，不是证据。命中仍走权限/验证/回滚。</p>
      <div class="panel">
        <h2>pattern <span id="fpMeta">—</span></h2>
        <table>
          <thead><tr><th>pattern</th><th>指标</th><th>波形</th><th>来源</th><th>命中</th><th>成功/失败</th><th>最后命中</th></tr></thead>
          <tbody id="fpBody"><tr><td colspan="7" class="empty">指纹库为空。</td></tr></tbody>
        </table>
      </div>
    </section>
    <section id="page-labels" style="display:none">
      <div class="card">
        <h2>标签审核</h2>
        <p class="muted">只有你批准，标签才生效；驳回后不会自动重提。待审项来自归纳总结 Agent 的提议。</p>
        <button id="labelRefresh">刷新</button>
        <h3>待审</h3><div id="labelPending" class="mono"></div>
        <h3>已生效</h3><div id="labelActive" class="mono"></div>
        <h3>已驳回</h3><div id="labelRejected" class="mono"></div>
      </div>
    </section>
    <section id="page-tickets" style="display:none">
      <h1>批量工单</h1>
      <p class="sub">一次列多个候选、按顺序执行；执行一次只落一个 patch。常驻结论只 <b>unverified</b>；批量批准不绕过单个 patch 的权限闸。</p>
      <div style="margin:10px 0 14px"><button id="buildTicketBtn" style="background:#14314a;color:#dbe7f3;border:1px solid #2a4a63;border-radius:8px;padding:9px 14px;cursor:pointer">从当前 cluster 生成工单</button>
        <button id="oppTicketBtn" style="background:#1b3a2d;color:#dbe7f3;border:1px solid #2a6348;border-radius:8px;padding:9px 14px;cursor:pointer;margin-left:6px">从机会生成工单</button>
        <button id="candTicketBtn" style="background:#2a2340;color:#e3dbf3;border:1px solid #4a3a63;border-radius:8px;padding:9px 14px;cursor:pointer;margin-left:6px">从证据候选生成工单</button>
        <span id="ticketHint" style="color:var(--dim);font-size:12px;margin-left:10px">I3a：建单/审批可用；机会工单来自当前源码扫描。</span></div>
      <div class="panel">
        <h2>工单 <span id="batchMeta">—</span></h2>
        <table>
          <thead><tr><th>工单</th><th>状态</th><th>候选</th><th>待批/需人工</th><th>校验码</th><th>操作</th></tr></thead>
          <tbody id="batchBody"><tr><td colspan="6" class="empty">还没有批量工单。</td></tr></tbody>
        </table>
      </div>
      <div class="panel" style="margin-top:12px">
        <h2>当前工单明细 <span>指标 / 指纹 / 权限 / 状态</span></h2>
        <div id="batchDetail"><div class="empty">点“明细”查看候选。</div></div>
      </div>
      <div class="panel" id="approvalPanel" style="display:none;margin-top:12px">
        <h2>审批 <span id="approvalTitle">—</span></h2>
        <div class="form">
          <label>批准人 / 驳回人<input id="appName" placeholder="姓名"></label>
          <label>批语<textarea id="appNote" rows="3" placeholder="说明你已读证据/计划"></textarea></label>
          <label id="appTokenRow">4 位校验码<input id="appToken" maxlength="4" placeholder="读完工单后填"></label>
          <div><button id="appConfirm" class="pbtn">确认</button><button id="appCancel" class="pbtn">取消</button>
            <span id="appError" class="small"></span></div>
        </div>
      </div>
    </section>
    <section id="page-regression" style="display:none">
      <h1>回归验证</h1>
      <p class="sub">同一 replay/协议跑 3 次基线，只收稳定 cluster；修后同 replay 再验。只有这里能记 <b>verified</b>。</p>
      <div style="margin:10px 0 14px"><button id="createSuiteBtn" style="background:#14314a;color:#dbe7f3;border:1px solid #2a4a63;border-radius:8px;padding:9px 14px;cursor:pointer">新建回归套件</button>
        <span style="color:var(--dim);font-size:12px;margin-left:10px">建好后每跑完一次 replay，点“记录本次基线”；满 3 次自动出计划。</span></div>
      <div class="panel">
        <h2>套件 <span id="regMeta">—</span></h2>
        <table>
          <thead><tr><th>套件</th><th>协议</th><th>基线次数</th><th>稳定 cluster</th><th>计划</th><th>操作</th></tr></thead>
          <tbody id="regBody"><tr><td colspan="6" class="empty">还没有回归套件。</td></tr></tbody>
        </table>
      </div>
    </section>
    <section id="page-reports" style="display:none">
      <h1>报告</h1>
      <p class="sub">回归报告只读；只有“同协议 + 目标消失 + 无新 cluster + 守护不劣化”才记 verified。</p>
      <div class="panel">
        <h2>回归报告 <span id="reportMeta">—</span></h2>
        <table>
          <thead><tr><th>套件</th><th>基线</th><th>verified</th><th>最近 verdict</th><th>操作</th></tr></thead>
          <tbody id="reportBody"><tr><td colspan="5" class="empty">还没有回归套件。</td></tr></tbody>
        </table>
      </div>
    </section>
    <section id="page-projects" style="display:none">
      <h1>工程</h1>
      <p class="sub">修复对象只能从这里选；选择后所有探针/工单/回归/报告都绑定它。没有工程时不会回退夹具。</p>
      <div class="panel">
        <h2>已注册工程 <span id="projectListMeta">—</span></h2>
        <table>
          <thead><tr><th>工程</th><th>路径</th><th>场景</th><th>状态</th><th>最近</th><th>操作</th></tr></thead>
          <tbody id="projectBody"><tr><td colspan="6" class="empty">暂无工程，请在下方新增。</td></tr></tbody>
        </table>
      </div>
      <div class="panel">
        <h2>新增工程</h2>
        <div class="form">
          <label>工程目录（Unity 工程根）<input id="newProjectPath" placeholder="D:\MyGame"></label>
          <label>显示名<input id="newProjectName" placeholder="留空用目录名"></label>
          <label>默认场景（相对工程）<input id="newProjectScene" placeholder="Assets/Scenes/Main.unity"></label>
          <label>质量档<input id="newProjectQuality" value="urp-balanced"></label>
          <label>Replay 命令（可空）<input id="newProjectReplay" placeholder="python tools/replay.py"></label>
          <button class="pbtn" id="addProjectBtn">新增并校验</button>
        </div>
        <div class="small" id="projectMsg"></div>
      </div>
    </section>
    <section id="page-settings" style="display:none">
      <h1>设置 / 接入</h1>
      <p class="sub">只读展示当前工程、探针、指纹库、回归目录；写入/权限仍走各自页面与审批。</p>
      <div class="panel">
        <h2>当前接入</h2>
        <table><tbody id="settingsBody"><tr><td class="empty">加载中…</td></tr></tbody></table>
      </div>
      <div class="panel" style="margin-top:12px">
        <h2>探针与采集</h2>
        <div style="padding:10px 15px;font-size:12px;color:var(--muted);line-height:1.9">
          <div>探针状态：<b id="setProbeState">—</b> <span id="setProbeMeta" class="small"></span></div>
          <div style="margin-top:6px" id="setProbeActions">
            <button class="pbtn" data-pact="install">安装</button>
            <button class="pbtn" data-pact="sample-on">采样开</button>
            <button class="pbtn" data-pact="sample-off">采样关</button>
            <button class="pbtn" data-pact="unload">卸载</button>
          </div>
          <div style="margin-top:12px">
            取证线程 <input id="capThreads" type="number" min="1" max="64" style="width:66px">
            取证帧数 <input id="capFrames" type="number" min="1" max="32" style="width:66px">
            预算ms <input id="capBudget" type="number" min="500" max="20000" style="width:84px">
            板子帧数 <input id="capBoardFrames" type="number" min="1" max="8" style="width:66px">
            采样窗口 <input id="capSampleWindow" type="number" min="30" max="3600" style="width:76px">
            <button class="pbtn" id="capSave">保存</button>
            <span id="capMsg" class="small"></span>
          </div>
          <div class="small" style="margin-top:6px">这些值与 I8 Lab 共用 config/i8_lab.json；下一次取证/采样生效。</div>
        </div>
      </div>
      <div class="panel" style="margin-top:12px">
        <h2>模式档位（升维 / 影子）</h2>
        <table><thead><tr><th>pattern</th><th>档位</th><th>新问题</th><th>操作</th></tr></thead>
        <tbody id="patternBody"><tr><td colspan="4" class="empty">加载中…</td></tr></tbody></table>
        <div class="small" style="padding:8px 15px">升 auto 必须走升维提案（体检表 + 样本量）；
          这里只做 semi ↔ shadow 的人工切换，变更会留痕。</div>
      </div>
      <div class="panel" style="margin-top:12px">
        <h2>首次使用顺序</h2>
        <div id="onboard" style="padding:10px 15px;color:var(--muted);font-size:12px;line-height:1.9">
          加载中…
        </div>
      </div>
    </section>
    <section id="page-other" style="display:none">
      <div class="placeholder"><b id="otherTitle">此页待接入</b><span id="otherHint">该页面在后续迭代交付；I0 只保证壳与状态通道。</span></div>
    </section>
  </main>
</div>
<script>
const TOKEN = "__UPI_TOKEN__";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const q = (p) => p + (p.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN);
async function post(path, body){
  const r = await fetch(q(path), {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(body || {})});
  let data = {};
  try{ data = await r.json(); }catch(_){}
  if(!r.ok || data.ok === false) throw new Error(data.error || data.message || ("HTTP " + r.status));
  return data;
}
const batchCache = {};
async function getJSON(path){
  const r = await fetch(q(path));
  let data = {};
  try{ data = await r.json(); }catch(_){}
  if(!r.ok || data.ok === false) throw new Error(data.error || ("HTTP " + r.status));
  return data;
}
function labelRows(items, actions){
  if(!items || !items.length) return '<div class="muted">无</div>';
  return items.map(l => `<div class="kv" style="padding:6px 0;border-bottom:1px solid var(--line)">
    <b>${esc(l.mechanism)}</b> · ${esc(l.file)}::${esc(l.method)}
    <span class="muted">conf ${esc(l.confidence)} · ${esc(l.status)} · ${esc(l.proposed_at)}</span>
    <div class="muted">${esc(l.rationale || "")}</div>
    ${actions ? `<button data-lid="${esc(l.label_id)}" data-dec="approve">批准</button>
      <button data-lid="${esc(l.label_id)}" data-dec="reject">驳回</button>` : ""}
  </div>`).join("");
}
async function loadLabels(){
  try{
    const d = await getJSON("/api/labels");
    $("labelPending").innerHTML = labelRows(d.pending, true);
    $("labelActive").innerHTML = labelRows(d.active, false);
    $("labelRejected").innerHTML = labelRows(d.rejected, false);
    document.querySelectorAll("#labelPending button").forEach(b => b.addEventListener("click", async () => {
      const reason = prompt(b.dataset.dec === "approve" ? "批准理由（可空）" : "驳回理由");
      if(b.dataset.dec === "reject" && reason === null) return;
      try{
        await post("/api/labels/decision", {label_id: b.dataset.lid,
          decision: b.dataset.dec, reviewer: "user", reason: reason || ""});
        loadLabels();
      }catch(e){ alert("失败：" + e.message); }
    }));
  }catch(e){ $("labelPending").textContent = "加载失败：" + e.message; }
}
function renderTickets(s){
  const ts = s.batch_tickets || [];
  $("batchMeta").textContent = `${ts.length} 张`;
  $("batchBody").innerHTML = ts.length ? ts.map(t =>
    `<tr><td class="mono">${esc(t.ticket_id)}<div style="color:var(--dim);font-size:10.5px">${esc(t.created_at)}</div></td>` +
    `<td><span class="badge ${t.status === "approved" ? "cluster" : "step"}">${esc(t.status)}</span></td>` +
    `<td>${esc(t.items)}</td><td>${esc(t.pending)} / ${esc(t.needs_human)}</td>` +
    `<td class="mono">${esc(t.approve_token || "—")}</td>` +
    `<td><button data-tid="${esc(t.ticket_id)}" class="tbtn" data-act="view">明细</button>` +
    (t.status === "awaiting_approval"
      ? `<button data-tid="${esc(t.ticket_id)}" class="tbtn" data-act="approve">批准</button>` +
        `<button data-tid="${esc(t.ticket_id)}" class="tbtn" data-act="request_changes">退回修改</button>` +
        `<button data-tid="${esc(t.ticket_id)}" class="tbtn" data-act="reject">驳回</button>`
      : "") +
    (t.status === "approved" && t.pending > 0
      ? `<button data-tid="${esc(t.ticket_id)}" class="tbtn" data-act="execute">执行</button>`
      : "") + `</td></tr>`
  ).join("") : `<tr><td colspan="6" class="empty">还没有批量工单。</td></tr>`;
  ts.forEach(t => { batchCache[t.ticket_id] = t; });
}

function renderRegression(s){
  const rs = s.regression_suites || [];
  $("regMeta").textContent = `${rs.length} 个`;
  $("regBody").innerHTML = rs.length ? rs.map(x => {
    const plan = x.plan || {};
    const lv = x.last_verdict ? ` · 上次 ${x.last_verdict.status}` : "";
    const planText = plan.status === "ready"
      ? `${plan.items.length} 个稳定 cluster${lv}`
      : (plan.status || "—");
    const vb = x.runs >= 4
      ? `<button class="vbtn" data-vid="${esc(x.suite_id)}">验证最后一次</button>` : "";
    const bb = x.command
      ? `<button class="bbtn" data-bid="${esc(x.suite_id)}">跑3次基线</button>` : "";
    const gb = `<button class="gbtn" data-gid="${esc(x.suite_id)}">报告</button>`;
    return `<tr><td>${esc(x.name || x.suite_id)}<div class="mono" style="color:var(--dim);font-size:10.5px">${esc(x.suite_id)}</div></td>` +
      `<td class="mono">${esc(x.protocol_fingerprint || "—")}</td>` +
      `<td>${esc(x.runs)} / ${esc(x.min_runs || 3)}</td><td>${esc(x.stable_clusters || 0)}</td>` +
      `<td>${esc(planText)}</td>` +
      `<td><button class="rbtn" data-rid="${esc(x.suite_id)}">记录本次基线</button>${bb}${vb}${gb}</td></tr>`;
  }).join("") : `<tr><td colspan="6" class="empty">还没有回归套件。</td></tr>`;
}

async function createSuite(){
  const name = prompt("回归套件名（例如 F-CPU-01 回放）:");
  if(!name) return;
  const protocol = prompt("协议指纹（同 replay/画质/档位；留空=manual）:", "manual");
  const command = prompt("replay 命令（可留空，之后手点记录）:", "");
  try{
    const r = await post("/api/regression/suites",
                         {name, protocol_fingerprint: protocol || "manual",
                          command: command || ""});
    alert("已创建 " + (r.summary ? r.summary.suite_id : "套件"));
    await refresh();
  }catch(e){ alert("创建失败：" + e.message); }
}
async function recordRun(sid){
  try{
    const r = await post(`/api/regression/suites/${encodeURIComponent(sid)}/record`, {});
    const sm = r.summary || {};
    alert(`已记录第 ${sm.runs} 次；计划：${(sm.plan || {}).status || "—"}`);
    await refresh();
  }catch(e){ alert("记录失败：" + e.message); }
}
function renderReports(s){
  const rs = s.regression_suites || [];
  $("reportMeta").textContent = `${rs.length} 个套件`;
  $("reportBody").innerHTML = rs.length ? rs.map(x => {
    const v = x.last_verdict || {};
    return `<tr><td>${esc(x.name || x.suite_id)}<div class="mono" style="color:var(--dim);font-size:10.5px">${esc(x.suite_id)}</div></td>` +
      `<td>${esc(x.runs)} / ${esc(x.min_runs || 3)}</td><td>${esc(x.verified_count || 0)}</td>` +
      `<td>${esc(v.status || "—")}${v.verification_status ? " (" + esc(v.verification_status) + ")" : ""}</td>` +
      `<td><button class="gbtn" data-gid="${esc(x.suite_id)}">生成/打开报告</button></td></tr>`;
  }).join("") : `<tr><td colspan="5" class="empty">还没有回归套件。</td></tr>`;
}
function renderSettings(s){
  const st = s.settings || {};
  const rows = [
    ["profile", st.profile_id], ["project", st.project],
    ["probe_file", st.probe_file],
    ["fingerprint_db", st.fingerprint_db], ["ticket_dir", st.ticket_dir],
    ["regression_dir", st.regression_dir], ["error", st.error],
  ].filter(([, v]) => v != null && v !== "");
  $("settingsBody").innerHTML = rows.length
    ? rows.map(([k, v]) => `<tr><th style="width:160px">${esc(k)}</th><td class="mono small">${esc(v)}</td></tr>`).join("")
    : `<tr><td class="empty">无设置数据。</td></tr>`;
  const p = s.probe || {}, bt = s.batch_tickets || [], rs2 = s.regression_suites || [];
  const steps = [
    ["安装探针", p.state === "alive"],
    ["有探针事件/聚类", (s.cluster_total || 0) > 0],
    ["有批量工单", bt.length > 0],
    ["有回归套件", rs2.length > 0],
  ];
  $("onboard").innerHTML = steps.map(([label, ok]) =>
    `<div>${ok ? '<span class="ok">✓</span>' : '<span class="bad">○</span>'} ${esc(label)}</div>`).join("")
    + `<div class="small" style="margin-top:6px">顺序：装探针 → 采样开 → 巡检/建单 → 批准 → 回归 3 次基线 → 验证/报告</div>`;
  $("setProbeState").textContent = p.state === "alive" ? "在线"
    : (p.state === "stale" ? "心跳超龄" : "无心跳");
  $("setProbeMeta").textContent = p.heartbeat_age_s == null
    ? "未安装/未运行探针" : `心跳 ${p.heartbeat_age_s}s 前`;
  const cc = s.capture_config || {};
  if (!cc.error) {
    $("capThreads").value = cc.evidence_threads;
    $("capFrames").value = cc.evidence_frames;
    $("capBudget").value = cc.evidence_budget_ms;
    $("capBoardFrames").value = cc.board_frames;
    $("capSampleWindow").value = cc.probe_sample_window;
  }
  const pats = (s.learning || {}).patterns || [];
  $("patternBody").innerHTML = pats.length ? pats.map(p =>
    `<tr><td class="mono small">${esc(p.pattern_id)}</td><td>${esc(p.tier)}</td>` +
    `<td>${esc(p.n_new_issue)}</td><td>` +
    (p.tier === "semi"
      ? `<button class="patBtn" data-pid="${esc(p.pattern_id)}" data-tier="shadow">升影子</button>` : "") +
    (p.tier === "shadow"
      ? `<button class="patBtn" data-pid="${esc(p.pattern_id)}" data-tier="semi">回半自动</button>` : "") +
    `</td></tr>`).join("")
    : `<tr><td colspan="4" class="empty">还没有模式记录。</td></tr>`;
}
async function setPatternTier(pid, tier){
  const actor = prompt("操作人（档位变更会留痕）:", "ui");
  if(!actor) return;
  try{
    await post(`/api/knowledge/patterns/${encodeURIComponent(pid)}/tier`, {tier, actor});
    await refresh();
  }catch(e){ alert("档位变更失败：" + e.message); }
}
async function saveCaptureCfg(){
  try{
    const body = {
      evidence_threads: +$("capThreads").value,
      evidence_frames: +$("capFrames").value,
      evidence_budget_ms: +$("capBudget").value,
      board_frames: +$("capBoardFrames").value,
      probe_sample_window: +$("capSampleWindow").value,
    };
    const r = await post("/api/capture/config", body);
    $("capMsg").textContent = `已保存（线程 ${r.config.evidence_threads} / 帧 ${r.config.evidence_frames} / 预算 ${r.config.evidence_budget_ms}ms）`;
    await refresh();
  }catch(e){ $("capMsg").textContent = "失败：" + e.message; }
}
document.addEventListener("click", (ev) => {
  const t = ev.target.closest("#capSave");
  if (t) { ev.preventDefault(); saveCaptureCfg(); }
});
async function verifyLast(sid){
  try{
    const r = await post(`/api/regression/suites/${encodeURIComponent(sid)}/verify`, {});
    const v = (r.record || {}).verdict || {};
    alert(`验证：${v.status || "—"}（${v.verification_status || "—"}）\n${v.reason || ""}`);
    await refresh();
  }catch(e){ alert("验证失败：" + e.message); }
}
async function runBaseline(sid){
  try{
    const r = await post(`/api/regression/suites/${encodeURIComponent(sid)}/run-baseline`,
                         {runs: 3, align: true});
    alert(`已跑 ${r.runs} 次；计划：${((r.summary || {}).plan || {}).status || "—"}`);
    await refresh();
  }catch(e){ alert("跑基线失败：" + e.message); }
}
async function genReport(sid){
  try{
    await post(`/api/regression/suites/${encodeURIComponent(sid)}/report`, {});
    window.open(q(`/api/regression/suites/${encodeURIComponent(sid)}/report`), "_blank");
  }catch(e){ alert("生成报告失败：" + e.message); }
}
async function buildOpportunityTicket(){
  try{
    const r = await post("/api/tickets/opportunities", {});
    await refresh();
    if(r.ticket){ showTicketDetail(r.ticket); }
  }catch(e){ alert("机会建单失败：" + e.message); }
}
async function buildTicket(){
  try{
    const r = await post("/api/tickets/build", {});
    await refresh();
    if(r.ticket){ showTicketDetail(r.ticket); }
  }catch(e){ alert("建单失败：" + e.message); }
}
async function buildCandidateTicket(){
  try{
    const r = await post("/api/tickets/candidates", {});
    await refresh();
    if(r.ticket){ showTicketDetail(r.ticket); }
  }catch(e){ alert("证据候选建单失败：" + e.message); }
}
function showTicketDetail(t){
  const items = (t.item_list || t.items || []).map(i => ({...i, tier: i.tier || i.permission_tier}));
  const tl = (t.results || []).map(r =>
    `<div class="small">· ${esc(r.metric || r.item_id || "")} → ${esc(r.status || "—")}` +
    `${r.observation ? " · " + esc(JSON.stringify(r.observation).slice(0, 120)) : ""}</div>`).join("");
  const hist = (t.history || []).map(h =>
    `<div class="small">· ${esc(h.ts)} ${esc(h.action)} ${esc(h.actor || "")}` +
    `${h.metric ? " · " + esc(h.metric) : ""}${h.note ? " · " + esc(h.note) : ""}</div>`).join("");
  $("batchDetail").innerHTML = `<div style="padding:10px 15px;color:var(--muted);font-size:12px">` +
    `工单 <b class="mono">${esc(t.ticket_id)}</b> · 状态 ${esc(t.status)} · 校验码 <b class="mono">${esc(t.approve_token || "—")}</b>` +
    (t.change_request ? ` · <span class="bad">退回意见：${esc(t.change_request)}</span>` : "") +
    `</div>` +
    (items.length ? `<table><thead><tr><th>指标</th><th>波形</th><th>指纹</th><th>playbook</th><th>权限</th><th>状态</th><th>计划</th><th>单条批准</th><th>原因</th></tr></thead><tbody>` +
      items.map(i => {
        const files = (i.plan_files || []).join(", ");
        const ap = i.human_approved ? "已单批"
          : (i.status === "rejected" ? "已驳回"
          : (i.approve_token ? `<button class="itemBtn" data-tid="${esc(t.ticket_id)}" data-iid="${esc(i.item_id)}">批准 ${esc(i.approve_token)}</button>` +
             `<button class="itemRejBtn" data-tid="${esc(t.ticket_id)}" data-iid="${esc(i.item_id)}">驳回</button>` : "—"));
        return `<tr><td class="mono">${esc(i.metric)}</td><td>${esc(i.shape)}</td>` +
        `<td>${i.fingerprint_hit ? "命中" : "未命中"}</td><td class="mono">${esc(i.playbook_id || "—")}</td>` +
        `<td>${esc(i.tier)}</td><td>${esc(i.status)}</td>` +
        `<td class="small"><details><summary>${esc(files || "—")}${i.diff_count ? " (" + i.diff_count + ")" : ""}</summary>` +
        `${(i.plan_diffs || []).map(d => `<div class="diff"><b>${esc(d.file)}</b>` +
          `${d.why ? "<br><i>" + esc(d.why) + "</i>" : ""}` +
          `<pre>- ${esc(d.find || "")}\n+ ${esc(d.replace || "")}</pre></div>`).join("")}</details></td>` +
        `<td>${ap}</td><td class="small">${esc(i.error || "—")}${i.change_request ? " · 上轮意见：" + esc(i.change_request) : ""}</td></tr>`;
      }).join("") +
      `</tbody></table>` : `<div class="empty">无候选。</div>`) +
    (tl ? `<div style="padding:8px 15px;border-top:1px solid #182333"><b class="small">执行时间线</b>${tl}</div>` : "") +
    (hist ? `<div style="padding:8px 15px;border-top:1px solid #182333"><b class="small">审批历史</b>${hist}</div>` : "");
}
async function probeAction(act){
  try{
    let path, body = {};
    if(act.startsWith("resident-")){
      path = "/api/resident/" + act.slice("resident-".length);
      body = {interval: 60};
    }else{
      path = "/api/probe/" + (act.startsWith("sample") ? "sample" : act);
      body = act === "sample-on" ? {on:true, window:300}
           : act === "sample-off" ? {on:false}
           : {};
    }
    const r = await post(path, body);
    alert((r.detail || r.error || "完成"));
    await refresh();
  }catch(e){ alert("操作失败：" + e.message); }
}
let approvalCtx = null;
function openApproval(tid, act, iid){
  const t = batchCache[tid] || {};
  const isItem = act === "item-approve" || act === "item-reject";
  const it = isItem ? (t.item_list || []).find(x => x.item_id === iid) : null;
  approvalCtx = isItem ? {kind: act === "item-reject" ? "item-reject" : "item",
                          tid, iid} : {kind:"ticket", tid, act};
  $("approvalTitle").textContent = isItem
    ? `${act} · ${(it && it.metric) || iid}` : `${act} · ${tid}`;
  $("appName").value = ""; $("appNote").value = ""; $("appError").textContent = "";
  $("appToken").value = isItem ? ((it && it.approve_token) || "") : (t.approve_token || "");
  $("appTokenRow").style.display = (act === "reject" || act === "item-reject"
    || act === "request_changes") ? "none" : "";
  $("approvalPanel").style.display = "";
  $("approvalPanel").scrollIntoView({behavior:"smooth", block:"nearest"});
  $("appName").focus();
}
function hideApproval(){ approvalCtx = null; $("approvalPanel").style.display = "none"; }
async function confirmApproval(){
  if(!approvalCtx) return;
  const approver = $("appName").value.trim();
  const note = $("appNote").value.trim();
  const token = $("appToken").value.trim();
  if(!approver || !note){ $("appError").textContent = "批准人和批语必填"; return; }
  const noToken = (approvalCtx.kind === "item-reject"
    || approvalCtx.act === "reject" || approvalCtx.act === "request_changes");
  if(!noToken && !token){ $("appError").textContent = "校验码必填"; return; }
  try{
    let r;
    if(approvalCtx.kind === "item"){
      r = await post(`/api/tickets/${encodeURIComponent(approvalCtx.tid)}/items/${encodeURIComponent(approvalCtx.iid)}/approve`,
                     {approver, note, token});
      if(r.ticket) showTicketDetail(r.ticket);
    }else if(approvalCtx.kind === "item-reject"){
      r = await post(`/api/tickets/${encodeURIComponent(approvalCtx.tid)}/items/${encodeURIComponent(approvalCtx.iid)}/reject`,
                     {approver, note});
      if(r.ticket) showTicketDetail(r.ticket);
    }else if(approvalCtx.act === "approve"){
      r = await post(`/api/tickets/${encodeURIComponent(approvalCtx.tid)}/approve`,
                     {approver, note, token});
    }else if(approvalCtx.act === "request_changes"){
      r = await post(`/api/tickets/${encodeURIComponent(approvalCtx.tid)}/request_changes`,
                     {approver, note});
    }else{
      r = await post(`/api/tickets/${encodeURIComponent(approvalCtx.tid)}/reject`,
                     {approver, note});
    }
    hideApproval();
    await refresh();
  }catch(e){ $("appError").textContent = "失败：" + e.message; }
}
async function ticketAction(tid, act, iid){
  if(act === "execute"){
    try{
      const r = await post(`/api/tickets/${encodeURIComponent(tid)}/execute`, {});
      alert(r.ticket ? "执行完成（unverified）" : "已提交");
      await refresh();
    }catch(e){ alert("执行失败：" + e.message); }
    return;
  }
  openApproval(tid, act, iid);
}

function renderProjects(s){
  const ps = s.projects || {}, entries = ps.entries || [], active = ps.active;
  const sel = $("projectSelect");
  if(sel){
    const selectable = entries.filter(e => e.status !== "archived");
    sel.innerHTML = selectable.length
      ? `<option value="">选择工程…</option>` + selectable.map(e =>
          `<option value="${esc(e.project_id)}"${e.project_id === active ? " selected" : ""}>${esc(e.display_name)}</option>`).join("")
      : `<option value="">未注册工程</option>`;
  }
  $("projectListMeta").textContent = `${entries.length} 个 · 当前 ${active || "无"}`;
  $("projectBody").innerHTML = entries.length ? entries.map(e => {
    const v = e.validation || {};
    const state = e.status === "archived" ? "已归档" : (v.ok ? "有效" : "校验失败");
    const ops = [
      e.status !== "archived" ? `<button class="prjbtn" data-pact="activate" data-pid="${esc(e.project_id)}">切换</button>` : "",
      `<button class="prjbtn" data-pact="validate" data-pid="${esc(e.project_id)}">校验</button>`,
      e.status !== "archived" ? `<button class="prjbtn" data-pact="archive" data-pid="${esc(e.project_id)}">归档</button>` : "",
      `<button class="prjbtn" data-pact="delete" data-pid="${esc(e.project_id)}">删除</button>`,
    ].join("");
    return `<tr><td>${esc(e.display_name)}<div class="mono" style="color:var(--dim);font-size:10.5px">${esc(e.project_id)}</div></td>` +
      `<td class="mono small">${esc(e.project_path)}</td><td class="mono small">${esc(e.default_scene || "—")}</td>` +
      `<td>${esc(state)}${v.errors && v.errors.length ? `<div class="small" style="color:var(--amber)">${esc(v.errors.join("; "))}</div>` : ""}</td>` +
      `<td class="mono small">${esc(e.last_used_at || "—")}</td><td>${ops}</td></tr>`;
  }).join("") : `<tr><td colspan="6" class="empty">暂无工程，请在下方新增。</td></tr>`;
}
async function activateProject(pid){
  if(!pid) return;
  try{
    await post(`/api/projects/${encodeURIComponent(pid)}/activate`, {});
    await refresh();
  }catch(e){ alert("切换失败：" + e.message); }
}
async function projectAction(act, pid){
  if(act === "delete" && !confirm(`确认删除工程 ${pid}？有历史 run 会被拒绝，只能归档。`)) return;
  try{
    await post(`/api/projects/${encodeURIComponent(pid)}/${act}`, {});
    await refresh();
  }catch(e){ alert("操作失败：" + e.message); }
}
async function addProject(){
  const project_path = $("newProjectPath").value.trim();
  if(!project_path){ $("projectMsg").textContent = "工程目录必填"; return; }
  $("projectMsg").textContent = "校验中…";
  try{
    const r = await post("/api/projects", {
      project_path,
      display_name: $("newProjectName").value.trim(),
      default_scene: $("newProjectScene").value.trim(),
      quality_tier: $("newProjectQuality").value.trim() || "urp-balanced",
      replay_command: $("newProjectReplay").value.trim(),
    });
    $("projectMsg").textContent = `已新增：${(r.project || {}).display_name || ""}`;
    await refresh();
  }catch(e){ $("projectMsg").textContent = "新增失败：" + e.message; }
}

function setClass(el, cls){ el.className = "val " + (cls || ""); }

function render(s){
  const d = s.daemon || {}, p = s.project || {}, pr = s.probe || {}, tk = s.tickets || [];
  $("mode").textContent = s.mode === "regression" ? "回归" : "常驻";
  setClass($("daemonVal"), d.running ? "ok" : "bad");
  $("daemonVal").textContent = d.running ? "运行中" : "未启动";
  $("daemonMeta").textContent = d.running
    ? `pid=${d.pid || "—"} · 阶段=${d.phase || "—"} · 采样=${d.sampling || "—"}`
    : (d.error || "python -m upi start 后台启动；或 I3 在界面内启动");
  setClass($("projectVal"), p.error ? "warn" : "cyan");
  $("projectVal").textContent = p.name || "—";
  $("projectMeta").textContent = p.error || `${p.id || "—"} · ${p.path || ""}`;
  setClass($("probeVal"), pr.state === "alive" ? "ok" : (pr.state === "stale" ? "warn" : "bad"));
  $("probeVal").textContent = pr.state === "alive" ? "在线" : (pr.state === "stale" ? "心跳超龄" : "无心跳");
  $("probeMeta").textContent = (pr.heartbeat_age_s == null
    ? "未安装/未运行探针（I1 接单探针）" : `心跳 ${pr.heartbeat_age_s}s 前 · ${pr.heartbeat}`)
    + ` · 事件 ${pr.events_total || 0}（breach ${(pr.by_kind||{}).breach || 0} / sample ${(pr.by_kind||{}).sample || 0}）`;
  $("probeTop").textContent = pr.state === "alive" ? "在线" : "离线";
  const rs = s.resident_scheduler || {};
  $("schedMeta").textContent = `巡检：${rs.running ? "开" : "关"} · tick ${rs.tick_count || 0}` +
    ` · 最近 ${rs.last_ticket || "—"}` + (rs.last_error ? ` · 错误 ${rs.last_error}` : "");
  $("ticketVal").textContent = tk.length ? (tk.length + " 张") : "0";
  $("ticketVal").className = "val " + (tk.length ? "warn" : "ok");
  $("ticketMeta").textContent = tk.length ? "在「工单」页逐项批准（I3 接入）" : "没有待批工单";
  $("ticketTop").textContent = tk.length;
  $("updated").textContent = "更新于 " + (s.time || "—");
  const feed = $("feed");
  const daemonEvs = (s.events || []).map(e => ({...e, _src:"daemon"}));
  const probeEvs = (s.probe_events || []).map(e => ({...e, _src:"probe"}));
  const evs = [...daemonEvs, ...probeEvs]
      .sort((a,b) => String(b.ts || "").localeCompare(String(a.ts || "")))
      .slice(0, 12);
  feed.innerHTML = evs.length ? evs.map(e => {
    const ts = (e.ts || e.time || "").toString().slice(11,19);
    const kind = e.kind || e.type || "event";
    const detail = e._src === "probe"
      ? (e.metric
          ? `${e.metric}=${e.value}${e.unit || ""}` + (e.series_n ? ` · n=${e.series_n}` : "")
          : `metrics=${Object.keys(e.metrics || {}).length}`)
      : (e.error || e.reason || e.run_id || e.detail || "");
    const label = (e._src === "probe" ? "probe/" : "") + kind;
    return `<div class="ev"><div class="t">${esc(ts)}</div><div class="n">${esc(label)}</div>` +
           (detail ? `<div class="d">${esc(detail)}</div>` : "") + `</div>`;
  }).join("") : `<div class="empty">暂无事件。启动探针/守护后，这里会实时出现。</div>`;
  const box = $("tickets");
  box.innerHTML = tk.length ? tk.map(t =>
    `<div class="ticket"><div><div class="id">${esc(t.request_id)}</div>` +
    `<div class="d" style="color:var(--muted);font-size:11.5px">${esc(t.run_id)}</div></div>` +
    `<span class="pill warn">待批</span></div>`).join("")
    : `<div class="empty">没有待批工单。</div>`;

  const cl = s.clusters || [];
  $("clusterMeta").textContent = `${s.cluster_total || cl.length} 个 cluster`;
  $("clusterBody").innerHTML = cl.length ? cl.map(c => {
    const vals = (c.values || []).slice(0, 4).map(v => Number(v).toFixed(2)).join(" / ");
    const shape = esc(c.shape || "unknown");
    return `<tr><td class="mono">${esc(c.metric)}</td>` +
           `<td><span class="badge ${shape}">${shape}</span></td>` +
           `<td class="mono">${esc(c.first_rel)}${c.last_rel !== c.first_rel ? "–" + esc(c.last_rel) : ""}</td>` +
           `<td>${esc(c.count)}</td><td class="mono">${esc(c.data_tier)}</td>` +
           `<td class="mono">${esc(vals || "—")}</td></tr>`;
  }).join("") : `<tr><td colspan="6" class="empty">暂无 cluster。启动探针后这里会出现聚类结果。</td></tr>`;

  const fp = s.fingerprints || {}, fr = s.fingerprint_records || [];
  $("fpMeta").textContent = `${fp.total || 0} pattern · 命中 ${fp.hits || 0} · 未命中 ${fp.misses || 0} · 成功 ${fp.success || 0} / 失败 ${fp.fail || 0}`;
  $("fpBody").innerHTML = fr.length ? fr.map(r =>
    `<tr><td class="mono">${esc(r.pattern_id)}</td>` +
    `<td class="mono">${esc(r.features && r.features.metric)}</td>` +
    `<td><span class="badge ${esc(r.features && r.features.shape || "unknown")}">${esc(r.features && r.features.shape || "unknown")}</span></td>` +
    `<td>${esc(r.source)}</td><td>${esc(r.n_hits)}</td>` +
    `<td>${esc(r.n_success)} / ${esc(r.n_fail)}</td>` +
    `<td class="mono">${esc(r.last_seen || "—")}</td></tr>`
  ).join("") : `<tr><td colspan="7" class="empty">指纹库为空。</td></tr>`;

  renderTickets(s);
  renderRegression(s);
  renderReports(s);
  renderProjects(s);
  renderSettings(s);
}

async function refresh(){
  try{
    const r = await fetch(q("/api/status"), {cache:"no-store"});
    if(!r.ok) throw new Error("HTTP " + r.status);
    render(await r.json());
  }catch(e){
    $("updated").textContent = "连接失败：" + e.message;
  }
}

function nav(page, label){
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.page === page));
  const home = $("page-home"), issues = $("page-issues"), library = $("page-library"),
        labels = $("page-labels"),
        tickets = $("page-tickets"), regression = $("page-regression"),
        reports = $("page-reports"), projects = $("page-projects"),
        settings = $("page-settings"), other = $("page-other");
  const pages = {home, issues, library, labels, tickets, regression, reports, projects, settings};
  Object.values(pages).forEach(p => { if(p) p.style.display = "none"; });
  if(other) other.style.display = "none";
  if(pages[page]){ pages[page].style.display = ""; if(page === "labels") loadLabels(); return; }
  if(other) other.style.display = "";
  $("otherTitle").textContent = "此页待接入";
  $("otherHint").textContent = "I5 逐项接入中；当前已交付问题/指纹/工单/回归/报告/设置。";
}

document.querySelectorAll("#nav a").forEach(a => a.addEventListener("click", ev => {
  ev.preventDefault(); nav(a.dataset.page, a.textContent.trim());
}));
const buildBtn = $("buildTicketBtn");
if(buildBtn) buildBtn.addEventListener("click", buildTicket);
const labelRefreshBtn = $("labelRefresh");
if(labelRefreshBtn) labelRefreshBtn.addEventListener("click", loadLabels);
const oppBtn = $("oppTicketBtn");
if(oppBtn) oppBtn.addEventListener("click", buildOpportunityTicket);
const candBtn = $("candTicketBtn");
if(candBtn) candBtn.addEventListener("click", buildCandidateTicket);
const createSuiteBtn = $("createSuiteBtn");
if(createSuiteBtn) createSuiteBtn.addEventListener("click", createSuite);
const projectSelect = $("projectSelect");
if(projectSelect) projectSelect.addEventListener("change", ev => activateProject(ev.target.value));
const addProjectBtn = $("addProjectBtn");
if(addProjectBtn) addProjectBtn.addEventListener("click", addProject);
const appConfirm = $("appConfirm");
if(appConfirm) appConfirm.addEventListener("click", confirmApproval);
const appCancel = $("appCancel");
if(appCancel) appCancel.addEventListener("click", hideApproval);
document.addEventListener("click", (ev) => {
  const pb = ev.target.closest && ev.target.closest(".pbtn");
  if(pb){ probeAction(pb.dataset.pact); return; }
  const rb = ev.target.closest && ev.target.closest(".rbtn");
  if(rb){ recordRun(rb.dataset.rid); return; }
  const vb = ev.target.closest && ev.target.closest(".vbtn");
  if(vb){ verifyLast(vb.dataset.vid); return; }
  const bb = ev.target.closest && ev.target.closest(".bbtn");
  if(bb){ runBaseline(bb.dataset.bid); return; }
  const gb = ev.target.closest && ev.target.closest(".gbtn");
  if(gb){ genReport(gb.dataset.gid); return; }
  const ib = ev.target.closest && ev.target.closest(".itemBtn");
  if(ib){ ticketAction(ib.dataset.tid, "item-approve", ib.dataset.iid); return; }
  const irb = ev.target.closest && ev.target.closest(".itemRejBtn");
  if(irb){ ticketAction(irb.dataset.tid, "item-reject", irb.dataset.iid); return; }
  const prj = ev.target.closest && ev.target.closest(".prjbtn");
  if(prj){ projectAction(prj.dataset.pact, prj.dataset.pid); return; }
  const pat = ev.target.closest && ev.target.closest(".patBtn");
  if(pat){ setPatternTier(pat.dataset.pid, pat.dataset.tier); return; }
  const b = ev.target.closest && ev.target.closest(".tbtn");
  if(!b) return;
  const tid = b.dataset.tid, act = b.dataset.act;
  if(!tid) return;
  if(act === "view"){
    const t = batchCache[tid];
    if(t) showTicketDetail(t);
  }else if(act === "approve" || act === "reject" || act === "request_changes"){
    ticketAction(tid, act);
  }
});

refresh();
setInterval(refresh, 5000);
try{
  const es = new EventSource(q("/api/events"));
  es.onmessage = (m) => { try{ render(JSON.parse(m.data)); }catch(_){} };
  es.onerror = () => { $("updated").textContent = "事件流断开，轮询重试中…"; };
}catch(_){}
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "UPIWeb/0.1"

    def log_message(self, fmt, *args):  # noqa: A003 - 标准库签名
        return

    @property
    def ui(self) -> "WebUI":
        return self.server.ui  # type: ignore[attr-defined]

    def _authorized(self, qs: dict) -> bool:
        token = (qs.get("token") or [""])[0]
        if token and secrets.compare_digest(token, self.ui.token):
            return True
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            morsel = cookie.get("upi_token")
        except Exception:  # noqa: BLE001 - 坏 cookie 不得变成 500
            return False
        return bool(morsel and secrets.compare_digest(morsel.value, self.ui.token))

    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_GET(self):  # noqa: N802 - 标准库签名
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if not self._authorized(qs):
            self._send(403, b"forbidden: missing/invalid token", "text/plain; charset=utf-8")
            return
        if parsed.path == "/":
            html = _INDEX_HTML.replace("__UPI_TOKEN__", self.ui.token).encode("utf-8")
            self._send(200, html, "text/html; charset=utf-8",
                       {"Set-Cookie": f"upi_token={self.ui.token}; Path=/; HttpOnly; SameSite=Strict"})
            return
        if parsed.path == "/api/status":
            self._send(200, _json_bytes(build_status()), "application/json; charset=utf-8")
            return
        if parsed.path == "/repair":
            from upi.agents.repair_ui import REPAIR_HTML
            html = REPAIR_HTML.replace("__UPI_TOKEN__", self.ui.token).encode("utf-8")
            self._send(200, html, "text/html; charset=utf-8",
                       {"Set-Cookie": f"upi_token={self.ui.token}; Path=/; HttpOnly; SameSite=Strict"})
            return
        if parsed.path == "/api/repair/trees":
            from upi.agents.repair_ui import trees
            self._send_json(200, {"ok": True, "trees": trees()})
            return
        if parsed.path == "/api/repair/budget":
            from upi.agents.repair_ui import budget
            self._send_json(200, {"ok": True, "budget": budget()})
            return
        if parsed.path == "/api/blacklist":
            from upi.agents.repair_ui import blacklist_entries
            self._send_json(200, {"ok": True, "entries": blacklist_entries()})
            return
        if parsed.path == "/api/events":
            self._sse()
            return
        if parsed.path == "/api/projects":
            self._send_json(200, {"ok": True, **_projects_payload()})
            return
        if parsed.path == "/api/labels":
            try:
                from upi.config.project_profile import active_profile
                from upi.core.labels import LabelStore
                pid = active_profile().profile_id
                store = LabelStore(pid)
                self._send_json(200, {"ok": True, "project_id": pid,
                                      "pending": store.pending(),
                                      "active": store.active(),
                                      "rejected": store.list("rejected")})
            except Exception as e:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(e)})
            return
        if parsed.path == "/api/capture/config":
            try:
                from upi.core import capture_config as CC
                self._send_json(200, {"ok": True, "config": CC.load(),
                                      "defaults": CC.DEFAULTS,
                                      "limits": {k: list(v) for k, v in CC.LIMITS.items()},
                                      "probe_config": str(CC.PROBE_CONFIG_PATH)})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        m = re.match(r"^/api/regression/suites/(rs-[A-Za-z0-9_\-]+)/report$",
                     parsed.path)
        if m:
            try:
                from upi.core.regression import RegressionStore
                from upi.core.regression_report import report_path
                store = RegressionStore(os.environ.get("UPI_REGRESSION_DIR") or None)
                p = report_path(store, m.group(1))
                if not p.exists():
                    self._send(404, b"report not generated", "text/plain; charset=utf-8")
                    return
                self._send(200, p.read_bytes(), "text/html; charset=utf-8")
            except Exception as e:  # noqa: BLE001
                self._send(500, f"report error: {e}".encode("utf-8"),
                           "text/plain; charset=utf-8")
            return
        if parsed.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            for _ in range(30):                 # 约 60s；浏览器会自动重连
                payload = _json_bytes(build_status()).decode("utf-8")
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _send_json(self, code: int, obj):
        self._send(code, _json_bytes(obj), "application/json; charset=utf-8")

    def _body_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        raw = self.rfile.read(n) if n > 0 else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_POST(self):  # noqa: N802 - 标准库签名
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if not self._authorized(qs):
            self._send(403, b"forbidden: missing/invalid token", "text/plain; charset=utf-8")
            return
        try:
            body = self._body_json()
        except Exception as e:  # noqa: BLE001
            self._send_json(400, {"ok": False, "error": f"bad json: {e}"})
            return

        if parsed.path == "/api/projects":
            try:
                from upi.config.project_registry import ProjectRegistry
                reg = ProjectRegistry()
                tags = body.get("tags") or []
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                e = reg.add(
                    project_path=str(body.get("project_path") or ""),
                    display_name=str(body.get("display_name") or ""),
                    default_scene=str(body.get("default_scene") or ""),
                    quality_tier=str(body.get("quality_tier") or "urp-balanced"),
                    replay_mode=str(body.get("replay_mode") or "none"),
                    replay_command=str(body.get("replay_command") or ""),
                    tags=list(tags), notes=str(body.get("notes") or ""))
                self._send_json(200, {"ok": True, "project": e.model_dump(mode="json"),
                                      **_projects_payload()})
            except Exception as e:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/labels/decision":
            try:
                from upi.config.project_profile import active_profile
                from upi.core.labels import LabelStore
                pid = active_profile().profile_id
                store = LabelStore(pid)
                okd, msg = store.decide(
                    str(body.get("label_id") or ""),
                    decision=str(body.get("decision") or ""),
                    reviewer=str(body.get("reviewer") or "user"),
                    reason=str(body.get("reason") or ""))
                self._send_json(200 if okd else 400,
                                {"ok": okd, "result": msg,
                                 "label": store.get(str(body.get("label_id") or ""))})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        m = re.match(r"^/api/projects/([A-Za-z0-9_\-]+)/(activate|validate|archive|delete|update)$",
                     parsed.path)
        if m:
            pid, action = m.group(1), m.group(2)
            try:
                if action in {"activate", "archive", "delete"}:
                    block = _project_switch_blocked()
                    if block:
                        self._send_json(409, {"ok": False, "error": f"{block}，不能切换/归档工程"})
                        return
                from upi.config.project_registry import ProjectRegistry
                reg = ProjectRegistry()
                if action == "activate":
                    e = reg.activate(pid)
                elif action == "validate":
                    e = reg.update(pid)
                elif action == "archive":
                    e = reg.archive(pid)
                elif action == "delete":
                    reg.delete(pid, force=bool(body.get("force", False)))
                    self._send_json(200, {"ok": True, **_projects_payload()})
                    return
                else:
                    fields = {k: body.get(k) for k in (
                        "display_name", "default_scene", "quality_tier",
                        "replay_mode", "replay_command", "tags", "notes")}
                    if isinstance(fields.get("tags"), str):
                        fields["tags"] = [t.strip() for t in fields["tags"].split(",") if t.strip()]
                    e = reg.update(pid, **fields)
                self._send_json(200, {"ok": True, "project": e.model_dump(mode="json"),
                                      **_projects_payload()})
            except ValueError as e:
                self._send_json(409, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        if parsed.path == "/api/tickets/opportunities":
            # I9e：静态机会扫描只是线索，不再建单（避免“按代码文本匹配”进入修复链）
            self._send_json(409, {
                "ok": False,
                "error": "静态扫描仅作线索，不建单；请基于运行证据生成候选/工单（I9e）"})
            return

        if parsed.path == "/api/tickets/candidates":
            try:
                t = _build_candidate_ticket_latest()
                self._send_json(200, {"ok": True, "ticket": t.model_dump(),
                                      "summary": t.summary()})
            except Exception as e:  # noqa: BLE001
                self._send_json(409, {"ok": False, "error": str(e)[:240]})
            return

        if parsed.path == "/api/tickets/build":
            try:
                from upi.core.resident import ResidentEngine
                eng = ResidentEngine(
                    fp_store=None,
                    ticket_store=None,
                )
                t = eng.build_ticket(plan_provider=None, limit=8)
                self._send_json(200, {"ok": True, "ticket": t.model_dump()})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        m = re.match(r"^/api/regression/suites/(rs-[A-Za-z0-9_\-]+)/report$",
                     parsed.path)
        if m:
            try:
                from upi.core.regression import RegressionStore
                from upi.core.regression_report import write_report
                store = RegressionStore(os.environ.get("UPI_REGRESSION_DIR") or None)
                sid = m.group(1)
                if store.load_suite(sid) is None:
                    self._send_json(404, {"ok": False, "error": "suite not found"})
                    return
                p = write_report(store, sid)
                self._send_json(200, {"ok": True, "path": str(p),
                                      "url": f"/api/regression/suites/{sid}/report"})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        if parsed.path == "/api/regression/suites":
            try:
                from upi.core.regression import RegressionStore, ReplaySuite
                store = RegressionStore(os.environ.get("UPI_REGRESSION_DIR") or None)
                sid = str(body.get("suite_id") or ("rs-" + secrets.token_hex(3)))
                if store.load_suite(sid):
                    self._send_json(409, {"ok": False, "error": "suite already exists"})
                    return
                suite = ReplaySuite(
                    suite_id=sid, name=str(body.get("name") or sid),
                    project_id=(body.get("project_id") or _project_status().get("id") or ""),
                    project=str(body.get("project") or ""),
                    scene=str(body.get("scene") or ""),
                    replay_script=str(body.get("replay_script") or ""),
                    quality_tier=str(body.get("quality_tier") or ""),
                    command=str(body.get("command") or ""),
                    protocol_fingerprint=str(body.get("protocol_fingerprint") or "manual"),
                    created_at=E.now())
                store.save_suite(suite)
                self._send_json(200, {"ok": True, "suite": suite.model_dump(),
                                      "summary": store.summary(sid)})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        m = re.match(r"^/api/regression/suites/(rs-[A-Za-z0-9_\-]+)/run-baseline$",
                     parsed.path)
        if m:
            try:
                from upi.core.regression import RegressionRunner, RegressionStore
                from upi.core.replay_driver import driver_for
                store = RegressionStore(os.environ.get("UPI_REGRESSION_DIR") or None)
                suite = store.load_suite(m.group(1))
                if suite is None:
                    self._send_json(404, {"ok": False, "error": "suite not found"})
                    return
                if not (suite.command or "").strip():
                    self._send_json(400, {"ok": False,
                                          "error": "suite.command 为空：先配置 replay 命令"})
                    return
                n = max(3, min(10, int(body.get("runs", 3) or 3)))
                runner = RegressionRunner(
                    suite, store, driver_for(suite).run_once,
                    align=bool(body.get("align", True)))
                mtx = runner.collect_baseline(n)
                self._send_json(200, {"ok": True, "runs": len(mtx.runs),
                                      "summary": store.summary(suite.suite_id)})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        m = re.match(r"^/api/regression/suites/(rs-[A-Za-z0-9_\-]+)/verify$",
                     parsed.path)
        if m:
            try:
                from upi.core.regression import RegressionStore
                store = RegressionStore(os.environ.get("UPI_REGRESSION_DIR") or None)
                res = store.verify_last(m.group(1))
                self._send_json(200 if res.get("ok") else 400, res)
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        m = re.match(r"^/api/regression/suites/(rs-[A-Za-z0-9_\-]+)/record$",
                     parsed.path)
        if m:
            try:
                from upi.core.probe_events import EventLog
                from upi.core.regression import RegressionStore
                store = RegressionStore(os.environ.get("UPI_REGRESSION_DIR") or None)
                r = store.record_run(m.group(1), EventLog(),
                                     protocol=body.get("protocol_fingerprint"))
                if r is None:
                    self._send_json(404, {"ok": False, "error": "suite not found"})
                    return
                self._send_json(200, {"ok": True, "run": r.model_dump(),
                                      "summary": store.summary(m.group(1))})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        if parsed.path in ("/api/resident/start", "/api/resident/stop",
                           "/api/resident/tick"):
            try:
                from upi.core.scheduler import get_scheduler
                sch = get_scheduler()
                if parsed.path.endswith("/start"):
                    res = sch.start(float(body.get("interval", 60) or 60))
                    self._send_json(200, {"ok": True, "scheduler": res})
                elif parsed.path.endswith("/stop"):
                    res = sch.stop()
                    self._send_json(200, {"ok": True, "scheduler": res})
                else:
                    t = sch.tick()
                    self._send_json(200, {"ok": True, "scheduler": sch.status(),
                                          "ticket": t.model_dump() if t else None})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        if parsed.path == "/api/capture/config":
            try:
                from upi.core import capture_config as CC
                cfg = CC.save(body)
                CC.write_probe_config()
                self._send_json(200, {"ok": True, "config": cfg,
                                      "probe_config": str(CC.PROBE_CONFIG_PATH)})
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        m = re.match(r"^/api/knowledge/patterns/(pat-[A-Za-z0-9_\-]+)/tier$",
                     parsed.path)
        if m:
            pid, tier = m.group(1), str(body.get("tier", "")).strip()
            try:
                from upi.core.knowledge import KnowledgeStore, default_path
                if tier not in ("semi", "shadow"):
                    raise ValueError("只能人工切 semi/shadow；auto 必须走升维提案")
                ks = KnowledgeStore(default_path())
                res = ks.set_tier(pid, tier, actor=str(body.get("actor", "ui")),
                                  reason=str(body.get("reason", "UI 手动调整档位")))
                ks.close()
                self._send_json(200, {"ok": True, "change": res})
            except KeyError as e:
                self._send_json(404, {"ok": False, "error": str(e)})
            except ValueError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        if parsed.path in ("/api/probe/install", "/api/probe/unload",
                           "/api/probe/sample"):
            try:
                from upi.tooling import probe_control as PC
                if parsed.path.endswith("/install"):
                    res = PC.install()
                elif parsed.path.endswith("/unload"):
                    res = PC.unload()
                else:
                    from upi.core import capture_config as CC
                    window = int(body.get("window") or CC.load()["probe_sample_window"])
                    res = PC.sample(bool(body.get("on", True)), window)
                self._send_json(200 if res.get("ok") else 400, res)
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        m = re.match(r"^/api/tickets/(bt-[A-Za-z0-9_\-]+)/(approve|reject|request_changes)$",
                     parsed.path)
        if m:
            tid, action = m.group(1), m.group(2)
            try:
                from upi.core.ticket import TicketStore, approve, reject, request_changes
                from upi.core.knowledge import KnowledgeStore, default_path
                store = TicketStore(os.environ.get("UPI_TICKET_DIR") or None)
                ks = KnowledgeStore(default_path())
                t = store.load(tid)
                if t is None:
                    ks.close()
                    self._send_json(404, {"ok": False, "error": "ticket not found"})
                    return
                if action == "approve":
                    ok, msg = approve(t, approver=str(body.get("approver", "")),
                                      note=str(body.get("note", "")),
                                      token=str(body.get("token", "")))
                elif action == "request_changes":
                    ok, msg = request_changes(
                        t, approver=str(body.get("approver", "")),
                        note=str(body.get("note", "")))
                else:
                    ok, msg = reject(t, approver=str(body.get("approver", "")),
                                     note=str(body.get("note", "")),
                                     knowledge_store=ks)
                if ok:
                    store.save(t)
                shadow = ks.shadow_agreement() if ok else None
                ks.close()
                self._send_json(200 if ok else 400, {"ok": ok, "message": msg,
                                                     "shadow_agreement": shadow,
                                                     "ticket": t.model_dump()})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        m = re.match(r"^/api/tickets/(bt-[A-Za-z0-9_\-]+)/items/"
                     r"(it-[A-Za-z0-9_\-]+)/approve$", parsed.path)
        if m:
            tid, iid = m.group(1), m.group(2)
            try:
                from upi.core.ticket import TicketStore, approve_item
                from upi.core.knowledge import KnowledgeStore, default_path
                store = TicketStore(os.environ.get("UPI_TICKET_DIR") or None)
                ks = KnowledgeStore(default_path())
                t = store.load(tid)
                if t is None:
                    ks.close()
                    self._send_json(404, {"ok": False, "error": "ticket not found"})
                    return
                ok, msg = approve_item(
                    t, iid, approver=str(body.get("approver", "")),
                    note=str(body.get("note", "")), token=str(body.get("token", "")),
                    knowledge_store=ks)
                if ok:
                    store.save(t)
                shadow = ks.shadow_agreement() if ok else None
                ks.close()
                self._send_json(200 if ok else 400, {"ok": ok, "message": msg,
                                                     "shadow_agreement": shadow,
                                                     "ticket": t.model_dump()})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        m = re.match(r"^/api/tickets/(bt-[A-Za-z0-9_\-]+)/items/"
                     r"(it-[A-Za-z0-9_\-]+)/reject$", parsed.path)
        if m:
            tid, iid = m.group(1), m.group(2)
            try:
                from upi.core.ticket import TicketStore, reject_item
                store = TicketStore(os.environ.get("UPI_TICKET_DIR") or None)
                t = store.load(tid)
                if t is None:
                    self._send_json(404, {"ok": False, "error": "ticket not found"})
                    return
                ok, msg = reject_item(
                    t, iid, approver=str(body.get("approver", "")),
                    note=str(body.get("note", "")))
                if ok:
                    store.save(t)
                self._send_json(200 if ok else 400, {"ok": ok, "message": msg,
                                                     "ticket": t.model_dump()})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        if re.match(r"^/api/tickets/bt-[A-Za-z0-9_\-]+/execute$", parsed.path):
            try:
                from upi.core.ticket import TicketStore
                from upi.core.unity_exec import UnityTicketExecutor
                store = TicketStore(os.environ.get("UPI_TICKET_DIR") or None)
                tid = parsed.path.split("/")[-2]
                t = store.load(tid)
                if t is None:
                    self._send_json(404, {"ok": False, "error": "ticket not found"})
                    return
                if t.status != "approved":
                    self._send_json(400, {"ok": False,
                                          "error": f"未批准（{t.status}）"})
                    return
                runnable = [i for i in t.items
                            if i.status == "pending" and i.plan
                            and (i.permission_tier == "auto" or i.human_approved)]
                if not runnable:
                    self._send_json(409, {
                        "ok": False,
                        "error": "没有可自动执行的候选（无 plan 或需人工）",
                        "needs_human": sum(1 for i in t.items if i.status == "needs_human"),
                    })
                    return
                out = UnityTicketExecutor(store).execute(t)
                self._send_json(200, {"ok": True, "ticket": out.model_dump()})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        self._send_json(404, {"ok": False, "error": "not found"})


class WebUI:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 token: Optional[str] = None):
        self.host = host
        self.port = int(port)
        self.token = token or secrets.token_urlsafe(18)
        self.httpd: Optional[ThreadingHTTPServer] = None

    def start(self) -> "WebUI":
        self.httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.ui = self  # type: ignore[attr-defined]
        self.port = int(self.httpd.server_address[1])
        return self

    def serve_forever(self):
        if self.httpd is None:
            self.start()
        assert self.httpd is not None
        self.httpd.serve_forever(poll_interval=0.5)

    def stop(self):
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
            self.httpd = None

    def url(self, path: str = "/") -> str:
        sep = "&" if "?" in path else "?"
        return f"http://{self.host}:{self.port}{path}{sep}token={quote(self.token)}"


def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
        open_browser: bool = True, check: bool = False) -> int:
    ui = WebUI(host=host, port=port)
    try:
        ui.start()
    except OSError as e:
        print(f"!! 无法绑定 {host}:{port}（{e}）——换端口：python -m upi ui --port 0")
        return 1

    if check:
        # 真起一次 HTTP，而不是只调 build_status：骨架的验收点是“服务能通”。
        t = threading.Thread(target=ui.serve_forever, daemon=True)
        t.start()
        try:
            with urllib.request.urlopen(ui.url("/api/status"), timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
            # --check 是给 CI/脚本读的：ASCII 转义，避免 Windows CP936 终端
            # 把中文写成 GBK 后调用方按 UTF-8 解析失败。
            print(json.dumps(data, ensure_ascii=True, indent=2))
        except Exception as e:  # noqa: BLE001
            print(f"!! 自检失败: {type(e).__name__}: {e}")
            return 1
        finally:
            ui.stop()
        return 0

    print(f"UPI Web 控制台: {ui.url('/')}")
    print(f"  绑定 {host}:{ui.port}（仅本机）；Ctrl+C 停止界面，守护不受影响")
    if open_browser:
        try:
            webbrowser.open(ui.url("/"))
        except Exception:  # noqa: BLE001
            pass
    try:
        ui.serve_forever()
    except KeyboardInterrupt:
        print("\n界面已停止")
    finally:
        ui.stop()
    return 0

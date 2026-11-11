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
import secrets
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
    return {
        "ok": True,
        "time": E.now(),
        "ui": {"shell": "I2", "single_channel": True},
        "mode": "resident",
        "daemon": _daemon_status(),
        "project": _project_status(),
        "probe": probe,
        "probe_events": pevents,
        "clusters": [c.summary() for c in clusters[:20]],
        "cluster_total": len(clusters),
        "fingerprints": fp_stats,
        "fingerprint_records": fp_records,
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
  <span class="tag">I2 聚类</span>
  <span class="tag">UI = 唯一人机通道</span>
  <div class="spacer"></div>
  <div class="stat">形态 <b id="mode">常驻</b></div>
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
    <a href="#" data-page="reports"><span class="k">▥</span>报告</a>
    <a href="#" data-page="settings"><span class="k">⚙</span>设置</a>
  </nav>
  <main class="main">
    <section id="page-home">
      <h1>现在发生了什么</h1>
      <p class="sub">探针是唯一数据源；1Hz 轮询只做哨兵。所有人类操作都将在这里完成。</p>
      <div class="grid">
        <div class="card"><div class="lab">守护进程</div><div class="val" id="daemonVal">—</div><div class="meta" id="daemonMeta"></div></div>
        <div class="card"><div class="lab">工程</div><div class="val" id="projectVal">—</div><div class="meta" id="projectMeta"></div></div>
        <div class="card"><div class="lab">探针</div><div class="val" id="probeVal">—</div><div class="meta" id="probeMeta"></div></div>
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
        <div class="step"><span class="s">05</span><span>常驻形态：批量工单、串行修复、unverified 账本</span><span class="pill">I3</span></div>
        <div class="step"><span class="s">06</span><span>回归形态：3 次基线、replay 验证、verified 账本</span><span class="pill">I4</span></div>
        <div class="step"><span class="s">07</span><span>全部页面 + 审批 + 回归矩阵 + 报告</span><span class="pill">I5</span></div>
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
  const home = $("page-home"), issues = $("page-issues"), library = $("page-library"), other = $("page-other");
  if(page === "home"){ home.style.display = ""; issues.style.display = "none"; library.style.display = "none"; other.style.display = "none"; return; }
  if(page === "issues"){ home.style.display = "none"; issues.style.display = ""; library.style.display = "none"; other.style.display = "none"; return; }
  if(page === "library"){ home.style.display = "none"; issues.style.display = "none"; library.style.display = ""; other.style.display = "none"; return; }
  home.style.display = "none"; issues.style.display = "none"; library.style.display = "none"; other.style.display = "";
  const titles = {tickets:"工单", regression:"回归", reports:"报告", settings:"设置 / 接入"};
  $("otherTitle").textContent = titles[page] || "此页待接入";
  $("otherHint").textContent = page === "tickets"
    ? "I3 接入：批量候选、批准、串行执行时间线、unverified 账本。"
    : page === "regression"
    ? "I4 接入：replay 套件、3 次基线矩阵、复现率、replay 验证。"
    : "I3~I5 逐项接入；I2 已交付问题聚类与指纹库页。";
}

document.querySelectorAll("#nav a").forEach(a => a.addEventListener("click", ev => {
  ev.preventDefault(); nav(a.dataset.page, a.textContent.trim());
}));

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
        if parsed.path == "/api/events":
            self._sse()
            return
        if parsed.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
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

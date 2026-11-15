"""I25：修复状态树 / 黑名单只读页面与数据。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REPAIR_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>UPI 修复状态</title>
<style>
:root{--bg:#0f1319;--panel:#171d26;--line:#273140;--tx:#dce5f2;--mut:#8798ad;--ok:#68d391;--warn:#f6c177;--bad:#ff7b72;--acc:#7aa2f7}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 ui-sans-serif,system-ui,"Segoe UI",sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center}
h1{font-size:18px;margin:0}.badge{padding:3px 10px;border-radius:999px;background:#1f2b3d;color:var(--acc)}
.wrap{padding:24px;display:grid;gap:18px}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;overflow:auto}
h2{font-size:15px;margin:0 0 12px}table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #222c3a;white-space:nowrap}
th{color:var(--mut);font-weight:500}.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mut{color:var(--mut)}
a{color:var(--acc)}.empty{color:var(--mut);padding:12px 0}
.row{display:flex;gap:8px;flex-wrap:wrap}.row input,textarea{background:#0d1219;color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:7px 9px;font:12px ui-monospace,monospace}
button{background:#22314a;color:var(--tx);border:1px solid #36527a;border-radius:8px;padding:7px 12px;cursor:pointer}
</style></head><body>
<header><h1>修复状态</h1><span class="badge" id="mode">production</span>
<span class="mut" id="hint">只读视图；写操作后续开放</span>
<a href="/?token=__UPI_TOKEN__">返回控制台</a></header>
<div class="wrap">
<div class="card"><h2>预算</h2><div id="budget" class="mono mut">加载中…</div></div>
<div class="card"><h2>Checkpoint 状态树</h2><div id="trees" class="mut">加载中…</div></div>
<div class="card"><h2>黑名单</h2><div id="blacklist" class="mut">加载中…</div></div>
<div class="card"><h2>黑名单命中</h2><div id="skips" class="mut">加载中…</div></div>
<div class="card"><h2>新增黑名单</h2>
<div class="row"><input id="bl-sig" placeholder="signature"><input id="bl-win" placeholder="window">
<input id="bl-metric" placeholder="metric"><input id="bl-proto" placeholder="protocol_fingerprint">
<input id="bl-ttl" placeholder="ttl 秒" type="number"><input id="bl-reason" placeholder="reason">
<button onclick="addBL()">提交 proposed</button></div></div>
<div class="card"><h2>预算</h2><textarea id="budgetEdit" rows="8"></textarea>
<button onclick="saveBudget()">保存预算</button><span id="budgetMsg" class="mut"></span></div>
<div class="card"><h2>审计</h2><div id="audit" class="mut">加载中…</div></div>
<div class="card"><h2>回滚 checkpoint</h2>
<div class="row"><input id="rb-run" placeholder="run_id"><input id="rb-unit" placeholder="unit_id">
<input id="rb-state" placeholder="state_id"><button onclick="rollbackState()">回滚指针</button></div>
<div class="mut">只切换状态指针并审计；不恢复工程文件。</div></div>
</div>
<script>
const TOKEN="__UPI_TOKEN__";
const q=(u)=>u+(u.includes("?")?"&":"?")+"token="+encodeURIComponent(TOKEN);
const esc=(s)=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function get(u){const r=await fetch(q(u),{cache:"no-store"});return await r.json()}
function table(rows,cols){
  if(!rows.length)return '<div class="empty">暂无</div>';
  return '<table><thead><tr>'+cols.map(c=>`<th>${esc(c[0])}</th>`).join('')+'</tr></thead><tbody>'+
    rows.map(r=>'<tr>'+cols.map(c=>`<td class="${c[2]||''}">${c[1](r)}</td>`).join('')+'</tr>').join('')+'</tbody></table>';
}
async function post(u,b){const r=await fetch(q(u),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});return await r.json()}
async function approval(action,target){
 const r=await get("/api/repair/approval?action="+encodeURIComponent(action)+"&target="+encodeURIComponent(target));
 return prompt("审批码（可核对后确认）",r.code||"");
}
async function addBL(){
 const v=(id)=>document.getElementById(id).value;
 const target=[v("bl-sig"),v("bl-win"),v("bl-metric"),v("bl-proto")].join("|");
 const code=await approval("add",target); if(code===null)return;
 const r=await post("/api/blacklist",{action:"add",signature:v("bl-sig"),window:v("bl-win"),
   metric:v("bl-metric"),protocol_fingerprint:v("bl-proto"),ttl:Number(v("bl-ttl")||0),
   reason:v("bl-reason"),status:"proposed",actor:"ui",approval_code:code});
 if(!r.ok)alert(r.error||"失败");else location.reload();
}
async function setBL(id,status){
 const code=await approval("status",id+"|"+status); if(code===null)return;
 const r=await post("/api/blacklist",{action:"status",fingerprint_id:id,status,reason:"ui",actor:"ui",approval_code:code});
 if(!r.ok)alert(r.error||"失败");else location.reload();
}
async function delBL(id){
 if(!confirm("确认删除？"))return;
 const code=await approval("delete",id); if(code===null)return;
 const r=await post("/api/blacklist",{action:"delete",fingerprint_id:id,reason:"ui",actor:"ui",approval_code:code});
 if(!r.ok)alert(r.error||"失败");else location.reload();
}
async function saveBudget(){
 let obj;try{obj=JSON.parse(document.getElementById("budgetEdit").value)}catch(e){alert("JSON 无效");return}
 const code=await approval("budget","budget"); if(code===null)return;
 const r=await post("/api/repair/budget",{budget:obj,actor:"ui",approval_code:code});
 document.getElementById("budgetMsg").textContent=r.ok?"已保存":"失败: "+(r.error||"");
 if(r.ok)location.reload();
}
async function rollbackState(){
 const run=document.getElementById("rb-run").value.trim();
 const unit=document.getElementById("rb-unit").value.trim();
 const state=document.getElementById("rb-state").value.trim();
 if(!run||!unit||!state){alert("run/unit/state 都要填");return}
 const code=await approval("rollback",run+"|"+unit+"|"+state); if(code===null)return;
 const r=await post("/api/repair/rollback",{run_id:run,unit_id:unit,state_id:state,approval_code:code});
 if(!r.ok)alert(r.error||"失败");else{alert("已切换指针，未恢复工程文件");location.reload()}
}
(async()=>{
 try{
  const b=await get("/api/repair/budget");
  document.getElementById("budget").textContent=JSON.stringify(b.budget||b,null,2);
  document.getElementById("budgetEdit").value=JSON.stringify(b.budget||b,null,2);
  const t=await get("/api/repair/trees");
  document.getElementById("trees").innerHTML=table(t.trees||[], [
    ["finding",r=>`<span class="mono">${esc(r.finding_id)}</span>`],
    ["states",r=>esc((r.states||[]).length)],
    ["attempts",r=>esc(r.total_attempts)],
    ["current",r=>`<span class="mono">${esc(r.current)}</span>`],
    ["root_failed",r=>`<span class="${r.root_failed?'bad':'ok'}">${r.root_failed}</span>`],
    ["reason",r=>esc(r.reason||"")],
    ["run",r=>`<span class="mono mut">${esc(r.run_id||"")}</span>`],
  ]);
  const l=await get("/api/blacklist");
  document.getElementById("blacklist").innerHTML=table(l.entries||[], [
    ["signature",r=>`<span class="mono">${esc(r.signature)}</span>`],
    ["window",r=>esc(r.window)],
    ["metric",r=>esc(r.metric)],
    ["status",r=>`<span class="${r.status==='active'?'warn':'mut'}">${esc(r.status)}</span>`],
    ["attempts",r=>esc(r.attempts)],
    ["ttl",r=>esc(r.ttl)],
    ["reason",r=>esc(r.reason||"")],
    ["操作",r=>`<button onclick="setBL('${esc(r.fingerprint_id)}','active')">启用</button>
      <button onclick="setBL('${esc(r.fingerprint_id)}','retired')">停用</button>
      <button onclick="delBL('${esc(r.fingerprint_id)}')">删除</button>`],
  ]);
  const a=await get("/api/repair/audit");
  document.getElementById("audit").innerHTML=table(a.entries||[], [
    ["时间",r=>`<span class="mono mut">${esc(r.ts)}</span>`],
    ["actor",r=>esc(r.actor||"")],
    ["action",r=>esc(r.action||"")],
    ["detail",r=>esc(r.detail||"")],
  ]);
  const s=await get("/api/repair/skips");
  document.getElementById("skips").innerHTML=table(s.skips||[], [
    ["run",r=>`<span class="mono">${esc(r.run_id)}</span>`],
    ["fingerprint",r=>`<span class="mono">${esc(r.fingerprint_id||"")}</span>`],
    ["signature",r=>esc(r.signature||"")],
    ["reason",r=>esc(r.reason||"")],
  ]);
 }catch(e){document.getElementById("trees").innerHTML='<div class="bad">'+esc(e)+'</div>';}
})();
</script></body></html>"""


def trees(limit: int = 20) -> list[dict]:
    out = []
    for p in sorted((ROOT / "runs").rglob("repair_tree.json"),
                    key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        d["run_id"] = p.parent.parent.name
        d["unit_id"] = p.parent.name
        out.append(d)
    return out


def skips(limit: int = 50) -> list[dict]:
    out = []
    for p in sorted((ROOT / "runs").rglob("blacklist_skip.json"),
                    key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        d["run_id"] = p.parent.name
        out.append(d)
    return out


def blacklist_entries() -> list[dict]:
    from upi.core.finding_blacklist import BlacklistStore
    path = ROOT / "runs" / "blacklist.db"
    if not path.is_file():
        return []
    try:
        return [e.__dict__ for e in BlacklistStore(path).all()]
    except Exception:  # noqa: BLE001
        return []


def budget() -> dict:
    from upi.core.repair_tree import load_budget
    return load_budget().as_dict()


def rollback_tree(path: str | Path, state_id: str) -> dict:
    """只切换 repair_tree.json 的 current 指针并审计。

    不恢复工程文件；真实回滚需带 git_ref 的 checkpoint。
    """
    p = Path(path)
    d = json.loads(p.read_text(encoding="utf-8"))
    ids = {str(s.get("state_id")) for s in d.get("states") or []}
    if state_id not in ids:
        raise ValueError(f"state not found: {state_id}")
    from_state = d.get("current")
    from upi.core.repair_tree import RepairTree, restore_checkpoint, save_tree
    tree = RepairTree.from_dict(d)
    result = restore_checkpoint(tree, state_id)
    save_tree(tree, p)
    d = json.loads(p.read_text(encoding="utf-8"))
    d["current"] = state_id
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    audit = ROOT / "runs" / "repair_ui_audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    with audit.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
            "actor": "ui", "action": "rollback_pointer",
            "detail": f"{from_state}->{state_id}",
            "pointer_only": result.get("pointer_only", True),
            "path": str(p)}, ensure_ascii=False) + "\n")
    result["from"] = from_state
    return result


def audit_entries(limit: int = 200) -> list[dict]:
    out = []
    p = ROOT / "runs" / "repair_ui_audit.jsonl"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    bp = ROOT / "runs" / "blacklist.db"
    if bp.is_file():
        try:
            from upi.core.finding_blacklist import BlacklistStore
            for e in BlacklistStore(bp).audit(limit):
                out.append({"ts": e.get("ts", ""), "actor": e.get("actor", ""),
                            "action": e.get("action", ""),
                            "detail": e.get("detail", ""),
                            "fingerprint_id": e.get("fingerprint_id", "")})
        except Exception:  # noqa: BLE001
            pass
    return sorted(out, key=lambda x: str(x.get("ts", "")), reverse=True)[:limit]

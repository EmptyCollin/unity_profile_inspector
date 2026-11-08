"""因子判定链离线测试（不烧真实推理，I13 成本纪律）。

运行：C:/Python314/python.exe scripts/test_factor_judge.py

手法：注入假 client（记录请求、回放预制 JSON），真模型只验"接线"，
     不验"内容"——内容质量属于 M4 评估（人判靠谱率），不是单测职责。
覆盖：连通性门禁、量表映射、无指针作废、档位非法、非 JSON 输出、
     证据编号引用与 anchor: 引用两条路径、公式分离（LLM 分数不进置信度）。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from upi.agents import factor_judge as FJ                      # noqa: E402
from upi.config.settings import load_config                    # noqa: E402
from upi.core.models import Evidence, Issue, IssueSignature    # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def chk(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("  " + extra if extra else ""))


class _Usage:
    prompt_tokens = 1234
    completion_tokens = 210


class _Msg:
    def __init__(self, content):
        self.content = content
        self.tool_calls = None


class _Resp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": _Msg(content)})()]
        self.usage = _Usage()


class FakeClient:
    """记录请求参数；按脚本回放 completions 内容。"""

    class _Completions:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kw):
            self.outer.calls.append(kw)
            return _Resp(self.outer.reply)

    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.chat = type("Chat", (), {"completions": self._Completions(self)})()


def make_issue() -> Issue:
    return Issue(
        signature=IssueSignature(dim="gc_alloc_per_frame", metric="gc_alloc_per_frame",
                                 mechanism="per_frame_heap_alloc_in_hot_path",
                                 threshold=3000.0),
        link="cpu",
        evidence=[Evidence(tool="probe", raw_ref="runs/r1/evidence/a2_f0001.json",
                           value=76947.0, unit="B", data_tier="A2"),
                  Evidence(tool="unity command", raw_ref="runs/r1/evidence/perf_w1.json",
                           value=1.52, unit="ms", noise_band=0.06, data_tier="A")],
        magnitude=68576.0, deviation_ratio=9.19)


GOOD_REPLY = json.dumps({
    "proximity": {"verdict": "yes", "evidence_refs": ["anchor:CombatSkillManager.cs:41"], "reason": "热路径"},
    "explains_share": {"verdict": "yes", "evidence_refs": ["e0"], "reason": "量级对得上"},
    "competitiveness": {"verdict": "likely", "evidence_refs": ["e0"], "reason": "无更强候选"},
    "prior_hit": {"verdict": "unclear", "evidence_refs": [], "reason": "未附知识库"},
    "counterfactual": {"verdict": "likely", "evidence_refs": ["anchor:CombatSkillManager.cs:41-52"], "reason": "去 new 即回落"},
    "mechanism": {"verdict": "yes", "evidence_refs": ["e0", "e1"], "reason": "链条自洽"},
})


def main():
    cfg = load_config(Path(__file__).resolve().parents[1] / "llm_config.yaml")
    issue = make_issue()

    # ---- 1. 连通性门禁：结果文件缺失 → 拒发请求 ----
    orig = FJ.RESULT_PATH_DEFAULT
    FJ.RESULT_PATH_DEFAULT = Path(tempfile.mktemp(suffix=".json"))  # 不存在
    try:
        FJ.judge_factors(cfg, issue, "rc", ["a.cs:1"], client=FakeClient(GOOD_REPLY))
        chk("连通性未过 → JudgeError", False)
    except FJ.JudgeError as e:
        chk("连通性未过 → JudgeError（未发请求）", "从未跑过" in str(e), str(e)[:60])
    FJ.RESULT_PATH_DEFAULT = orig

    # ---- 门禁通过路径：写一个 all_passed=false 与 true 的记录各验一次 ----
    rec_bad = Path(tempfile.mktemp(suffix=".json"))
    rec_bad.write_text(json.dumps({"all_passed": False, "provider": "p", "model": "m"}), encoding="utf-8")
    FJ.RESULT_PATH_DEFAULT = rec_bad
    try:
        FJ.judge_factors(cfg, issue, "rc", [], client=FakeClient(GOOD_REPLY))
        chk("all_passed=false → 拒", False)
    except FJ.JudgeError as e:
        chk("all_passed=false → 拒", "未通过" in str(e))
    FJ.RESULT_PATH_DEFAULT = orig

    # ---- 2. 正常判定：量表映射 + 指针有效性 ----
    fake = FakeClient(GOOD_REPLY)
    with _gate_passed():
        judgments, judged = FJ.judge_factors(cfg, issue, "每帧新建 List 于热路径",
                                             ["CombatSkillManager.cs:41"], client=fake)
    byf = {j.factor: j for j in judgments}
    chk("六因子全收", len(judgments) == 6)
    chk("yes→1.0", byf["proximity"].value == 1.0)
    chk("likely→0.7", byf["competitiveness"].value == 0.7)
    # unclear 档位值由量表保证；prior_hit 答 unclear 且无指针 → 被作废线归零（设计语义）
    chk("unclear 量表=0.4", FJ.FACTOR_SCALE["unclear"] == 0.4)
    chk("unclear 无指针 → 作废归零（防自问自答）", byf["prior_hit"].value == 0.0)
    chk("anchor: 引用保留原样", byf["proximity"].evidence_ref == "anchor:CombatSkillManager.cs:41",
        byf["proximity"].evidence_ref)
    chk("e 编号引用→解析为 raw_ref", byf["explains_share"].evidence_ref == "runs/r1/evidence/a2_f0001.json",
        byf["explains_share"].evidence_ref)
    chk("无指针判定 value→0", byf["prior_hit"].evidence_ref == "" and byf["prior_hit"].value == 0.0)

    # 请求参数合规：json_mode 生效、system 含"禁止输出数值结论"
    call = fake.calls[0]
    chk("json_mode 按配置传入", call.get("response_format") == {"type": "json_object"})
    chk("system 声明置信度非 LLM 职责", "公式计算" in call["messages"][0]["content"])

    # ---- 3. 非法档位 ----
    bogus = json.dumps({**json.loads(GOOD_REPLY),
                        "mechanism": {"verdict": "super-confident", "evidence_refs": ["e0"]}})
    with _gate_passed():
        js, jd = FJ.judge_factors(cfg, issue, "rc", [], client=FakeClient(bogus))
    byf2 = {j.factor: j for j in js}
    chk("非法档位 → value=0 且标注", byf2["mechanism"].value == 0.0
        and "invalid" in jd["mechanism"]["verdict"])

    # ---- 4. 非 JSON 输出 ----
    with _gate_passed():
        try:
            FJ.judge_factors(cfg, issue, "rc", [], client=FakeClient("我觉得大概是 GC"))
            chk("非 JSON 输出 → JudgeError", False)
        except FJ.JudgeError as e:
            chk("非 JSON 输出 → JudgeError", "非 JSON" in str(e))

    # ---- 5. 证据指针防伪：引用不存在的 e99 ----
    fake_ref = json.dumps({**json.loads(GOOD_REPLY),
                           "mechanism": {"verdict": "yes", "evidence_refs": ["e99"]}})
    with _gate_passed():
        js, jd = FJ.judge_factors(cfg, issue, "rc", [], client=FakeClient(fake_ref))
    byf3 = {j.factor: j for j in js}
    chk("引用不存在编号 → 作废", byf3["mechanism"].value == 0.0
        and "作废" in jd["mechanism"]["verdict"])

    # ---- 6. 公式分离：置信度不由 LLM 报 ----
    with _gate_passed():
        hs, archive = FJ.build_hypotheses_for_issue(
            cfg, issue, [("每帧新建 List", ["CombatSkillManager.cs:41"])], client=FakeClient(GOOD_REPLY))
    h = hs[0]
    chk("Hypothesis.confidence 为公式值（非 LLM 输出）",
        h.weights_version == "hand-set-prior-v1" and 0 < h.confidence < 1,
        f"conf={h.confidence}")
    chk("判定留档含 token 用量（I10）", "token_in" in archive["每帧新建 List"]["_meta"],
        str(archive["每帧新建 List"]["_meta"]["token_in"]))

    print(f"\n判定链测试 {len(PASS)}/{len(PASS) + len(FAIL)}"
          + (f"，失败项: {FAIL}" if FAIL else "，全部通过"))
    return 0 if not FAIL else 1


class _gate_passed:
    """临时把连通性结果指到一份 all_passed=true 的记录（不碰真文件）。"""

    def __enter__(self):
        self.orig = FJ.RESULT_PATH_DEFAULT
        rec = Path(tempfile.mktemp(suffix=".json"))
        rec.write_text(json.dumps({"all_passed": True, "provider": "deepseek-official",
                                   "model": "deepseek-flash"}), encoding="utf-8")
        FJ.RESULT_PATH_DEFAULT = rec
        return self

    def __exit__(self, *a):
        FJ.RESULT_PATH_DEFAULT = self.orig


if __name__ == "__main__":
    sys.exit(main())

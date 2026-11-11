"""Review Agent 隔离裁定链离线测试（#37 走 b；不烧真实推理，I13 成本纪律）。

运行：C:/Python314/python.exe scripts/test_review_adjudicate.py

手法与 test_factor_judge 同源：注入假 client 回放预制 JSON——验的是**接线**
（配置真消费、门禁真拦、收紧方向真守），不是模型输出质量。

牙齿清单（每条都对应一个可被违反的纪律）：
  ① 连通性门禁缺失/未过 → 拒发请求（ReviewAdjudicateError，client 零调用）；
  ② llm_config 的 review 段真被读：temperature=0.0 / review_isolated /
     provider+model 从 defaults 合并 —— 这条就是"孤儿配置变实接线"的证明；
  ③ 只能收紧：模型给更紧裁定 → 采纳；给更松裁定 → **丢弃保留规则值**并在
     meta 记 model_overruled（I5/§4：硬门禁不许被 LLM 推翻）；
  ④ 输入白名单：Fix 自辩/自评置信度这类字段**根本不在**渲染出的输入里
     （缺席即隔离，不是"传了让模型别看"）；
  ⑤ 非 JSON / 非对象 → ReviewAdjudicateError（调用方降级回规则）；
  ⑥ 留档含 token 用量与弱隔离声明（I10 可回溯；DESIGN §2.3.5 要求产物
     如实标注"弱隔离（同模型）"，谎称模型级隔离按诚实性问题处理）。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from upi.agents import review_adjudicate as RA                   # noqa: E402
from upi.config.settings import load_config                      # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def chk(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("  " + extra if extra else ""))


class _Usage:
    prompt_tokens = 900
    completion_tokens = 80


class _Msg:
    def __init__(self, content):
        self.content = content


class _Resp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": _Msg(content)})()]
        self.usage = _Usage()


class FakeClient:
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


class _gate_passed:
    """临时把连通性结果指到 all_passed=true 的记录（不碰真文件）。"""

    def __enter__(self):
        self.orig = RA.RESULT_PATH_DEFAULT
        rec = Path(tempfile.mktemp(suffix=".json"))
        rec.write_text(json.dumps({"all_passed": True, "provider": "deepseek-official",
                                   "model": "deepseek-flash"}), encoding="utf-8")
        RA.RESULT_PATH_DEFAULT = rec
        return self

    def __exit__(self, *a):
        RA.RESULT_PATH_DEFAULT = self.orig


def whitelist_sample() -> dict:
    return RA._whitelist_input(
        status="not_significant",
        target={"metric": "gc_alloc_per_frame", "before": 7.6e4, "after": 7.5e4,
                "delta": 0.01e4, "sigma": 0.2e4, "significant": False},
        guards=[{"metric": "cpuFrameTimeMs", "before": 1.5, "after": 1.6,
                 "delta": 0.1, "regressed": True},
                {"metric": "monoUsedBytes", "before": 1e8, "after": 1e8,
                 "delta": 0.0, "regressed": False}],
        conflicts=[{"rule_id": "P4-batch-split", "metrics": {"drawCalls": 120},
                    "expected": "合批生效 batches 升", "observed": "batches 不变"}],
        perception={"render_diff_score": None, "anim_event_diff": None,
                    "replay_divergence": None, "passed": False},
        compile_ok=True, realized=0.05,
        root_cause="per_frame_heap_alloc_in_hot_path",
        side_effect="none", diff_files=["Assets/Scripts/CombatSkillManager.cs"])


def main():
    cfg = load_config(Path(__file__).resolve().parents[1] / "llm_config.yaml")
    inputs = whitelist_sample()

    # ---- ① 连通性门禁 ----
    orig = RA.RESULT_PATH_DEFAULT
    RA.RESULT_PATH_DEFAULT = Path(tempfile.mktemp(suffix=".json"))
    fake = FakeClient("{}")
    try:
        RA.adjudicate(cfg, rule_decision="continue_with_direction",
                      inputs=inputs, client=fake)
        chk("门禁文件缺失 → ReviewAdjudicateError", False)
    except RA.ReviewAdjudicateError as e:
        chk("门禁文件缺失 → ReviewAdjudicateError（未发请求）",
            "从未跑过" in str(e) and not fake.calls)
    RA.RESULT_PATH_DEFAULT = orig

    # ---- ② review 段配置真消费（孤儿配置变实接线的证明）----
    keep_reply = json.dumps({"decision": "continue_with_direction",
                             "is_perception_in_essence": False,
                             "evidence_fields": ["verdict_status"],
                             "reason": "无感知证据，保持规则裁定"})
    with _gate_passed():
        fake = FakeClient(keep_reply)
        final, meta = RA.adjudicate(cfg, rule_decision="continue_with_direction",
                                    inputs=inputs, client=fake)
    call = fake.calls[0]
    chk("provider/model 来自 effective_for('review')（defaults 合并）",
        call["model"] == "deepseek-flash", call["model"])
    chk("temperature 真用 review 段（0.0，非 defaults 的 0.2）",
        call["temperature"] == 0.0, str(call["temperature"]))
    chk("json_mode 按配置传入", call.get("response_format") == {"type": "json_object"})
    chk("留档 prompt_profile=review_isolated（配置值真到了产物）",
        meta.get("prompt_profile") == "review_isolated", str(meta.get("prompt_profile")))
    chk("system 声明'只能收紧不得放松'",
        "不得放松" in call["messages"][0]["content"])

    # ---- ③ 只能收紧 / 不得放宽 ----
    chk("模型 keep → 规则值保持", final == "continue_with_direction")

    tight_reply = json.dumps({"decision": "escalate_human",
                              "is_perception_in_essence": True,
                              "evidence_fields": ["edit_side_effect", "perception"],
                              "reason": "改动触及渲染，本质感知类"})
    with _gate_passed():
        final2, meta2 = RA.adjudicate(cfg, rule_decision="continue_with_direction",
                                      inputs=inputs, client=FakeClient(tight_reply))
    chk("模型收紧（continue→escalate）→ 采纳", final2 == "escalate_human")
    chk("收紧采纳时 model_overruled 为空", meta2.get("model_overruled") is None)

    loose_reply = json.dumps({"decision": "continue_with_direction",
                              "is_perception_in_essence": False,
                              "evidence_fields": ["verdict_status"],
                              "reason": "再试一次吧"})
    with _gate_passed():
        final3, meta3 = RA.adjudicate(cfg, rule_decision="rollback_and_replan",
                                      inputs=inputs, client=FakeClient(loose_reply))
    chk("模型放宽（rollback→continue）→ 丢弃保留规则值",
        final3 == "rollback_and_replan" and meta3["model_overruled"] == "continue_with_direction",
        f"final={final3} overruled={meta3.get('model_overruled')}")

    # 规则已是 escalate 时 nodes 侧不会送模型（升级路由不该被再收紧）——
    # 但本模块若被直接喂 escalate，模型也只能保持或同样 escalate：
    with _gate_passed():
        final4, _m4 = RA.adjudicate(cfg, rule_decision="escalate_human",
                                    inputs=inputs, client=FakeClient(loose_reply))
    chk("规则已 escalate → 模型任何松裁定无效", final4 == "escalate_human")

    # ---- ④ 输入白名单：Fix 自辩类字段根本进不来 ----
    keys = set(inputs)
    banned = ("fix_defense", "self_defense", "confidence", "self_confidence",
              "fixer_notes", "辩护", "自评")
    leak = [b for b in banned if any(b in k for k in keys)]
    chk("白名单无 Fix 自辩/自评置信度键（缺席即隔离）", not leak, str(leak))
    with _gate_passed():
        fc = FakeClient(keep_reply)
        RA.adjudicate(cfg, rule_decision="continue_with_direction",
                      inputs=inputs, client=fc)
    sent = fc.calls[0]["messages"][1]["content"]
    chk("confidence 数值未随 Hypothesis 进 prompt（只给 root_cause 机制名）",
        "confidence" not in sent and "per_frame_heap_alloc" in sent)

    # ---- ⑤ 输出畸形 ----
    with _gate_passed():
        try:
            RA.adjudicate(cfg, rule_decision="continue_with_direction",
                          inputs=inputs, client=FakeClient("我觉得还行"))
            chk("非 JSON → ReviewAdjudicateError", False)
        except RA.ReviewAdjudicateError as e:
            chk("非 JSON → ReviewAdjudicateError", "非 JSON" in str(e))
    with _gate_passed():
        try:
            RA.adjudicate(cfg, rule_decision="continue_with_direction",
                          inputs=inputs, client=FakeClient('["列表不是对象"]'))
            chk("非对象 JSON → 拒", False)
        except RA.ReviewAdjudicateError as e:
            chk("非对象 JSON → 拒", "非对象" in str(e))
    try:
        with _gate_passed():
            RA.adjudicate(cfg, rule_decision="瞎写", inputs=inputs,
                          client=FakeClient(keep_reply))
        chk("非法 rule_decision → 拒", False)
    except RA.ReviewAdjudicateError as e:
        chk("非法 rule_decision → 拒", "非法初始裁定" in str(e))

    # ---- ⑥ 留档：token 与弱隔离声明（I10 + §2.3.5 诚实标注）----
    chk("留档含 token 用量（I10）",
        meta.get("token_in") == 900 and meta.get("token_out") == 80)
    chk("留档如实标注弱隔离（同模型），不虚称模型级隔离",
        "weak" in meta.get("isolation", "") and "same-model" in meta.get("isolation", ""),
        str(meta.get("isolation")))
    chk("留档带 prompt 哈希可回溯（G13 证据指针）", len(meta.get("prompt_sha256_8", "")) == 8)

    print(f"\n隔离裁定链测试 {len(PASS)}/{len(PASS) + len(FAIL)}"
          + (f"，失败项: {FAIL}" if FAIL else "，全部通过"))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())

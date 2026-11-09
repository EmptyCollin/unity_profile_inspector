"""运行配置单一真相源（阶段 5：配置一律走 UI）。

为什么单独一个模块：曾经"加一个能被改的配置项"要同时动三处——UI 的
CFG_FIELDS、daemon `set` 分支的白名单、各自的边界校验。任何一处漏改的
后果都很难看：
  - 漏了 daemon 白名单 ⇒ UI 显示"改成功了"，守护其实按未知字段静默丢弃
    （"看起来改了其实没改"，与本轮反复在堵的假接线同一类失效）；
  - 漏了 UI ⇒ 该项只能手改 yaml，违背"任何配置都由 UI 支持"；
  - 漏了边界 ⇒ 把 interval 改成 1 秒这种会当场烧穿积分的输入无人拦。

现在字段名、类型、上下界、嵌套写入路径只在这份 spec 里写一次：UI 渲染
表单、daemon 受理 `set`、落盘时的字段裁剪全部从这里派生。测试里有一条
双向机器校验——"spec 字段都能被 UI 编辑"且"UI 能编辑的字段守护全认得"，
防止日后有人绕过 spec 在某一侧硬编码。

不进这份 spec 的两类：
  - enabled（采样总开关）：走专门的 enable/disable 命令，有独立留痕事件
    （sampling_enabled/disabled），不与普通数值项混在一张表单里；
  - fixtures 清单、episode 探针路径：结构是列表/嵌套路径，属于阶段 7
    ProjectProfile 的范畴（按工程注入），不作为运行期手改项。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpecItem:
    key: str                 # UI 表单与 set 命令参数用的扁平名
    label: str               # 人读的中文名
    kind: type               # int | bool
    path: tuple              # 在 conf dict 里的嵌套写入路径
    lo: int | None = None    # 数值下界（含）。bool 项不填
    hi: int | None = None    # 数值上界（含）
    unit: str = ""           # 展示单位，帮人判断量级（秒/次/窗口）
    # 改这个值会带来的**非直观代价**。为什么要有这个字段：有些配置改下去
    # 不会报错，只是把历史结论变得不可比（G7），而这种后果只在 yaml 注释里
    # 写过。既然要求"配置一律走 UI"，代价就必须跟着 UI 表单一起出现，
    # 否则 UI 反而比手改 yaml 更危险——用户在看不见后果的地方按了回车。
    warn: str = ""

    def get(self, conf: dict):
        node = conf
        for p in self.path:
            if not isinstance(node, dict):
                return None
            node = node.get(p)
        return node

    def set(self, conf: dict, val) -> None:
        node = conf
        for p in self.path[:-1]:
            node = node.setdefault(p, {})
        node[self.path[-1]] = val

    def cast(self, v):
        """把外部输入转成规范值并校验边界。越界/类型不符抛 ValueError。

        bool 文本只认 y/n/true/false/1/0，**乱填一律拒绝**——把 "maybe" 静默
        当成 False 是更危险的"看起来改了其实改了别的东西"。int 走上下界。
        daemon 侧收到的多半已是正确类型，这里仍统一过一遍，保证"UI 挡不住
        的越界值到守护这关也挡得住"——两道闸口径必须同源。
        """
        if self.kind is bool:
            if isinstance(v, str):
                s = v.strip().lower()
                if s in ("y", "yes", "true", "1"):
                    return True
                if s in ("n", "no", "false", "0"):
                    return False
                raise ValueError(f"布尔项只能填 y/n，收到 {v!r}")
            return bool(v)
        try:
            n = int(str(v).strip()) if isinstance(v, str) else int(v)
        except (ValueError, TypeError):
            raise ValueError(f"{self.key} 需要整数，收到 {v!r}") from None
        if self.lo is not None and n < self.lo:
            raise ValueError(f"{self.key}={n} 低于下限 {self.lo}{self.unit}")
        if self.hi is not None and n > self.hi:
            raise ValueError(f"{self.key}={n} 高于上限 {self.hi}{self.unit}")
        return n


# 顺序即 UI 表单的展示顺序。这里的上下界是**设计护栏**（不是采样实测值，
# 别把定性判断写成实测——I1）：给明显不合理的输入兜底，例如 interval=1 秒
# 会当场把积分烧穿、wallclock 小于一次巡检就不可能跑完。budget.* 的默认
# 对齐 AGENTS 的 N=3 口径，边界只限制"别调失控"，不代表推荐值。
SPEC: list[SpecItem] = [
    SpecItem("interval_s", "巡检间隔", int, ("interval_s",), 60, 86400, "秒"),
    SpecItem("windows", "每夹具采样窗口数", int, ("windows",), 2, 12, "个",
             warn="该值进协议指纹（n_samples）：改完与既有标定基线不可比，"
                  "G7 会拒判本轮检出，须重新标定才能恢复检出能力"),
    SpecItem("cooldown_s", "唤起冷却", int, ("cooldown_s",), 0, 86400, "秒"),
    SpecItem("waterline_timeout_s", "重采超时", int,
             ("waterline_timeout_s",), 60, 7200, "秒"),
    SpecItem("episode_heartbeat_max_age_s", "探针心跳最大可接受年龄", int,
             ("episode", "heartbeat_max_age_s"), 2, 300, "秒"),
    SpecItem("max_consecutive_failures", "连续失败熔断阈值", int,
             ("max_consecutive_failures",), 1, 20, "次"),
    SpecItem("budget_max_review_rounds", "单问题审查轮上限", int,
             ("budget", "max_review_rounds"), 0, 10, "轮"),
    SpecItem("budget_max_attempts_per_issue", "单问题修复尝试上限", int,
             ("budget", "max_attempts_per_issue"), 1, 10, "次"),
    SpecItem("budget_wallclock_s", "单轮墙钟上限", int,
             ("budget", "wallclock_s"), 60, 72000, "秒"),
    SpecItem("budget_max_tokens", "单轮 token 上限", int,
             ("budget", "max_tokens"), 0, 2_000_000, ""),
    SpecItem("auto_fix_enabled", "自动修复总开关", bool,
             ("auto_fix", "enabled")),
    SpecItem("live_llm", "真模型归因（花积分）", bool,
             ("auto_fix", "live_llm")),
]

KEYS: frozenset[str] = frozenset(i.key for i in SPEC)


def spec_for(key: str) -> SpecItem | None:
    return next((i for i in SPEC if i.key == key), None)

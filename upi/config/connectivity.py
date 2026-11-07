"""连通性测试（DESIGN §2.3.4）。

触发时机（已确认）：first_use（用户通道）/ 开发联调 --dev / 用户主动复检 / model id 或 base_url 变更。
⚠️ 步骤 2~4 会向服务商发起真实推理请求并产生费用；不得循环执行。

回填策略：能力字段（supports_tool_calling / supports_json_mode / context_window / verified_at）
以"实测结果"形式写入 knowledge/llm_connectivity.json，由 settings 在加载时合并。
不直接改写 llm_config.yaml —— 保留该文件的注释与用户手改内容。

安全：结果文件只含摘要、耗时与哈希，不含任何密钥值。
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    NotFoundError,
    OpenAI,
    RateLimitError,
)

from upi.config.settings import LlmConfig, Provider

RESULT_PATH_DEFAULT = Path("knowledge/llm_connectivity.json")


class UnknownModelError(Exception):
    """配置中的 model id 不在服务商 /models 列表内。"""


def classify_error(exc: Exception) -> str:
    if isinstance(exc, UnknownModelError):
        return "unknown_model"
    if isinstance(exc, AuthenticationError):
        return "auth_error"            # 401/403：Key 无效或额度
    if isinstance(exc, NotFoundError):
        return "unknown_model"         # 404：model id 不在列表
    if isinstance(exc, RateLimitError):
        return "rate_limited"          # 429：限流
    if isinstance(exc, APIConnectionError):
        return "unreachable"           # 网络/地址
    if isinstance(exc, APIStatusError):
        body = str(exc.response.text)[:200].lower()
        if "model" in body and ("not" in body or "invalid" in body):
            return "unknown_model"
        return f"http_{exc.status_code}"
    return "unexpected"


def make_client(provider: Provider) -> OpenAI:
    import os

    key = os.environ.get(provider.api_key_env, "").strip()
    if not key:
        raise RuntimeError(f"环境变量 {provider.api_key_env} 未设置（只报告变量名，不涉及值）")
    return OpenAI(
        api_key=key,
        base_url=provider.base_url,
        timeout=provider.timeout_s,
        max_retries=provider.max_retries,
    )


def _step(name: str, fn) -> dict:
    t0 = time.perf_counter()
    try:
        detail = fn()
        return {"name": name, "ok": True, "duration_ms": round((time.perf_counter() - t0) * 1000), "detail": detail}
    except Exception as exc:  # noqa: BLE001 — 分类后上抛摘要，不外泄原始 header
        return {"name": name, "ok": False, "fail_mode": classify_error(exc),
                "error": exc.__class__.__name__, "duration_ms": round((time.perf_counter() - t0) * 1000)}


def run_check(cfg: LlmConfig, provider_name: str, model_id: str,
              result_path: Path = RESULT_PATH_DEFAULT, channel: str = "user") -> dict:
    """执行四步测试。返回摘要 dict（不含凭据）。任一步失败即停止后续真实推理步骤。

    channel: "user" = 终端用户首次使用；"dev" = 维护者开发联调（DESIGN §2.3.3）。
    两者语义不同，dev 结果不得冒充用户通道已完成引导。
    """
    provider = cfg.providers[provider_name]
    client = make_client(provider)

    def step_models():
        data = client.models.list().data
        listed = [m.id for m in data]
        if model_id not in listed:
            raise UnknownModelError(f"model '{model_id}' 不在服务商列表（共 {len(listed)} 项）")
        target = next(m for m in data if m.id == model_id)
        # 能力元数据直接取自服务商返回，不做转录、不猜测
        measured = {}
        for attr in ("context_window", "max_input_tokens", "max_output_tokens"):
            val = getattr(target, attr, None)
            if val is not None:
                measured[attr] = val
        return {"model_count": len(listed), "target_in_list": True, "measured_meta": measured}

    def step_chat():
        r = client.chat.completions.create(
            model=model_id, messages=[{"role": "user", "content": "只回复两个字：正常"}], max_tokens=8)
        text = (r.choices[0].message.content or "").strip()
        return {"reply_len": len(text), "reply_sha256_8": hashlib.sha256(text.encode()).hexdigest()[:8]}

    def step_tools():
        tools = [{
            "type": "function",
            "function": {
                "name": "echo", "description": "回显一段文本",
                "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            },
        }]
        r = client.chat.completions.create(
            model=model_id, messages=[{"role": "user", "content": "必须调用 echo 工具回显 hi，不要直接回答"}],
            tools=tools, max_tokens=64)
        calls = getattr(r.choices[0].message, "tool_calls", None)
        if not calls:
            raise RuntimeError("声明支持 tool calling 但实测未产生 tool_calls")
        return {"tool_name": calls[0].function.name}

    def step_json():
        r = client.chat.completions.create(
            model=model_id, messages=[{"role": "user", "content": '返回 JSON：{"ok": true}'}],
            response_format={"type": "json_object"}, max_tokens=32)
        parsed = json.loads(r.choices[0].message.content or "{}")
        return {"json_parsed": isinstance(parsed, dict)}

    steps, failed = [], None
    for name, fn in [("list_models", step_models), ("minimal_chat", step_chat),
                     ("tool_call_smoke", step_tools), ("json_mode_smoke", step_json)]:
        s = _step(name, fn)
        steps.append(s)
        if not s["ok"]:
            failed = s
            break

    measured = next((s["detail"].get("measured_meta", {}) for s in steps if s["name"] == "list_models"), {})
    capability = {
        # 来自服务商 /models 实测返回；未提供则保持 null（禁止猜测，§2.3.4）
        "context_window": measured.get("context_window"),
        "max_output_tokens": measured.get("max_output_tokens"),
        "supports_tool_calling": next((x["ok"] for x in steps if x["name"] == "tool_call_smoke"), None),
        "supports_json_mode": next((x["ok"] for x in steps if x["name"] == "json_mode_smoke"), None),
    }
    result = {
        "passed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": channel,
        "provider": provider_name,
        "model": model_id,
        "base_url": provider.base_url,
        "api_key_env": provider.api_key_env,        # 变量名引用，非值
        "steps": steps,
        "capability_backfill": capability,
        "all_passed": failed is None,
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

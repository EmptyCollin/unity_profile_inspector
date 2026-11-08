"""llm_config.yaml 的加载与校验（DESIGN §2.3.1 / REQ-9）。

红线（AGENTS I12/I13）：
- 本模块只读取环境变量**名**与**存在性**，任何函数不返回、不打印、不落盘密钥值。
- 配置文件只允许 `api_key_env: <变量名>`；出现密钥样内容直接判为校验失败。
- schema 非法时启动即失败并报出具体字段，不静默回退内置默认值。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

_KEYLIKE = re.compile(r"sk-[A-Za-z0-9]{6,}", re.I)
_ENVNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class ModelSpec(BaseModel):
    """providers 表内的模型条目。能力字段初始为 null，由连通性测试回填（§2.3.4）。"""

    id: str
    display_name: str = ""
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_tool_calling: Optional[bool] = None
    supports_json_mode: Optional[bool] = None
    verified_at: Optional[str] = None
    verified_channel: Optional[str] = None   # "user" | "dev"（§2.3.3 双通道）


class Provider(BaseModel):
    display_name: str = ""
    base_url: str
    protocol: str = "openai_compatible"
    api_key_env: str
    timeout_s: int = 60
    max_retries: int = 2
    rate_limit_backoff_s: list[int] = Field(default_factory=lambda: [1, 4, 10])
    models: list[ModelSpec]

    @field_validator("protocol")
    @classmethod
    def _protocol_must_be_openai_compatible(cls, v: str) -> str:
        if v != "openai_compatible":
            raise ValueError("只支持 openai_compatible 协议（§2.3 接口约束）")
        return v

    @field_validator("api_key_env")
    @classmethod
    def _must_be_env_name_reference(cls, v: str) -> str:
        if _KEYLIKE.search(v):
            raise ValueError("api_key_env 疑似密钥值：配置只允许存环境变量名（AGENTS I12）")
        if not _ENVNAME.match(v):
            raise ValueError("api_key_env 必须是合法环境变量名（字母/下划线开头，仅字母数字下划线）")
        return v

    def key_presence(self) -> tuple[bool, str]:
        """只回答"变量是否存在且非空"与变量名。绝不返回值。"""
        return (bool(os.environ.get(self.api_key_env, "").strip()), self.api_key_env)


class AgentParams(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_output_tokens: Optional[int] = Field(None, gt=0)
    json_mode: Optional[bool] = None
    prompt_profile: Optional[str] = None


class AgentModelParameters(BaseModel):
    defaults: AgentParams
    agents: dict[str, AgentParams] = Field(default_factory=dict)


class MonitorConfig(BaseModel):
    """pipeline.monitor：自主巡检调度参数（DESIGN §2.4 T1/T2w/T3）。

    判定口径不在此处——阈值永远来自标定链（§8.1），本段只管"什么时候跑、
    跑哪些、跑完之后唤不唤起修复循环"。"""
    enabled: bool = False
    interval_s: int = Field(1800, ge=60)
    windows: int = Field(4, ge=2)
    fixtures: list[str] = Field(default_factory=list)
    episode: dict = Field(default_factory=dict)
    waterline_timeout_s: int = 900
    auto_fix: dict = Field(default_factory=dict)
    cooldown_s: int = 0
    max_consecutive_failures: int = Field(3, ge=1)
    budget: dict = Field(default_factory=dict)


class LlmConfig(BaseModel):
    schema_version: int
    providers: dict[str, Provider]
    agent_model_parameters: AgentModelParameters
    bootstrap: dict = Field(default_factory=dict)
    connectivity_check: dict = Field(default_factory=dict)
    pipeline: dict = Field(default_factory=dict)   # 内含 monitor 段；缺=不启用

    def monitor(self) -> MonitorConfig:
        """取巡检配置。未配置 pipeline.monitor ⇒ enabled=False（不猜默认）。"""
        m = (self.pipeline or {}).get("monitor") or {}
        return MonitorConfig(**m)

    @model_validator(mode="after")
    def _refs_must_resolve(self) -> "LlmConfig":
        ids = {p: {m.id for m in pr.models} for p, pr in self.providers.items()}
        merged = [("defaults", self.agent_model_parameters.defaults)]
        merged += list(self.agent_model_parameters.agents.items())
        for agent, params in merged:
            for p in self._effective_chain(params):
                prov = p.get("provider")
                mdl = p.get("model")
                if prov is None or mdl is None:
                    continue
                if prov not in ids:
                    raise ValueError(f"agent '{agent}' 引用了不存在的 provider: {prov}")
                if mdl not in ids[prov]:
                    raise ValueError(f"agent '{agent}' 引用了 provider '{prov}' 中不存在的 model: {mdl}")
        return self

    def _effective_chain(self, params: AgentParams) -> list[dict]:
        d = self.agent_model_parameters.defaults.model_dump()
        return [{**d, **params.model_dump(exclude_none=True)}]

    def effective_for(self, agent: str) -> dict:
        d = self.agent_model_parameters.defaults.model_dump(exclude_none=True)
        a = self.agent_model_parameters.agents.get(agent, AgentParams()).model_dump(exclude_none=True)
        return {**d, **a}


def format_validation_error(exc: Exception) -> str:
    """把 pydantic 错误转成逐字段可读信息（不含值内容，只含字段路径）。"""
    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        lines = [f"配置字段不合法（共 {len(exc.errors())} 处）："]
        for e in exc.errors():
            loc = ".".join(str(x) for x in e["loc"]) or "<root>"
            lines.append(f"  - {loc}: {e['msg']}")
        return "\n".join(lines)
    return f"配置读取失败：{exc}"


def load_config(path: str | Path = "llm_config.yaml") -> LlmConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg = LlmConfig.model_validate(raw)
    _merge_verified_capability(cfg, path.parent / VERIFY_RECORD)
    return cfg


VERIFY_RECORD = Path("knowledge/llm_connectivity.json")


def _merge_verified_capability(cfg: LlmConfig, record: Path) -> None:
    """把连通性实测得到的能力字段并入内存配置。

    不写回 llm_config.yaml —— 保留该文件的注释与用户手改内容（§2.3.1）。
    只接受 provider + model 同时匹配的纪录；channel 原样带出，供调用方区分通道。
    """
    if not record.is_file():
        return
    try:
        rec = json.loads(record.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not rec.get("all_passed"):
        return
    cap = rec.get("capability_backfill") or {}
    for pname, provider in cfg.providers.items():
        if pname != rec.get("provider"):
            continue
        for model in provider.models:
            if model.id != rec.get("model"):
                continue
            for k in ("context_window", "max_output_tokens", "supports_tool_calling", "supports_json_mode"):
                if cap.get(k) is not None:
                    setattr(model, k, cap[k])
            model.verified_at = rec.get("passed_at")
            model.verified_channel = rec.get("channel", "user")


def verification_gate(cfg: LlmConfig, provider: str, model: str,
                     channel: str = "user") -> tuple[bool, str]:
    """真实调用前的放行判定。

    通道语义（§2.3.3）：dev 纪录不能充当 user 通道已引导完成的证据。
    """
    for pname, p in cfg.providers.items():
        if pname != provider:
            continue
        for m in p.models:
            if m.id != model:
                continue
            if not m.verified_at:
                return (False, f"model '{model}' 未通过连通性测试，禁止真实调用")
            if channel == "user" and m.verified_channel == "dev":
                return (False, f"model '{model}' 仅有开发通道(dev)验证纪录，"
                               f"用户通道需自行完成一次连通性测试（§2.3.3）")
            return (True, f"已验证（通道 {m.verified_channel}，{m.verified_at}）")
    return (False, f"provider '{provider}' 中不存在 model '{model}'")

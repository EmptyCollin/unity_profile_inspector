"""轻量 C# 源码符号索引：把调用栈/函数名落到工程内 file:span。

I8a 先用保守的正则 + 类型上下文，不假装 AST：
- 只索引 .cs，跳过 Library/Temp/obj/Editor 生成目录；
- 支持 `Type.Method` 与单方法名 fallback；
- 方法体行范围按大括号深度估算；解析不到就返回 None，不猜。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from upi.core.evidence_v2 import SourceSpan

_TYPE_RE = re.compile(
    r"^\s*(?:public|internal|private|protected)?\s*"
    r"(?:(?:sealed|abstract|static|partial)\s+)*"
    r"(?:class|struct|interface)\s+(\w+)"
)
_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|sealed|async|extern|unsafe|new|partial|abstract)\s+)*"
    r"(?:[\w<>\[\],.?]+\s+)+(\w+)\s*\([^;{}]*\)\s*$"
)
_SKIP_DIRS = {"Library", "Temp", "Obj", "obj", "Logs", "UserSettings", "Build", "Builds"}


@dataclass(frozen=True)
class SourceIndex:
    project_root: Path
    spans: dict[str, SourceSpan]
    by_method: dict[str, list[SourceSpan]]

    def resolve(self, symbol: str) -> SourceSpan | None:
        """`Namespace.Type.Method` → 精确；`Type.Method` / `Method` → 唯一命中才返回。"""
        if not symbol:
            return None
        s = symbol.strip()
        if "!" in s:
            s = s.split("!")[-1]
        s = s.replace("::", ".")
        s = re.sub(r"\([^)]*\)\s*$", "", s)
        if s in self.spans:
            return self.spans[s]
        short = ".".join(s.split(".")[-2:])
        if short in self.spans:
            return self.spans[short]
        method = s.split(".")[-1]
        cands = self.by_method.get(method, [])
        return cands[0] if len(cands) == 1 else None

    @classmethod
    def build(cls, project_root: Path | str) -> "SourceIndex":
        root = Path(project_root).resolve()
        spans: dict[str, SourceSpan] = {}
        by_method: dict[str, list[SourceSpan]] = {}
        for file in sorted(root.rglob("*.cs")):
            if any(part in _SKIP_DIRS for part in file.relative_to(root).parts):
                continue
            try:
                lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            rel = str(file.relative_to(root)).replace("\\", "/")
            types: list[tuple[int, str]] = []
            i = 0
            while i < len(lines):
                line = lines[i]
                indent = len(line) - len(line.lstrip())
                tm = _TYPE_RE.match(line)
                if tm:
                    types.append((indent, tm.group(1)))
                mm = _METHOD_RE.match(line)
                if not mm:
                    i += 1
                    continue
                method = mm.group(1)
                if method in {"if", "for", "while", "switch", "catch", "using", "return"}:
                    i += 1
                    continue
                # 方法属于“最近的、缩进更浅的类型”；嵌套类型不会污染外层方法。
                current_type = next((name for ind, name in reversed(types) if ind < indent), "")
                end = _find_end(lines, i)
                if end is None:
                    i += 1
                    continue
                sym = f"{current_type}.{method}" if current_type else method
                span = SourceSpan(file=rel, start_line=i + 1, end_line=end + 1,
                                  symbol=sym)
                spans.setdefault(sym, span)
                by_method.setdefault(method, []).append(span)
                i += 1
        return cls(project_root=root, spans=spans, by_method=by_method)

    def annotate_stack(self, frames: list) -> list:
        """给 CallStackFrame 补 source（原地返回新列表）。"""
        out = []
        for f in frames:
            span = f.source or self.resolve(f.symbol)
            out.append(f.model_copy(update={"source": span}) if hasattr(f, "model_copy") else f)
        return out


def _find_end(lines: list[str], start: int) -> int | None:
    """从方法声明行开始按大括号找结束行；找不到或表达式体返回 None/下一行。"""
    text = "\n".join(lines[start:start + 400])
    brace = text.find("{")
    if brace < 0:
        return start if "=>" in text[:200] else None
    depth = 0
    seen = False
    line_no = start
    for ch in text[brace:]:
        if ch == "\n":
            line_no += 1
        elif ch == "{":
            depth += 1
            seen = True
        elif ch == "}":
            depth -= 1
            if seen and depth == 0:
                return line_no
    return None

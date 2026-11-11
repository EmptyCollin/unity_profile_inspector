"""Console output guard (I0).

Windows 默认代码页是 936（GBK）。项目里大量中文/符号（✓、⇒、▸）在 GBK
下会抛 UnicodeEncodeError——首个撞上的是 `upi ui` 和测试脚本。这里不强行
把终端改成 UTF-8（那会让 GBK 终端显示乱码），只把不可编码字符替换掉：
**不崩**比“符号好看”重要。真正的 UTF-8 输出走文件与 Web UI（浏览器天然
UTF-8）。

用法：进程入口最早调用一次 force_utf8_streams()。
"""
from __future__ import annotations

import sys


def force_utf8_streams() -> None:
    """让 stdout/stderr 遇到不可编码字符时替换而不是抛异常。

    幂等；reconfigure 在非 TextIOWrapper（如测试替身）上可能不存在，静默跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - 输出守门本身不得成为崩溃源
            pass

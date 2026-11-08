using System;
using System.Text;
using UnityEngine;
using UnityEngine.Profiling;

// M3 地板复核前置实验：手动 GC 能否压低读数？哪些口径响应？
// 关键：monoUsedBytes/monoHeapBytes 属 Mono GC；totalAllocatedBytes/
// totalReservedBytes 属 C++ 原生堆——两套回收器，未必同步响应。
var sb = new StringBuilder();
long mu0 = Profiler.GetMonoUsedSizeLong();
long mh0 = Profiler.GetMonoHeapSizeLong();
long ta0 = Profiler.GetTotalAllocatedMemoryLong();
long tr0 = Profiler.GetTotalReservedMemoryLong();
int[] cc0 = { GC.CollectionCount(0), GC.CollectionCount(1), GC.CollectionCount(2) };

GC.Collect();
GC.WaitForPendingFinalizers();
GC.Collect();

long mu1 = Profiler.GetMonoUsedSizeLong();
long mh1 = Profiler.GetMonoHeapSizeLong();
long ta1 = Profiler.GetTotalAllocatedMemoryLong();
long tr1 = Profiler.GetTotalReservedMemoryLong();
int[] cc1 = { GC.CollectionCount(0), GC.CollectionCount(1), GC.CollectionCount(2) };

sb.Append("monoUsed_MB ").Append((mu0 / 1e6).ToString("F1")).Append(" -> ")
  .Append((mu1 / 1e6).ToString("F1")).Append("  (delta ").Append(((mu1 - mu0) / 1e6).ToString("F1")).Append(")\n");
sb.Append("monoHeap_MB ").Append((mh0 / 1e6).ToString("F1")).Append(" -> ")
  .Append((mh1 / 1e6).ToString("F1")).Append("  (delta ").Append(((mh1 - mh0) / 1e6).ToString("F1")).Append(")\n");
sb.Append("totalAlloc_MB ").Append((ta0 / 1e6).ToString("F1")).Append(" -> ")
  .Append((ta1 / 1e6).ToString("F1")).Append("  (delta ").Append(((ta1 - ta0) / 1e6).ToString("F1")).Append(")\n");
sb.Append("totalReser_MB ").Append((tr0 / 1e6).ToString("F1")).Append(" -> ")
  .Append((tr1 / 1e6).ToString("F1")).Append("  (delta ").Append(((tr1 - tr0) / 1e6).ToString("F1")).Append(")\n");
sb.Append("GC counts ").Append(cc0[0]).Append("/").Append(cc0[1]).Append("/").Append(cc0[2])
  .Append(" -> ").Append(cc1[0]).Append("/").Append(cc1[1]).Append("/").Append(cc1[2]);
return sb.ToString();

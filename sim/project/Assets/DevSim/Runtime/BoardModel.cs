using System;
using UnityEngine;

namespace DevSim
{
    /// <summary>
    /// 棋盘数据模型：颜色、叠层（数字色块）、匹配、重力与补充。
    /// 与 UI 解耦，便于确定性测试；尺寸由外部按回合难度设置。
    /// 叠层语义：Layers = 该格叠了几个色块。Layers == 1 是普通色块；
    /// Layers >= 2 显示数字且锁定（不能参与换位），每次被消除只减 1 层。
    /// </summary>
    public sealed class BoardModel
    {
        public int N { get; private set; }
        public int Colors { get; }
        public int[,] Color { get; private set; }
        public int[,] Layers { get; private set; }

        public BoardModel(int n, int colors)
        {
            Colors = Mathf.Max(3, colors);
            Allocate(n);
        }

        public void Allocate(int n)
        {
            N = Mathf.Max(3, n);
            Color = new int[N, N];
            Layers = new int[N, N];
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                {
                    Color[r, c] = -1;
                    Layers[r, c] = 0;
                }
        }

        public bool InBounds(int r, int c) => r >= 0 && c >= 0 && r < N && c < N;

        public bool IsEmpty(int r, int c) => !InBounds(r, c) || Color[r, c] < 0 || Layers[r, c] <= 0;

        public bool IsLocked(int r, int c) => InBounds(r, c) && Layers[r, c] >= 2;

        public void SetCell(int r, int c, int color, int layers)
        {
            Color[r, c] = color;
            Layers[r, c] = color < 0 ? 0 : Mathf.Max(1, layers);
        }

        /// <summary>保留给结算流程调用；当前实现不依赖跨步锁（每次命中只扣 1 层）。</summary>
        public void ClearGuard()
        {
        }

        /// <summary>生成一个开局无三连的盘面。</summary>
        public void Build(System.Random rng)
        {
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                {
                    int v = rng.Next(Colors);
                    int guard = 0;
                    while (WouldMatch(r, c, v) && guard++ < 12) v = (v + 1) % Colors;
                    Color[r, c] = v;
                    Layers[r, c] = 1;
                }
            ClearGuard();
        }

        /// <summary>测试用：整盘载入（不做无三连修正）。</summary>
        public void Load(int[] colors, int[] layers)
        {
            for (int i = 0; i < N * N; i++)
            {
                int r = i / N, c = i % N;
                int col = i < colors.Length ? colors[i] : 0;
                int lay = layers != null && i < layers.Length ? layers[i] : 1;
                SetCell(r, c, col, lay);
            }
            ClearGuard();
        }

        private bool WouldMatch(int r, int c, int v)
        {
            if (c >= 2 && Color[r, c - 1] == v && Color[r, c - 2] == v) return true;
            if (r >= 2 && Color[r - 1, c] == v && Color[r - 2, c] == v) return true;
            return false;
        }

        public bool CanSwap(int r1, int c1, int r2, int c2)
        {
            if (!InBounds(r1, c1) || !InBounds(r2, c2)) return false;
            if (Mathf.Abs(r1 - r2) + Mathf.Abs(c1 - c2) != 1) return false;
            if (IsLocked(r1, c1) || IsLocked(r2, c2)) return false;
            if (IsEmpty(r1, c1) || IsEmpty(r2, c2)) return false;
            return true;
        }

        public void SwapRaw(int r1, int c1, int r2, int c2)
        {
            int t = Color[r1, c1]; Color[r1, c1] = Color[r2, c2]; Color[r2, c2] = t;
            int a = Layers[r1, c1]; Layers[r1, c1] = Layers[r2, c2]; Layers[r2, c2] = a;
        }

        public bool HasMatch()
        {
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N - 2; c++)
                    if (Color[r, c] >= 0
                        && Color[r, c] == Color[r, c + 1] && Color[r, c] == Color[r, c + 2]) return true;
            for (int c = 0; c < N; c++)
                for (int r = 0; r < N - 2; r++)
                    if (Color[r, c] >= 0
                        && Color[r, c] == Color[r + 1, c] && Color[r, c] == Color[r + 2, c]) return true;
            return false;
        }

        /// <summary>本次交换是否直接让 (r,c) 参与一次消除（用于判断交换有效性）。</summary>
        public bool MatchInvolving(int r, int c)
        {
            var hit = FindMatches();
            return InBounds(r, c) && hit[r, c];
        }

        public bool[,] FindMatches()
        {
            var hit = new bool[N, N];
            for (int r = 0; r < N; r++)
            {
                int run = 1;
                for (int c = 1; c <= N; c++)
                {
                    bool same = c < N && Color[r, c] >= 0
                                && Color[r, c] == Color[r, c - 1];
                    if (same) run++;
                    else
                    {
                        if (run >= 3)
                            for (int k = c - run; k < c; k++) hit[r, k] = true;
                        run = 1;
                    }
                }
            }
            for (int c = 0; c < N; c++)
            {
                int run = 1;
                for (int r = 1; r <= N; r++)
                {
                    bool same = r < N && Color[r, c] >= 0
                                && Color[r, c] == Color[r - 1, c];
                    if (same) run++;
                    else
                    {
                        if (run >= 3)
                            for (int k = r - run; k < r; k++) hit[k, c] = true;
                        run = 1;
                    }
                }
            }
            return hit;
        }

        public int Count(bool[,] hit)
        {
            int n = 0;
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                    if (hit[r, c]) n++;
            return n;
        }

        /// <summary>被匹配的格子各扣 1 层；返回按颜色统计的命中数。</summary>
        public int[] CountByColor(bool[,] hit)
        {
            var per = new int[Colors];
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                    if (hit[r, c] && Color[r, c] >= 0) per[Color[r, c]]++;
            return per;
        }

        /// <summary>
        /// 结算一次命中：每格只扣自身 1 层。Layers 1 → 消失；Layers 2 → 1（变成普通块）；
        /// Layers >= 3 → 仍叠层，本步锁定不再重复命中。
        /// </summary>
        public void ApplyDamage(bool[,] hit, out int removed)
        {
            removed = 0;
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                {
                    if (!hit[r, c]) continue;
                    if (Color[r, c] < 0) continue;
                    Layers[r, c]--;
                    if (Layers[r, c] <= 0)
                    {
                        Color[r, c] = -1;
                        Layers[r, c] = 0;
                        removed++;
                    }
                }
        }

        /// <summary>重力 + 顶部补充；tier 越高补充越容易出现叠层块。</summary>
        public void DropAndRefill(System.Random rng, int tier, float baseChance = 0.05f, float perTier = 0.06f)
        {
            for (int c = 0; c < N; c++)
            {
                int write = N - 1;
                for (int r = N - 1; r >= 0; r--)
                {
                    if (Color[r, c] < 0) continue;
                    if (write != r)
                    {
                        Color[write, c] = Color[r, c];
                        Layers[write, c] = Layers[r, c];
                        Color[r, c] = -1;
                        Layers[r, c] = 0;
                    }
                    write--;
                }
                float stackChance = Mathf.Clamp(baseChance + perTier * tier, 0f, 0.4f);
                for (int r = write; r >= 0; r--)
                {
                    Color[r, c] = rng.Next(Colors);
                    Layers[r, c] = 1;
                    if (rng.NextDouble() < stackChance)
                        Layers[r, c] = rng.Next(2, Mathf.Min(2 + tier, 6) + 1);
                }
            }
        }

        public bool HasPossibleMove()
        {
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                {
                    if (IsLocked(r, c)) continue;
                    if (c + 1 < N && !IsLocked(r, c + 1) && SwapCreatesMatch(r, c, r, c + 1)) return true;
                    if (r + 1 < N && !IsLocked(r + 1, c) && SwapCreatesMatch(r, c, r + 1, c)) return true;
                }
            return false;
        }

        private bool SwapCreatesMatch(int r1, int c1, int r2, int c2)
        {
            if (Color[r1, c1] < 0 || Color[r2, c2] < 0) return false;
            SwapRaw(r1, c1, r2, c2);
            bool hit = MatchInvolving(r1, c1) || MatchInvolving(r2, c2);
            SwapRaw(r1, c1, r2, c2);
            return hit;
        }

        public void Shuffle(System.Random rng)
        {
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                {
                    int rr = rng.Next(N), cc = rng.Next(N);
                    SwapRaw(r, c, rr, cc);
                }
            for (int attempt = 0; attempt < 40 && !HasPossibleMove(); attempt++)
            {
                for (int r = 0; r < N; r++)
                    for (int c = 0; c < N; c++)
                        if (!IsLocked(r, c)) Color[r, c] = rng.Next(Colors);
            }
        }

        /// <summary>是否还有叠层块（UI/统计用）。</summary>
        public int StackedCount()
        {
            int n = 0;
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                    if (Layers[r, c] >= 2) n++;
            return n;
        }
    }
}

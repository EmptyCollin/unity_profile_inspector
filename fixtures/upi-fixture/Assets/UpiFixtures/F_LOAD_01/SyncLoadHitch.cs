// F-LOAD-01 缺陷夹具（holdout 集）：进场同步全量加载
// 缺陷机制（对应 DESIGN §1 链路 加载，DOM-4）：
//   ① Start 时同步 Resources.Load 全量预热（一次性主线程阻塞 ⇒ 首帧尖峰）
//   ② 加载后立即构造大数组常驻（内存峰值）
//   ③ 无异步/无分组：所有资源同帧申请
// 修复方向：分帧异步加载 ⇒ 改时序=可能影响体验 ⇒ semi/blocked
// 本文件属夹具脚本，blocked 档，仅人工可改。
using UnityEngine;

namespace Upi.Fixtures
{
    public sealed class SyncLoadHitch : MonoBehaviour
    {
        [SerializeField] private int loadCount = 48;
        [SerializeField] private int residentKb = 16384;

        private TextAsset[] _keep;
        private byte[][] _resident;
        private int _t;

        private void Start()
        {
            _keep = new TextAsset[loadCount];
            _resident = new byte[loadCount][];

            // ①③ 同帧同步全量加载（无分帧、无异步）
            for (int i = 0; i < loadCount; i++)
            {
                _keep[i] = Resources.Load<TextAsset>("upi_payload_" + i);
                _resident[i] = new byte[residentKb];   // ② 加载即常驻
            }
        }

        private void Update()
        {
            // 保持引用活跃，避免被裁剪；同时提供轻量每帧负载
            _t++;
            if ((_t & 63) == 0)
            {
                int sum = 0;
                for (int i = 0; i < _resident.Length; i++) sum += _resident[i].Length;
                _ = sum;
            }
        }
    }
}

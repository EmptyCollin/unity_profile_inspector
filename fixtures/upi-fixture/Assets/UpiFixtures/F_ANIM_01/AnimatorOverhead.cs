// F-ANIM-01 缺陷夹具（holdout 集）：Animator 每帧跨层查询与参数往返
// 缺陷机制（对应 DESIGN §1 链路 动画，DOM-3/SCN-1）：
//   ① 每帧遍历所有 layer 取 animator.stateInfo（跨层查询）
//   ② 每帧 SetBool/SetFloat/ResetTrigger 参数往返
//   ③ 每帧 GetComponent<Animator>
// 修复方向：控制器分层与参数写入前判等（状态缓存）⇒ 触碰状态机=感知层 ⇒ semi
// 本文件属夹具脚本，blocked 档，仅人工可改。
using UnityEngine;

namespace Upi.Fixtures
{
    public sealed class AnimatorOverhead : MonoBehaviour
    {
        [SerializeField] private int trackedCount = 16;

        private Animator[] _pool;
        private int _t;
        private int _probe;   // 防止被优化掉

        private void Awake()
        {
            // 运行时自建 Animator 池（构建期 Bind 会因域重载丢失，不可依赖）
            var ctl = Resources.Load<RuntimeAnimatorController>("upi_simple");
            _pool = new Animator[trackedCount];
            for (int i = 0; i < trackedCount; i++)
            {
                var go = new GameObject("AnimUnit" + i);
                go.transform.SetParent(transform, false);
                go.transform.position = new Vector3((i % 4) * 2f, 0f, (i / 4) * 2f);
                _pool[i] = go.AddComponent<Animator>();
                if (ctl != null) _pool[i].runtimeAnimatorController = ctl;
            }
        }

        private void Update()
        {
            _t++;
            for (int i = 0; i < _pool.Length; i++)
            {
                var a = _pool[i];
                if (a == null) continue;

                a.GetComponent<Animator>().enabled = true;       // ③ 每帧 GetComponent

                int layers = a.layerCount;
                for (int l = 0; l < layers; l++)                  // ① 每帧跨层查询
                {
                    var st = a.GetCurrentAnimatorStateInfo(l);
                    if (st.normalizedTime > 1.5f)
                    {
                        a.Play(st.nameHash, l, 0f);
                    }
                    _probe += st.nameHash;
                }

                // ② 参数往返：无判等，每帧全量写
                a.SetBool("Attack", (_t & 1) == 0);
                a.SetFloat("Speed", (_t % 17) * 0.1f);
                a.ResetTrigger("Hit");
                a.SetInteger("Combo", _t % 5);
            }
        }
    }
}

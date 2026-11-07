// UPI 夹具动态驱动组件（运行时程序集）
// G17 交付判据要求：所有夹具场景必须有真实动态（指标本身有抖动），
// 否则静止场景 drawCalls 唯一值=1 ⇒ MAD=0 ⇒ k·σ 判据失效（DESIGN §2.2.1 陷阱）。
// 本文件属夹具脚本，blocked 档，仅人工可改。
using UnityEngine;

namespace Upi.Fixtures.Drivers
{
    /// <summary>轨道相机：持续改变视点 ⇒ 每帧重绘、culling 变化。</summary>
    public sealed class CircleDrift : MonoBehaviour
    {
        [SerializeField] private float radius = 12f;
        [SerializeField] private float speed = 0.18f;
        private Transform _target;

        private void Awake()
        {
            var m = GameObject.Find("Mover");
            _target = m != null ? m.transform : null;
        }

        private void Update()
        {
            float t = Time.time * speed;
            transform.position = new Vector3(Mathf.Cos(t) * radius, 1.5f, Mathf.Sin(t) * radius);
            if (_target != null) transform.LookAt(_target);
            else transform.LookAt(Vector3.zero);
        }
    }

    /// <summary>运动物体：持续位移 ⇒ 渲染与变换更新有真实负载。</summary>
    public sealed class Mover : MonoBehaviour
    {
        [SerializeField] private float radius = 3f;
        [SerializeField] private float speed = 1.1f;

        private void Update()
        {
            float t = Time.time * speed;
            transform.position = new Vector3(Mathf.Cos(t) * radius, 1f + Mathf.Sin(t * 2.3f) * 0.4f, Mathf.Sin(t) * radius);
        }
    }
}

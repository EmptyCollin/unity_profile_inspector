// F-GPU-01 缺陷夹具（tuning 集）：逐实例材质破坏合批
// 缺陷机制（对应 DESIGN §1 链路 GPU/渲染，DOM-1/DOM-2）：
//   ① 每物体 renderer.material（GetMaterialInstance 语义）⇒ 产生材质实例 ⇒ SRP Batcher 断、Draw Call 升
//   ② 每帧再设置一次材质属性 ⇒ 持续打断合批
// 修复方向：共享材质 + MaterialPropertyBlock ⇒ 但触碰材质=感知层 ⇒ semi（演示权限分档）
// 本文件属夹具脚本，blocked 档，仅人工可改。
using UnityEngine;

namespace Upi.Fixtures
{
    public sealed class BatchBreaker : MonoBehaviour
    {
        [SerializeField] private int objectCount = 40;
        [SerializeField] private float spacing = 1.2f;
        [SerializeField] private Shader fallbackShader;

        private readonly GameObject[] _items = new GameObject[40];
        private int _t;

        private void Start()
        {
            var baseMat = new Material(fallbackShader != null ? fallbackShader : Shader.Find("Universal Render Pipeline/Lit"));
            baseMat.color = new Color(0.8f, 0.5f, 0.2f);

            for (int i = 0; i < objectCount && i < _items.Length; i++)
            {
                var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
                go.name = "GPU01_Cube" + i;
                go.transform.position = new Vector3((i % 8) * spacing, (i / 8) * spacing, 0f);
                _items[i] = go;
            }

            foreach (var go in _items)
            {
                if (go == null) continue;
                var r = go.GetComponent<Renderer>();
                // ① 逐实例材质：.material 会克隆出每个渲染器独占的材质实例
                r.material = new Material(baseMat);
            }
        }

        private void Update()
        {
            _t++;
            if ((_t & 31) != 0) return;   // 每 32 帧一次属性写入，持续破坏批处理稳定性
            foreach (var go in _items)
            {
                if (go == null) continue;
                // ② 运行时逐实例改色（MaterialPropertyBlock 才是语义保持的替代方案）
                var r = go.GetComponent<Renderer>();
                r.material.SetColor("_BaseColor", new Color(0.8f, 0.5f, 0.2f, 1f));
            }
        }
    }
}

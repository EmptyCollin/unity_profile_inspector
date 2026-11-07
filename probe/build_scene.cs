// probe/build_scene.cs —— 编辑态构建可渲染场景（M0' 验收前置）
// 实测原因：退出 Play Mode 会触发域重载，销毁上一轮的临时物体；
//           每轮录制前必须在编辑态重建，否则渲染计数器全为 0。
// 仅改动内存场景，不调用 Save，因此不影响已提交的夹具资产。
using UnityEditor;
using UnityEngine;

// 幂等：先清掉上一轮的探针物体（按前缀匹配，不依赖静态字段）
foreach (var o in Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
{
    if (o.name.StartsWith("Probe")) Object.DestroyImmediate(o);
}

var cam = new GameObject("ProbeCam");
cam.tag = "MainCamera";
var camera = cam.AddComponent<Camera>();
camera.clearFlags = CameraClearFlags.SolidColor;
cam.transform.position = new Vector3(0f, 1f, -10f);
cam.transform.rotation = Quaternion.identity;

for (int i = 0; i < 20; i++)
{
    var c = GameObject.CreatePrimitive(PrimitiveType.Cube);
    c.name = "ProbeCube" + i;
    c.transform.position = new Vector3((i % 5) * 2f, (i / 5) * 2f, 0f);
}

var light = new GameObject("ProbeLight");
light.AddComponent<Light>().type = LightType.Directional;

int renderers = Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None).Length;
int cams = Object.FindObjectsByType<Camera>(FindObjectsSortMode.None).Length;
return $"scene ready renderers={renderers} cameras={cams}";

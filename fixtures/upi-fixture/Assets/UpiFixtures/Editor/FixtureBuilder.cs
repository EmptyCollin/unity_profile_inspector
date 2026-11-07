// UPI 夹具场景构建器（Editor 程序集）：一个菜单项按规格构建 tuning/holdout/clean 场景
// 依赖运行时组件：Upi.Fixtures.*（缺陷夹具）与 Upi.Fixtures.Drivers.*（动态驱动，G17）
// AnimatorController 落在 Resources/ 下 ⇒ F-ANIM-01 运行时 Resources.Load 可取到
// （构建期 Bind() 注入的 Animator 池会因域重载丢失，已废弃该路径）
// 本文件属夹具脚本，blocked 档，仅人工可改。
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using Upi.Fixtures;

namespace Upi.Fixtures.EditorTools
{
    public static class FixtureBuilder
    {
        const string FixtureRoot = "Assets/UpiFixtures";
        const string ResourcesDir = FixtureRoot + "/Resources";

        public struct Spec
        {
            public string id;        // F-CPU-01 | F-GPU-01 | F-ANIM-01 | F-LOAD-01 | CLEAN
            public bool enabled;     // 缺陷组件开关
            public int scale;        // 规模（同机制不同规模 ⇒ 新 n_effective，§8.2）
            public bool dynamic;     // 动态驱动（G17）
            public int holdout;      // 0=tuning 1=holdout
        }

        static readonly List<Spec> Specs = new List<Spec>
        {
            new Spec { id = "F-CPU-01",  enabled = true,  scale = 100, dynamic = true, holdout = 0 },
            new Spec { id = "F-GPU-01",  enabled = true,  scale = 40, dynamic = true, holdout = 0 },
            new Spec { id = "F-ANIM-01", enabled = true,  scale = 128, dynamic = true, holdout = 1 },
            new Spec { id = "F-LOAD-01", enabled = true,  scale = 400, dynamic = true, holdout = 1 },
            new Spec { id = "CLEAN",     enabled = false, scale = 0,  dynamic = true, holdout = 0 },
        };

        [MenuItem("UPI/Fixtures/Build All (tuning+holdout+clean)")]
        public static void BuildAll()
        {
            EnsureResources();
            foreach (var s in Specs) BuildOne(s);
            SaveJson();
            AssetDatabase.Refresh();
            Debug.Log("[UPI] 夹具场景全部构建完成（含动态驱动）");
        }

        [MenuItem("UPI/Fixtures/Clean Baseline (FP 标定用)")]
        public static void BuildClean()
        {
            EnsureResources();
            var s = Specs[Specs.Count - 1];
            s.enabled = false;
            BuildOne(s);
            SaveJson();
            AssetDatabase.Refresh();
        }

        static void SaveJson()
        {
            var sb = new System.Text.StringBuilder("{\"fixtures\":[");
            for (int i = 0; i < Specs.Count; i++)
            {
                var s = Specs[i];
                if (i > 0) sb.Append(",");
                sb.Append("{\"id\":\"" + s.id + "\",\"enabled\":" + (s.enabled ? "true" : "false")
                  + ",\"scale\":" + s.scale + ",\"dynamic\":" + (s.dynamic ? "true" : "false")
                  + ",\"holdout\":" + s.holdout + "}");
            }
            sb.Append("]}");
            Directory.CreateDirectory(ResourcesDir);
            File.WriteAllText(FixtureRoot + "/Runtime/fixture_spec.json", sb.ToString());
        }

        static void EnsureResources()
        {
            if (!Directory.Exists(ResourcesDir)) Directory.CreateDirectory(ResourcesDir);
            // F-LOAD-01 需要同步可读的 Resources 资源：48 个 ~64KB 文本占位
            if (!File.Exists(ResourcesDir + "/upi_payload_399.txt"))
            {
                var big = new string('x', 64 * 1024);
                for (int i = 0; i < 400; i++)
                    File.WriteAllText(ResourcesDir + "/upi_payload_" + i + ".txt", big);
            }
            AssetDatabase.Refresh();
        }

        static void BuildOne(Spec spec)
        {
            string sceneDir = FixtureRoot + "/Scenes";
            if (!Directory.Exists(sceneDir)) Directory.CreateDirectory(sceneDir);
            string scenePath = sceneDir + "/Scene_" + spec.id + ".unity";

            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            foreach (var go in Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
                Object.DestroyImmediate(go);

            var camGo = new GameObject("TrackCamera");
            camGo.tag = "MainCamera";
            camGo.AddComponent<Camera>();
            camGo.AddComponent<Upi.Fixtures.Drivers.CircleDrift>();
            camGo.transform.position = new Vector3(0, 1.2f, -14f);

            var light = new GameObject("KeyLight");
            light.AddComponent<Light>().type = LightType.Directional;

            // 统一基底负载（G1 检出前提）：所有夹具场景含相同 20 立方体共享材质，
            // clean 与缺陷场景唯一差异 = 缺陷组件本身 ⇒ drawCalls 差才可归因于缺陷
            var baseRoot = new GameObject("CubeBase");
            var sharedMat = new Material(Shader.Find("Universal Render Pipeline/Lit"));
            sharedMat.color = new Color(0.3f, 0.6f, 0.9f);
            for (int i = 0; i < 20; i++)
            {
                var c = GameObject.CreatePrimitive(PrimitiveType.Cube);
                c.name = "BaseCube" + i;
                c.transform.SetParent(baseRoot.transform, false);
                c.transform.position = new Vector3(((i % 5) - 2) * 1.6f, ((i / 5) - 1) * 1.6f + 1f, 1f);
                var r = c.GetComponent<Renderer>();
                r.sharedMaterial = sharedMat;        // 共享：基底必须零合批破坏
            }

            if (spec.id == "F-CPU-01" && spec.enabled)
            {
                var root = new GameObject("CombatRoot");
                for (int i = 0; i < 4; i++)
                {
                    var go = new GameObject("CombatSkill" + i);
                    go.transform.SetParent(root.transform, false);
                    go.AddComponent<Rigidbody>();
                    go.AddComponent<CapsuleCollider>();
                    var m = go.AddComponent<CombatSkillManager>();
                    SetField(m, "skillCount", spec.scale);
                }
            }
            else if (spec.id == "F-GPU-01" && spec.enabled)
            {
                var go = new GameObject("BatchBreaker");
                var b = go.AddComponent<BatchBreaker>();
                SetField(b, "objectCount", spec.scale);
                SetField(b, "fallbackShader", Shader.Find("Universal Render Pipeline/Lit"));
            }
            else if (spec.id == "F-ANIM-01" && spec.enabled)
            {
                EnsureAnimatorController();
                var mgr = new GameObject("AnimatorOverhead");
                var m = mgr.AddComponent<AnimatorOverhead>();
                SetField(m, "trackedCount", spec.scale);
                // 不在此处 Bind：Animator 池由组件 Awake 内 Resources.Load 自建（域重载安全）
            }
            else if (spec.id == "F-LOAD-01" && spec.enabled)
            {
                var go = new GameObject("SyncLoadHitch");
                var h = go.AddComponent<SyncLoadHitch>();
                SetField(h, "loadCount", spec.scale);
            }
            // else：CLEAN 变体 = 仅统一基底 + 驱动，无任何缺陷组件 ⇒ FP 标定基准

            var mover = new GameObject("Mover");
            mover.AddComponent<Upi.Fixtures.Drivers.Mover>();
            mover.transform.position = new Vector3(0, 1, 0);

            var scene = EditorSceneManager.GetActiveScene();
            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene, scenePath);
            Debug.Log("[UPI] 夹具场景已保存: " + scenePath);
        }

        static void EnsureAnimatorController()
        {
            string path = ResourcesDir + "/upi_simple.controller";
            var existing = AssetDatabase.LoadAssetAtPath<UnityEditor.Animations.AnimatorController>(path);
            if (existing != null) return;

            var ac = UnityEditor.Animations.AnimatorController.CreateAnimatorControllerAtPath(path);
            ac.AddParameter("Attack", AnimatorControllerParameterType.Bool);
            ac.AddParameter("Speed", AnimatorControllerParameterType.Float);
            ac.AddParameter("Combo", AnimatorControllerParameterType.Int);
            ac.AddParameter("Hit", AnimatorControllerParameterType.Trigger);
            ac.layers[0].stateMachine.AddState("Idle");
            ac.AddLayer("Move");   // 实测修正：AddLayer 返回 void，不可赋值 ⇒ 从 layers 末尾取
            var moveLayer = ac.layers[ac.layers.Length - 1];
            moveLayer.stateMachine.AddState("Walk");
            AssetDatabase.SaveAssets();
        }

        static void SetField(Object target, string name, object value)
        {
            var so = new SerializedObject(target);
            var prop = so.FindProperty(name);
            if (prop == null) { Debug.LogWarning("[UPI] 字段不存在: " + name); return; }
            if (value is int iv) prop.intValue = iv;
            else prop.objectReferenceValue = (Object)value;
            so.ApplyModifiedPropertiesWithoutUndo();
        }
    }
}

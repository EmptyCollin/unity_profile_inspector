using System;
using System.IO;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.AddressableAssets;
using UnityEngine.ResourceManagement.AsyncOperations;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.UI;

namespace DevSim
{
    /// <summary>
    /// Chroma Rogue vertical slice：动态棋盘三消 + 轻肉鸽。
    /// 设计分辨率 1280x720（与 Game View / 截图 1:1），全部 UI 运行时生成。
    /// 轮次由时间驱动；棋盘尺寸随难度从 10x10 递减到 6x6。
    /// </summary>
    public sealed class ChromaGame : MonoBehaviour
    {
        private const int Colors = 5;
        private const float DesignW = 1280f, DesignH = 720f;

        private const float BoardX = 24f, BoardY = 92f, BoardSize = 560f;
        private const float BoardPad = 12f, BoardGap = 4f;

        private const float DragThreshold = 20f;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Boot()
        {
            if (FindFirstObjectByType<ChromaGame>() == null)
            {
                var go = new GameObject("ChromaRogue");
                go.AddComponent<ChromaGame>();
            }
        }

        private enum Phase { Lobby, Playing, Preview, Shop, Bonus, Over }

        private CaseConfig cfg;
        private System.Random rng;
        private Font font;
        private Canvas canvas;
        private RectTransform canvasRect;
        private GameObject gameRoot, lobbyRoot, shopPanel, bonusPanel, overPanel, previewPanel;
        private RectTransform boardRoot, sideRect;
        private GameObject helpPanel, caseMenu;
        private Text caseButtonLabel;

        private BoardModel model;
        private int boardN;
        private float tilePx, gapPx;
        private readonly List<GameObject> boardCells = new List<GameObject>();
        private Image[,] tileImages;
        private Text[,] tileArmor;
        private RectTransform[,] tileRects;
        private Vector2[,] tileBase;
        private readonly Dictionary<int, Sprite> tileSprites = new Dictionary<int, Sprite>();
        private readonly Dictionary<int, Sprite> iconSprites = new Dictionary<int, Sprite>();
        private readonly List<Sprite> _spritesLegacy = new List<Sprite>();

        private Text roundText, targetText, scoreText, timeText, coinText, statusText, highText;
        private Text shopCoins, overText, stepText, previewTitle, previewBody, previewCountdown;
        private Image timeFill;
        private Text[] itemCountLabels;
        private readonly List<GameObject> itemSlots = new List<GameObject>();

        private sealed class ShopCard
        {
            public Image bg, icon, soldOverlay;
            public Text title, desc, price, buyLabel, state;
            public Button buy;
        }
        private readonly ShopCard[] shopCards = new ShopCard[3];

        private readonly Text[] bonusLabels = new Text[3];
        private readonly CanvasGroup[] bonusGroups = new CanvasGroup[3];

        private readonly List<Image> _fxPool = new List<Image>();
        private int _fxCursor;

        private bool busy, dragging;
        private int dragR = -1, dragC = -1, hoverR = -1, hoverC = -1;
        private int selectedR = -1, selectedC = -1;
        private int bonusTriggeredCount;
        private AudioSource sfx;
        private AudioClip clickClip, clearClip, bonusClip;

        private readonly int[] inventory = new int[8];
        private readonly string[] itemNames = { "炸弹", "改色卡", "锤子", "洗牌" };
        private readonly string[] itemDesc =
        {
            "3x3 拆一层 / 消除",
            "把一格改成下一种颜色",
            "单格拆一层 / 消除",
            "重排整个棋盘",
        };
        private readonly int[] itemPrice = { 120, 80, 100, 60 };

        private Phase phase = Phase.Lobby;
        private int round, target, totalScore, coins, tier;
        private float timeLeft, nextHud, previewLeft;
        private int multiplier = 1;
        private int bonusTime;
        private bool bonusChoiceLocked;
        private bool bonusOpen, overOpen;
        private bool pendingRoundEnd, pendingGameOver;

        private sealed class Offer
        {
            public int item;
            public int price;
            public bool sold;
            public Offer(int item, int price) { this.item = item; this.price = price; }
        }
        private readonly Offer[] offers = new Offer[3];
        private int shopVisits, refreshCount;
        private bool[] boughtThisShop = new bool[8];
        private string shopMessage = "";

        private int lastCleared, lastChain, lastSameColorCount, lastSameColorId;
        private string lastMessage = "";
        private readonly int[] lastPerColor = new int[Colors];

        private static readonly Color32[] Palette =
        {
            new Color32(255, 96, 112, 255),   // ruby
            new Color32(255, 194, 72, 255),   // amber
            new Color32(84, 220, 142, 255),   // emerald
            new Color32(84, 176, 255, 255),   // sapphire
            new Color32(186, 120, 255, 255),  // violet
        };
        private static readonly string[] PaletteName = { "R", "A", "E", "S", "V" };

        private void Start()
        {
            cfg = CaseConfig.Load();
            WriteFeatureState(cfg);
            rng = new System.Random(cfg.seed);
            tier = cfg.start_tier;
            Application.targetFrameRate = 60;
            font = Font.CreateDynamicFontFromOSFont(new[] { "Microsoft YaHei", "Segoe UI", "Arial" }, 24);
            if (font == null) font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            BuildCanvas();
            BuildEventSystem();
            BuildAudio();
            BuildUI();
            ShowLobby();
            if (cfg.autoplay) StartGame();
        }

        // ───────────────────────── UI scaffolding ─────────────────────────

        private void BuildCanvas()
        {
            if (Camera.main == null)
            {
                var camGo = new GameObject("Main Camera");
                camGo.tag = "MainCamera";
                camGo.transform.SetParent(transform, false);
                var cam = camGo.AddComponent<Camera>();
                cam.clearFlags = CameraClearFlags.SolidColor;
                cam.backgroundColor = new Color(0.027f, 0.039f, 0.059f);
                cam.orthographic = true;
                if (FindFirstObjectByType<AudioListener>() == null) camGo.AddComponent<AudioListener>();
            }
            var go = new GameObject("Canvas", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            go.transform.SetParent(transform, false);
            canvas = go.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.pixelPerfect = false;
            canvasRect = go.GetComponent<RectTransform>();
            var scaler = go.GetComponent<CanvasScaler>();
            // 与设计/截图同尺寸时 scaleFactor = 1，文字与色块按原始像素栅格化，不再二次缩放。
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(DesignW, DesignH);
            scaler.screenMatchMode = CanvasScaler.ScreenMatchMode.MatchWidthOrHeight;
            scaler.matchWidthOrHeight = 1f;
            scaler.referencePixelsPerUnit = 100f;
        }

        private void BuildEventSystem()
        {
            if (FindFirstObjectByType<EventSystem>() != null) return;
            var go = new GameObject("EventSystem", typeof(EventSystem), typeof(InputSystemUIInputModule));
            go.transform.SetParent(transform, false);
        }

        private void BuildAudio()
        {
            sfx = gameObject.AddComponent<AudioSource>();
            sfx.playOnAwake = false;
            sfx.volume = 0.16f;
            clickClip = Tone("click", 740f, 0.045f);
            clearClip = Tone("clear", 980f, 0.08f);
            bonusClip = Tone("bonus", 1320f, 0.18f);
        }

        private static AudioClip Tone(string name, float freq, float seconds)
        {
            const int rate = 22050;
            int n = Mathf.Max(1, (int)(rate * seconds));
            var data = new float[n];
            for (int i = 0; i < n; i++)
            {
                float env = 1f - i / (float)n;
                data[i] = Mathf.Sin(2f * Mathf.PI * freq * i / rate) * env * 0.18f;
            }
            var clip = AudioClip.Create(name, n, 1, rate, false);
            clip.SetData(data, 0);
            return clip;
        }

        private void Play(AudioClip clip)
        {
            if (sfx != null && clip != null) sfx.PlayOneShot(clip);
        }

        private RectTransform MakeRect(string name, Transform parent, float x, float y, float w, float h)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            var rt = go.GetComponent<RectTransform>();
            rt.anchorMin = rt.anchorMax = new Vector2(0, 1);
            rt.pivot = new Vector2(0, 1);
            rt.anchoredPosition = new Vector2(x, -y);
            Vector2 size = new Vector2(w, h);
            rt.sizeDelta = size;
            return rt;
        }

        private Image MakePanel(Transform parent, float x, float y, float w, float h, Color color, string name = "Panel")
        {
            var rt = MakeRect(name, parent, x, y, w, h);
            var img = rt.gameObject.AddComponent<Image>();
            img.color = color;
            return img;
        }

        private Text MakeLabel(Transform parent, string text, float x, float y, float w, float h,
                           int size, Color color, TextAnchor anchor = TextAnchor.UpperLeft)
        {
            var rt = MakeRect("Label", parent, x, y, w, h);
            var t = rt.gameObject.AddComponent<Text>();
            t.font = font;
            t.fontSize = size;
            t.color = color;
            t.alignment = anchor;
            t.horizontalOverflow = HorizontalWrapMode.Wrap;
            t.verticalOverflow = VerticalWrapMode.Overflow;
            t.raycastTarget = false;
            t.text = text;
            return t;
        }

        private Button MakeButton(Transform parent, string text, float x, float y, float w, float h,
                              Action click, Color? color = null, int size = 20)
        {
            var rt = MakeRect("Btn", parent, x, y, w, h);
            var img = rt.gameObject.AddComponent<Image>();
            img.color = color ?? new Color(0.13f, 0.19f, 0.26f, 0.96f);
            var b = rt.gameObject.AddComponent<Button>();
            b.targetGraphic = img;
            var t = MakeLabel(rt, text, 0, 0, w, h, size, new Color(0.92f, 0.97f, 1f), TextAnchor.MiddleCenter);
            if (click != null) b.onClick.AddListener(() => click());
            b.onClick.AddListener(() => Play(clickClip));
            return b;
        }

        private void FullPanel(string name, Color color)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(canvas.transform, false);
            var rt = go.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.pivot = new Vector2(0.5f, 0.5f);
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;
            var img = go.AddComponent<Image>();
            img.color = color;
            img.raycastTarget = true;
        }

        [Serializable]
        private sealed class FeatureState
        {
            public string case_id;
            public string project_path;
            public bool legacy_ui, legacy_material, legacy_match_alloc, sync_shop_load;
            public bool legacy_particle, legacy_canvas, legacy_texture, legacy_hint;
            public bool legacy_anim;
        }

        private static void WriteFeatureState(CaseConfig c)
        {
            try
            {
                var fs = new FeatureState
                {
                    case_id = c.case_id,
                    project_path = Path.GetDirectoryName(Application.dataPath).Replace("\\", "/"),
                    legacy_ui = c.legacy_ui,
                    legacy_material = c.legacy_material,
                    legacy_match_alloc = c.legacy_match_alloc,
                    sync_shop_load = c.sync_shop_load,
                    legacy_particle = c.legacy_particle,
                    legacy_canvas = c.legacy_canvas,
                    legacy_texture = c.legacy_texture,
                    legacy_hint = c.legacy_hint,
                    legacy_anim = c.legacy_anim,
                };
                // 运行期特征状态，仅用于“当前激活了哪些路径”；不是 oracle。
                File.WriteAllText(Path.Combine(Path.GetTempPath(), "devsim_active_features.json"),
                                  JsonUtility.ToJson(fs));
            }
            catch (Exception e) { Debug.LogWarning("DevSim feature state write failed: " + e.Message); }
        }

        private void BuildUI()
        {
            FullPanel("Bg", new Color(0.027f, 0.039f, 0.059f));
            MakePanel(canvas.transform, 0, 0, DesignW, 56, new Color(0.055f, 0.082f, 0.118f, 0.96f), "TopBand");
            MakePanel(canvas.transform, 0, 684, DesignW, 36, new Color(0.055f, 0.082f, 0.118f, 0.96f), "BottomBand");

            gameRoot = new GameObject("GameRoot", typeof(RectTransform));
            gameRoot.transform.SetParent(canvas.transform, false);
            var gr = gameRoot.GetComponent<RectTransform>();
            gr.anchorMin = Vector2.zero; gr.anchorMax = Vector2.one;
            gr.offsetMin = gr.offsetMax = Vector2.zero;

            MakeLabel(gameRoot.transform, "CHROMA ROGUE", 24, 12, 250, 34, 24,
                  new Color(0.95f, 0.85f, 0.55f), TextAnchor.MiddleLeft);
            roundText = MakeLabel(gameRoot.transform, "ROUND 1", 286, 14, 100, 30, 18, Color.white);
            targetText = MakeLabel(gameRoot.transform, "TARGET 600", 386, 14, 150, 30, 18, new Color(0.7f, 0.85f, 1f));
            scoreText = MakeLabel(gameRoot.transform, "SCORE 0", 540, 14, 210, 30, 18, Color.white);
            timeText = MakeLabel(gameRoot.transform, "30.0s", 760, 12, 90, 32, 22,
                  new Color(1f, 0.75f, 0.4f), TextAnchor.MiddleLeft);
            var barBg = MakePanel(gameRoot.transform, 852, 20, 180, 12, new Color(0.1f, 0.15f, 0.21f), "TimeBg");
            var fillGo = MakePanel(barBg.transform, 0, 0, 180, 12, new Color(0.33f, 0.78f, 1f), "TimeFill");
            timeFill = fillGo;
            timeFill.type = Image.Type.Filled;
            timeFill.fillMethod = Image.FillMethod.Horizontal;
            timeFill.fillOrigin = (int)Image.OriginHorizontal.Left;
            coinText = MakeLabel(gameRoot.transform, "COINS 0", 1050, 14, 200, 30, 18,
                  new Color(1f, 0.86f, 0.45f), TextAnchor.MiddleLeft);
            statusText = MakeLabel(gameRoot.transform, "拖拽相邻色块交换 · 3 连消除", 24, 62, 800, 26, 15,
                  new Color(0.65f, 0.76f, 0.9f));
            stepText = MakeLabel(gameRoot.transform, "", 840, 44, 416, 26, 15,
                  new Color(0.55f, 0.9f, 0.8f), TextAnchor.MiddleRight);

            var boardPanel = MakePanel(gameRoot.transform, BoardX, BoardY, BoardSize, BoardSize,
                                  new Color(0.055f, 0.08f, 0.115f, 0.98f), "BoardPanel");
            boardRoot = boardPanel.rectTransform;

            var side = MakePanel(gameRoot.transform, 604, BoardY, 652, BoardSize,
                            new Color(0.055f, 0.08f, 0.115f, 0.98f), "SidePanel");
            sideRect = side.rectTransform;
            BuildSidePanel(side.transform);

            BuildLobby();
            BuildShop();
            BuildBonus();
            BuildOver();
            BuildPreview();
            BuildFxPool();
        }

        private void BuildSidePanel(Transform side)
        {
            MakeLabel(side, "ITEMS  ·  拖到棋盘格使用", 18, 12, 420, 24, 16, new Color(0.9f, 0.8f, 0.5f));
            itemCountLabels = new Text[itemNames.Length];
            for (int i = 0; i < itemNames.Length; i++)
            {
                int id = i;
                float x = 18 + (i % 2) * 316;
                float y = 40 + (i / 2) * 82;
                var rt = MakeRect("ItemSlot", side, x, y, 300, 72);
                var bg = rt.gameObject.AddComponent<Image>();
                bg.color = new Color(0.11f, 0.165f, 0.225f, 0.98f);
                var icon = MakeRect("Icon", rt, 10, 8, 56, 56).gameObject.AddComponent<Image>();
                icon.sprite = ItemIcon(id);
                icon.color = Color.white;
                icon.raycastTarget = false;
                var label = MakeLabel(rt, itemNames[id], 76, 12, 210, 30, 20, Color.white);
                label.raycastTarget = false;
                itemCountLabels[id] = MakeLabel(rt, "x0", 76, 42, 210, 22, 15,
                                                new Color(0.7f, 0.82f, 0.95f));
                var drag = rt.gameObject.AddComponent<ItemDrag>();
                drag.game = this;
                drag.item = id;
                itemSlots.Add(rt.gameObject);
                // 槽位只响应拖拽，不做“点选后再点格子”。
            }

            MakeLabel(side, "HOW TO PLAY", 18, 216, 400, 24, 16, new Color(0.9f, 0.8f, 0.5f));
            MakeLabel(side,
                  "• 拖拽相邻色块交换，3 连消除；无效交换会回弹。\n" +
                  "• 每轮倒计时结束才进下一轮；达到目标分才能继续。\n" +
                  "• 一次操作及连锁中，同一种颜色消除 ≥6 → BONUS。\n" +
                  "• 数字 = 该格叠的色块数，≥2 不能换位；同色消除/道具只拆 1 层。\n" +
                  "• 每 3 轮进商店；棋盘随难度从 10×10 缩到 6×6。",
                  18, 244, 616, 150, 14, new Color(0.78f, 0.86f, 0.96f));

            MakeLabel(side, "HIGH SCORES", 18, 398, 300, 24, 16, new Color(0.9f, 0.8f, 0.5f));
            highText = MakeLabel(side, "—", 18, 424, 300, 120, 13, new Color(0.75f, 0.84f, 0.95f));
            MakeButton(side, "END RUN", 470, 500, 160, 44, EndRun, new Color(0.28f, 0.12f, 0.16f), 16);
        }

        private void BuildFxPool()
        {
            // 基线：粒子池；legacy_particle 分支才 Instantiate/Destroy。
            for (int i = 0; i < 32; i++)
            {
                var img = MakePanel(canvas.transform, 0, 0, 20, 20, Color.white, "Fx" + i);
                img.raycastTarget = false;
                img.gameObject.SetActive(false);
                _fxPool.Add(img);
            }
        }

        private void BuildLobby()
        {
            lobbyRoot = new GameObject("Lobby", typeof(RectTransform));
            lobbyRoot.transform.SetParent(canvas.transform, false);
            var rt = lobbyRoot.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
            rt.offsetMin = rt.offsetMax = Vector2.zero;
            MakePanel(lobbyRoot.transform, 0, 0, DesignW, DesignH, new Color(0.027f, 0.039f, 0.059f, 1f), "LobbyBg");
            MakePanel(lobbyRoot.transform, 96, 88, 1088, 544, new Color(0.055f, 0.085f, 0.13f, 0.97f), "LobbyCard");
            MakeLabel(lobbyRoot.transform, "CHROMA", 150, 128, 640, 74, 62, new Color(1f, 0.82f, 0.42f));
            MakeLabel(lobbyRoot.transform, "ROGUE", 150, 194, 640, 66, 62, new Color(0.55f, 0.8f, 1f));
            MakeLabel(lobbyRoot.transform, "A tiny match-3 roguelite. Swap fast, break the stack, bank the clock.",
                  150, 274, 860, 32, 18, new Color(0.72f, 0.82f, 0.95f));
            MakeButton(lobbyRoot.transform, "开始游戏", 150, 336, 300, 60, StartDefaultGame,
                   new Color(0.18f, 0.42f, 0.32f), 24);
            MakeButton(lobbyRoot.transform, "说明", 150, 408, 300, 52, ShowHelp,
                   new Color(0.18f, 0.23f, 0.3f), 20);
            var caseBtn = MakeButton(lobbyRoot.transform, "", 150, 474, 400, 50,
                   ToggleCaseMenu, new Color(0.12f, 0.18f, 0.26f), 16);
            caseButtonLabel = caseBtn.GetComponentInChildren<Text>();
            caseButtonLabel.text = "专项测试  c18 · 混合  ▾";
            MakeLabel(lobbyRoot.transform, "LOCAL ARCADE", 830, 336, 300, 26, 18, new Color(0.9f, 0.75f, 0.4f));
            MakeLabel(lobbyRoot.transform, "Top scores are stored on this machine.", 830, 366, 320, 50, 15,
                   new Color(0.65f, 0.76f, 0.88f));
            MakeLabel(lobbyRoot.transform, "v0.3 · 时间驱动回合 · 动态棋盘 · 拖拽道具 · no network",
                   150, 566, 900, 26, 14, new Color(0.4f, 0.5f, 0.62f));
            BuildCaseMenu();
            BuildHelp();
        }

        private void BuildCaseMenu()
        {
            caseMenu = new GameObject("CaseMenu", typeof(RectTransform));
            caseMenu.transform.SetParent(lobbyRoot.transform, false);
            var rt = caseMenu.GetComponent<RectTransform>();
            rt.anchorMin = rt.anchorMax = new Vector2(0, 1);
            rt.pivot = new Vector2(0, 1);
            rt.anchoredPosition = new Vector2(150, -532);
            rt.sizeDelta = new Vector2(920, 300);
            MakePanel(caseMenu.transform, 0, 0, 920, 300, new Color(0.04f, 0.07f, 0.11f, 0.99f), "CaseBg");
            MakeLabel(caseMenu.transform, "专项测试（选中即启动，等价于 replay --case）", 16, 8, 860, 24, 14,
                  new Color(0.75f, 0.85f, 0.95f));
            for (int i = 0; i < DevSimScenarios.Ids.Length; i++)
            {
                string id = DevSimScenarios.Ids[i];
                float x = 16 + (i % 2) * 460;
                float y = 38 + (i / 2) * 21;
                MakeButton(caseMenu.transform, DevSimScenarios.Label(id), x, y, 430, 19,
                       () => StartCase(id), new Color(0.1f, 0.16f, 0.23f), 11);
            }
            caseMenu.SetActive(false);
        }

        private void BuildHelp()
        {
            helpPanel = new GameObject("Help", typeof(RectTransform));
            helpPanel.transform.SetParent(lobbyRoot.transform, false);
            var rt = helpPanel.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
            rt.offsetMin = rt.offsetMax = Vector2.zero;
            MakePanel(helpPanel.transform, 0, 0, DesignW, DesignH, new Color(0f, 0f, 0f, 0.8f), "HelpDim");
            MakePanel(helpPanel.transform, 240, 92, 800, 540, new Color(0.06f, 0.1f, 0.15f, 1f), "HelpCard");
            MakeLabel(helpPanel.transform, "说明", 280, 116, 300, 40, 30, new Color(1f, 0.82f, 0.42f));
            MakeLabel(helpPanel.transform,
                  "• 拖拽相邻色块交换；3 连消除，无效交换回弹。\n" +
                  "• 每轮倒计时结束才结算：达到目标分进入下一轮，否则本局结束。\n" +
                  "• 回合间有预告：目标分、当前分、下一轮时长、棋盘尺寸变化。\n" +
                  "• 一次交换及其连锁中，同一种颜色消除 ≥6 → BONUS 三选一。\n" +
                  "• 棋盘随难度从 10×10 缩到 6×6；每 3 轮进一次商店。\n" +
                  "• 数字色块 = 叠了 N 个色块：N≥2 不能换位，同色消除或道具只拆 1 层。\n" +
                  "• 道具按住拖到棋盘目标格使用；非法目标回弹并提示。\n" +
                  "• 本地 Top 10 存在这台机器上。",
                  280, 168, 720, 380, 17, new Color(0.8f, 0.88f, 0.98f));
            MakeButton(helpPanel.transform, "关闭", 540, 566, 200, 46,
                   () => helpPanel.SetActive(false), new Color(0.18f, 0.3f, 0.42f), 18);
            helpPanel.SetActive(false);
        }

        private void ToggleCaseMenu()
        {
            if (caseMenu != null) caseMenu.SetActive(!caseMenu.activeSelf);
        }

        private void StartDefaultGame()
        {
            cfg = new CaseConfig
            {
                case_id = "default", seed = 20261112,
                round_seconds = 30f, target_base = 600,
            };
            WriteFeatureState(cfg);
            caseButtonLabel.text = "专项测试  c18 · 混合  ▾";
            StartGame();
        }

        private void StartCase(string id)
        {
            cfg = DevSimScenarios.For(id);
            WriteFeatureState(cfg);
            caseButtonLabel.text = "专项测试  " + DevSimScenarios.Label(id) + "  ▾";
            if (caseMenu != null) caseMenu.SetActive(false);
            StartGame();
        }

        // ───────────────────────── preview / shop / bonus / over ─────────────────────────

        private void BuildPreview()
        {
            previewPanel = new GameObject("Preview", typeof(RectTransform));
            previewPanel.transform.SetParent(canvas.transform, false);
            var rt = previewPanel.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
            rt.offsetMin = rt.offsetMax = Vector2.zero;
            MakePanel(previewPanel.transform, 0, 0, DesignW, DesignH, new Color(0f, 0f, 0f, 0.55f), "PreviewDim");
            var card = MakePanel(previewPanel.transform, 340, 190, 600, 340,
                             new Color(0.07f, 0.1f, 0.15f, 1f), "PreviewCard");
            previewTitle = MakeLabel(card.transform, "ROUND 2", 30, 18, 540, 46, 36, new Color(0.55f, 0.85f, 1f));
            MakeLabel(card.transform, "目标分 / 当前分 / 本轮时长 / 棋盘尺寸", 30, 66, 540, 24, 13,
                  new Color(0.55f, 0.66f, 0.78f));
            previewBody = MakeLabel(card.transform, "", 30, 94, 540, 136, 18, new Color(0.82f, 0.9f, 0.98f));
            previewCountdown = MakeLabel(card.transform, "", 30, 236, 300, 30, 16, new Color(0.6f, 0.72f, 0.85f));
            MakeButton(card.transform, "开始本轮", 380, 228, 190, 50, ContinueFromPreview,
                   new Color(0.18f, 0.42f, 0.32f), 18);
            previewPanel.SetActive(false);
        }

        private void BuildShop()
        {
            shopPanel = new GameObject("Shop", typeof(RectTransform));
            shopPanel.transform.SetParent(canvas.transform, false);
            var rt = shopPanel.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
            rt.offsetMin = rt.offsetMax = Vector2.zero;
            MakePanel(shopPanel.transform, 0, 0, DesignW, DesignH, new Color(0f, 0f, 0f, 0.72f), "ShopDim");
            MakePanel(shopPanel.transform, 150, 92, 980, 536, new Color(0.07f, 0.1f, 0.15f, 1f), "ShopCard");
            MakeLabel(shopPanel.transform, "SHOP  ·  每 3 轮一次", 190, 116, 500, 44, 32, new Color(1f, 0.82f, 0.42f));
            shopCoins = MakeLabel(shopPanel.transform, "COINS 0", 790, 124, 300, 32, 20,
                              new Color(1f, 0.85f, 0.4f), TextAnchor.MiddleRight);
            for (int i = 0; i < 3; i++)
            {
                int slot = i;
                var card = new ShopCard();
                card.bg = MakePanel(shopPanel.transform, 190 + i * 300, 176, 280, 330,
                                new Color(0.1f, 0.15f, 0.21f), "Offer" + i);
                card.icon = MakeRect("Icon", card.bg.transform, 100, 12, 80, 80).gameObject.AddComponent<Image>();
                card.icon.raycastTarget = false;
                card.title = MakeLabel(card.bg.transform, "", 16, 98, 248, 28, 20, Color.white,
                                  TextAnchor.MiddleCenter);
                card.desc = MakeLabel(card.bg.transform, "", 16, 128, 248, 56, 13,
                                  new Color(0.72f, 0.82f, 0.93f), TextAnchor.MiddleCenter);
                card.price = MakeLabel(card.bg.transform, "", 16, 188, 248, 24, 16,
                                  new Color(1f, 0.86f, 0.45f), TextAnchor.MiddleCenter);
                card.buy = MakeButton(card.bg.transform, "BUY", 20, 224, 240, 46, () => BuySlot(slot),
                                  new Color(0.18f, 0.38f, 0.3f), 17);
                card.buyLabel = card.buy.GetComponentInChildren<Text>();
                card.state = MakeLabel(card.bg.transform, "", 16, 276, 248, 24, 14,
                                  new Color(1f, 0.55f, 0.45f), TextAnchor.MiddleCenter);
                card.soldOverlay = MakePanel(card.bg.transform, 0, 0, 280, 330,
                                  new Color(0.02f, 0.03f, 0.05f, 0.55f), "Sold");
                card.soldOverlay.gameObject.SetActive(false);
                shopCards[i] = card;
            }
            MakeButton(shopPanel.transform, "刷新 (40)", 190, 540, 200, 48, RefreshShop,
                   new Color(0.22f, 0.3f, 0.4f), 17);
            MakeButton(shopPanel.transform, "离开商店", 730, 540, 220, 48, CloseShop,
                   new Color(0.3f, 0.24f, 0.16f), 18);
            var msg = MakeLabel(shopPanel.transform, "", 400, 548, 320, 32, 14,
                            new Color(0.85f, 0.75f, 0.6f), TextAnchor.MiddleCenter);
            msg.name = "ShopMsg";
            shopPanel.SetActive(false);
        }

        private void BuildBonus()
        {
            bonusPanel = new GameObject("Bonus", typeof(RectTransform));
            bonusPanel.transform.SetParent(canvas.transform, false);
            var rt = bonusPanel.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
            rt.offsetMin = rt.offsetMax = Vector2.zero;
            MakePanel(bonusPanel.transform, 0, 0, DesignW, DesignH, new Color(0f, 0f, 0f, 0.72f), "BonusDim");
            MakePanel(bonusPanel.transform, 230, 170, 820, 330, new Color(0.07f, 0.1f, 0.15f, 1f), "BonusCard");
            MakeLabel(bonusPanel.transform, "BONUS", 270, 192, 400, 44, 36, new Color(0.55f, 0.85f, 1f));
            MakeLabel(bonusPanel.transform, "同色消除 ≥6 · 选择一项，倒计时暂停", 270, 236, 700, 28, 17,
                  new Color(0.75f, 0.84f, 0.95f));
            var bonusCard = bonusPanel.transform.Find("BonusCard");
            for (int i = 0; i < 3; i++)
            {
                int slot = i;
                var b = MakeButton(bonusCard, "", 30 + i * 260, 78, 240, 170, () => ChooseBonus(slot),
                       new Color(0.12f, 0.18f, 0.25f), 18);
                bonusLabels[i] = b.GetComponentInChildren<Text>();
                bonusLabels[i].fontSize = 20;
                bonusGroups[i] = b.gameObject.AddComponent<CanvasGroup>();
            }
            bonusPanel.SetActive(false);
        }

        private void BuildOver()
        {
            overPanel = new GameObject("Over", typeof(RectTransform));
            overPanel.transform.SetParent(canvas.transform, false);
            var rt = overPanel.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
            rt.offsetMin = rt.offsetMax = Vector2.zero;
            MakePanel(overPanel.transform, 0, 0, DesignW, DesignH, new Color(0f, 0f, 0f, 0.78f), "OverDim");
            MakePanel(overPanel.transform, 340, 170, 600, 370, new Color(0.07f, 0.1f, 0.15f, 1f), "OverCard");
            MakeLabel(overPanel.transform, "RUN OVER", 380, 194, 520, 48, 38, new Color(1f, 0.55f, 0.45f));
            overText = MakeLabel(overPanel.transform, "", 380, 250, 520, 130, 20, Color.white);
            MakeButton(overPanel.transform, "再来一局", 380, 430, 220, 54, () => { overPanel.SetActive(false); StartGame(); },
                   new Color(0.18f, 0.42f, 0.32f), 20);
            MakeButton(overPanel.transform, "回主菜单", 640, 430, 220, 54, () => { overPanel.SetActive(false); ShowLobby(); },
                   new Color(0.2f, 0.26f, 0.34f), 20);
            overPanel.SetActive(false);
        }

        // ───────────────────────── sprites ─────────────────────────

        private static float RoundRectDist(float px, float py, float hx, float hy, float r)
        {
            float qx = Mathf.Abs(px) - hx + r;
            float qy = Mathf.Abs(py) - hy + r;
            float outside = Mathf.Sqrt(Mathf.Max(qx, 0f) * Mathf.Max(qx, 0f)
                                       + Mathf.Max(qy, 0f) * Mathf.Max(qy, 0f));
            float inside = Mathf.Min(Mathf.Max(qx, qy), 0f);
            return outside + inside - r;
        }

        /// <summary>按最终显示像素生成圆角色块：4x 超采样得到平滑边缘，1:1 绘制不放大。</summary>
        private Sprite BuildTileSprite(int size)
        {
            if (tileSprites.TryGetValue(size, out var cached) && cached != null) return cached;
            var sp = MakeTileSprite(size);
            tileSprites[size] = sp;
            return sp;
        }

        /// <summary>不缓存的同款色块贴图；劣化路径用它制造“每格一份纹理”，外观与共享贴图完全一致。</summary>
        private Sprite MakeTileSprite(int size)
        {
            int S = Mathf.Clamp(size, 16, 160);
            var tex = new Texture2D(S, S, TextureFormat.RGBA32, false);
            tex.name = "devsim_tile_" + S;
            tex.filterMode = FilterMode.Bilinear;
            tex.wrapMode = TextureWrapMode.Clamp;
            var px = new Color32[S * S];
            const int SS = 4;
            float r = Mathf.Clamp(S * 0.24f, 3f, 34f) - 1.2f;
            float half = S * 0.5f - 1.2f;
            for (int y = 0; y < S; y++)
            {
                for (int x = 0; x < S; x++)
                {
                    int cover = 0;
                    float shade = 0f;
                    for (int sy = 0; sy < SS; sy++)
                    {
                        for (int sx = 0; sx < SS; sx++)
                        {
                            float fx = x + (sx + 0.5f) / SS;
                            float fy = y + (sy + 0.5f) / SS;
                            float d = RoundRectDist(fx - S * 0.5f, fy - S * 0.5f, half, half, r);
                            if (d <= 0f) { cover++; shade += fy / S; }
                        }
                    }
                    if (cover == 0) { px[y * S + x] = new Color32(255, 255, 255, 0); continue; }
                    float dc = RoundRectDist(x + 0.5f - S * 0.5f, y + 0.5f - S * 0.5f, half, half, r);
                    float rim = Mathf.Clamp01(1f - Mathf.Abs(dc + 1.4f) / 1.8f) * 0.30f;
                    float grad = 0.82f + 0.18f * (shade / cover);
                    float v = Mathf.Clamp01(grad + rim);
                    byte b = (byte)(v * 255f);
                    byte a = (byte)(255f * cover / (SS * SS));
                    px[y * S + x] = new Color32(b, b, b, a);
                }
            }
            tex.SetPixels32(px);
            tex.Apply(false, false);
            return Sprite.Create(tex, new Rect(0, 0, S, S), new Vector2(0.5f, 0.5f), 100f, 0,
                                 SpriteMeshType.FullRect);
        }

        public Sprite ItemIcon(int item)
        {
            item = Mathf.Clamp(item, 0, itemNames.Length - 1);
            return BuildItemIcon(item);
        }

        private Sprite BuildItemIcon(int item)
        {
            int key = 100 + item;
            if (iconSprites.TryGetValue(key, out var cached) && cached != null) return cached;
            const int S = 56;
            var tex = new Texture2D(S, S, TextureFormat.RGBA32, false);
            tex.name = "devsim_icon_" + item;
            tex.filterMode = FilterMode.Bilinear;
            tex.wrapMode = TextureWrapMode.Clamp;
            var px = new Color32[S * S];
            const int SS = 3;
            Color32 clear = new Color32(0, 0, 0, 0);
            for (int y = 0; y < S; y++)
                for (int x = 0; x < S; x++) px[y * S + x] = clear;

            void Dot(float cx, float cy, float rad, Color32 col, float alpha)
            {
                for (int y = 0; y < S; y++)
                    for (int x = 0; x < S; x++)
                    {
                        int cover = 0;
                        for (int sy = 0; sy < SS; sy++)
                            for (int sx = 0; sx < SS; sx++)
                            {
                                float fx = x + (sx + 0.5f) / SS - cx;
                                float fy = y + (sy + 0.5f) / SS - cy;
                                if (fx * fx + fy * fy <= rad * rad) cover++;
                            }
                        if (cover == 0) continue;
                        float a = alpha * cover / (SS * SS);
                        var cur = px[y * S + x];
                        float na = a + cur.a / 255f * (1f - a);
                        if (na <= 0f) continue;
                        px[y * S + x] = new Color32(
                            (byte)((col.r * a + cur.r * (cur.a / 255f) * (1f - a)) / na),
                            (byte)((col.g * a + cur.g * (cur.a / 255f) * (1f - a)) / na),
                            (byte)((col.b * a + cur.b * (cur.a / 255f) * (1f - a)) / na),
                            (byte)(na * 255f));
                    }
            }

            void Bar(float x0, float y0, float x1, float y1, float thick, Color32 col)
            {
                float dx = x1 - x0, dy = y1 - y0;
                float len2 = Mathf.Max(0.001f, dx * dx + dy * dy);
                for (int y = 0; y < S; y++)
                    for (int x = 0; x < S; x++)
                    {
                        int cover = 0;
                        for (int sy = 0; sy < SS; sy++)
                            for (int sx = 0; sx < SS; sx++)
                            {
                                float fx = x + (sx + 0.5f) / SS;
                                float fy = y + (sy + 0.5f) / SS;
                                float t = Mathf.Clamp01(((fx - x0) * dx + (fy - y0) * dy) / len2);
                                float ex = x0 + dx * t - fx, ey = y0 + dy * t - fy;
                                if (ex * ex + ey * ey <= thick * thick) cover++;
                            }
                        if (cover == 0) continue;
                        float a = cover / (SS * SS);
                        var cur = px[y * S + x];
                        float na = a + cur.a / 255f * (1f - a);
                        if (na <= 0f) continue;
                        px[y * S + x] = new Color32(
                            (byte)((col.r * a + cur.r * (cur.a / 255f) * (1f - a)) / na),
                            (byte)((col.g * a + cur.g * (cur.a / 255f) * (1f - a)) / na),
                            (byte)((col.b * a + cur.b * (cur.a / 255f) * (1f - a)) / na),
                            (byte)(na * 255f));
                    }
            }

            switch (item)
            {
                case 0: // bomb
                    Dot(28, 24, 16, new Color32(52, 58, 70, 255), 1f);
                    Dot(28, 24, 12, new Color32(88, 96, 112, 255), 1f);
                    Dot(22, 30, 4.5f, new Color32(210, 220, 235, 255), 0.9f);
                    Bar(34, 38, 42, 48, 3.2f, new Color32(255, 176, 64, 255));
                    Dot(45, 50, 4.5f, new Color32(255, 214, 96, 255), 1f);
                    break;
                case 1: // recolor
                    Dot(22, 33, 12, Palette[0], 0.95f);
                    Dot(34, 33, 12, Palette[1], 0.95f);
                    Dot(28, 21, 12, Palette[3], 0.95f);
                    break;
                case 2: // hammer
                    Bar(24, 12, 24, 40, 4.2f, new Color32(196, 148, 92, 255));
                    Bar(12, 42, 44, 42, 7.5f, new Color32(150, 160, 178, 255));
                    Bar(12, 34, 44, 34, 5f, new Color32(120, 130, 148, 255));
                    break;
                default: // shuffle
                    Bar(10, 36, 42, 36, 3.6f, new Color32(120, 220, 255, 255));
                    Bar(42, 36, 34, 42, 3.2f, new Color32(120, 220, 255, 255));
                    Bar(42, 36, 34, 30, 3.2f, new Color32(120, 220, 255, 255));
                    Bar(10, 20, 42, 20, 3.6f, new Color32(255, 200, 90, 255));
                    Bar(10, 20, 18, 26, 3.2f, new Color32(255, 200, 90, 255));
                    Bar(10, 20, 18, 14, 3.2f, new Color32(255, 200, 90, 255));
                    break;
            }
            tex.SetPixels32(px);
            tex.Apply(false, false);
            var sp = Sprite.Create(tex, new Rect(0, 0, S, S), new Vector2(0.5f, 0.5f), 100f, 0,
                                   SpriteMeshType.FullRect);
            iconSprites[key] = sp;
            return sp;
        }

        // ───────────────────────── state / loop ─────────────────────────

        private void ShowLobby()
        {
            phase = Phase.Lobby;
            busy = false; dragging = false; dragR = dragC = -1;
            pendingRoundEnd = pendingGameOver = false;
            ShowOnlyOverlay(null);
            if (gameRoot) gameRoot.SetActive(false);
            if (caseMenu) caseMenu.SetActive(false);
            if (helpPanel) helpPanel.SetActive(false);
            if (lobbyRoot) lobbyRoot.SetActive(true);
            RefreshHighScores();
        }

        private void StartGame()
        {
            phase = Phase.Playing;
            busy = false; dragging = false; dragR = dragC = -1;
            hoverR = hoverC = -1; selectedR = selectedC = -1;
            round = 1; target = cfg.TargetFor(1); totalScore = 0; coins = 0; tier = cfg.start_tier;
            multiplier = 1; bonusTime = 0;
            bonusTriggeredCount = 0;
            shopVisits = 0; refreshCount = 0; shopMessage = "";
            lastCleared = 0; lastChain = 0; lastSameColorCount = 0; lastSameColorId = 0;
            for (int i = 0; i < inventory.Length; i++) inventory[i] = 0;
            for (int i = 0; i < lastPerColor.Length; i++) lastPerColor[i] = 0;
            if (lobbyRoot) lobbyRoot.SetActive(false);
            if (caseMenu) caseMenu.SetActive(false);
            if (helpPanel) helpPanel.SetActive(false);
            if (gameRoot) gameRoot.SetActive(true);
            ShowOnlyOverlay(null);
            overOpen = false; bonusOpen = false; bonusChoiceLocked = false;
            pendingRoundEnd = pendingGameOver = false;
            model = null;
            BeginRound(1);
            if (cfg.autoplay) StartCoroutine(AutoPlay());
        }

        private void BeginRound(int r)
        {
            round = r;
            target = cfg.TargetFor(r);
            int n = cfg.BoardSizeFor(r);
            timeLeft = cfg.RoundSecondsFor(r) + bonusTime;
            bonusTime = 0;
            phase = Phase.Playing;
            int oldN = model != null ? model.N : -1;
            if (oldN != n) BuildBoard(n);
            else model.ClearGuard();
            ShowOnlyOverlay(null);
            pendingRoundEnd = pendingGameOver = false;
            RefreshTiles();
            UpdateHud();
            statusText.text = "ROUND " + r + " — 目标 " + target + " · " + Mathf.RoundToInt(timeLeft) + "s";
        }

        private void Update()
        {
            if (cfg.legacy_canvas && sideRect != null)
                sideRect.sizeDelta = new Vector2(652, 560);
            if (cfg.legacy_hint && phase == Phase.Playing) HasMatch();
            if (cfg.legacy_anim && phase == Phase.Playing) AnimateTilesLegacy();
            float dt = Time.deltaTime;
            if (phase == Phase.Playing)
            {
                timeLeft -= dt;
                if (timeLeft <= 0f)
                {
                    // 时间到：若还在播消除/掉落动画，先记账，等动效落完再结算，
                    // 避免“预告已弹出、随后又触发 BONUS”两套遮罩互相锁死。
                    timeLeft = 0f;
                    pendingRoundEnd = true;
                }
            }
            else if (phase == Phase.Preview)
            {
                if (previewLeft > 0f)
                {
                    previewLeft -= dt;
                    if (previewLeft <= 0f) { previewLeft = 0f; ContinueFromPreview(); }
                    else if (previewCountdown != null)
                        previewCountdown.text = "自动开始 " + previewLeft.ToString("0.0") + "s";
                }
            }
            ResolvePendingWhenIdle();
            if (cfg.legacy_ui || Time.unscaledTime >= nextHud)
            {
                nextHud = Time.unscaledTime + 0.1f;
                UpdateHud();
            }
        }

        /// <summary>只有“没有动画在播、没有 BONUS 挡着”时才真正结算时间到/手动结束。</summary>
        private void ResolvePendingWhenIdle()
        {
            if (busy || bonusOpen || phase != Phase.Playing) return;
            if (pendingGameOver)
            {
                pendingGameOver = false;
                EndGame();
                return;
            }
            if (pendingRoundEnd) EndRound();
        }

        private bool HasMatch()
        {
            return model != null && model.HasMatch();
        }

        private void AnimateTilesLegacy()
        {
            // “图省事”的逐帧 UI 动画：所有格子每帧重算缩放。
            for (int r = 0; r < boardN; r++)
                for (int c = 0; c < boardN; c++)
                {
                    var rt = tileRects[r, c];
                    if (rt == null) continue;
                    float s = 1f + 0.002f * Mathf.Sin(Time.time * 4f + (r + c) * 0.25f);
                    rt.localScale = new Vector3(s, s, 1f);
                }
        }

        private void UpdateHud()
        {
            if (roundText == null) return;
            roundText.text = "ROUND " + round;
            targetText.text = "TARGET " + target;
            scoreText.text = "SCORE " + totalScore;
            timeText.text = Mathf.Max(0, timeLeft).ToString("0.0") + "s";
            if (timeFill != null)
            {
                float total = Mathf.Max(1f, cfg.RoundSecondsFor(round));
                float ratio = Mathf.Clamp01(timeLeft / total);
                timeFill.fillAmount = ratio;
                timeFill.color = ratio > 0.5f ? new Color(0.33f, 0.78f, 1f)
                    : ratio > 0.2f ? new Color(1f, 0.75f, 0.4f)
                    : new Color(1f, 0.36f, 0.36f);
                if (ratio < 0.2f) timeText.rectTransform.localScale =
                    Vector3.one * (1f + 0.08f * Mathf.Sin(Time.unscaledTime * 12f));
                else timeText.rectTransform.localScale = Vector3.one;
            }
            coinText.text = "COINS " + coins;
            RefreshItemLabels();
        }

        private void RefreshItemLabels()
        {
            if (itemCountLabels == null) return;
            for (int i = 0; i < itemCountLabels.Length; i++)
                if (itemCountLabels[i] != null) itemCountLabels[i].text = "x" + inventory[i];
        }

        // ───────────────────────── board ─────────────────────────

        private void ClearBoardCells()
        {
            for (int i = 0; i < boardCells.Count; i++)
                if (boardCells[i] != null) Destroy(boardCells[i]);
            boardCells.Clear();
            tileImages = null;
            tileArmor = null;
            tileRects = null;
            tileBase = null;
        }

        private void BuildBoard(int n)
        {
            boardN = Mathf.Clamp(n, 4, 12);
            model = new BoardModel(boardN, Colors);
            model.Build(rng);
            ClearBoardCells();

            float usable = BoardSize - BoardPad * 2f;
            gapPx = boardN <= 8 ? 5f : 4f;
            tilePx = Mathf.Floor((usable - gapPx * (boardN - 1)) / boardN);
            float step = tilePx + gapPx;
            float offset = (usable - (tilePx * boardN + gapPx * (boardN - 1))) * 0.5f;

            tileImages = new Image[boardN, boardN];
            tileArmor = new Text[boardN, boardN];
            tileRects = new RectTransform[boardN, boardN];
            tileBase = new Vector2[boardN, boardN];
            var spr = BuildTileSprite((int)tilePx);
            int armorSize = Mathf.Max(12, Mathf.RoundToInt(tilePx * 0.44f));

            for (int r = 0; r < boardN; r++)
            {
                for (int c = 0; c < boardN; c++)
                {
                    float x = BoardPad + offset + c * step;
                    float y = BoardPad + offset + r * step;
                    var go = new GameObject("Tile_" + r + "_" + c, typeof(RectTransform), typeof(Image));
                    go.transform.SetParent(boardRoot, false);
                    var rt = go.GetComponent<RectTransform>();
                    rt.anchorMin = rt.anchorMax = new Vector2(0, 1);
                    rt.pivot = new Vector2(0, 1);
                    rt.anchoredPosition = new Vector2(x, -y);
                    rt.sizeDelta = new Vector2(tilePx, tilePx);
                    var img = go.GetComponent<Image>();
                    img.sprite = spr;
                    img.type = Image.Type.Simple;
                    img.color = Palette[model.Color[r, c]];
                    var shadow = go.AddComponent<Shadow>();
                    shadow.effectColor = new Color(0f, 0f, 0f, 0.42f);
                    shadow.effectDistance = new Vector2(0f, -3f);
                    var drag = go.AddComponent<TileDrag>();
                    drag.game = this; drag.row = r; drag.col = c;
                    var label = MakeLabel(go.transform, "", 0, 0, tilePx, tilePx, armorSize,
                                      Color.white, TextAnchor.MiddleCenter);
                    label.fontStyle = FontStyle.Bold;
                    var outline = label.gameObject.AddComponent<Outline>();
                    outline.effectColor = new Color(0.05f, 0.06f, 0.09f, 0.95f);
                    outline.effectDistance = new Vector2(1.2f, -1.2f);
                    tileImages[r, c] = img;
                    tileRects[r, c] = rt;
                    tileArmor[r, c] = label;
                    tileBase[r, c] = rt.anchoredPosition;
                    boardCells.Add(go);

                    if (cfg.legacy_texture)
                    {
                        // 每格一份独立纹理：像素与共享贴图完全一致（外观不变），只是浪费显存/上传带宽。
                        var sp = MakeTileSprite((int)tilePx);
                        _spritesLegacy.Add(sp);
                        img.sprite = sp;
                    }
                    if (cfg.legacy_material)
                    {
                        var sh = Shader.Find("UI/Default");
                        if (sh != null) img.material = new Material(sh);
                    }
                }
            }
            RefreshTiles();
        }

        private void RefreshTiles()
        {
            if (tileImages == null || model == null) return;
            for (int r = 0; r < boardN; r++)
                for (int c = 0; c < boardN; c++)
                {
                    var img = tileImages[r, c];
                    if (img == null) continue;
                    int v = model.Color[r, c];
                    Color col = v >= 0 ? (Color)Palette[v] : new Color(0.06f, 0.08f, 0.1f);
                    if (model.Layers[r, c] >= 2) col = Color.Lerp(col, new Color(0.1f, 0.12f, 0.18f), 0.22f);
                    if ((r == selectedR && c == selectedC) || (r == hoverR && c == hoverC))
                        col = Color.Lerp(col, Color.white, 0.4f);
                    img.color = col;
                    var t = tileArmor[r, c];
                    if (t == null) continue;
                    int layers = model.Layers[r, c];
                    t.text = layers >= 2 ? layers.ToString() : "";
                    t.color = layers >= 2 ? Color.white : Color.clear;
                }
        }

        private void HighlightCell(int r, int c)
        {
            if (hoverR == r && hoverC == c) return;
            hoverR = r; hoverC = c;
            RefreshTiles();
        }

        private void ClearHighlight()
        {
            if (hoverR < 0 && hoverC < 0) return;
            hoverR = hoverC = -1;
            RefreshTiles();
        }

        private bool CellFromScreen(Vector2 screenPos, out int r, out int c)
        {
            r = c = -1;
            if (boardRoot == null || tilePx <= 0f) return false;
            Vector2 local;
            if (!RectTransformUtility.ScreenPointToLocalPointInRectangle(boardRoot, screenPos, null, out local))
                return false;
            float lx = local.x, ly = -local.y;
            float step = tilePx + gapPx;
            if (lx < BoardPad || ly < BoardPad) return false;
            int cc = Mathf.FloorToInt((lx - BoardPad) / step);
            int rr = Mathf.FloorToInt((ly - BoardPad) / step);
            if (rr < 0 || cc < 0 || rr >= boardN || cc >= boardN) return false;
            float ox = lx - BoardPad - cc * step;
            float oy = ly - BoardPad - rr * step;
            if (ox > tilePx || oy > tilePx) return false;
            r = rr; c = cc;
            return true;
        }

        /// <summary>开始拖拽；返回 false 表示当前不允许操作（动画中/回合结束/已超时），调用方应放弃拖拽。</summary>
        public bool BeginDrag(int r, int c)
        {
            if (phase != Phase.Playing || busy || timeLeft <= 0f) return false;
            if (model == null || !model.InBounds(r, c)) return false;
            dragging = true;
            dragR = r; dragC = c;
            selectedR = r; selectedC = c;
            RefreshTiles();
            return true;
        }

        public void UpdateDragTarget(int r, int c, Vector2 delta)
        {
            if (!dragging || phase != Phase.Playing) return;
            if (ResolveDragTarget(r, c, delta, out int tr, out int tc)) HighlightCell(tr, tc);
            else ClearHighlight();
        }

        /// <summary>屏幕像素位移 → 目标格。上 = 行号 -1，下 = 行号 +1。</summary>
        private bool ResolveDragTarget(int r, int c, Vector2 delta, out int tr, out int tc)
        {
            tr = r; tc = c;
            if (delta.magnitude < DragThreshold) return false;
            if (Mathf.Abs(delta.x) > Mathf.Abs(delta.y))
                tc += delta.x > 0f ? 1 : -1;
            else
                tr += delta.y > 0f ? -1 : 1;
            if (model == null || tr < 0 || tr >= boardN || tc < 0 || tc >= boardN) return false;
            return true;
        }

        public void EndDrag(int r, int c, Vector2 delta)
        {
            if (!dragging) return;
            dragging = false;
            selectedR = selectedC = -1;
            ClearHighlight();
            RefreshTiles();
            if (phase != Phase.Playing || busy) return;
            if (!ResolveDragTarget(r, c, delta, out int tr, out int tc)) return;
            TrySwap(r, c, tr, tc);
        }

        private void TrySwap(int r1, int c1, int r2, int c2)
        {
            if (busy || phase != Phase.Playing || timeLeft <= 0f) return;
            if (model == null || !model.InBounds(r1, c1) || !model.InBounds(r2, c2)) return;
            if (model.IsLocked(r1, c1) || model.IsLocked(r2, c2))
            {
                lastMessage = "数字色块叠了 " + Mathf.Max(model.Layers[r1, c1], model.Layers[r2, c2])
                              + " 层，不能换位：用同色消除或道具拆一层";
                statusText.text = lastMessage;
                RefreshTiles();
                return;
            }
            StartCoroutine(SwapRoutine(r1, c1, r2, c2));
        }

        private IEnumerator SwapRoutine(int r1, int c1, int r2, int c2)
        {
            busy = true;
            var a = tileRects[r1, c1];
            var b = tileRects[r2, c2];
            Vector2 pa = tileBase[r1, c1], pb = tileBase[r2, c2];
            a.anchoredPosition = pa; b.anchoredPosition = pb;
            const float T = 0.12f;
            for (float t = 0; t < T; t += Time.deltaTime)
            {
                float k = Mathf.SmoothStep(0f, 1f, t / T);
                if (a != null) a.anchoredPosition = Vector2.Lerp(pa, pb, k);
                if (b != null) b.anchoredPosition = Vector2.Lerp(pb, pa, k);
                yield return null;
            }
            model.SwapRaw(r1, c1, r2, c2);
            bool forms = model.MatchInvolving(r1, c1) || model.MatchInvolving(r2, c2);
            if (!forms)
            {
                model.SwapRaw(r1, c1, r2, c2);
                for (float t = 0; t < T; t += Time.deltaTime)
                {
                    float k = Mathf.SmoothStep(0f, 1f, t / T);
                    if (a != null) a.anchoredPosition = Vector2.Lerp(pb, pa, k);
                    if (b != null) b.anchoredPosition = Vector2.Lerp(pa, pb, k);
                    yield return null;
                }
                if (a != null) a.anchoredPosition = pa;
                if (b != null) b.anchoredPosition = pb;
                lastMessage = "这次交换没有形成消除";
                statusText.text = lastMessage;
                RefreshTiles();
                busy = false;
                yield break;
            }
            if (a != null) a.anchoredPosition = pa;
            if (b != null) b.anchoredPosition = pb;
            RefreshTiles();
            if (cfg.legacy_match_alloc) { var waste = new List<int>(); for (int i = 0; i < 64; i++) waste.Add(i); }
            yield return StartCoroutine(ResolveRoutine());
            busy = false;
        }

        private sealed class StepOutcome
        {
            public int chain, totalCleared, removed;
            public readonly int[] perColor = new int[Colors];
            public int maxSameColor, sameColorId;
        }

        private IEnumerator ResolveRoutine()
        {
            var o = new StepOutcome();
            while (true)
            {
                var hit = model.FindMatches();
                int n = model.Count(hit);
                if (n == 0) break;
                o.chain++;
                var per = model.CountByColor(hit);
                for (int i = 0; i < Colors; i++) o.perColor[i] += per[i];
                o.totalCleared += n;
                var removedMask = ComputeRemoved(hit);
                Play(clearClip);
                yield return StartCoroutine(AnimateClear(removedMask));
                var plan = PlanDrop(removedMask);
                model.ApplyDamage(hit, out int removed);
                o.removed += removed;
                if (cfg.legacy_particle)
                {
                    for (int r = 0; r < boardN; r++)
                        for (int c = 0; c < boardN; c++)
                            if (hit[r, c]) Burst(r, c, model.Color[r, c]);
                }
                totalScore += n * 10 * multiplier * o.chain;
                model.DropAndRefill(rng, tier, cfg.stack_chance_base, cfg.stack_chance_per_tier);
                RefreshTiles();
                yield return StartCoroutine(AnimateDropByPlan(plan));
            }
            model.ClearGuard();
            FinishStep(o);
        }

        /// <summary>确定性路径：与协程使用同一批结算函数，只是不播动画。</summary>
        private StepOutcome ResolveAllSync()
        {
            var o = new StepOutcome();
            int safety = 0;
            while (safety++ < 128)
            {
                var hit = model.FindMatches();
                int n = model.Count(hit);
                if (n == 0) break;
                o.chain++;
                var per = model.CountByColor(hit);
                for (int i = 0; i < Colors; i++) o.perColor[i] += per[i];
                o.totalCleared += n;
                model.ApplyDamage(hit, out int removed);
                o.removed += removed;
                totalScore += n * 10 * multiplier * o.chain;
                model.DropAndRefill(rng, tier, cfg.stack_chance_base, cfg.stack_chance_per_tier);
            }
            model.ClearGuard();
            FinishStep(o);
            return o;
        }

        private void FinishStep(StepOutcome o)
        {
            int best = 0, bestId = 0;
            for (int i = 0; i < Colors; i++)
                if (o.perColor[i] > best) { best = o.perColor[i]; bestId = i; }
            o.maxSameColor = best;
            o.sameColorId = bestId;
            for (int i = 0; i < Colors; i++) lastPerColor[i] = o.perColor[i];
            lastCleared = o.totalCleared;
            lastChain = o.chain;
            lastSameColorCount = best;
            lastSameColorId = bestId;

            if (o.totalCleared > 0)
            {
                lastMessage = "消除 " + o.totalCleared + " · 同色最多 " + PaletteName[bestId] + "×" + best
                              + (o.chain > 1 ? " · 连锁 ×" + o.chain : "");
                statusText.text = lastMessage;
                ShowStepFeedback(o);
                Debug.Log("STEP cleared=" + o.totalCleared + " same=" + best
                          + " color=" + PaletteName[bestId] + " chain=" + o.chain + " round=" + round);
                if (!model.HasPossibleMove())
                {
                    model.Shuffle(rng);
                    RefreshTiles();
                    statusText.text += " · 无解已洗牌";
                }
                if (best >= 6 && !bonusOpen)
                {
                    ShowBonus(o);
                    return;
                }
            }
            RefreshTiles();
            UpdateHud();
        }

        private void ShowStepFeedback(StepOutcome o)
        {
            if (stepText == null) return;
            string color = PaletteName[o.sameColorId];
            stepText.text = "本步 同色 " + color + " ×" + o.maxSameColor
                            + " · 连锁 ×" + o.chain + " · 共消 " + o.totalCleared;
            stepText.color = o.maxSameColor >= 6
                ? new Color(1f, 0.85f, 0.35f) : new Color(0.55f, 0.9f, 0.8f);
            stepText.rectTransform.localScale = Vector3.one * 1.12f;
            StartCoroutine(StepPop());
        }

        private IEnumerator StepPop()
        {
            float t = 0f;
            while (t < 0.2f && stepText != null)
            {
                t += Time.deltaTime;
                float k = Mathf.SmoothStep(0f, 1f, t / 0.2f);
                stepText.rectTransform.localScale = Vector3.one * Mathf.Lerp(1.12f, 1f, k);
                yield return null;
            }
        }

        /// <summary>本步真正会退场的格子（叠层块只减 1 层、不消失）。</summary>
        private bool[,] ComputeRemoved(bool[,] hit)
        {
            var removed = new bool[boardN, boardN];
            for (int r = 0; r < boardN; r++)
                for (int c = 0; c < boardN; c++)
                    removed[r, c] = hit[r, c] && model.Color[r, c] >= 0 && model.Layers[r, c] <= 1;
            return removed;
        }

        private sealed class DropPlan
        {
            public readonly int[,] FromRow;
            public readonly bool[,] Moved;
            public readonly bool[,] Spawned;

            public DropPlan(int n)
            {
                FromRow = new int[n, n];
                Moved = new bool[n, n];
                Spawned = new bool[n, n];
            }
        }

        /// <summary>下落计划：只标出真正移动的格子和新生成的格子；其余格子不动。</summary>
        private DropPlan PlanDrop(bool[,] removed)
        {
            var p = new DropPlan(boardN);
            for (int c = 0; c < boardN; c++)
            {
                int dest = boardN - 1;
                for (int r = boardN - 1; r >= 0; r--)
                {
                    if (removed[r, c]) continue;
                    p.FromRow[dest, c] = r;
                    p.Moved[dest, c] = r != dest;
                    dest--;
                }
                while (dest >= 0)
                {
                    p.Spawned[dest, c] = true;
                    dest--;
                }
            }
            return p;
        }

        /// <summary>退场动效：只作用于被消除的格子。</summary>
        private IEnumerator AnimateClear(bool[,] removed)
        {
            float t = 0f;
            while (t < 0.16f)
            {
                t += Time.deltaTime;
                float k = t / 0.16f;
                for (int r = 0; r < boardN; r++)
                    for (int c = 0; c < boardN; c++)
                        if (removed[r, c] && tileImages[r, c] != null)
                        {
                            float s = k < 0.4f ? Mathf.Lerp(1f, 1.1f, k / 0.4f)
                                              : Mathf.Lerp(1.1f, 0.35f, (k - 0.4f) / 0.6f);
                            tileRects[r, c].localScale = new Vector3(s, s, 1f);
                            var col = model.Color[r, c] >= 0 ? (Color)Palette[model.Color[r, c]] : Color.white;
                            tileImages[r, c].color = Color.Lerp(col, Color.white, 0.5f * k);
                        }
                yield return null;
            }
            for (int r = 0; r < boardN; r++)
                for (int c = 0; c < boardN; c++)
                    if (removed[r, c] && tileRects[r, c] != null)
                        tileRects[r, c].localScale = Vector3.one;
        }

        /// <summary>下落/生成动效：受重力影响的格子才移动，新格子才播生成；其余格子保持不动。</summary>
        private IEnumerator AnimateDropByPlan(DropPlan p)
        {
            float step = tilePx + gapPx;
            const float dur = 0.24f;
            for (int r = 0; r < boardN; r++)
                for (int c = 0; c < boardN; c++)
                {
                    var rt = tileRects[r, c];
                    if (rt == null) continue;
                    if (p.Spawned[r, c])
                    {
                        // 新块从棋盘上沿生成（不越过面板顶边）。
                        float spawnY = Mathf.Min(tileBase[r, c].y + step * 1.4f, 0f);
                        rt.anchoredPosition = new Vector2(tileBase[r, c].x, spawnY);
                        rt.localScale = new Vector3(0.72f, 0.72f, 1f);
                    }
                    else if (p.Moved[r, c])
                    {
                        int src = p.FromRow[r, c];
                        // 从原位置（更靠上）向下落到目标格：重力方向。
                        rt.anchoredPosition = tileBase[r, c] + new Vector2(0f, (r - src) * step);
                        rt.localScale = Vector3.one;
                    }
                    else
                    {
                        // 不受影响的格子：位置/缩放都不动。
                        rt.localScale = Vector3.one;
                    }
                }

            float t = 0f;
            while (t < dur)
            {
                t += Time.deltaTime;
                float k = Mathf.SmoothStep(0f, 1f, Mathf.Clamp01(t / dur));
                for (int r = 0; r < boardN; r++)
                    for (int c = 0; c < boardN; c++)
                    {
                        var rt = tileRects[r, c];
                        if (rt == null) continue;
                        if (p.Spawned[r, c])
                        {
                            float spawnY = Mathf.Min(tileBase[r, c].y + step * 1.4f, 0f);
                            rt.anchoredPosition = Vector2.Lerp(
                                new Vector2(tileBase[r, c].x, spawnY), tileBase[r, c], k);
                            float s = Mathf.Lerp(0.72f, 1f, k);
                            rt.localScale = new Vector3(s, s, 1f);
                        }
                        else if (p.Moved[r, c])
                        {
                            int src = p.FromRow[r, c];
                            rt.anchoredPosition = Vector2.Lerp(
                                tileBase[r, c] + new Vector2(0f, (r - src) * step), tileBase[r, c], k);
                        }
                    }
                yield return null;
            }
            for (int r = 0; r < boardN; r++)
                for (int c = 0; c < boardN; c++)
                    if (tileRects[r, c] != null && (p.Spawned[r, c] || p.Moved[r, c]))
                    {
                        tileRects[r, c].anchoredPosition = tileBase[r, c];
                        tileRects[r, c].localScale = Vector3.one;
                    }
        }

        private void Burst(int r, int c, int color)
        {
            Color col = color >= 0 ? (Color)Palette[color] : Color.white;
            if (cfg.legacy_particle)
            {
                var img = MakePanel(canvas.transform,
                                    BoardX + BoardPad + c * (tilePx + gapPx),
                                    BoardY + BoardPad + r * (tilePx + gapPx),
                                    22, 22, col, "fx");
                StartCoroutine(FadeFx(img));
                return;
            }
            if (_fxPool.Count == 0) return;
            var pooled = _fxPool[_fxCursor];
            _fxCursor = (_fxCursor + 1) % _fxPool.Count;
            pooled.gameObject.SetActive(true);
            pooled.color = col;
            pooled.rectTransform.anchoredPosition = new Vector2(
                BoardX + BoardPad + c * (tilePx + gapPx) + 10,
                -(BoardY + BoardPad + r * (tilePx + gapPx) + 10));
            pooled.rectTransform.localScale = Vector3.one;
            pooled.transform.SetAsLastSibling();
            StartCoroutine(FadePoolFx(pooled));
        }

        private IEnumerator FadePoolFx(Image img)
        {
            float t = 0f;
            while (t < 0.25f && img != null)
            {
                t += Time.deltaTime;
                var c = img.color; c.a = 1f - t / 0.25f; img.color = c;
                img.rectTransform.localScale = Vector3.one * (1f + t * 1.5f);
                yield return null;
            }
            if (img != null)
            {
                img.gameObject.SetActive(false);
                img.rectTransform.localScale = Vector3.one;
            }
        }

        private IEnumerator FadeFx(Image img)
        {
            float t = 0f;
            while (t < 0.25f && img != null)
            {
                t += Time.deltaTime;
                var c = img.color; c.a = 1f - t / 0.25f; img.color = c;
                img.rectTransform.localScale = Vector3.one * (1f + t * 1.5f);
                yield return null;
            }
            if (img != null) Destroy(img.gameObject);
        }

        private IEnumerator AutoPlay()
        {
            while (phase != Phase.Over && phase != Phase.Lobby)
            {
                yield return new WaitForSeconds(0.3f);
                if (phase == Phase.Over) yield break;
                if (phase == Phase.Bonus)
                {
                    if (!cfg.pause_on_bonus) ChooseBonus(0);
                    continue;
                }
                if (phase == Phase.Shop) { CloseShop(); continue; }
                if (phase == Phase.Preview) { ContinueFromPreview(); continue; }
                if (phase != Phase.Playing || busy) continue;
                if (TryAutoSwap()) continue;
                for (int k = 0; k < 8; k++)
                {
                    int r = rng.Next(boardN), c = rng.Next(boardN);
                    int dr = rng.Next(2) == 0 ? 1 : 0, dc = dr == 1 ? 0 : 1;
                    int r2 = r + dr, c2 = c + dc;
                    if (r2 >= boardN || c2 >= boardN) continue;
                    if (model.IsLocked(r, c) || model.IsLocked(r2, c2)) continue;
                    TrySwap(r, c, r2, c2);
                    break;
                }
            }
        }

        private bool TryAutoSwap()
        {
            for (int r = 0; r < boardN; r++)
                for (int c = 0; c < boardN; c++)
                {
                    if (model.IsLocked(r, c)) continue;
                    int[] dr = { 1, 0 };
                    int[] dc = { 0, 1 };
                    for (int k = 0; k < 2; k++)
                    {
                        int r2 = r + dr[k], c2 = c + dc[k];
                        if (r2 >= boardN || c2 >= boardN || model.IsLocked(r2, c2)) continue;
                        model.SwapRaw(r, c, r2, c2);
                        bool hit = model.HasMatch();
                        model.SwapRaw(r, c, r2, c2);
                        if (hit)
                        {
                            TrySwap(r, c, r2, c2);
                            return true;
                        }
                    }
                }
            return false;
        }

        // ───────────────────────── rounds ─────────────────────────

        private void EndRound()
        {
            if (phase != Phase.Playing) return;
            if (busy)
            {
                // 动效还在播：等它落完再结算（期间倒计时停在 0，不再吃输入）。
                pendingRoundEnd = true;
                return;
            }
            pendingRoundEnd = false;
            ClearTransientInput();
            if (totalScore < target)
            {
                EndGame();
                return;
            }
            coins += 20 + round * 5 + Mathf.Max(0, totalScore - target) / 200;
            int finished = round;
            round = finished + 1;
            if (cfg.shop_every > 0 && finished % cfg.shop_every == 0) OpenShop();
            else ShowPreview();
        }

        private void ClearTransientInput()
        {
            dragging = false;
            selectedR = selectedC = -1;
            hoverR = hoverC = -1;
            if (tileImages != null) RefreshTiles();
        }

        private void ShowPreview()
        {
            if (previewPanel == null) { BeginRound(round); return; }
            if (busy) { pendingRoundEnd = true; return; }
            phase = Phase.Preview;
            ShowOnlyOverlay(previewPanel);
            previewLeft = cfg.preview_seconds;
            int next = round;
            int nextTarget = cfg.TargetFor(next);
            int nextSeconds = cfg.RoundSecondsFor(next) + bonusTime;
            int sizeNow = model != null ? model.N : cfg.BoardSizeFor(next - 1);
            int sizeNext = cfg.BoardSizeFor(next);
            previewTitle.text = "ROUND " + next + " 预告";
            string sizeLine = sizeNow == sizeNext
                ? "棋盘 " + sizeNext + "×" + sizeNext
                : "棋盘 " + sizeNow + "×" + sizeNow + " → " + sizeNext + "×" + sizeNext + "（难度提升）";
            previewBody.text = "目标分  " + nextTarget + "\n当前分  " + totalScore
                               + "\n本轮时长  " + nextSeconds + "s" + (bonusTime > 0 ? "（含 BONUS +2s）" : "")
                               + "\n" + sizeLine;
            previewCountdown.text = cfg.preview_seconds > 0f
                ? "自动开始 " + previewLeft.ToString("0.0") + "s" : "点击开始本轮";
            previewPanel.SetActive(true);
            UpdateHud();
        }

        private void ContinueFromPreview()
        {
            if (phase != Phase.Preview) return;
            previewPanel.SetActive(false);
            previewLeft = 0f;
            pendingRoundEnd = false;
            BeginRound(round);
        }

        private void EndRun()
        {
            EndGame();
        }

        private void EndGame()
        {
            if (phase == Phase.Over) return;
            if (busy)
            {
                // 结束也要等动效落完，避免结算面板和消除/BONUS 同屏。
                pendingGameOver = true;
                return;
            }
            pendingGameOver = false;
            pendingRoundEnd = false;
            ClearTransientInput();
            phase = Phase.Over;
            overOpen = true;
            ShowOnlyOverlay(overPanel);
            overText.text = "SCORE  " + totalScore + "\nROUND  " + round + "\n\n本机最高分已记录";
            SaveScore(totalScore, round);
        }

        /// <summary>任何时刻最多只有一个全屏遮罩（预告/商店/BONUS/结算），从根上避免互相挡按钮。</summary>
        private void ShowOnlyOverlay(GameObject keep)
        {
            if (previewPanel != null) previewPanel.SetActive(keep == previewPanel);
            if (shopPanel != null) shopPanel.SetActive(keep == shopPanel);
            if (bonusPanel != null) bonusPanel.SetActive(keep == bonusPanel);
            if (overPanel != null) overPanel.SetActive(keep == overPanel);
        }

        // ───────────────────────── shop ─────────────────────────

        private void OpenShop()
        {
            if (busy) { pendingRoundEnd = true; return; }
            if (cfg.sync_shop_load)
            {
                // 故意保留同步等待路径：case 用它测“打开商店卡主线程”
                int ok = 0;
                for (int i = 0; i < 24; i++)
                {
                    var h = Addressables.LoadAssetAsync<TextAsset>("devsim/payload_" + (i % 8));
                    h.WaitForCompletion();
                    if (h.Status == AsyncOperationStatus.Succeeded) ok++;
                    Addressables.Release(h);
                }
                Debug.Log("DevSim sync Addressables: " + ok + "/24");
            }
            else
            {
                StartCoroutine(LoadShopAsync());
            }
            phase = Phase.Shop;
            shopVisits++;
            shopMessage = "";
            ShowOnlyOverlay(shopPanel);
            for (int i = 0; i < boughtThisShop.Length; i++) boughtThisShop[i] = false;
            RollOffers();
            UpdateShopUI();
            UpdateHud();
        }

        private IEnumerator LoadShopAsync()
        {
            for (int i = 0; i < 8; i++)
            {
                var h = Addressables.LoadAssetAsync<TextAsset>("devsim/payload_" + i);
                yield return h;
                if (h.Status != AsyncOperationStatus.Succeeded)
                    Debug.LogWarning("devsim addressable failed: " + i + " " + h.OperationException);
                Addressables.Release(h);
            }
        }

        private void RollOffers()
        {
            var pool = new List<int>();
            for (int i = 0; i < itemNames.Length; i++)
                if (!boughtThisShop[i]) pool.Add(i);
            for (int i = 0; i < 3; i++)
            {
                if (pool.Count == 0)
                {
                    offers[i] = null;
                    continue;
                }
                int idx = rng.Next(pool.Count);
                int item = pool[idx];
                pool.RemoveAt(idx);
                int price = itemPrice[item] + (round - 1) * 5;
                offers[i] = new Offer(item, price) { sold = false };
            }
        }

        private void UpdateShopUI()
        {
            if (shopCoins != null) shopCoins.text = "COINS " + coins + "  ·  ROUND " + round;
            for (int i = 0; i < 3; i++)
            {
                var card = shopCards[i];
                if (card == null) continue;
                var o = offers[i];
                if (o == null)
                {
                    card.icon.gameObject.SetActive(false);
                    card.title.text = "已售完";
                    card.desc.text = "本店暂无可售商品";
                    card.price.text = "";
                    card.buyLabel.text = "—";
                    card.buy.interactable = false;
                    card.state.text = "";
                    card.soldOverlay.gameObject.SetActive(true);
                    continue;
                }
                card.icon.gameObject.SetActive(true);
                card.icon.sprite = ItemIcon(o.item);
                card.title.text = itemNames[o.item];
                card.desc.text = itemDesc[o.item];
                card.price.text = "PRICE " + o.price;
                bool sold = o.sold;
                card.buyLabel.text = sold ? "已售罄" : "BUY";
                card.buy.interactable = !sold;
                card.state.text = sold ? "本店已购买，不能重复购买"
                    : (coins < o.price ? "金币不足" : "");
                card.soldOverlay.gameObject.SetActive(sold);
            }
            var msg = shopPanel.transform.Find("ShopMsg");
            if (msg != null)
            {
                var t = msg.GetComponent<Text>();
                if (t != null) t.text = shopMessage;
            }
        }

        private void BuySlot(int slot)
        {
            if (phase != Phase.Shop) return;
            if (slot < 0 || slot >= offers.Length) return;
            var o = offers[slot];
            if (o == null) { shopMessage = "该商品已售罄"; UpdateShopUI(); return; }
            if (o.sold) { shopMessage = "已购买，不能重复购买"; UpdateShopUI(); return; }
            if (coins < o.price) { shopMessage = "金币不足"; UpdateShopUI(); return; }
            coins -= o.price;
            inventory[o.item]++;
            o.sold = true;
            boughtThisShop[o.item] = true;
            shopMessage = "已购买 " + itemNames[o.item];
            Debug.Log("SHOP buy item=" + o.item + " price=" + o.price + " coins=" + coins);
            UpdateShopUI();
            UpdateHud();
        }

        private void RefreshShop()
        {
            if (phase != Phase.Shop) return;
            if (coins < cfg.shop_refresh_cost)
            {
                shopMessage = "金币不足，无法刷新（需要 " + cfg.shop_refresh_cost + "）";
                UpdateShopUI();
                return;
            }
            bool anyLeft = false;
            for (int i = 0; i < itemNames.Length; i++) if (!boughtThisShop[i]) anyLeft = true;
            if (!anyLeft)
            {
                shopMessage = "本店商品已售完，无法刷新";
                UpdateShopUI();
                return;
            }
            coins -= cfg.shop_refresh_cost;
            refreshCount++;
            RollOffers();
            shopMessage = "已刷新";
            Debug.Log("SHOP refresh count=" + refreshCount + " coins=" + coins);
            UpdateShopUI();
            UpdateHud();
        }

        private void CloseShop()
        {
            if (phase != Phase.Shop) return;
            shopPanel.SetActive(false);
            tier = Mathf.Min(6, tier + 1);
            ShowPreview();
        }

        // ───────────────────────── bonus ─────────────────────────

        private readonly string[] bonusNames = { "得分 ×1.1", "本轮 +2s", "炸弹 +1" };
        private readonly int[] bonusIds = new int[3];

        private void ShowBonus(StepOutcome o)
        {
            bonusOpen = true;
            bonusChoiceLocked = false;
            bonusTriggeredCount++;
            phase = Phase.Bonus;
            Play(bonusClip);
            lastMessage = "BONUS 触发：同色 " + PaletteName[o.sameColorId] + "×" + o.maxSameColor
                          + " · 连锁 ×" + o.chain;
            Debug.Log("BONUS triggered: color=" + PaletteName[o.sameColorId]
                      + " cleared=" + o.maxSameColor + " chain=" + o.chain
                      + " total=" + o.totalCleared + " round=" + round
                      + " count=" + bonusTriggeredCount);
            for (int i = 0; i < 3; i++) bonusIds[i] = i;
            for (int i = 2; i > 0; i--)
            {
                int j = rng.Next(i + 1);
                int t = bonusIds[i]; bonusIds[i] = bonusIds[j]; bonusIds[j] = t;
            }
            for (int i = 0; i < 3; i++)
            {
                int id = bonusIds[i];
                if (bonusLabels[i] != null) bonusLabels[i].text = bonusNames[id];
                if (bonusGroups[i] != null)
                {
                    bonusGroups[i].alpha = 0f;
                    bonusGroups[i].transform.localScale = new Vector3(0.8f, 0.8f, 1f);
                }
            }
            ShowOnlyOverlay(bonusPanel);
            StartCoroutine(BonusIntro());
            StartCoroutine(BonusCardsIntro());
            statusText.text = "BONUS — 同色消除 ≥6，选择一项";
            UpdateHud();
        }

        private IEnumerator BonusIntro()
        {
            float t = 0f;
            while (t < 0.28f)
            {
                t += Time.deltaTime;
                float k = Mathf.SmoothStep(0f, 1f, t / 0.28f);
                bonusPanel.transform.localScale = Vector3.Lerp(
                    new Vector3(0.9f, 0.9f, 1f), Vector3.one, k);
                yield return null;
            }
            bonusPanel.transform.localScale = Vector3.one;
        }

        private IEnumerator BonusCardsIntro()
        {
            for (int i = 0; i < 3; i++)
            {
                float t = 0f;
                while (t < 0.2f)
                {
                    t += Time.deltaTime;
                    float k = Mathf.SmoothStep(0f, 1f, t / 0.2f);
                    if (bonusGroups[i] != null)
                    {
                        bonusGroups[i].alpha = k;
                        bonusGroups[i].transform.localScale =
                            Vector3.Lerp(new Vector3(0.8f, 0.8f, 1f), Vector3.one, k);
                    }
                    yield return null;
                }
                if (bonusGroups[i] != null)
                {
                    bonusGroups[i].alpha = 1f;
                    bonusGroups[i].transform.localScale = Vector3.one;
                }
                yield return new WaitForSeconds(0.05f);
            }
        }

        private void ChooseBonus(int slot)
        {
            if (!bonusOpen || bonusChoiceLocked) return;
            StartCoroutine(ChooseBonusRoutine(slot));
        }

        private IEnumerator ChooseBonusRoutine(int slot)
        {
            bonusChoiceLocked = true;
            slot = Mathf.Clamp(slot, 0, 2);
            Play(clickClip);
            float t = 0f;
            while (t < 0.14f)
            {
                t += Time.deltaTime;
                float k = Mathf.SmoothStep(0f, 1f, t / 0.14f);
                if (bonusGroups[slot] != null)
                    bonusGroups[slot].transform.localScale =
                        Vector3.Lerp(Vector3.one, new Vector3(1.1f, 1.1f, 1f), k);
                yield return null;
            }
            int id = bonusIds[slot];
            if (id == 0) multiplier += 1;
            else if (id == 1) bonusTime += 2;
            else inventory[0]++;
            bonusOpen = false;
            bonusPanel.SetActive(false);
            for (int i = 0; i < 3; i++)
            {
                if (bonusGroups[i] == null) continue;
                bonusGroups[i].alpha = 1f;
                bonusGroups[i].transform.localScale = Vector3.one;
            }
            phase = Phase.Playing;
            statusText.text = "BONUS 已选择：" + bonusNames[id];
            RefreshItemLabels();
            UpdateHud();
            ResolvePendingWhenIdle();
        }

        // ───────────────────────── items ─────────────────────────

        public bool BeginItemDrag(int item)
        {
            if (phase != Phase.Playing || busy || dragging || timeLeft <= 0f)
            {
                statusText.text = "现在不能使用道具";
                return false;
            }
            if (item < 0 || item >= itemNames.Length) return false;
            if (inventory[item] <= 0)
            {
                statusText.text = itemNames[item] + " 数量为 0";
                return false;
            }
            return true;
        }

        public void HoverItemTarget(int item, Vector2 screenPos)
        {
            if (phase != Phase.Playing || busy || timeLeft <= 0f) { ClearHighlight(); return; }
            if (CellFromScreen(screenPos, out int r, out int c) && CanUseItemAt(item, r, c))
                HighlightCell(r, c);
            else
                ClearHighlight();
        }

        public void ClearItemHover()
        {
            ClearHighlight();
        }

        public bool DropItem(int item, Vector2 screenPos)
        {
            if (phase != Phase.Playing || busy || timeLeft <= 0f) return false;
            if (!CellFromScreen(screenPos, out int r, out int c))
            {
                statusText.text = "把道具拖到棋盘目标格";
                return false;
            }
            if (!CanUseItemAt(item, r, c))
            {
                statusText.text = itemNames[item] + " 不能作用在这个格子";
                return false;
            }
            if (inventory[item] <= 0)
            {
                statusText.text = itemNames[item] + " 数量为 0";
                return false;
            }
            inventory[item]--;
            lastMessage = "使用 " + itemNames[item];
            statusText.text = lastMessage;
            RefreshItemLabels();
            UpdateHud();
            StartCoroutine(ItemRoutine(item, r, c));
            return true;
        }

        private IEnumerator ItemRoutine(int item, int r, int c)
        {
            busy = true;
            ApplyItemCore(item, r, c);
            RefreshTiles();
            UpdateHud();
            yield return StartCoroutine(ResolveRoutine());
            busy = false;
        }

        private bool CanUseItemAt(int item, int r, int c)
        {
            if (model == null || !model.InBounds(r, c)) return false;
            if (item == 3) return true; // 洗牌：拖到棋盘任意格
            if (model.IsEmpty(r, c)) return false;
            if (item == 1 && model.Layers[r, c] >= 2) return false; // 改色只对普通块
            return true;
        }

        private void ApplyItemCore(int item, int r, int c)
        {
            switch (item)
            {
                case 0:
                    for (int rr = Mathf.Max(0, r - 1); rr <= Mathf.Min(boardN - 1, r + 1); rr++)
                        for (int cc = Mathf.Max(0, c - 1); cc <= Mathf.Min(boardN - 1, c + 1); cc++)
                            DamageCell(rr, cc);
                    break;
                case 1:
                    model.Color[r, c] = (model.Color[r, c] + 1) % Colors;
                    break;
                case 2:
                    DamageCell(r, c);
                    break;
                default:
                    model.Shuffle(rng);
                    break;
            }
        }

        /// <summary>道具/消除对单格造成 1 层伤害；叠层块只减 1 层。</summary>
        private bool DamageCell(int r, int c)
        {
            if (model == null || !model.InBounds(r, c) || model.IsEmpty(r, c)) return false;
            model.Layers[r, c]--;
            if (model.Layers[r, c] <= 0)
            {
                model.Color[r, c] = -1;
                model.Layers[r, c] = 0;
            }
            return true;
        }

        // ───────────────────────── deterministic debug API ─────────────────────────
        // 供 sim/tests 的确定性测试与 replay 取证使用；不读 oracle、不含期望答案。

        [Serializable]
        public sealed class DebugStateDto
        {
            public string phase;
            public int round, target, score, boardN, tier, coins;
            public float timeLeft;
            public int[] inventory;
            public bool bonusOpen, previewOpen, shopOpen, overOpen;
            public bool pendingRoundEnd, pendingGameOver, busy;
            public int bonusTriggered;
            public string lastMessage, lastStep;
            public int lastChain, lastCleared, lastSameColorId, lastSameColorCount;
            public int shopVisits, refreshCount;
            public string[] offerNames;
            public int[] offerPrices;
            public bool[] offerSold;
            public int[] layers, colors, lastPerColor;
            public string[] armorTexts;
            public int stackCount;
            public float previewLeft;
            public int nextRoundTarget, nextRoundSeconds, nextBoardN;
            public string[] itemNames;
        }

        [Serializable]
        public sealed class DebugSwapDto
        {
            public bool accepted;
            public string reason;
            public int r1, c1, r2, c2;
            public int sourceColor, targetColor, sourceLayers, targetLayers;
            public int totalCleared, removed, chain, maxSameColor, sameColorId;
            public bool bonusTriggered;
            public int[] perColor;
        }

        [Serializable]
        public sealed class DebugDragDto
        {
            public bool valid;
            public int fromR, fromC, toR, toC;
            public float dx, dy;
        }

        [Serializable]
        public sealed class DebugPlanDto
        {
            public int round, target, seconds, boardN;
        }

        [Serializable]
        public sealed class DebugRenderDto
        {
            public int screenW, screenH;
            public float canvasScale;
            public float refW, refH;
            public int tilePx, boardN, tileTextureSize;
            public int msaa, antiAliasing;
            public bool canvasPixelPerfect;
        }

        public string DebugState()
        {
            var d = new DebugStateDto
            {
                phase = phase.ToString(),
                round = round,
                target = target,
                score = totalScore,
                boardN = model != null ? model.N : 0,
                tier = tier,
                coins = coins,
                timeLeft = timeLeft,
                inventory = (int[])inventory.Clone(),
                bonusOpen = bonusOpen,
                pendingRoundEnd = pendingRoundEnd,
                pendingGameOver = pendingGameOver,
                busy = busy,
                previewOpen = phase == Phase.Preview,
                shopOpen = phase == Phase.Shop,
                overOpen = overOpen,
                bonusTriggered = bonusTriggeredCount,
                lastMessage = lastMessage,
                lastStep = stepText != null ? stepText.text : "",
                lastChain = lastChain,
                lastCleared = lastCleared,
                lastSameColorId = lastSameColorId,
                lastSameColorCount = lastSameColorCount,
                shopVisits = shopVisits,
                refreshCount = refreshCount,
                lastPerColor = (int[])lastPerColor.Clone(),
                previewLeft = previewLeft,
                nextRoundTarget = cfg != null ? cfg.TargetFor(round) : 0,
                nextRoundSeconds = cfg != null ? cfg.RoundSecondsFor(round) : 0,
                nextBoardN = cfg != null ? cfg.BoardSizeFor(round) : 0,
                itemNames = (string[])itemNames.Clone(),
            };
            int n = model != null ? model.N : 0;
            d.layers = new int[Mathf.Max(0, n * n)];
            d.colors = new int[Mathf.Max(0, n * n)];
            d.armorTexts = new string[Mathf.Max(0, n * n)];
            d.stackCount = 0;
            for (int r = 0; r < n; r++)
                for (int c = 0; c < n; c++)
                {
                    d.layers[r * n + c] = model.Layers[r, c];
                    d.colors[r * n + c] = model.Color[r, c];
                    d.armorTexts[r * n + c] = tileArmor[r, c] != null ? tileArmor[r, c].text : "";
                    if (model.Layers[r, c] >= 2) d.stackCount++;
                }
            d.offerNames = new string[3];
            d.offerPrices = new int[3];
            d.offerSold = new bool[3];
            for (int i = 0; i < 3; i++)
            {
                var o = offers[i];
                d.offerNames[i] = o != null ? itemNames[o.item] : "";
                d.offerPrices[i] = o != null ? o.price : 0;
                d.offerSold[i] = o != null && o.sold;
            }
            return JsonUtility.ToJson(d);
        }

        public void DebugLoadConfig(string json)
        {
            if (string.IsNullOrEmpty(json)) return;
            try { JsonUtility.FromJsonOverwrite(json, cfg); }
            catch (Exception e) { Debug.LogWarning("DebugLoadConfig failed: " + e.Message); }
            rng = new System.Random(cfg.seed);
            tier = Mathf.Clamp(cfg.start_tier, 1, 6);
        }

        public void DebugStartRun()
        {
            StartGame();
        }

        public string DebugSetBoard(int n, int[] colors, int[] layers)
        {
            if (phase == Phase.Lobby) StartGame();
            BuildBoard(Mathf.Clamp(n, 4, 12));
            model.Load(colors ?? new int[0], layers);
            totalScore = 0;
            multiplier = 1;
            bonusTime = 0;
            timeLeft = cfg.RoundSecondsFor(round);
            previewLeft = 0f;
            busy = false;
            dragging = false;
            selectedR = selectedC = -1;
            hoverR = hoverC = -1;
            bonusOpen = false;
            overOpen = false;
            pendingRoundEnd = pendingGameOver = false;
            ShowOnlyOverlay(null);
            lastCleared = lastChain = lastSameColorCount = lastSameColorId = 0;
            for (int i = 0; i < lastPerColor.Length; i++) lastPerColor[i] = 0;
            phase = Phase.Playing;
            lastMessage = "";
            if (stepText != null) stepText.text = "";
            statusText.text = "ROUND " + round + " — 目标 " + target;
            RefreshTiles();
            UpdateHud();
            return DebugState();
        }

        public string DebugSwap(int r1, int c1, int r2, int c2)
        {
            var d = new DebugSwapDto
            {
                r1 = r1, c1 = c1, r2 = r2, c2 = c2,
                perColor = new int[Colors],
            };
            if (phase != Phase.Playing) { d.reason = "not-playing"; return JsonUtility.ToJson(d); }
            if (model == null || !model.InBounds(r1, c1) || !model.InBounds(r2, c2))
            { d.reason = "out-of-bounds"; return JsonUtility.ToJson(d); }
            d.sourceColor = model.Color[r1, c1];
            d.targetColor = model.Color[r2, c2];
            d.sourceLayers = model.Layers[r1, c1];
            d.targetLayers = model.Layers[r2, c2];
            if (model.IsLocked(r1, c1) || model.IsLocked(r2, c2))
            { d.reason = "locked"; return JsonUtility.ToJson(d); }
            if (!model.CanSwap(r1, c1, r2, c2))
            { d.reason = "not-adjacent"; return JsonUtility.ToJson(d); }
            model.SwapRaw(r1, c1, r2, c2);
            if (!(model.MatchInvolving(r1, c1) || model.MatchInvolving(r2, c2)))
            {
                model.SwapRaw(r1, c1, r2, c2);
                d.reason = "no-match";
                RefreshTiles();
                return JsonUtility.ToJson(d);
            }
            d.accepted = true;
            d.reason = "ok";
            var o = ResolveAllSync();
            d.totalCleared = o.totalCleared;
            d.removed = o.removed;
            d.chain = o.chain;
            d.maxSameColor = o.maxSameColor;
            d.sameColorId = o.sameColorId;
            d.perColor = o.perColor;
            d.bonusTriggered = bonusOpen;
            RefreshTiles();
            UpdateHud();
            return JsonUtility.ToJson(d);
        }

        public string DebugResolveBoard()
        {
            var d = new DebugSwapDto { reason = "no-match", perColor = new int[Colors], sourceColor = -1, targetColor = -1 };
            if (phase != Phase.Playing || model == null) { d.reason = "not-playing"; return JsonUtility.ToJson(d); }
            var o = ResolveAllSync();
            d.accepted = o.totalCleared > 0;
            d.reason = d.accepted ? "ok" : "no-match";
            d.totalCleared = o.totalCleared;
            d.removed = o.removed;
            d.chain = o.chain;
            d.maxSameColor = o.maxSameColor;
            d.sameColorId = o.sameColorId;
            d.perColor = o.perColor;
            d.bonusTriggered = bonusOpen;
            RefreshTiles();
            UpdateHud();
            return JsonUtility.ToJson(d);
        }

        /// <summary>只结算一步消除（一次命中事件），用于验证“每步只扣 1 层”。</summary>
        public string DebugResolveStep()
        {
            var d = new DebugSwapDto { reason = "no-match", perColor = new int[Colors], sourceColor = -1, targetColor = -1 };
            if (phase != Phase.Playing || model == null) { d.reason = "not-playing"; return JsonUtility.ToJson(d); }
            var hit = model.FindMatches();
            int n = model.Count(hit);
            if (n == 0) { RefreshTiles(); return JsonUtility.ToJson(d); }
            d.accepted = true;
            d.reason = "ok";
            d.totalCleared = n;
            d.perColor = model.CountByColor(hit);
            model.ApplyDamage(hit, out int removed);
            d.removed = removed;
            totalScore += n * 10 * multiplier;
            model.ClearGuard();
            RefreshTiles();
            UpdateHud();
            return JsonUtility.ToJson(d);
        }

        public string DebugDragTarget(int r, int c, float dx, float dy)
        {
            var d = new DebugDragDto { fromR = r, fromC = c, dx = dx, dy = dy, toR = r, toC = c };
            d.valid = ResolveDragTarget(r, c, new Vector2(dx, dy), out int tr, out int tc);
            d.toR = tr; d.toC = tc;
            return JsonUtility.ToJson(d);
        }

        public string DebugScreenPosOfCell(int r, int c)
        {
            var d = new DebugDragDto { fromR = r, fromC = c };
            if (model == null || !model.InBounds(r, c)) return JsonUtility.ToJson(d);
            float usable = BoardSize - BoardPad * 2f;
            float step = tilePx + gapPx;
            float offset = (usable - (tilePx * boardN + gapPx * (boardN - 1))) * 0.5f;
            float x = BoardPad + offset + c * step + tilePx * 0.5f;
            float y = BoardPad + offset + r * step + tilePx * 0.5f;
            Vector3 world = boardRoot.TransformPoint(new Vector3(x, -y, 0f));
            Vector2 screen = RectTransformUtility.WorldToScreenPoint(null, world);
            d.dx = screen.x;
            d.dy = screen.y;
            d.valid = true;
            return JsonUtility.ToJson(d);
        }

        public string DebugAdvance(float seconds)
        {
            if (phase == Phase.Playing)
            {
                timeLeft -= seconds;
                if (timeLeft <= 0f) { timeLeft = 0f; pendingRoundEnd = true; }
            }
            else if (phase == Phase.Preview)
            {
                if (previewLeft > 0f)
                {
                    previewLeft -= seconds;
                    if (previewLeft <= 0f) { previewLeft = 0f; ContinueFromPreview(); }
                }
            }
            ResolvePendingWhenIdle();
            UpdateHud();
            return DebugState();
        }

        public string DebugSetTime(float seconds)
        {
            timeLeft = Mathf.Max(0f, seconds);
            if (timeLeft <= 0f) pendingRoundEnd = true;
            UpdateHud();
            return DebugState();
        }

        public string DebugSetBusy(bool value)
        {
            busy = value;
            return DebugState();
        }

        public string DebugContinue()
        {
            if (phase == Phase.Preview) ContinueFromPreview();
            return DebugState();
        }

        public string DebugSetCoins(int v)
        {
            coins = Mathf.Max(0, v);
            UpdateHud();
            if (phase == Phase.Shop) UpdateShopUI();
            return DebugState();
        }

        public string DebugSetScore(int v) { totalScore = Mathf.Max(0, v); UpdateHud(); return DebugState(); }

        public string DebugJumpToRound(int rr)
        {
            rr = Mathf.Max(1, rr);
            phase = Phase.Playing;
            round = rr;
            target = cfg.TargetFor(rr);
            timeLeft = cfg.RoundSecondsFor(rr);
            bonusTime = 0;
            busy = false;
            dragging = false;
            model = null;
            pendingRoundEnd = pendingGameOver = false;
            shopVisits = 0;
            refreshCount = 0;
            shopMessage = "";
            ShowOnlyOverlay(null);
            BuildBoard(cfg.BoardSizeFor(rr));
            UpdateHud();
            return DebugState();
        }

        public string DebugPlan(int rr)
        {
            var d = new DebugPlanDto
            {
                round = rr,
                target = cfg.TargetFor(rr),
                seconds = cfg.RoundSecondsFor(rr),
                boardN = cfg.BoardSizeFor(rr),
            };
            return JsonUtility.ToJson(d);
        }

        public string DebugBuy(int slot)
        {
            BuySlot(slot);
            return DebugState();
        }

        public string DebugRefreshShop()
        {
            RefreshShop();
            return DebugState();
        }

        public string DebugLeaveShop()
        {
            CloseShop();
            return DebugState();
        }

        public string DebugChooseBonus(int slot)
        {
            if (!bonusOpen) return DebugState();
            int id = bonusIds[Mathf.Clamp(slot, 0, 2)];
            if (id == 0) multiplier += 1;
            else if (id == 1) bonusTime += 2;
            else inventory[0]++;
            bonusOpen = false;
            bonusChoiceLocked = false;
            if (bonusPanel != null) bonusPanel.SetActive(false);
            phase = Phase.Playing;
            statusText.text = "BONUS 已选择：" + bonusNames[id];
            RefreshItemLabels();
            UpdateHud();
            ResolvePendingWhenIdle();
            return DebugState();
        }

        public string DebugItem(int item, int r, int c)
        {
            var d = new DebugSwapDto { r1 = r, c1 = c, r2 = r, c2 = c, perColor = new int[Colors] };
            if (phase != Phase.Playing || model == null) { d.reason = "not-playing"; return JsonUtility.ToJson(d); }
            if (item < 0 || item >= itemNames.Length) { d.reason = "bad-item"; return JsonUtility.ToJson(d); }
            if (inventory[item] <= 0) { d.reason = "no-stock"; return JsonUtility.ToJson(d); }
            if (!CanUseItemAt(item, r, c)) { d.reason = "invalid-target"; return JsonUtility.ToJson(d); }
            inventory[item]--;
            ApplyItemCore(item, r, c);
            var o = ResolveAllSync();
            d.accepted = true;
            d.reason = "ok";
            d.totalCleared = o.totalCleared;
            d.removed = o.removed;
            d.chain = o.chain;
            d.maxSameColor = o.maxSameColor;
            d.sameColorId = o.sameColorId;
            d.perColor = o.perColor;
            d.bonusTriggered = bonusOpen;
            RefreshTiles();
            RefreshItemLabels();
            UpdateHud();
            return JsonUtility.ToJson(d);
        }

        /// <summary>测试用：直接给库存，避免必须跑完整经济系统。</summary>
        public void DebugGrantItem(int item, int count)
        {
            if (item < 0 || item >= inventory.Length) return;
            inventory[item] = Mathf.Max(0, count);
            RefreshItemLabels();
        }

        /// <summary>测试用：返回当前棋盘格对象（避免 GameObject.Find 命中待销毁的旧格子）。</summary>
        public GameObject DebugTileObject(int r, int c)
        {
            if (tileRects == null || model == null || !model.InBounds(r, c)) return null;
            var rt = tileRects[r, c];
            return rt != null ? rt.gameObject : null;
        }

        /// <summary>测试用：返回道具槽对象（用于派发真实拖拽事件）。</summary>
        public GameObject DebugItemSlot(int item)
        {
            if (item < 0 || item >= itemSlots.Count) return null;
            return itemSlots[item];
        }

        [Serializable]
        public sealed class DebugDropPlanDto
        {
            public bool[] moved;
            public bool[] spawned;
            public bool[] removed;
            public int[] fromRow;
        }

        /// <summary>测试用：给定“本步退场格”，返回下落/生成计划（未受影响的格子必须为 false）。</summary>
        public string DebugPlanDrop(int[] removedFlags)
        {
            var d = new DebugDropPlanDto();
            if (model == null) return JsonUtility.ToJson(d);
            var removed = new bool[boardN, boardN];
            for (int r = 0; r < boardN; r++)
                for (int c = 0; c < boardN; c++)
                    removed[r, c] = removedFlags != null && removedFlags.Length == boardN * boardN
                                    && removedFlags[r * boardN + c] != 0;
            var p = PlanDrop(removed);
            d.moved = new bool[boardN * boardN];
            d.spawned = new bool[boardN * boardN];
            d.removed = new bool[boardN * boardN];
            d.fromRow = new int[boardN * boardN];
            for (int r = 0; r < boardN; r++)
                for (int c = 0; c < boardN; c++)
                {
                    int i = r * boardN + c;
                    d.moved[i] = p.Moved[r, c];
                    d.spawned[i] = p.Spawned[r, c];
                    d.removed[i] = removed[r, c];
                    d.fromRow[i] = p.FromRow[r, c];
                }
            return JsonUtility.ToJson(d);
        }

        public string DebugRenderInfo()
        {            var scaler = canvas != null ? canvas.GetComponent<CanvasScaler>() : null;
            var d = new DebugRenderDto
            {
                screenW = Screen.width,
                screenH = Screen.height,
                canvasScale = canvas != null ? canvas.scaleFactor : 0f,
                canvasPixelPerfect = canvas != null && canvas.pixelPerfect,
                refW = scaler != null ? scaler.referenceResolution.x : 0f,
                refH = scaler != null ? scaler.referenceResolution.y : 0f,
                tilePx = Mathf.RoundToInt(tilePx),
                boardN = boardN,
                msaa = QualitySettings.antiAliasing,
                antiAliasing = QualitySettings.antiAliasing,
            };
            if (tileImages != null && boardN > 0 && tileImages[0, 0] != null
                && tileImages[0, 0].sprite != null && tileImages[0, 0].sprite.texture != null)
                d.tileTextureSize = tileImages[0, 0].sprite.texture.width;
            return JsonUtility.ToJson(d);
        }

        // ───────────────────────── persistence ─────────────────────────

        private void ShowHelp()
        {
            if (helpPanel != null) helpPanel.SetActive(true);
        }

        [Serializable] private class ScoreEntry { public int score; public int round; public string date; }
        [Serializable] private class ScoreBook { public List<ScoreEntry> entries = new List<ScoreEntry>(); }

        private const string ScoreKey = "chroma_high_scores_v1";

        private void SaveScore(int score, int runRound)
        {
            var book = new ScoreBook();
            var raw = PlayerPrefs.GetString(ScoreKey, "");
            if (!string.IsNullOrEmpty(raw))
            {
                try { book = JsonUtility.FromJson<ScoreBook>(raw) ?? new ScoreBook(); } catch { }
            }
            book.entries.Add(new ScoreEntry { score = score, round = runRound, date = DateTime.Now.ToString("yyyy-MM-dd") });
            book.entries.Sort((a, b) => b.score.CompareTo(a.score));
            if (book.entries.Count > 10) book.entries.RemoveRange(10, book.entries.Count - 10);
            PlayerPrefs.SetString(ScoreKey, JsonUtility.ToJson(book));
            PlayerPrefs.Save();
            RefreshHighScores();
        }

        private void RefreshHighScores()
        {
            if (highText == null) return;
            var raw = PlayerPrefs.GetString(ScoreKey, "");
            if (string.IsNullOrEmpty(raw)) { highText.text = "— 还没有记录 —"; return; }
            try
            {
                var book = JsonUtility.FromJson<ScoreBook>(raw);
                if (book == null || book.entries.Count == 0) { highText.text = "— 还没有记录 —"; return; }
                var sb = new System.Text.StringBuilder();
                for (int i = 0; i < book.entries.Count && i < 5; i++)
                    sb.AppendLine((i + 1) + ".  " + book.entries[i].score + "   R" + book.entries[i].round);
                highText.text = sb.ToString();
            }
            catch { highText.text = "—"; }
        }
    }
}

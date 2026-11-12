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
    /// Chroma Rogue vertical slice: 8x8 match-3 + light roguelite.
    /// 全部 UI 运行时生成，默认场景保持为空场景，方便 replay 直接 scene open。
    /// </summary>
    public sealed class ChromaGame : MonoBehaviour
    {
        private const int N = 8;
        private const int Colors = 5;
        private const float Tile = 68f;
        private const float Gap = 7f;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Boot()
        {
            if (FindFirstObjectByType<ChromaGame>() == null)
            {
                var go = new GameObject("ChromaRogue");
                go.AddComponent<ChromaGame>();
            }
        }

        private CaseConfig cfg;
        private System.Random rng;
        private Font font;
        private Canvas canvas;
        private GameObject gameRoot, lobbyRoot, shopPanel, bonusPanel, overPanel;
        private RectTransform boardRoot, sideRect;
        private readonly Image[,] tileImages = new Image[N, N];
        private readonly Text[,] tileArmor = new Text[N, N];
        private readonly Button[,] tileButtons = new Button[N, N];
        private readonly List<Sprite> _sprites = new List<Sprite>();
        private readonly Text[] bonusLabels = new Text[3];
        private Text roundText, targetText, scoreText, timeText, coinText, statusText, highText;
        private Text shopCoins, overText;
        private int[,] board = new int[N, N];
        private int[,] armor = new int[N, N];
        private readonly int[] inventory = new int[8];
        private readonly string[] itemNames = { "炸弹", "改色卡", "锤子", "洗牌" };
        private readonly int[] itemPrice = { 120, 80, 100, 60 };
        private bool playing, paused, bonusOpen, overOpen;
        private int round, target, totalScore, coins, tier, selectedR = -1, selectedC = -1;
        private float timeLeft, nextHud;
        private int multiplier = 1;
        private int bonusTime;

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
            font = Font.CreateDynamicFontFromOSFont(new[] { "Segoe UI", "Microsoft YaHei", "Arial" }, 24);
            if (font == null) font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            BuildCanvas();
            BuildEventSystem();
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
                cam.backgroundColor = new Color(0.035f, 0.05f, 0.075f);
                cam.orthographic = true;
            }
            var go = new GameObject("Canvas", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            go.transform.SetParent(transform, false);
            canvas = go.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            var scaler = go.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920, 1080);
            scaler.matchWidthOrHeight = 0f;
        }

        private void BuildEventSystem()
        {
            if (FindFirstObjectByType<EventSystem>() != null) return;
            var go = new GameObject("EventSystem", typeof(EventSystem), typeof(InputSystemUIInputModule));
            go.transform.SetParent(transform, false);
        }

        private RectTransform MakeRect(string name, Transform parent, float x, float y, float w, float h)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            var rt = go.GetComponent<RectTransform>();
            rt.anchorMin = rt.anchorMax = new Vector2(0, 1);
            rt.pivot = new Vector2(0, 1);
            rt.anchoredPosition = new Vector2(x, -y);
            rt.sizeDelta = new Vector2(w, h);
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
                              Action click, Color? color = null, int size = 22)
        {
            var rt = MakeRect("Btn", parent, x, y, w, h);
            var img = rt.gameObject.AddComponent<Image>();
            img.color = color ?? new Color(0.13f, 0.19f, 0.26f, 0.96f);
            var b = rt.gameObject.AddComponent<Button>();
            b.targetGraphic = img;
            var t = MakeLabel(rt, text, 0, 0, w, h, size, new Color(0.92f, 0.97f, 1f), TextAnchor.MiddleCenter);
            b.onClick.AddListener(() => click());
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
                };
                // 运行期特征状态，仅用于“当前激活了哪些路径”；不是 oracle。
                File.WriteAllText(Path.Combine(Path.GetTempPath(), "devsim_active_features.json"),
                                  JsonUtility.ToJson(fs));
            }
            catch (Exception e) { Debug.LogWarning("DevSim feature state write failed: " + e.Message); }
        }

        private void BuildUI()
        {
            FullPanel("Bg", new Color(0.035f, 0.05f, 0.075f));
            // soft top/bottom bands
            MakePanel(canvas.transform, 0, 0, 1920, 96, new Color(0.06f, 0.09f, 0.13f, 0.95f), "TopBand");
            MakePanel(canvas.transform, 0, 980, 1920, 100, new Color(0.06f, 0.09f, 0.13f, 0.95f), "BottomBand");

            gameRoot = new GameObject("GameRoot", typeof(RectTransform));
            gameRoot.transform.SetParent(canvas.transform, false);
            var gr = gameRoot.GetComponent<RectTransform>();
            gr.anchorMin = Vector2.zero; gr.anchorMax = Vector2.one;
            gr.offsetMin = gr.offsetMax = Vector2.zero;

            MakeLabel(gameRoot.transform, "CHROMA ROGUE", 60, 18, 500, 40, 30, new Color(0.95f, 0.85f, 0.55f), TextAnchor.MiddleLeft);
            roundText = MakeLabel(gameRoot.transform, "ROUND 1", 560, 20, 180, 36, 22, Color.white);
            targetText = MakeLabel(gameRoot.transform, "TARGET 600", 740, 20, 210, 36, 22, new Color(0.7f, 0.85f, 1f));
            scoreText = MakeLabel(gameRoot.transform, "SCORE 0", 960, 20, 230, 36, 22, Color.white);
            timeText = MakeLabel(gameRoot.transform, "30.0s", 1210, 18, 150, 40, 30, new Color(1f, 0.75f, 0.4f), TextAnchor.MiddleLeft);
            coinText = MakeLabel(gameRoot.transform, "COINS 0", 1370, 20, 180, 36, 22, new Color(1f, 0.86f, 0.45f));
            statusText = MakeLabel(gameRoot.transform, "SWAP ADJACENT TILES", 60, 92, 700, 34, 18, new Color(0.65f, 0.76f, 0.9f));

            var boardPanel = MakePanel(gameRoot.transform, 52, 134, 624, 624, new Color(0.055f, 0.08f, 0.115f, 0.98f), "BoardPanel");
            boardRoot = boardPanel.rectTransform;
            for (int r = 0; r < N; r++)
            {
                for (int c = 0; c < N; c++)
                {
                    int rr = r, cc = c;
                    var b = MakeButton(boardRoot, "", c * (Tile + Gap) + 6, r * (Tile + Gap) + 6,
                                   Tile, Tile, () => OnTile(rr, cc), Palette[0]);
                    tileButtons[r, c] = b;
                    tileImages[r, c] = b.GetComponent<Image>();
                    tileArmor[r, c] = MakeLabel(b.transform, "", 0, 0, Tile, Tile, 24,
                                            new Color(0.08f, 0.08f, 0.1f), TextAnchor.MiddleCenter);
                }
            }

            var side = MakePanel(gameRoot.transform, 704, 134, 1164, 624, new Color(0.055f, 0.08f, 0.115f, 0.98f), "SidePanel");
            sideRect = side.rectTransform;
            MakeLabel(side.transform, "ITEMS", 24, 18, 300, 30, 20, new Color(0.9f, 0.8f, 0.5f));
            for (int i = 0; i < itemNames.Length; i++)
            {
                int id = i;
                MakeButton(side.transform, $"{itemNames[i]}  x0", 24 + (i % 2) * 240, 56 + (i / 2) * 72,
                       220, 56, () => UseItem(id), new Color(0.12f, 0.18f, 0.24f), 18);
            }
            MakeLabel(side.transform, "HIGH SCORES", 24, 220, 300, 30, 20, new Color(0.9f, 0.8f, 0.5f));
            highText = MakeLabel(side.transform, "—", 24, 256, 300, 220, 17, new Color(0.75f, 0.84f, 0.95f));
            MakeLabel(side.transform, "HOW TO PLAY", 520, 18, 620, 30, 20, new Color(0.9f, 0.8f, 0.5f));
            MakeLabel(side.transform,
                  "交换相邻色块，3 连消除。\n30 秒内达到目标分；超出部分结转到下一轮。\n" +
                  "一次消 5 个以上 → BONUS 三选一。\n每 5 轮进商店；商店后难度提升。\n" +
                  "带数字的高级色块不能换位，每次交换数字 -1。",
                  520, 56, 620, 210, 18, new Color(0.78f, 0.86f, 0.96f));
            MakeButton(side.transform, "END RUN", 520, 430, 180, 46, EndRun, new Color(0.28f, 0.12f, 0.16f), 16);

            BuildLobby();
            BuildShop();
            BuildBonus();
            BuildOver();
        }

        private void BuildLobby()
        {
            lobbyRoot = new GameObject("Lobby", typeof(RectTransform));
            lobbyRoot.transform.SetParent(canvas.transform, false);
            var rt = lobbyRoot.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
            rt.offsetMin = rt.offsetMax = Vector2.zero;
            MakePanel(lobbyRoot.transform, 0, 0, 1920, 1080, new Color(0.035f, 0.05f, 0.075f, 1f), "LobbyBg");
            MakePanel(lobbyRoot.transform, 160, 140, 1600, 800, new Color(0.06f, 0.09f, 0.14f, 0.96f), "LobbyCard");
            MakeLabel(lobbyRoot.transform, "CHROMA", 230, 190, 700, 90, 76, new Color(1f, 0.82f, 0.42f));
            MakeLabel(lobbyRoot.transform, "ROGUE", 230, 270, 700, 80, 76, new Color(0.55f, 0.8f, 1f));
            MakeLabel(lobbyRoot.transform, "A tiny match-3 roguelite. Clear fast, bank the overflow, break the armor.",
                  230, 370, 900, 38, 20, new Color(0.72f, 0.82f, 0.95f));
            MakeButton(lobbyRoot.transform, "开始游戏", 230, 470, 300, 66, StartGame, new Color(0.18f, 0.42f, 0.32f), 24);
            MakeButton(lobbyRoot.transform, "商店练习", 230, 552, 300, 56, () => { StartGame(); OpenShop(); },
                   new Color(0.22f, 0.28f, 0.42f), 20);
            MakeButton(lobbyRoot.transform, "背包 / 说明", 230, 622, 300, 56, ShowHelp,
                   new Color(0.18f, 0.23f, 0.3f), 20);
            MakeLabel(lobbyRoot.transform, "LOCAL ARCADE", 1260, 470, 360, 30, 20, new Color(0.9f, 0.75f, 0.4f));
            MakeLabel(lobbyRoot.transform, "Top scores are stored on this machine.", 1260, 506, 380, 60, 17,
                  new Color(0.65f, 0.76f, 0.88f));
            MakeLabel(lobbyRoot.transform, "v0.1 vertical slice · no network · 30s rounds", 230, 840, 800, 30, 16,
                  new Color(0.4f, 0.5f, 0.62f));
        }

        private void BuildShop()
        {
            shopPanel = new GameObject("Shop", typeof(RectTransform));
            shopPanel.transform.SetParent(canvas.transform, false);
            var rt = shopPanel.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
            rt.offsetMin = rt.offsetMax = Vector2.zero;
            MakePanel(shopPanel.transform, 0, 0, 1920, 1080, new Color(0f, 0f, 0f, 0.72f), "ShopDim");
            MakePanel(shopPanel.transform, 380, 190, 1160, 680, new Color(0.07f, 0.1f, 0.15f, 0.99f), "ShopCard");
            MakeLabel(shopPanel.transform, "SHOP", 440, 230, 300, 54, 42, new Color(1f, 0.82f, 0.42f));
            shopCoins = MakeLabel(shopPanel.transform, "COINS 0", 1180, 240, 300, 40, 24, new Color(1f, 0.85f, 0.4f), TextAnchor.MiddleRight);
            for (int i = 0; i < 3; i++)
            {
                int slot = i;
                var card = MakePanel(shopPanel.transform, 440 + i * 340, 330, 300, 420,
                                 new Color(0.1f, 0.15f, 0.21f), "Offer" + i);
                MakeLabel(card.transform, "", 20, 18, 260, 50, 24, Color.white);
                MakeLabel(card.transform, "", 20, 74, 260, 120, 17, new Color(0.72f, 0.82f, 0.93f));
                MakeButton(card.transform, "BUY", 20, 340, 260, 52, () => BuySlot(slot),
                       new Color(0.18f, 0.38f, 0.3f), 20);
            }
            MakeButton(shopPanel.transform, "LEAVE SHOP", 760, 780, 400, 58, CloseShop,
                   new Color(0.3f, 0.24f, 0.16f), 22);
        }

        private void BuildBonus()
        {
            bonusPanel = new GameObject("Bonus", typeof(RectTransform));
            bonusPanel.transform.SetParent(canvas.transform, false);
            var rt = bonusPanel.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
            rt.offsetMin = rt.offsetMax = Vector2.zero;
            MakePanel(bonusPanel.transform, 0, 0, 1920, 1080, new Color(0f, 0f, 0f, 0.72f), "BonusDim");
            MakePanel(bonusPanel.transform, 380, 250, 1160, 560, new Color(0.07f, 0.1f, 0.15f, 0.99f), "BonusCard");
            MakeLabel(bonusPanel.transform, "BONUS", 440, 290, 500, 56, 42, new Color(0.55f, 0.85f, 1f));
            MakeLabel(bonusPanel.transform, "Choose one. The run continues.", 440, 350, 700, 40, 20,
                  new Color(0.75f, 0.84f, 0.95f));
            var bonusCard = bonusPanel.transform.Find("BonusCard");
            for (int i = 0; i < 3; i++)
            {
                int slot = i;
                var b = MakeButton(bonusCard, "", 60 + i * 340, 180, 300, 180, () => ChooseBonus(slot),
                       new Color(0.12f, 0.18f, 0.25f), 20);
                bonusLabels[i] = b.GetComponentInChildren<Text>();
                bonusLabels[i].fontSize = 22;
            }
        }

        private void BuildOver()
        {
            overPanel = new GameObject("Over", typeof(RectTransform));
            overPanel.transform.SetParent(canvas.transform, false);
            var rt = overPanel.GetComponent<RectTransform>();
            rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one;
            rt.offsetMin = rt.offsetMax = Vector2.zero;
            MakePanel(overPanel.transform, 0, 0, 1920, 1080, new Color(0f, 0f, 0f, 0.78f), "OverDim");
            MakePanel(overPanel.transform, 560, 260, 800, 520, new Color(0.07f, 0.1f, 0.15f, 0.99f), "OverCard");
            MakeLabel(overPanel.transform, "RUN OVER", 620, 300, 680, 60, 46, new Color(1f, 0.55f, 0.45f));
            overText = MakeLabel(overPanel.transform, "", 620, 380, 680, 160, 24, Color.white);
            MakeButton(overPanel.transform, "再来一局", 620, 590, 300, 60, () => { overPanel.SetActive(false); StartGame(); },
                   new Color(0.18f, 0.42f, 0.32f), 22);
            MakeButton(overPanel.transform, "回主菜单", 960, 590, 300, 60, () => { overPanel.SetActive(false); ShowLobby(); },
                   new Color(0.2f, 0.26f, 0.34f), 22);
        }

        // ───────────────────────── state / loop ─────────────────────────

        private void ShowLobby()
        {
            playing = false; paused = true; overOpen = false;
            if (gameRoot) gameRoot.SetActive(false);
            if (shopPanel) shopPanel.SetActive(false);
            if (bonusPanel) bonusPanel.SetActive(false);
            if (overPanel) overPanel.SetActive(false);
            if (lobbyRoot) lobbyRoot.SetActive(true);
            RefreshHighScores();
        }

        private void StartGame()
        {
            playing = true; paused = false; bonusOpen = false; overOpen = false;
            round = 1; target = cfg.target_base; totalScore = 0; coins = 0; tier = cfg.start_tier;
            multiplier = 1; timeLeft = cfg.round_seconds; bonusTime = 0;
            for (int i = 0; i < inventory.Length; i++) inventory[i] = 0;
            if (lobbyRoot) lobbyRoot.SetActive(false);
            if (gameRoot) gameRoot.SetActive(true);
            if (shopPanel) shopPanel.SetActive(false);
            if (bonusPanel) bonusPanel.SetActive(false);
            if (overPanel) overPanel.SetActive(false);
            BuildBoard();
            UpdateHud();
            statusText.text = "ROUND 1 — reach " + target;
            if (cfg.autoplay) StartCoroutine(AutoPlay());
        }

        private void Update()
        {
            if (cfg.legacy_canvas && sideRect != null)
                sideRect.sizeDelta = new Vector2(1164, 624 + (Time.frameCount % 2));
            if (cfg.legacy_hint && playing && !paused) HasMatch();
            if (!playing || paused || overOpen) return;
            timeLeft -= Time.deltaTime;
            if (timeLeft <= 0f) { timeLeft = 0f; EndRound(); return; }
            if (cfg.legacy_ui || Time.unscaledTime >= nextHud)
            {
                nextHud = Time.unscaledTime + 0.1f;
                UpdateHud();
            }
        }

        private void UpdateHud()
        {
            if (roundText == null) return;
            roundText.text = "ROUND " + round;
            targetText.text = "TARGET " + target;
            scoreText.text = "SCORE " + totalScore + (totalScore >= target ? "  +" + (totalScore - target) : "");
            timeText.text = Mathf.Max(0, timeLeft).ToString("0.0") + "s";
            coinText.text = "COINS " + coins;
            RefreshItemLabels();
        }

        private void RefreshItemLabels()
        {
            if (gameRoot == null) return;
            var side = gameRoot.transform.Find("SidePanel");
            if (side == null) return;
            int idx = 0;
            foreach (Transform child in side)
            {
                if (child.name != "Btn") continue;
                var t = child.GetComponentInChildren<Text>();
                if (t != null && idx < itemNames.Length)
                {
                    t.text = itemNames[idx] + "  x" + inventory[idx];
                    idx++;
                }
            }
        }

        // ───────────────────────── board ─────────────────────────

        private void BuildBoard()
        {
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                {
                    board[r, c] = rng.Next(Colors);
                    armor[r, c] = 0;
                    if (cfg.legacy_texture && tileImages[r, c] != null)
                    {
                        var tex = new Texture2D(8, 8, TextureFormat.RGBA32, false);
                        var px = new Color32[64];
                        var col = (Color32)Palette[board[r, c]];
                        for (int i = 0; i < px.Length; i++) px[i] = col;
                        tex.SetPixels32(px); tex.Apply();
                        var sp = Sprite.Create(tex, new Rect(0, 0, 8, 8), new Vector2(.5f, .5f), 8f);
                        _sprites.Add(sp);
                        tileImages[r, c].sprite = sp;
                    }
                    if (tileImages[r, c] != null && cfg.legacy_material)
                    {
                        var sh = Shader.Find("UI/Default");
                        if (sh != null) tileImages[r, c].material = new Material(sh);
                    }
                }
            // 去掉初始三连，避免开局自动消
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                {
                    if (c >= 2 && board[r, c] == board[r, c - 1] && board[r, c] == board[r, c - 2])
                        board[r, c] = (board[r, c] + 1) % Colors;
                    if (r >= 2 && board[r, c] == board[r - 1, c] && board[r, c] == board[r - 2, c])
                        board[r, c] = (board[r, c] + 1) % Colors;
                }
            RefreshTiles();
        }

        private void RefreshTiles()
        {
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                {
                    var img = tileImages[r, c];
                    if (img == null) continue;
                    int v = board[r, c];
                    img.color = v >= 0 ? Palette[v] : new Color(0.06f, 0.08f, 0.1f);
                    if (r == selectedR && c == selectedC) img.color = Color.Lerp(img.color, Color.white, 0.35f);
                    var t = tileArmor[r, c];
                    if (t == null) continue;
                    t.text = armor[r, c] > 0 ? armor[r, c].ToString() : "";
                    t.color = armor[r, c] > 0 ? new Color(0.08f, 0.08f, 0.1f) : Color.clear;
                }
        }

        private void OnTile(int r, int c)
        {
            if (!playing || paused) return;
            if (selectedR < 0) { selectedR = r; selectedC = c; RefreshTiles(); return; }
            if (selectedR == r && selectedC == c) { selectedR = selectedC = -1; RefreshTiles(); return; }
            if (Mathf.Abs(selectedR - r) + Mathf.Abs(selectedC - c) != 1)
            {
                selectedR = r; selectedC = c; RefreshTiles(); return;
            }
            int ar = selectedR, ac = selectedC;
            selectedR = selectedC = -1;
            TrySwap(ar, ac, r, c);
        }

        private void TrySwap(int r1, int c1, int r2, int c2)
        {
            if (armor[r1, c1] > 0 || armor[r2, c2] > 0)
            {
                statusText.text = "带数字的高级色块被锁住：先用交换/道具破甲";
                RefreshTiles();
                return;
            }
            SwapCells(r1, c1, r2, c2);
            if (!HasMatch())
            {
                SwapCells(r1, c1, r2, c2);
                statusText.text = "这次交换没有形成消除";
                RefreshTiles();
                return;
            }
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                    if (armor[r, c] > 0) armor[r, c]--;
            if (cfg.legacy_match_alloc) { var waste = new List<int>(); for (int i = 0; i < 64; i++) waste.Add(i); }
            ResolveBoard();
        }

        private void SwapCells(int r1, int c1, int r2, int c2)
        {
            int t = board[r1, c1]; board[r1, c1] = board[r2, c2]; board[r2, c2] = t;
            int a = armor[r1, c1]; armor[r1, c1] = armor[r2, c2]; armor[r2, c2] = a;
        }

        private bool HasMatch()
        {
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N - 2; c++)
                    if (board[r, c] >= 0 && board[r, c] == board[r, c + 1] && board[r, c] == board[r, c + 2]) return true;
            for (int c = 0; c < N; c++)
                for (int r = 0; r < N - 2; r++)
                    if (board[r, c] >= 0 && board[r, c] == board[r + 1, c] && board[r, c] == board[r + 2, c]) return true;
            return false;
        }

        private bool[,] FindMatches()
        {
            var hit = new bool[N, N];
            for (int r = 0; r < N; r++)
            {
                int run = 1;
                for (int c = 1; c <= N; c++)
                {
                    bool same = c < N && board[r, c] >= 0 && board[r, c] == board[r, c - 1];
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
                    bool same = r < N && board[r, c] >= 0 && board[r, c] == board[r - 1, c];
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

        private int Count(bool[,] hit)
        {
            int n = 0;
            for (int r = 0; r < N; r++) for (int c = 0; c < N; c++) if (hit[r, c]) n++;
            return n;
        }

        private void ResolveBoard()
        {
            int chain = 0, totalCleared = 0;
            while (true)
            {
                var hit = FindMatches();
                int n = Count(hit);
                if (n == 0) break;
                chain++;
                totalCleared += n;
                for (int r = 0; r < N; r++)
                    for (int c = 0; c < N; c++)
                        if (hit[r, c]) { board[r, c] = -1; armor[r, c] = 0; }
                totalScore += n * 10 * multiplier * chain;
                if (cfg.legacy_particle)
                    for (int r = 0; r < N; r++)
                        for (int c = 0; c < N; c++)
                            if (hit[r, c]) Burst(r, c, board[r, c]);
                DropAndRefill();
            }
            RefreshTiles();
            UpdateHud();
            if (totalCleared > 0) statusText.text = "CLEARED " + totalCleared + (chain > 1 ? "  CHAIN x" + chain : "");
            if (totalCleared >= 5 && !bonusOpen) { ShowBonus(); return; }
            CheckRound();
        }

        private void Burst(int r, int c, int color)
        {
            var img = MakePanel(canvas.transform, 52 + 6 + c * (Tile + Gap), 134 + 6 + r * (Tile + Gap),
                                30, 30, color >= 0 ? Palette[color] : Color.white, "fx");
            StartCoroutine(FadeFx(img));
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
            while (playing)
            {
                yield return new WaitForSeconds(0.35f);
                if (overOpen) yield break;
                if (bonusOpen) { ChooseBonus(0); continue; }
                if (shopPanel != null && shopPanel.activeSelf) { CloseShop(); continue; }
                if (!playing || paused) continue;
                for (int k = 0; k < 8; k++)
                {
                    int r = rng.Next(N), c = rng.Next(N);
                    int dr = rng.Next(2) == 0 ? 1 : 0, dc = dr == 1 ? 0 : 1;
                    int r2 = r + dr, c2 = c + dc;
                    if (r2 >= N || c2 >= N) continue;
                    if (armor[r, c] > 0 || armor[r2, c2] > 0) continue;
                    TrySwap(r, c, r2, c2);
                    break;
                }
            }
        }

        private void DropAndRefill()
        {
            for (int c = 0; c < N; c++)
            {
                int write = N - 1;
                for (int r = N - 1; r >= 0; r--)
                {
                    if (board[r, c] < 0) continue;
                    board[write, c] = board[r, c];
                    armor[write, c] = armor[r, c];
                    write--;
                }
                for (int r = write; r >= 0; r--)
                {
                    board[r, c] = rng.Next(Colors);
                    armor[r, c] = 0;
                    if (rng.NextDouble() < Mathf.Min(0.05f + 0.07f * tier, 0.45f))
                        armor[r, c] = rng.Next(2, Mathf.Min(2 + tier, 6) + 1);
                }
            }
        }

        // ───────────────────────── round / shop / bonus ─────────────────────────

        private void CheckRound()
        {
            if (!playing || bonusOpen) return;
            if (totalScore >= target) { EndRound(); return; }
            if (timeLeft <= 0f) EndGame();
        }

        private void EndRound()
        {
            if (!playing) return;
            if (totalScore < target) { EndGame(); return; }
            coins += 20 + round * 5 + Mathf.Max(0, totalScore - target) / 200;
            statusText.text = "ROUND " + round + " CLEAR — overflow " + (totalScore - target);
            if (round % 5 == 0) OpenShop();
            else NextRound();
        }

        private void NextRound()
        {
            round++;
            target = cfg.target_base + 300 * (round - 1);
            timeLeft = cfg.round_seconds + bonusTime;
            bonusTime = 0;
            paused = false;
            statusText.text = "ROUND " + round + " — reach " + target;
            RefreshTiles();
            UpdateHud();
        }

        private readonly struct Offer
        {
            public readonly int Item; public readonly int Price; public readonly string Desc;
            public Offer(int item, int price, string desc) { Item = item; Price = price; Desc = desc; }
        }
        private Offer[] offers = new Offer[3];

        private void OpenShop()
        {
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
            paused = true;
            offers[0] = new Offer(0, 120, "3x3 消除；高级色块护甲 -1");
            offers[1] = new Offer(1, 80, "把选中色块改成指定颜色");
            offers[2] = new Offer(2, 100, "单格消除；护甲 -1");
            shopPanel.SetActive(true);
            shopCoins.text = "COINS " + coins + "  ·  TIER " + tier;
            for (int i = 0; i < 3; i++)
            {
                var c = shopPanel.transform.Find("Offer" + i);
                var texts = c.GetComponentsInChildren<Text>();
                if (texts.Length >= 2)
                {
                    texts[0].text = itemNames[offers[i].Item] + "  " + offers[i].Price;
                    texts[1].text = offers[i].Desc;
                }
            }
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

        private void BuySlot(int slot)
        {
            var o = offers[slot];
            if (coins < o.Price) { shopCoins.text = "COINS " + coins + " — 不够"; return; }
            coins -= o.Price;
            inventory[o.Item]++;
            shopCoins.text = "COINS " + coins;
            RefreshItemLabels();
        }

        private void CloseShop()
        {
            shopPanel.SetActive(false);
            tier = Mathf.Min(6, tier + 1);
            NextRound();
        }

        private string[] bonusNames = { "得分 x1.1", "本轮 +2s", "炸弹 +1" };
        private int[] bonusIds = new int[3];

        private void ShowBonus()
        {
            bonusOpen = true; paused = true;
            string[] names = { "得分 x1.1", "本轮 +2s", "炸弹 +1" };
            for (int i = 0; i < 3; i++)
            {
                int id = rng.Next(3);
                bonusIds[i] = id;
                if (bonusLabels[i] != null) bonusLabels[i].text = names[id];
            }
            bonusPanel.SetActive(true);
            statusText.text = "BONUS — choose one";
        }

        private void ChooseBonus(int slot)
        {
            int id = bonusIds[Mathf.Clamp(slot, 0, 2)];
            if (id == 0) multiplier += 1;
            else if (id == 1) bonusTime += 2;
            else inventory[0]++;
            bonusOpen = false;
            bonusPanel.SetActive(false);
            paused = false;
            RefreshItemLabels();
            CheckRound();
        }

        private void EndRun()
        {
            EndGame();
        }

        private void EndGame()
        {
            if (!playing) return;
            playing = false; paused = true; overOpen = true;
            overPanel.SetActive(true);
            overText.text = "SCORE  " + totalScore + "\nROUND  " + round + "\n\n本机最高分已记录";
            SaveScore(totalScore, round);
        }

        // ───────────────────────── items / persistence ─────────────────────────

        private void UseItem(int id)
        {
            if (!playing || paused || selectedR < 0) { statusText.text = "先选一个色块"; return; }
            if (inventory[id] <= 0) { statusText.text = "道具数量为 0"; return; }
            inventory[id]--;
            int r = selectedR, c = selectedC;
            if (id == 0)
            {
                for (int rr = Mathf.Max(0, r - 1); rr <= Mathf.Min(N - 1, r + 1); rr++)
                    for (int cc = Mathf.Max(0, c - 1); cc <= Mathf.Min(N - 1, c + 1); cc++)
                    { board[rr, cc] = -1; armor[rr, cc] = 0; }
            }
            else if (id == 1) board[r, c] = (board[r, c] + 1) % Colors;
            else if (id == 2) { board[r, c] = -1; armor[r, c] = 0; }
            else ShuffleBoard();
            selectedR = selectedC = -1;
            RefreshItemLabels();
            ResolveBoard();
        }

        private void ShuffleBoard()
        {
            for (int r = 0; r < N; r++)
                for (int c = 0; c < N; c++)
                {
                    int rr = rng.Next(N), cc = rng.Next(N);
                    int t = board[r, c]; board[r, c] = board[rr, cc]; board[rr, cc] = t;
                    int a = armor[r, c]; armor[r, c] = armor[rr, cc]; armor[rr, cc] = a;
                }
            // 保证不是死局：至少有一个可消
            for (int k = 0; k < 50 && !HasMatch(); k++)
            {
                int r = rng.Next(N), c = rng.Next(N);
                board[r, c] = rng.Next(Colors);
            }
        }

        private void ShowHelp()
        {
            statusText.text = "3 连消除；5 连开 BONUS；每 5 轮商店；带数字色块锁住";
            if (lobbyRoot) lobbyRoot.SetActive(true);
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
                for (int i = 0; i < book.entries.Count && i < 10; i++)
                    sb.AppendLine((i + 1) + ".  " + book.entries[i].score + "   R" + book.entries[i].round);
                highText.text = sb.ToString();
            }
            catch { highText.text = "—"; }
        }
    }
}

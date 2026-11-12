// Chroma Rogue 确定性自测（在 Unity Play Mode 中经 `unity command script run` 执行）。
// 不读取 oracle / 期望机会；只验证玩法语义、轮次规则、商店与输入绑定。
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;
using DevSim;

public static class DevSimSelfTest
{
    [Serializable]
    public sealed class Check
    {
        public string name;
        public bool ok;
        public string detail;
    }

    [Serializable]
    public sealed class Result
    {
        public bool ok;
        public int passed;
        public int failed;
        public List<Check> checks = new List<Check>();
    }

    private static readonly List<Check> checks = new List<Check>();

    private static void Ok(string name, bool cond, object detail = null)
    {
        checks.Add(new Check
        {
            name = name,
            ok = cond,
            detail = detail == null ? "" : detail.ToString(),
        });
    }

    private static ChromaGame Game()
    {
        return UnityEngine.Object.FindFirstObjectByType<ChromaGame>();
    }

    private static ChromaGame.DebugStateDto State(ChromaGame g)
    {
        return JsonUtility.FromJson<ChromaGame.DebugStateDto>(g.DebugState());
    }

    private static ChromaGame.DebugSwapDto Swap(ChromaGame g, int r1, int c1, int r2, int c2)
    {
        return JsonUtility.FromJson<ChromaGame.DebugSwapDto>(g.DebugSwap(r1, c1, r2, c2));
    }

    private static ChromaGame.DebugDragDto Drag(ChromaGame g, int r, int c, float dx, float dy)
    {
        return JsonUtility.FromJson<ChromaGame.DebugDragDto>(g.DebugDragTarget(r, c, dx, dy));
    }

    private static string Cfg()
    {
        // 固定种子 + 关闭随机叠层，保证确定性。
        return "{\"case_id\":\"selftest\",\"seed\":12345,\"autoplay\":false,"
             + "\"round_seconds\":30,\"round_seconds_step\":0,\"round_seconds_min\":5,"
             + "\"target_base\":600,\"target_step\":300,"
             + "\"board_start\":10,\"board_min\":6,\"board_step_rounds\":2,"
             + "\"preview_seconds\":0,\"shop_every\":3,\"shop_refresh_cost\":40,"
             + "\"stack_chance_base\":0,\"stack_chance_per_tier\":0}";
    }

    private static int[] Latin(int n)
    {
        var a = new int[n * n];
        for (int r = 0; r < n; r++)
            for (int c = 0; c < n; c++)
                a[r * n + c] = (r * 2 + c) % 5;
        return a;
    }

    private static int[] SolidLayers(int n)
    {
        var a = new int[n * n];
        for (int i = 0; i < a.Length; i++) a[i] = 1;
        return a;
    }

    private static void Set(int[] colors, int[] layers, int n, int r, int c, int color, int layer = 1)
    {
        colors[r * n + c] = color;
        layers[r * n + c] = layer;
    }

    private static string Render(ChromaGame g)
    {
        return g.DebugRenderInfo();
    }

    public static string RunAll()
    {
        checks.Clear();
        var g = Game();
        if (g == null)
        {
            Ok("game_instance", false, "ChromaGame not found in scene");
            return Finish();
        }
        g.DebugLoadConfig(Cfg());
        g.DebugStartRun();

        // 1. 交换绑定：拖拽源与目的地必须正好是 A/B，未受其他临格影响。
        {
            int n = 6;
            var col = Latin(n);
            var lay = SolidLayers(n);
            // 交换 (0,0)<->(0,1) 后 col1 rows0..2 形成 1,1,1；(0,0) 保留目标色 0。
            Set(col, lay, n, 0, 0, 1);
            Set(col, lay, n, 0, 1, 0);
            Set(col, lay, n, 1, 1, 1);
            Set(col, lay, n, 2, 1, 1);
            g.DebugSetBoard(n, col, lay);
            var before = State(g);
            var d = Swap(g, 0, 0, 0, 1);
            var after = State(g);
            Ok("swap_binds_source_target",
                d.accepted && d.sourceColor == 1 && d.targetColor == 0
                && d.r1 == 0 && d.c1 == 0 && d.r2 == 0 && d.c2 == 1
                && d.totalCleared >= 3 && after.colors[0] == 0,
                "accepted=" + d.accepted + " src=" + d.sourceColor + " dst=" + d.targetColor
                + " cleared=" + d.totalCleared + " cell00After=" + after.colors[0]);

            var far = Swap(g, 0, 0, 0, 3);
            Ok("swap_rejects_non_adjacent",
                !far.accepted && far.reason == "not-adjacent", far.reason);

            var before2 = State(g);
            var bad = Swap(g, 3, 3, 3, 4);
            var after2 = State(g);
            bool same = true;
            for (int i = 0; i < before2.colors.Length; i++)
                if (before2.colors[i] != after2.colors[i]) same = false;
            Ok("invalid_swap_rejected_and_board_untouched",
                !bad.accepted && bad.reason == "no-match" && same,
                bad.reason + " untouched=" + same);
        }

        // 2. 既存匹配不得让无关交换“看起来有效”。
        {
            int n = 6;
            var col = Latin(n);
            var lay = SolidLayers(n);
            Set(col, lay, n, 5, 3, 2);
            Set(col, lay, n, 5, 4, 2);
            Set(col, lay, n, 5, 5, 2);
            g.DebugSetBoard(n, col, lay);
            var d = Swap(g, 2, 2, 2, 3);
            Ok("preexisting_match_does_not_validate_swap",
                !d.accepted && d.reason == "no-match", d.reason);
        }

        // 3. 叠层块不能换位；数字显示与层数一致。
        {
            int n = 6;
            var col = Latin(n);
            var lay = SolidLayers(n);
            Set(col, lay, n, 0, 0, 1, 3);
            Set(col, lay, n, 2, 2, 2, 2);
            g.DebugSetBoard(n, col, lay);
            var st = State(g);
            var d = Swap(g, 0, 0, 0, 1);
            var st2 = State(g);
            Ok("locked_tile_cannot_swap",
                !d.accepted && d.reason == "locked" && st2.stackCount == st.stackCount,
                d.reason);
            Ok("armor_number_matches_layers",
                st.armorTexts[0] == "3" && st.armorTexts[2 * n + 2] == "2"
                && st.armorTexts[1] == "" && st.stackCount == 2,
                "t00=" + st.armorTexts[0] + " t22=" + st.armorTexts[2 * n + 2]
                + " stack=" + st.stackCount);
        }

        // 4. 同色消除每步只减该格 1 层：N=2 → 1（变普通块、不显示数字），再一步才消掉。
        {
            int n = 6;
            var col = Latin(n);
            var lay = SolidLayers(n);
            for (int c = 0; c < 3; c++) Set(col, lay, n, 0, c, 2, 2);
            g.DebugSetBoard(n, col, lay);
            var before = State(g);
            var s1 = JsonUtility.FromJson<ChromaGame.DebugSwapDto>(g.DebugResolveStep());
            var mid = State(g);
            bool allOne = true;
            for (int c = 0; c < 3; c++)
                if (mid.layers[0 * n + c] != 1) allOne = false;
            bool noNumbers = true;
            for (int i = 0; i < mid.armorTexts.Length; i++)
                if (mid.layers[i] == 1 && mid.armorTexts[i] != "") noNumbers = false;
            Ok("one_layer_per_elimination_step",
                before.stackCount == 3 && s1.accepted && s1.totalCleared == 3
                && s1.removed == 0 && mid.stackCount == 0 && allOne,
                "matched=" + s1.totalCleared + " removed=" + s1.removed
                + " stackBefore=" + before.stackCount + " stackAfter=" + mid.stackCount
                + " layers=" + mid.layers[0] + "," + mid.layers[1] + "," + mid.layers[2]);
            Ok("normal_tile_shows_no_number", noNumbers, "noNumbers=" + noNumbers);

            var s2 = JsonUtility.FromJson<ChromaGame.DebugSwapDto>(g.DebugResolveStep());
            Ok("second_elimination_clears_normal_tiles",
                s2.accepted && s2.removed == 3,
                "accepted=" + s2.accepted + " removed=" + s2.removed);
        }

        // 5. 道具：锤子/炸弹对叠层块只拆 1 层；改色不能作用于叠层块；非法目标不消耗。
        {
            int n = 6;
            var col = Latin(n);
            var lay = SolidLayers(n);
            Set(col, lay, n, 2, 2, 3, 2);
            g.DebugSetBoard(n, col, lay);
            g.DebugGrantItem(2, 2);
            var d = JsonUtility.FromJson<ChromaGame.DebugSwapDto>(g.DebugItem(2, 2, 2));
            var st = State(g);
            Ok("hammer_strips_one_layer",
                d.accepted && st.stackCount == 0 && st.inventory[2] == 1,
                "accepted=" + d.accepted + " stack=" + st.stackCount + " inv=" + st.inventory[2]);

            g.DebugSetBoard(n, Latin(n), SolidLayers(n));
            col = Latin(n); lay = SolidLayers(n);
            Set(col, lay, n, 4, 4, 1, 2);
            g.DebugSetBoard(n, col, lay);
            g.DebugGrantItem(0, 1);
            var b = JsonUtility.FromJson<ChromaGame.DebugSwapDto>(g.DebugItem(0, 4, 4));
            var st2 = State(g);
            Ok("bomb_strips_one_layer_of_stack",
                b.accepted && st2.stackCount == 0,
                "accepted=" + b.accepted + " stack=" + st2.stackCount);

            g.DebugSetBoard(n, Latin(n), SolidLayers(n));
            col = Latin(n); lay = SolidLayers(n);
            Set(col, lay, n, 4, 4, 1, 2);
            g.DebugSetBoard(n, col, lay);
            g.DebugGrantItem(1, 1);
            var bad = JsonUtility.FromJson<ChromaGame.DebugSwapDto>(g.DebugItem(1, 4, 4));
            var st3 = State(g);
            Ok("recolor_rejects_stacked_target",
                !bad.accepted && bad.reason == "invalid-target" && st3.inventory[1] == 1,
                bad.reason + " inv=" + st3.inventory[1]);

            var outside = JsonUtility.FromJson<ChromaGame.DebugDragDto>(g.DebugScreenPosOfCell(4, 4));
            g.DebugGrantItem(0, 1);
            bool dropped = g.DropItem(0, new Vector2(5f, 5f));
            var st4 = State(g);
            Ok("item_drop_outside_board_rejected", !dropped && st4.inventory[0] == 1,
                "dropped=" + dropped + " inv=" + st4.inventory[0]);

            // 合法拖放路径：用格子屏幕坐标调用 DropItem，验证 CellFromScreen + 消耗。
            g.DebugSetBoard(n, Latin(n), SolidLayers(n));
            col = Latin(n); lay = SolidLayers(n);
            Set(col, lay, n, 3, 3, 1, 1);
            g.DebugSetBoard(n, col, lay);
            g.DebugGrantItem(2, 1);
            var pos = JsonUtility.FromJson<ChromaGame.DebugDragDto>(g.DebugScreenPosOfCell(3, 3));
            bool okDrop = g.DropItem(2, new Vector2(pos.dx, pos.dy));
            var st5 = State(g);
            Ok("item_drop_on_cell_consumes_stock", okDrop && st5.inventory[2] == 0,
                "drop=" + okDrop + " inv=" + st5.inventory[2]);
        }

        // 6. BONUS：同一种颜色（含连锁）≥6 才触发。
        {
            int n = 6;
            var col = Latin(n);
            var lay = SolidLayers(n);
            // 5 个同色一行：不触发。
            for (int c = 0; c < 5; c++) Set(col, lay, n, 0, c, 2);
            g.DebugSetBoard(n, col, lay);
            var d5 = JsonUtility.FromJson<ChromaGame.DebugSwapDto>(g.DebugResolveBoard());
            Ok("bonus_not_triggered_at_5_same_color",
                !d5.bonusTriggered && d5.maxSameColor < 6 && d5.totalCleared >= 5,
                "maxSame=" + d5.maxSameColor + " cleared=" + d5.totalCleared
                + " chain=" + d5.chain);

            // 两种颜色各 3 个：合计 6 但不触发。
            col = Latin(n); lay = SolidLayers(n);
            for (int c = 0; c < 3; c++) Set(col, lay, n, 0, c, 2);
            for (int c = 0; c < 3; c++) Set(col, lay, n, 1, c, 4);
            g.DebugSetBoard(n, col, lay);
            var dm = JsonUtility.FromJson<ChromaGame.DebugSwapDto>(g.DebugResolveBoard());
            Ok("bonus_not_triggered_by_mixed_colors_total_6",
                !dm.bonusTriggered && dm.totalCleared >= 6 && dm.maxSameColor < 6,
                "maxSame=" + dm.maxSameColor + " cleared=" + dm.totalCleared);

            // 同一种颜色 6 个：触发，并记录连消链。
            col = Latin(n); lay = SolidLayers(n);
            for (int c = 0; c < 3; c++) Set(col, lay, n, 0, c, 3);
            for (int c = 0; c < 3; c++) Set(col, lay, n, 1, c, 3);
            g.DebugSetBoard(n, col, lay);
            var d6 = JsonUtility.FromJson<ChromaGame.DebugSwapDto>(g.DebugResolveBoard());
            var st = State(g);
            Ok("bonus_triggered_at_6_same_color",
                d6.bonusTriggered && d6.maxSameColor >= 6 && st.bonusOpen,
                "maxSame=" + d6.maxSameColor + " bonus=" + d6.bonusTriggered
                + " chain=" + d6.chain);
            Ok("bonus_feedback_recorded",
                st.lastSameColorCount >= 6 && st.lastChain >= 1 && st.lastStep.Length > 0,
                "same=" + st.lastSameColorCount + " chain=" + st.lastChain
                + " step=" + st.lastStep);
            g.DebugChooseBonus(0);
        }

        // 7. 轮次时间驱动 + 预告内容 + 棋盘尺寸随难度。
        {
            g.DebugJumpToRound(1);
            g.DebugSetScore(99999);
            var still1 = State(g);
            Ok("target_does_not_advance_round_early",
                still1.round == 1 && still1.phase == "Playing",
                "round=" + still1.round + " phase=" + still1.phase);
            var adv = State2(g.DebugAdvance(31f));
            Ok("round_advances_when_time_ends",
                adv.round == 2 && adv.phase == "Preview",
                "round=" + adv.round + " phase=" + adv.phase);
            Ok("preview_shows_next_round_plan",
                adv.previewOpen && adv.nextRoundTarget == 900 && adv.nextRoundSeconds == 30
                && adv.nextBoardN == 10,
                "target=" + adv.nextRoundTarget + " seconds=" + adv.nextRoundSeconds
                + " board=" + adv.nextBoardN);
            var cont = State2(g.DebugContinue());
            Ok("preview_continue_starts_round",
                cont.phase == "Playing" && cont.round == 2 && Mathf.Abs(cont.timeLeft - 30f) < 0.11f,
                "phase=" + cont.phase + " round=" + cont.round + " t=" + cont.timeLeft);

            var plan1 = JsonUtility.FromJson<ChromaGame.DebugPlanDto>(g.DebugPlan(1));
            var plan3 = JsonUtility.FromJson<ChromaGame.DebugPlanDto>(g.DebugPlan(3));
            var plan9 = JsonUtility.FromJson<ChromaGame.DebugPlanDto>(g.DebugPlan(9));
            Ok("board_size_shrinks_10_to_6",
                plan1.boardN == 10 && plan3.boardN == 9 && plan9.boardN == 6
                && plan1.target == 600 && plan3.target == 1200,
                "r1=" + plan1.boardN + " r3=" + plan3.boardN + " r9=" + plan9.boardN);

            g.DebugJumpToRound(9);
            var st9 = State(g);
            Ok("board_rebuilds_at_difficulty_size", st9.boardN == 6, "boardN=" + st9.boardN);

            // 未达标 → 时间到即失败。
            g.DebugJumpToRound(1);
            g.DebugSetScore(10);
            var over = State2(g.DebugAdvance(31f));
            Ok("unmet_target_ends_run", over.phase == "Over" && over.overOpen,
                "phase=" + over.phase);
        }

        // 8. 商店：每 3 轮一次、刷新、售罄、重复购买拦截。
        {
            g.DebugJumpToRound(3);
            g.DebugSetScore(99999);
            var shop = State2(g.DebugAdvance(31f));
            Ok("shop_opens_every_3_rounds",
                shop.phase == "Shop" && shop.shopOpen && shop.shopVisits == 1,
                "phase=" + shop.phase + " visits=" + shop.shopVisits);

            g.DebugSetCoins(1000);
            var beforeBuy = State(g);
            int idxBefore = -1;
            for (int i = 0; i < 3; i++) if (beforeBuy.offerNames[i] != "") { idxBefore = i; break; }
            string boughtName = beforeBuy.offerNames[idxBefore];
            int invBefore = 0;
            for (int i = 0; i < beforeBuy.inventory.Length; i++) invBefore += beforeBuy.inventory[i];
            var afterBuy = State2(g.DebugBuy(idxBefore));
            int invAfter = 0;
            for (int i = 0; i < afterBuy.inventory.Length; i++) invAfter += afterBuy.inventory[i];
            Ok("shop_purchase_marks_sold",
                invAfter == invBefore + 1 && afterBuy.offerSold[idxBefore]
                && afterBuy.coins == 1000 - beforeBuy.offerPrices[idxBefore],
                "before=" + invBefore + " after=" + invAfter + " sold=" + afterBuy.offerSold[idxBefore]
                + " coins=" + afterBuy.coins);

            var again = State2(g.DebugBuy(idxBefore));
            int invAgain = 0;
            for (int i = 0; i < again.inventory.Length; i++) invAgain += again.inventory[i];
            Ok("shop_blocks_duplicate_purchase", invAgain == invAfter,
                "after=" + invAfter + " again=" + invAgain + " name=" + boughtName);

            int coinsBeforeRefresh = again.coins;
            var refreshed = State2(g.DebugRefreshShop());
            bool boughtItemGone = true;
            for (int i = 0; i < 3; i++)
                if (refreshed.offerNames[i] == boughtName) boughtItemGone = false;
            Ok("shop_refresh_works_and_hides_bought_item",
                refreshed.coins == coinsBeforeRefresh - 40 && refreshed.refreshCount == 1
                && boughtItemGone,
                "coins=" + refreshed.coins + " refresh=" + refreshed.refreshCount
                + " gone=" + boughtItemGone);

            var left = State2(g.DebugLeaveShop());
            Ok("shop_leave_opens_next_preview",
                left.phase == "Preview" && left.round == 4 && left.tier == 2,
                "phase=" + left.phase + " round=" + left.round + " tier=" + left.tier);
            g.DebugContinue();
            g.DebugSetScore(99999);
            var r4 = State2(g.DebugAdvance(31f));
            Ok("shop_not_reopened_at_round_4",
                r4.phase == "Preview" && r4.shopVisits == 1 && r4.round == 5,
                "phase=" + r4.phase + " visits=" + r4.shopVisits + " round=" + r4.round);
        }

        // 9. 画质：设计分辨率 1:1、色块纹理与显示像素一致。
        {
            g.DebugJumpToRound(1);
            var r = JsonUtility.FromJson<ChromaGame.DebugRenderDto>(Render(g));
            bool scaleOk = Mathf.Abs(r.canvasScale - (r.screenH / 720f)) < 0.02f;
            Ok("canvas_scaled_1to1_with_design",
                r.refW == 1280 && r.refH == 720 && scaleOk,
                "screen=" + r.screenW + "x" + r.screenH + " scale=" + r.canvasScale
                + " ref=" + r.refW + "x" + r.refH);
            Ok("tile_texture_matches_display_pixels",
                r.tileTextureSize == r.tilePx && r.tilePx >= 40,
                "tilePx=" + r.tilePx + " tex=" + r.tileTextureSize);
        }

        // 10. 动效范围：只对退场格播退场、只对受重力影响的格播移动、只对新格播生成。
        {
            int n = 6;
            g.DebugSetBoard(n, Latin(n), SolidLayers(n));
            int[] removed = new int[n * n];
            for (int r = 3; r < 6; r++) removed[r * n + 3] = 1;
            var p = JsonUtility.FromJson<ChromaGame.DebugDropPlanDto>(g.DebugPlanDrop(removed));
            int moved = 0, spawned = 0;
            bool touchesOtherColumn = false;
            bool gravityDownward = true;
            for (int i = 0; i < n * n; i++)
            {
                if (p.moved[i])
                {
                    moved++;
                    if (i % n != 3) touchesOtherColumn = true;
                    if (p.fromRow[i] >= i / n) gravityDownward = false;
                }
                if (p.spawned[i]) { spawned++; if (i % n != 3) touchesOtherColumn = true; }
            }
            Ok("animation_only_affects_changed_cells",
                moved == 3 && spawned == 3 && !touchesOtherColumn,
                "moved=" + moved + " spawned=" + spawned + " otherColumn=" + touchesOtherColumn);
            Ok("moved_tiles_fall_downward",
                gravityDownward && moved == 3,
                "fromRow=" + string.Join(",", new int[] { p.fromRow[3 * n + 3], p.fromRow[4 * n + 3], p.fromRow[5 * n + 3] }));

            var p0 = JsonUtility.FromJson<ChromaGame.DebugDropPlanDto>(
                g.DebugPlanDrop(new int[n * n]));
            int anyAnim = 0;
            for (int i = 0; i < n * n; i++) if (p0.moved[i] || p0.spawned[i]) anyAnim++;
            Ok("no_removal_no_animation", anyAnim == 0, "animated=" + anyAnim);
        }

        // 11. 拖拽方向映射 + ExecuteEvents 真实事件链（最后执行，会启动交换协程）。
        {
            var up = Drag(g, 2, 2, 0, 40);
            var down = Drag(g, 2, 2, 0, -40);
            var left = Drag(g, 2, 2, -40, 0);
            var right = Drag(g, 2, 2, 40, 0);
            var small = Drag(g, 2, 2, 5, 2);
            Ok("drag_direction_maps_to_correct_neighbor",
                up.valid && up.toR == 1 && up.toC == 2
                && down.valid && down.toR == 3 && down.toC == 2
                && left.valid && left.toR == 2 && left.toC == 1
                && right.valid && right.toR == 2 && right.toC == 3
                && !small.valid,
                "up=" + up.toR + "," + up.toC + " down=" + down.toR + "," + down.toC
                + " left=" + left.toR + "," + left.toC + " right=" + right.toR + "," + right.toC
                + " small=" + small.valid);

            int n = 6;
            var col = Latin(n);
            var lay = SolidLayers(n);
            Set(col, lay, n, 0, 0, 1);
            Set(col, lay, n, 0, 1, 0);
            Set(col, lay, n, 1, 1, 1);
            Set(col, lay, n, 2, 1, 1);
            g.DebugSetBoard(n, col, lay);
            var tile = g.DebugTileObject(0, 0);
            var td = tile != null ? tile.GetComponent<DevSim.TileDrag>() : null;
            bool dispatched = false;
            bool esOk = EventSystem.current != null;
            if (td != null && esOk)
            {
                var ped = new PointerEventData(EventSystem.current)
                {
                    button = PointerEventData.InputButton.Left,
                    position = new Vector2(Screen.width * 0.5f, Screen.height * 0.5f),
                    delta = new Vector2(20f, 0f),
                };
                ExecuteEvents.Execute(tile, ped, ExecuteEvents.beginDragHandler);
                ExecuteEvents.Execute(tile, ped, ExecuteEvents.dragHandler);
                ExecuteEvents.Execute(tile, ped, ExecuteEvents.dragHandler);
                ExecuteEvents.Execute(tile, ped, ExecuteEvents.endDragHandler);
                dispatched = true;
            }
            Ok("execute_events_drag_binds_row_col_delta",
                dispatched && td != null
                && td.lastBeginRow == 0 && td.lastBeginCol == 0
                && td.lastEndRow == 0 && td.lastEndCol == 0
                && Mathf.Abs(td.lastTotalDelta.x - 40f) < 0.01f
                && Mathf.Abs(td.lastTotalDelta.y) < 0.01f
                && td.dragEvents == 4,
                "tile=" + (tile != null) + " td=" + (td != null) + " es=" + esOk
                + " dispatched=" + dispatched
                + (td == null ? ""
                    : (" begin=" + td.lastBeginRow + "," + td.lastBeginCol
                       + " end=" + td.lastEndRow + "," + td.lastEndCol
                       + " delta=" + td.lastTotalDelta + " events=" + td.dragEvents)));
        }

        return Finish();
    }

    private static ChromaGame.DebugStateDto State2(string json)
    {
        return JsonUtility.FromJson<ChromaGame.DebugStateDto>(json);
    }

    private static string Esc(string s)
    {
        if (s == null) return "";
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"")
                .Replace("\n", "\\n").Replace("\r", "").Replace("\t", " ");
    }

    private static string Finish()
    {
        int passed = 0, failed = 0;
        var sb = new System.Text.StringBuilder();
        sb.Append("{\"checks\":[");
        for (int i = 0; i < checks.Count; i++)
        {
            var c = checks[i];
            if (c.ok) passed++; else failed++;
            if (i > 0) sb.Append(',');
            sb.Append("{\"name\":\"").Append(Esc(c.name)).Append("\",\"ok\":")
              .Append(c.ok ? "true" : "false").Append(",\"detail\":\"")
              .Append(Esc(c.detail)).Append("\"}");
        }
        sb.Append("],\"passed\":").Append(passed)
          .Append(",\"failed\":").Append(failed)
          .Append(",\"ok\":").Append(failed == 0 ? "true" : "false").Append('}');
        return sb.ToString();
    }
}

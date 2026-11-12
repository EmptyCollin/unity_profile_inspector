using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace DevSim
{
    /// <summary>
    /// 拖拽交换：按下 / 拖动 / 松手都交给 ChromaGame 判定。
    /// 坐标约定：eventData.delta 是屏幕像素（y 向上）；anchoredPosition 是 Canvas 单位。
    /// 方向映射见 ChromaGame.ResolveDragTarget（上 = 行号 -1）。
    /// </summary>
    public sealed class TileDrag : MonoBehaviour, IBeginDragHandler, IDragHandler, IEndDragHandler
    {
        public ChromaGame game;
        public int row;
        public int col;
        public int dragEvents;

        // 供确定性测试与排障读取的最近一次拖拽轨迹（不是 oracle）。
        public int lastBeginRow = -1, lastBeginCol = -1;
        public int lastEndRow = -1, lastEndCol = -1;
        public Vector2 lastTotalDelta;

        private RectTransform _rt;
        private Vector2 _start;
        private Vector2 _total;
        private Canvas _canvas;

        private void Awake()
        {
            _rt = GetComponent<RectTransform>();
            _canvas = GetComponentInParent<Canvas>();
        }

        public void OnBeginDrag(PointerEventData eventData)
        {
            if (game == null || _rt == null) return;
            _start = _rt.anchoredPosition;
            _total = Vector2.zero;
            dragEvents++;
            lastBeginRow = row;
            lastBeginCol = col;
            game.BeginDrag(row, col);
        }

        public void OnDrag(PointerEventData eventData)
        {
            if (game == null || _rt == null) return;
            _total += eventData.delta;
            dragEvents++;
            float s = _canvas != null ? _canvas.scaleFactor : 1f;
            _rt.anchoredPosition = _start + _total / Mathf.Max(0.01f, s);
            game.UpdateDragTarget(row, col, _total);
        }

        public void OnEndDrag(PointerEventData eventData)
        {
            if (game == null || _rt == null) return;
            dragEvents++;
            _rt.anchoredPosition = _start;
            lastEndRow = row;
            lastEndCol = col;
            lastTotalDelta = _total;
            game.EndDrag(row, col, _total);
        }
    }
}

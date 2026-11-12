using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace DevSim
{
    /// <summary>拖拽交换：按下/拖动/松手都交给 ChromaGame 判定。</summary>
    public sealed class TileDrag : MonoBehaviour, IBeginDragHandler, IDragHandler, IEndDragHandler
    {
        public ChromaGame game;
        public int row;
        public int col;
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
            game.BeginDrag(row, col);
        }

        public void OnDrag(PointerEventData eventData)
        {
            if (game == null || _rt == null) return;
            _total += eventData.delta;
            float s = _canvas != null ? _canvas.scaleFactor : 1f;
            _rt.anchoredPosition = _start + _total / Mathf.Max(0.01f, s);
        }

        public void OnEndDrag(PointerEventData eventData)
        {
            if (game == null || _rt == null) return;
            if (_rt != null) _rt.anchoredPosition = _start;
            game.EndDrag(row, col, _total);
        }
    }
}

using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace DevSim
{
    /// <summary>
    /// 道具拖拽：按住道具拖到棋盘目标格，松手生效；非法目标回弹并提示。
    /// 拖拽期间显示跟随指针的图标，并高亮目标格。
    /// </summary>
    public sealed class ItemDrag : MonoBehaviour, IBeginDragHandler, IDragHandler, IEndDragHandler
    {
        public ChromaGame game;
        public int item;

        private RectTransform _canvasRect;
        private RectTransform _slotRect;
        private GameObject _ghost;
        private RectTransform _ghostRect;
        private Image _ghostImage;
        private Vector2 _homeLocal;
        private bool _active;

        private void Awake()
        {
            _slotRect = GetComponent<RectTransform>();
            var canvas = GetComponentInParent<Canvas>();
            if (canvas != null) _canvasRect = canvas.transform as RectTransform;
        }

        public void OnBeginDrag(PointerEventData eventData)
        {
            if (game == null || _canvasRect == null) return;
            if (!game.BeginItemDrag(item)) return;
            _active = true;
            Vector3 slotScreen = RectTransformUtility.WorldToScreenPoint(null, _slotRect.position);
            RectTransformUtility.ScreenPointToLocalPointInRectangle(
                _canvasRect, slotScreen, null, out _homeLocal);

            _ghost = new GameObject("ItemGhost", typeof(RectTransform), typeof(Image));
            _ghost.transform.SetParent(_canvasRect, false);
            _ghostRect = _ghost.GetComponent<RectTransform>();
            Vector2 ghostSize = new Vector2(52f, 52f);
            _ghostRect.sizeDelta = ghostSize;
            _ghostRect.pivot = new Vector2(0.5f, 0.5f);
            _ghostImage = _ghost.GetComponent<Image>();
            _ghostImage.sprite = game.ItemIcon(item);
            _ghostImage.raycastTarget = false;
            _ghostImage.color = new Color(1f, 1f, 1f, 0.92f);
            MoveGhost(eventData.position);
        }

        public void OnDrag(PointerEventData eventData)
        {
            if (!_active) return;
            MoveGhost(eventData.position);
            game.HoverItemTarget(item, eventData.position);
        }

        public void OnEndDrag(PointerEventData eventData)
        {
            if (!_active) return;
            _active = false;
            bool ok = game.DropItem(item, eventData.position);
            game.ClearItemHover();
            if (ok)
            {
                if (_ghost != null) Destroy(_ghost);
            }
            else
            {
                // 非法目标：图标回弹到道具槽后再消失。
                StartCoroutine(BounceBack());
            }
        }

        private void MoveGhost(Vector2 screenPos)
        {
            if (_ghostRect == null) return;
            Vector2 local;
            RectTransformUtility.ScreenPointToLocalPointInRectangle(
                _canvasRect, screenPos, null, out local);
            _ghostRect.anchoredPosition = local;
        }

        private System.Collections.IEnumerator BounceBack()
        {
            if (_ghostRect == null) yield break;
            Vector2 from = _ghostRect.anchoredPosition;
            float t = 0f;
            while (t < 0.16f)
            {
                t += Time.deltaTime;
                float k = Mathf.SmoothStep(0f, 1f, t / 0.16f);
                if (_ghostRect == null) yield break;
                _ghostRect.anchoredPosition = Vector2.Lerp(from, _homeLocal, k);
                if (_ghostImage != null)
                {
                    var c = _ghostImage.color;
                    c.a = 1f - 0.5f * k;
                    _ghostImage.color = c;
                }
                yield return null;
            }
            if (_ghost != null) Destroy(_ghost);
        }
    }
}

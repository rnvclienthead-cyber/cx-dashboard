import os
import logging
import requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

SALEBOT_API_TOKEN = os.getenv("SALEBOT_API_TOKEN", "")
SALEBOT_API_BASE  = "https://chatter.salebot.pro/api"


def is_enabled() -> bool:
    return bool(SALEBOT_API_TOKEN)


def send_message(client_id: str, message: str) -> bool:
    """Отправить сообщение подписчику Salebot по его client_id."""
    if not is_enabled():
        logger.debug("Salebot не настроен (нет SALEBOT_API_TOKEN)")
        return False
    try:
        url = f"{SALEBOT_API_BASE}/{SALEBOT_API_TOKEN}/message"
        resp = requests.post(url, json={"client_id": client_id, "message": message}, timeout=10)
        if resp.status_code == 200:
            logger.info("Salebot: уведомление отправлено client_id=%s", client_id)
            return True
        logger.warning("Salebot: HTTP %s — %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.error("Salebot send_message error: %s", e)
        return False


def notify_approved(client_id: str, req_id: int) -> bool:
    msg = (
        "✅ Ваша заявка одобрена!\n"
        "Мы готовим отправку. Как только посылка уйдёт, вы получите трек-номер."
    )
    return send_message(client_id, msg)


def notify_shipped(client_id: str, req_id: int, track_number: str,
                   delivery_method: str = "cdek", confirm_url: str = "") -> bool:
    carrier = "СДЭК" if delivery_method == "cdek" else "Яндекс Доставка"
    msg = (
        "📦 Ваша посылка отправлена!\n"
        f"Служба доставки: {carrier}\n"
        f"Трек-номер: {track_number}\n"
    )
    if confirm_url:
        msg += f"\nПосле получения, пожалуйста, подтвердите: {confirm_url}"
    return send_message(client_id, msg)


def notify_rejected(client_id: str, req_id: int, reason: str = "") -> bool:
    msg = "❌ К сожалению, мы не можем выполнить вашу отправку."
    if reason:
        msg += f"\nПричина: {reason}"
    msg += "\nЕсли есть вопросы — напишите нам."
    return send_message(client_id, msg)

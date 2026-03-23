import json
import os
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARIFFS_PATH = os.path.join(BASE_DIR, "data", "tarifs.json")
TARIFFS_ALL: List[Dict[str, Any]] = []
TARIFFS_ACTIVE: List[Dict[str, Any]] = []
TARIFFS_BY_ID: Dict[str, Dict[str, Any]] = {}


def load_tariffs() -> None:
    global TARIFFS_ALL, TARIFFS_ACTIVE, TARIFFS_BY_ID

    if not os.path.exists(TARIFFS_PATH):
        raise FileNotFoundError(f"Файл тарифов не найден: {TARIFFS_PATH}")

    with open(TARIFFS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    plans = data.get("plans") or []
    if not isinstance(plans, list):
        raise ValueError("tarifs.json должен содержать список plans")

    for plan in plans:
        if "active" not in plan:
            plan["active"] = True

    TARIFFS_ALL = plans
    TARIFFS_ACTIVE = [p for p in plans if p.get("active", True)]
    TARIFFS_ACTIVE.sort(
        key=lambda p: (p.get("sort", 9999), p.get("price_rub", 0))
    )
    TARIFFS_BY_ID = {p.get("id"): p for p in plans if p.get("id")}


def get_all_active() -> List[Dict[str, Any]]:
    return list(TARIFFS_ACTIVE)


def get_by_id(plan_id: str) -> Optional[Dict[str, Any]]:
    return TARIFFS_BY_ID.get(plan_id)


def is_trial_plan(plan: Optional[Dict[str, Any]]) -> bool:
    if not plan:
        return False
    return plan.get("id") == "trial" or plan.get("price_rub", 0) == 0


def get_minimal_by_price() -> Optional[Dict[str, Any]]:
    if not TARIFFS_ACTIVE:
        return None
    eligible = [p for p in TARIFFS_ACTIVE if not is_trial_plan(p)]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda p: (
            p.get("price_rub", 0),
            p.get("traffic_gb", 0),
            p.get("ip_limit", 0),
        ),
    )


def format_traffic(traffic_gb: Any) -> str:
    try:
        value = float(traffic_gb)
    except Exception:
        return str(traffic_gb)

    if value >= 1024 and value % 1024 == 0:
        return f"{int(value / 1024)} ТБ"
    if value.is_integer():
        return f"{int(value)} ГБ"
    return f"{value} ГБ"


def format_duration(days: int) -> str:
    return f"{days} дней"

def build_tariffs_text(plans: Optional[List[Dict[str, Any]]] = None) -> str:
    plans = plans if plans is not None else get_all_active()
    if not plans:
        return "🔒 <b>Тарифы VPN</b>\n\nТарифы временно недоступны."

    text = "🔒 <b>Тарифы VPN</b>\n\n"
    for idx, plan in enumerate(plans, 1):
        price = plan.get("price_rub", 0)
        duration = int(plan.get("duration_days", 30))
        if price == 0:
            price_line = f"Бесплатно на {duration} дня"
        elif duration == 10:
            price_line = f"{price} ₽/мес"
        else:
            price_line = f"{price} ₽ / {duration} дней"
        text += (
            f"{idx}. <b>{plan.get('name', plan.get('id'))}</b> - {price_line}\n"
            f"- до {plan.get('ip_limit', 0)} устройств\n"
            f"- до {format_traffic(plan.get('traffic_gb', 0))} трафика\n"
        )
        if plan.get("description"):
            text += f"- {plan.get('description')}\n"
        text += "\n"

    text += (
        "В будущем появится автоматическая оплата, продление и более функциональный бот!"
    )
    return text


def build_buy_text(plans: Optional[List[Dict[str, Any]]] = None) -> str:
    plans = plans if plans is not None else get_all_active()
    if not plans:
        return "💳 <b>Купить подписку VPN</b>\n\nТарифы временно недоступны."

    text = "💳 <b>Выберите тариф:</b>\n\n"
    for idx, plan in enumerate(plans, 1):
        price = plan.get("price_rub", 0)
        duration = int(plan.get("duration_days", 30))
        if price == 0:
            price_line = f"Бесплатно на {duration} дня"
        elif duration == 10:
            price_line = f"{price} ₽/мес"
        else:
            price_line = f"{price} ₽ / {duration} дней"
        text += (
            f"{idx}. <b>{plan.get('name', plan.get('id'))}</b>\n"
            f"   💰 {price_line}\n"
            f"   📱 до {plan.get('ip_limit', 0)} устройств\n"
            f"   📦 {format_traffic(plan.get('traffic_gb', 0))}\n"
            f"   ⏱ {format_duration(duration)}\n\n"
        )
    return text



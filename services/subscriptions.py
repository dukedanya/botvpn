import asyncio
import logging
import os
import re
import secrets
import string
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config import Config
from db import Database
from services.panel import PanelAPI
from tariffs import get_by_id, format_duration

logger = logging.getLogger(__name__)


async def create_subscription(
    user_id: int,
    plan: Dict[str, Any],
    db: Database,
    panel: PanelAPI,
    *,
    extra_days: int = 0,
    days_override: Optional[int] = None,
    plan_suffix: Optional[str] = None,
) -> Optional[str]:
    if not plan:
        return None

    pending_days = await db.get_bonus_days_pending(user_id)
    if days_override is None:
        days = int(plan.get("duration_days", 30)) + extra_days + pending_days
    else:
        days = int(days_override) + pending_days

    if days <= 0:
        days = 1

    base_email = f"user_{user_id}@{Config.PANEL_EMAIL_DOMAIN}"
    await panel.delete_client(base_email)

    client = await panel.create_client(
        email=base_email,
        limit_ip=int(plan.get("ip_limit", 0)),
        total_gb=int(plan.get("traffic_gb", 0)),
        days=days,
    )

    if not client:
        return None

    vpn_url = f"{Config.SUB_PANEL_BASE}{client.get('subId', 'user_' + str(user_id))}"
    plan_name = plan.get("name", plan.get("id", ""))
    if plan_suffix:
        plan_name = f"{plan_name}{plan_suffix}"

    await db.set_subscription(
        user_id=user_id,
        plan_text=plan_name,
        ip_limit=int(plan.get("ip_limit", 0)),
        traffic_gb=int(plan.get("traffic_gb", 0)),
        vpn_url=vpn_url,
    )

    if pending_days > 0:
        await db.clear_bonus_days_pending(user_id)

    return vpn_url

async def is_active_subscription(user_id: int, db: Database, panel: PanelAPI) -> bool:
    user = await db.get_user(user_id)
    if not user or not user.get("vpn_url"):
        return False

    base_email = f"user_{user_id}@{Config.PANEL_EMAIL_DOMAIN}"
    clients = await panel.find_clients_by_base_email(base_email)
    if not clients:
        await db.remove_subscription(user_id)
        return False

    expiry_times = [c.get("expiryTime", 0) for c in clients]
    max_expiry = max(expiry_times) if expiry_times else 0
    if max_expiry and max_expiry < int(time.time() * 1000):
        await db.remove_subscription(user_id)
        return False

    return True


# --- Реферальные бонусы ---

async def reward_referrer_days(referrer_id: int, bonus_days: int, db: Database, panel: PanelAPI) -> None:
    """Начисляет рефереру бонусные дни (тип 1)."""
    ref_user = await db.get_user(referrer_id)
    if not ref_user:
        return

    pending = await db.get_bonus_days_pending(referrer_id)
    total_bonus = bonus_days + pending

    base_email = f"user_{referrer_id}@{Config.PANEL_EMAIL_DOMAIN}"
    has_active = bool(ref_user.get("vpn_url"))

    if has_active:
        success = await panel.extend_client_expiry(base_email, total_bonus)
        if success:
            if pending > 0:
                await db.clear_bonus_days_pending(referrer_id)
            await notify_user(
                referrer_id,
                f"🎉 Вам начислено {total_bonus} дней по реферальной программе!",
            )
            return

        await db.add_bonus_days_pending(referrer_id, bonus_days)
        await notify_admins(
            f"⚠️ Не удалось продлить подписку реферера {referrer_id}. Бонус {bonus_days} дней сохранен в ожидании."
        )
        return

    min_plan = get_minimal_by_price()
    if not min_plan:
        await db.add_bonus_days_pending(referrer_id, bonus_days)
        await notify_admins(
            f"⚠️ Нет доступных тарифов для выдачи бонуса рефереру {referrer_id}. Бонус сохранен."
        )
        return

    vpn_url = await create_subscription(
        referrer_id,
        min_plan,
        days_override=total_bonus,
        plan_suffix=" (реферальный бонус)",
    )

    if vpn_url:
        await notify_user(
            referrer_id,
            f"🎉 Вам выдана бесплатная подписка на {total_bonus} дней по реферальной программе!\n\nURL:\n<code>{vpn_url}</code>",
        )
    else:
        await db.add_bonus_days_pending(referrer_id, bonus_days)
        await notify_admins(
            f"⚠️ Не удалось выдать бесплатную подписку рефереру {referrer_id}. Бонус сохранен."
        )

async def reward_referrer_percent(user_id: int, amount: float, db: Database) -> None:
    """Начисляет реферерам проценты от суммы платежа (тип 2)."""
    user = await db.get_user(user_id)
    if not user:
        return
    referrer_id = user.get("ref_by")
    if not referrer_id:
        return

    # Начисляем первому уровню (25%)
    level1_amount = amount * Config.REF_PERCENT_LEVEL1 / 100
    await db.add_balance(referrer_id, level1_amount)
    await notify_user(
        referrer_id,
        f"🎉 Вам начислено {level1_amount:.2f} ₽ на баланс за реферала!",
    )

    # Второй уровень
    referrer = await db.get_user(referrer_id)
    if referrer and referrer.get("ref_by"):
        level2_id = referrer["ref_by"]
        if level2_id != user_id:
            level2_amount = amount * Config.REF_PERCENT_LEVEL2 / 100
            await db.add_balance(level2_id, level2_amount)
            await notify_user(
                level2_id,
                f"🎉 Вам начислено {level2_amount:.2f} ₽ на баланс за реферала второго уровня!",
            )


# --- Фоновые задачи ---


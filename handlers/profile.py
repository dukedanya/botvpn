import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.enums import ParseMode

from config import Config
from db import Database
from tariffs import (
    get_all_active, get_by_id, is_trial_plan, format_traffic, format_duration,
)
from keyboards import (
    profile_keyboard, subscriptions_keyboard, back_keyboard, kb,
)
from utils.helpers import replace_message, notify_admins, notify_user, smart_answer, get_visible_plans, get_db, get_panel, get_itpay
from services.subscriptions import create_subscription, is_active_subscription
from services.panel import PanelAPI
from services.itpay import ItpayAPI

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "📦 Подписки")
async def subscriptions_menu(message: Message, db: Database):
    user_id = message.from_user.id
    await db.add_user(user_id)

    # Проверяем активную подписку
    active = await is_active_subscription(user_id, db=db, panel=panel)
    user_data = await db.get_user(user_id)

    # Если нет подписки и не использован пробный и не отказывался
    if not active and user_data.get("trial_used") == 0 and user_data.get("trial_declined") == 0:
        # Показываем предложение пробного периода
        trial_plan = get_by_id("trial")
        if trial_plan and trial_plan.get("active"):
            text = (
                "🎁 <b>Пробный период!</b>\n\n"
                "Новым пользователям доступен пробный тариф:\n"
                f"✅ <b>{trial_plan.get('name', 'Пробный')}</b>\n"
                f"📦 Трафик: {format_traffic(trial_plan.get('traffic_gb', 10))}\n"
                f"📱 Устройств: до {trial_plan.get('ip_limit', 1)}\n"
                f"⏱ Срок: {format_duration(trial_plan.get('duration_days', 3))}\n\n"
                "Хотите попробовать?"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Попробовать", callback_data="trial_accept")],
                    [InlineKeyboardButton(text="❌ Отказаться", callback_data="trial_decline")],
                ]
            )
            await replace_message(user_id, text, reply_markup=keyboard, delete_user_msg=message)
            return
        # Если пробного тарифа нет, показываем обычные тарифы
        else:
            await show_available_tariffs(user_id, active, user_msg=message)
    else:
        # Показываем обычное меню подписок (текущий тариф + список платных)
        await show_available_tariffs(user_id, active, user_msg=message)

@router.callback_query(F.data == "trial_accept")
async def trial_accept(callback: CallbackQuery, db: Database):
    user_id = callback.from_user.id
    panel = get_panel()

    user_data = await db.get_user(user_id)
    if user_data.get("trial_used"):
        await callback.answer("❌ Пробный период уже использован.", show_alert=True)
        return

    from tariffs import get_by_id
    trial_plan = get_by_id("trial")
    if not trial_plan or not trial_plan.get("active"):
        await callback.answer("❌ Пробный тариф недоступен.", show_alert=True)
        return

    try:
        await callback.message.delete()
    except:
        pass

    vpn_url = await create_subscription(
        user_id,
        trial_plan,
        db=db,
        panel=panel,
        plan_suffix=" (пробный)",
    )

    if vpn_url:
        await db.update_user(user_id, trial_used=1)
        await db.set_has_subscription(user_id)

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.answer(
            f"✅ <b>Пробный период активирован!</b>\n\n"
            f"🔗 Ваша VPN-ссылка:\n<code>{vpn_url}</code>\n\n"
            f"Приятного использования! 🎉",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    else:
        await callback.message.answer(
            "❌ Не удалось активировать пробный период. Попробуйте позже или обратитесь в поддержку."
        )


@router.callback_query(F.data == "trial_decline")
async def trial_decline(callback: CallbackQuery, db: Database):
    user_id = callback.from_user.id
    await db.update_user(user_id, trial_declined=1)
    try:
        await callback.message.delete()
    except:
        pass
    from services.subscriptions import is_active_subscription
    active = await is_active_subscription(user_id, db=db, panel=get_panel())
    await show_available_tariffs(user_id, active)
    await callback.answer()


async def show_available_tariffs(user_id: int, has_active_subscription: bool, user_msg: Message = None):
    db = get_db()
    panel = get_panel()
    """Показывает текущий тариф (если есть) и список платных тарифов."""
    user_data = await db.get_user(user_id)
    active = has_active_subscription

    text = ""
    if active:
        plan_text = user_data.get("plan_text", "Неизвестно")
        ip_limit = user_data.get("ip_limit", 0)
        traffic_gb = user_data.get("traffic_gb", 0)
        # Получаем срок из панели
        base_email = f"user_{user_id}@{Config.PANEL_EMAIL_DOMAIN}"
        clients = await panel.find_clients_by_base_email(base_email)
        expiry_str = "неизвестно"
        if clients:
            expiry_times = [c.get("expiryTime", 0) for c in clients]
            max_expiry = max(expiry_times) if expiry_times else 0
            if max_expiry > 0:
                expiry_date = datetime.fromtimestamp(max_expiry / 1000).strftime("%d.%m.%Y %H:%M")
                expiry_str = expiry_date
        text = (
            "📦 <b>Ваша подписка</b>\n\n"
            f"Тариф: <b>{plan_text}</b>\n"
            f"Устройств: до {ip_limit}\n"
            f"Трафик: {format_traffic(traffic_gb)}\n"
            f"Срок действия: до {expiry_str}\n\n"
            "⬇️ <b>Доступные тарифы:</b>\n"
        )
    else:
        text = "📦 <b>Доступные тарифы:</b>\n"

    # Список платных тарифов
    plans = await get_visible_plans(user_id, for_admin=False, db=get_db())
    if not plans:
        text += "Тарифы временно недоступны."
    else:
        for idx, plan in enumerate(plans, 1):
            price = plan.get("price_rub", 0)
            duration = int(plan.get("duration_days", 30))
            if duration == 10:
                price_line = f"{price} ₽/мес"
            else:
                price_line = f"{price} ₽ / {duration} дней"
            text += (
                f"{idx}. <b>{plan.get('name')}</b> - {price_line}\n"
                f"   ➤ {plan.get('ip_limit')} устройств, {format_traffic(plan.get('traffic_gb'))}\n"
            )
    # Отправляем сообщение, удаляя предыдущее сообщение бота и, если передан user_msg, сообщение пользователя
    await replace_message(user_id, text, reply_markup=subscriptions_keyboard(active), delete_user_msg=user_msg)


# Функция для отображения списка тарифов в инлайн-режиме (выбор тарифа)

@router.callback_query(F.data == "back_to_subscriptions")
async def back_to_subscriptions(callback: CallbackQuery):
    db = get_db()
    panel = get_panel()
    user_id = callback.from_user.id
    active = await is_active_subscription(user_id, db=db, panel=panel)
    await callback.message.delete()
    await show_available_tariffs(user_id, active)
    await callback.answer()


@router.message(F.text == "⏸ Заморозить подписку")
async def freeze_subscription(message: Message, db: Database):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    if user.get("frozen_until"):
        await message.answer("❄️ Подписка уже заморожена.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="7 дней", callback_data="freeze:7"),
         InlineKeyboardButton(text="14 дней", callback_data="freeze:14"),
         InlineKeyboardButton(text="30 дней", callback_data="freeze:30")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="freeze:cancel")],
    ])
    await message.answer(
        "⏸ <b>Заморозка подписки</b>\n\n"
        "На сколько дней заморозить?\n"
        "Подписка будет приостановлена, срок не будет тратиться.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("freeze:"))
async def freeze_callback(callback: CallbackQuery, db: Database):
    user_id = callback.from_user.id
    action = callback.data.split(":")[1]

    if action == "cancel":
        await callback.message.delete()
        return

    days = int(action)
    panel = get_panel()
    base_email = f"user_{user_id}@{Config.PANEL_EMAIL_DOMAIN}"

    # Продлеваем expiry на панели на N дней
    success = await panel.extend_client_expiry(base_email, days)
    if success:
        from datetime import datetime, timedelta
        frozen_until = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        await db.set_frozen(user_id, frozen_until)
        await callback.message.edit_text(
            f"❄️ Подписка заморожена на <b>{days} дней</b>.\n"
            f"Срок действия продлён до {frozen_until[:10]}.",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("❌ Не удалось заморозить подписку. Попробуйте позже.")

@router.message(F.text == "▶️ Разморозить подписку")
async def unfreeze_subscription(message: Message, db: Database):
    user_id = message.from_user.id
    await db.clear_frozen(user_id)
    await message.answer("✅ Подписка разморожена.")


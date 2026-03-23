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
    get_by_id, format_traffic, format_duration,
)
from keyboards import (
    admin_menu_keyboard, back_keyboard, kb,
)
from utils.helpers import replace_message, notify_admins, notify_user, smart_answer, get_visible_plans, get_db, get_panel, get_itpay, get_bot
from services.subscriptions import create_subscription, is_active_subscription
from services.panel import PanelAPI
from services.itpay import ItpayAPI

logger = logging.getLogger(__name__)
router = Router()

from services.subscriptions import reward_referrer_days, reward_referrer_percent


@router.message(F.text == "💰 Ожидающие платежи")
async def admin_pending_payments(message: Message, db: Database):
    bot = get_bot()
    db = get_db()
    user_id = message.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        return
    pending = await db.get_all_pending_payments()
    if not pending:
        await replace_message(user_id, "🕒 Нет ожидающих платежей.", reply_markup=admin_menu_keyboard(), delete_user_msg=message)
        return
    await replace_message(user_id, "🕒 Список ожидающих платежей:", reply_markup=admin_menu_keyboard(), delete_user_msg=message)
    for payment in pending:
        payment_id = payment.get("payment_id", "")
        p_user_id = payment.get("user_id", 0)
        plan_id = payment.get("plan_id", "")
        amount = payment.get("amount", 0)
        timestamp = payment.get("created_at", "")
        plan = get_by_id(plan_id)
        plan_name = plan.get("name", plan_id) if plan else plan_id
        try:
            dt = datetime.fromisoformat(timestamp)
            time_str = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            time_str = timestamp

        text = (
            f"📋 <b>Платеж ID:</b> <code>{payment_id}</code>\n"
            f"👤 <b>Пользователь:</b> <code>{p_user_id}</code>\n"
            f"📦 <b>Тариф:</b> {plan_name}\n"
            f"💰 <b>Сумма:</b> {amount} ₽\n"
            f"🕐 <b>Время:</b> {time_str}"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"pay_await_accept:{payment_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"pay_await_reject:{payment_id}"),
                ]
            ]
        )
        await bot.send_message(user_id, text, reply_markup=keyboard)

@router.callback_query(F.data.startswith("pay_await_accept:"))
async def pay_await_accept(callback: CallbackQuery, db: Database):
    bot = get_bot()
    db = get_db()
    payment_id = callback.data.split(":", 1)[1]
    payment = await db.get_pending_payment(payment_id)

    if not payment or payment.get("status") != "pending":
        await callback.answer("❌ Платеж не найден или уже обработан", show_alert=True)
        return

    user_id = payment.get("user_id")
    plan_id = payment.get("plan_id")
    plan = get_by_id(plan_id)
    amount = payment.get("amount", 0)

    if not plan:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    user_data = await db.get_user(user_id)
    ref_by = user_data.get("ref_by") if user_data else None
    ref_rewarded = user_data.get("ref_rewarded") if user_data else None

    bonus_days_for_user = 0
    if ref_by and not ref_rewarded:
        # Если реферер использует систему дней, начисляем бонусные дни (7)
        referrer = await db.get_user(ref_by)
        if referrer and referrer.get("ref_system_type") == 1:
            bonus_days_for_user = Config.REF_BONUS_DAYS

    # Создаём или продлеваем подписку
    vpn_url = await create_subscription(user_id, plan, extra_days=bonus_days_for_user, db=db, panel=panel)

    if vpn_url:
        await db.set_has_subscription(user_id)

        # Начисляем бонусы реферерам, если реферал первый раз оплачивает
        if ref_by and not ref_rewarded:
            referrer = await db.get_user(ref_by)
            if referrer:
                if referrer.get("ref_system_type") == 1:
                    # Тип 1: бонус днями
                    await reward_referrer_days(ref_by, Config.REF_BONUS_DAYS, db=db, panel=panel)
                else:
                    # Тип 2: проценты на баланс
                    await reward_referrer_percent(user_id, amount, db=db)

            await db.mark_ref_rewarded(user_id)

        await db.update_payment_status(payment_id, "accepted")

        # Редактируем сообщение пользователя, если оно ещё существует
        user_msg_id = payment.get("msg_id")
        if user_msg_id:
            try:
                await bot.edit_message_text(
                    "✅ <b>Ваш платеж подтвержден!</b>\n\n"
                    "Спасибо за покупку! 🎉\n"
                    "Подписка активирована.",
                    chat_id=user_id,
                    message_id=user_msg_id,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="Главное меню", callback_data="main_menu")]]
                    ),
                )
            except Exception:
                pass

        # Отправляем новое сообщение с деталями подписки
        await notify_user(user_id,
            "✅ <b>Ваш платеж подтвержден!</b>\n\n"
            f"Тариф: <b>{plan.get('name', plan_id)}</b>\n"
            f"IP-адреса: <b>до {plan.get('ip_limit', 0)}</b>\n"
            f"Трафик: <b>{format_traffic(plan.get('traffic_gb', 0))}</b>\n"
            f"Срок: <b>{format_duration(int(plan.get('duration_days', 30)) + bonus_days_for_user)}</b>\n\n"
            f"URL для подключения:\n<code>{vpn_url}</code>\n\n"
            "Спасибо за покупку! 🎉",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Главное меню", callback_data="main_menu")]]
            ),
        )

        # Редактируем сообщение администратора
        await callback.message.edit_text(callback.message.text + "\n\n✅ <b>ПОДТВЕРЖДЕНО</b>", parse_mode="HTML")
        await callback.answer(f"✅ Платеж {payment_id} подтвержден!")
    else:
        await callback.answer(f"❌ Ошибка создания VPN для платежа {payment_id}", show_alert=True)

@router.callback_query(F.data.startswith("pay_await_reject:"))
async def pay_await_reject(callback: CallbackQuery, db: Database):
    bot = get_bot()
    db = get_db()
    payment_id = callback.data.split(":", 1)[1]
    payment = await db.get_pending_payment(payment_id)

    if not payment or payment.get("status") != "pending":
        await callback.answer("❌ Платеж не найден или уже обработан", show_alert=True)
        return

    user_id = payment.get("user_id")
    await db.update_payment_status(payment_id, "rejected")

    # Редактируем сообщение пользователя
    user_msg_id = payment.get("msg_id")
    if user_msg_id:
        try:
            await bot.edit_message_text(
                "❌ <b>Ваш платеж отклонен!</b>\n\n"
                "Пожалуйста, проверьте:\n"
                "1. Правильность суммы платежа\n"
                "2. Наличие комментария к платежу\n"
                "3. Актуальность данных карты\n\n"
                "Если вы уверены, что все сделали правильно, свяжитесь с поддержкой.",
                chat_id=user_id,
                message_id=user_msg_id,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Поддержка", url=Config.SUPPORT_URL)],
                        [InlineKeyboardButton(text="Главное меню", callback_data="main_menu")]
                    ]
                ),
            )
        except Exception:
            pass

    # Редактируем сообщение администратора
    await callback.message.edit_text(callback.message.text + "\n\n❌ <b>ОТКЛОНЕНО</b>", parse_mode="HTML")
    await callback.answer(f"❌ Платеж {payment_id} отклонен!")


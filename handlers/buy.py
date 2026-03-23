import logging
import time
from typing import Optional

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.enums import ParseMode

from config import Config
from tariffs import (
    get_by_id, is_trial_plan, format_traffic, format_duration,
    build_buy_text,
)
from keyboards import back_keyboard, subscriptions_keyboard
from utils.helpers import (
    replace_message, get_visible_plans, get_db, get_panel, get_itpay, get_bot,
)
from services.subscriptions import create_subscription, is_active_subscription

logger = logging.getLogger(__name__)
router = Router()


async def show_plans_list(
    user_id: int,
    message_id: Optional[int] = None,
    user_msg: Optional[Message] = None,
):
    db = get_db()
    bot = get_bot()
    plans = await get_visible_plans(user_id, for_admin=False, db=db)
    if not plans:
        text = "❌ Нет доступных тарифов."
        if message_id:
            await bot.edit_message_text(text, chat_id=user_id, message_id=message_id)
        else:
            await replace_message(user_id, text, reply_markup=back_keyboard(), delete_user_msg=user_msg)
        return

    text = build_buy_text(plans)
    keyboard = []
    for plan in plans:
        name = plan.get("name", plan.get("id"))
        keyboard.append([InlineKeyboardButton(text=name, callback_data=f"buy:{plan.get('id')}")])
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_subscriptions")])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    if message_id:
        await bot.edit_message_text(text, chat_id=user_id, message_id=message_id, reply_markup=markup)
    else:
        await replace_message(user_id, text, reply_markup=markup, delete_user_msg=user_msg)


@router.message(F.text.in_(["💰 Оформить подписку", "💰 Продлить подписку"]))
async def buy_subscription_menu(message: Message):
    await show_plans_list(message.from_user.id, user_msg=message)


@router.callback_query(F.data.startswith("buy:"))
async def buy_plan(callback: CallbackQuery):
    db = get_db()
    itpay = get_itpay()
    user_id = callback.from_user.id
    plan_id = callback.data.split(":", 1)[1]
    plan = get_by_id(plan_id)

    if not plan or not plan.get("active", True):
        await callback.answer("❌ Тариф не найден или недоступен", show_alert=True)
        return
    if is_trial_plan(plan):
        await callback.answer("⚠️ Пробный тариф оформляется отдельно.", show_alert=True)
        return

    amount = plan.get("price_rub", 0)
    payment_id = f"pay_{user_id}_{int(time.time())}"
    plan_name = plan.get("name", plan_id)

    # --- ИСПРАВЛЕНО: передаём user_id и plan_id ---
    itpay_payment = await itpay.create_payment(
        amount=amount,
        client_payment_id=payment_id,
        user_id=user_id,
        plan_id=plan_id,
        description=f"Подписка: {plan_name}",
        success_url=Config.TG_CHANNEL or None,
    )
    if not itpay_payment:
        await callback.answer("❌ Ошибка создания платежа, попробуйте позже", show_alert=True)
        return

    itpay_id = itpay_payment.get("id", "")
    qr_urls = itpay_payment.get("payment_qr_urls") or {}
    pay_url = (
        qr_urls.get("desktop")
        or qr_urls.get("android")
        or qr_urls.get("ios")
        or ""
    )

    await db.add_pending_payment(
        payment_id=payment_id,
        user_id=user_id,
        plan_id=plan_id,
        amount=amount,
        msg_id=callback.message.message_id,
    )
    await db.set_pending_payment_itpay_id(payment_id, itpay_id)

    duration = int(plan.get("duration_days", 30))
    price_line = f"{amount} руб/мес" if duration == 30 else f"{amount} руб/{duration} дней"

    text = (
        "💳 <b>Оплата подписки</b>\n\n"
        f"📦 Тариф: <b>{plan_name}</b>\n"
        f"💰 Сумма: <b>{price_line}</b>\n\n"
        "Нажмите кнопку ниже для перехода к оплате через СБП.\n"
        "После оплаты подписка активируется <b>автоматически</b>."
    )

    inline = []
    if pay_url:
        inline.append([InlineKeyboardButton(text="💳 Оплатить через СБП", url=pay_url)])
    inline.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_payment")])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=inline),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_payment")
async def cancel_payment(callback: CallbackQuery):
    await show_plans_list(callback.from_user.id, message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith("test:"))
async def test_plan(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        await callback.answer("⛔ Только для администраторов!", show_alert=True)
        return

    plan_id = callback.data.split(":", 1)[1]
    plan = get_by_id(plan_id)
    if not plan:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    db = get_db()
    panel = get_panel()
    vpn_url = await create_subscription(user_id, plan, db=db, panel=panel)
    if vpn_url:
        text = (
            "✅ <b>Тестовая подписка создана!</b>\n\n"
            f"Тариф: <b>{plan.get('name', plan_id)} (тест)</b>\n"
            f"IP-адреса: <b>до {plan.get('ip_limit', 0)}</b>\n"
            f"Трафик: <b>{format_traffic(plan.get('traffic_gb', 0))}</b>\n"
            f"Срок: <b>{format_duration(int(plan.get('duration_days', 30)))}</b>\n\n"
            f"URL:\n<code>{vpn_url}</code>\n\n"
            "Клиент: <b>Happ</b>\n"
            'iOS/macOS — <a href="https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973">App Store</a>\n'
            'Android — <a href="https://play.google.com/store/apps/details?id=com.happproxy">Google Play</a>\n'
            'Windows — <a href="https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe">Скачать</a>'
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]]
        )
        await callback.message.edit_text(text, reply_markup=keyboard)
    else:
        await callback.answer("❌ Ошибка создания тестовой подписки", show_alert=True)
    await callback.answer()

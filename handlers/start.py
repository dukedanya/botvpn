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
    get_all_active, get_by_id, is_trial_plan, get_minimal_by_price, format_traffic, format_duration,
)
from keyboards import (
    main_menu_keyboard, profile_keyboard, subscriptions_keyboard, back_keyboard, support_keyboard_reply, instruction_keyboard, kb,
)
from utils.helpers import replace_message, notify_admins, notify_user, smart_answer, get_visible_plans, get_db, get_panel
from services.subscriptions import create_subscription, is_active_subscription
from services.panel import PanelAPI
from services.itpay import ItpayAPI
from handlers.profile import show_available_tariffs

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, db: Database):
    await state.clear()
    user_id = message.from_user.id
    await db.add_user(user_id)
    await db.ensure_ref_code(user_id)

    # Обработка реферального кода (с поддержкой префиксов)
    parts = message.text.strip().split(maxsplit=1) if message.text else []
    ref_param = parts[1] if len(parts) > 1 else ""
    if ref_param:
        # Ожидаем формат: start=ref1_CODE или start=ref2_CODE
        if ref_param.startswith("ref1_") or ref_param.startswith("ref2_"):
            prefix, code = ref_param.split("_", 1)
            ref_user = await db.get_user_by_ref_code(code)
            if ref_user and ref_user.get("user_id") != user_id:
                await db.set_ref_by(user_id, int(ref_user.get("user_id")))
        else:
            # Старый формат без префикса (для совместимости)
            ref_user = await db.get_user_by_ref_code(ref_param)
            if ref_user and ref_user.get("user_id") != user_id:
                await db.set_ref_by(user_id, int(ref_user.get("user_id")))

    total_users = await db.get_total_users()
    banned_users = await db.get_banned_users_count()
    subs_ids = await db.get_subscribed_user_ids()
    active_vpns = len(subs_ids)

    text = (
        "👋 <b>Добро пожаловать в Какой-то VPN!</b>\n\n"
        f"Всего пользователей: <b>{total_users}</b>\n"
        f"Активных пользователей: <b>{active_vpns}</b>"
    )
    is_admin = user_id in Config.ADMIN_USER_IDS
    await replace_message(user_id, text, reply_markup=main_menu_keyboard(is_admin), delete_user_msg=message)

@router.message(F.text == "👤 Личный кабинет")
async def profile_menu(message: Message, db: Database, panel: PanelAPI):
    user_id = message.from_user.id
    await db.add_user(user_id)

    active_sub = await is_active_subscription(user_id, db=db, panel=panel)

    if user_id in Config.ADMIN_USER_IDS:
        text = (
            "👤 <b>Ваша подписка VPN</b>\n\n"
            "Тариф: <b>Admin</b>\n"
            "Остаток трафика: <b>Безлимит</b>\n"
            "IP-адреса: <b>Безлимит</b>\n"
            "Срок действия: <b>Безлимит</b>\n\n"
            "URL для подключения:\n"
            f"{Config.SUB_PANEL_BASE}Admin"
        )
        await replace_message(user_id, text, reply_markup=profile_keyboard(active_sub, is_frozen=bool((await db.get_user(user_id)).get('frozen_until'))), delete_user_msg=message)
        return

    user_data = await db.get_user(user_id)

    if not user_data or not active_sub:
        text = "👤 <b>Ваша подписка VPN</b>\n\nУ вас нет активной подписки."
        await replace_message(user_id, text, reply_markup=profile_keyboard(active_sub, is_frozen=bool((await db.get_user(user_id)).get('frozen_until'))), delete_user_msg=message)
        return

    base_email = f"user_{user_id}@{Config.PANEL_EMAIL_DOMAIN}"
    client_stats = await panel.get_client_stats(base_email)
    plan_text = user_data.get("plan_text", "Неизвестно")
    ip_limit = user_data.get("ip_limit", 0)
    vpn_url = user_data.get("vpn_url", "")
    traffic_gb = user_data.get("traffic_gb", 0)

    if client_stats:
        used_bytes = 0
        expiry_time = 0

        for client in client_stats:
            used_bytes += client.get("up", 0) + client.get("down", 0)
            client_expiry = client.get("expiryTime", 0)
            if client_expiry > expiry_time:
                expiry_time = client_expiry

        used_gb = used_bytes / 1073741824
        remaining_gb = max(0, traffic_gb - used_gb)

        if expiry_time > 0:
            expiry_date = datetime.fromtimestamp(expiry_time / 1000).strftime(
                "%d.%m.%Y %H:%M"
            )
        else:
            expiry_date = "не указана"

        text = (
            "👤 <b>Ваша подписка VPN</b>\n\n"
            f"Тариф: <b>{plan_text}</b>\n"
            f"Остаток трафика: <b>{remaining_gb:.1f} ГБ из {traffic_gb:.0f} ГБ</b>\n"
            f"IP-адреса: <b>до {ip_limit}</b>\n"
            f"Срок действия: <b>до {expiry_date}</b>\n\n"
            f"URL для подключения:\n"
            f"<code>{vpn_url}</code>"
        )
    else:
        text = (
            "👤 <b>Ваша подписка VPN</b>\n\n"
            f"Тариф: <b>{plan_text}</b>\n"
            f"IP-адреса: <b>до {ip_limit}</b>\n"
            f"Трафик: <b>{format_traffic(traffic_gb)}</b>\n"
            f"URL для подключения:\n"
            f"<code>{vpn_url}</code>\n\n"
            "<i>Статистика трафика временно недоступна</i>"
        )

    await replace_message(user_id, text, reply_markup=profile_keyboard(active_sub, is_frozen=bool((await db.get_user(user_id)).get('frozen_until'))), delete_user_msg=message)

@router.message(F.text == "📦 Подписки")
async def subscriptions_menu(message: Message, db: Database):
    db = get_db()
    panel = get_panel()
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

@router.message(F.text == "🆘 Поддержка")
async def support_menu(message: Message):
    user_id = message.from_user.id
    text = f"🆘 <b>Служба поддержки</b>\n\nЕсли у вас возникли вопросы, напишите нам:\n{Config.SUPPORT_URL}"
    await replace_message(user_id, text, reply_markup=support_keyboard_reply(), delete_user_msg=message)

@router.message(F.text == "Инструкция")
async def instruction_menu(message: Message):
    user_id = message.from_user.id
    text = (
        "📖 <b>Инструкция по подключению VPN</b>\n\n"
        "1. Скачайте приложение <b>Happ</b>:\n"
        "   • iOS/MacOS — <a href='https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973'>App Store</a>\n"
        "   • Android — <a href='https://play.google.com/store/apps/details?id=com.happproxy'>Google Play</a>\n"
        "   • Windows — <a href='https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe'>Официальный сайт</a>\n"
        "2. Скопируйте URL для подключения из личного кабинета.\n"
        "3. В приложении нажмите «Импорт» и вставьте ссылку.\n"
        "4. Включите VPN и пользуйтесь.\n\n"
        "Если у вас возникли трудности, обратитесь в поддержку."
    )
    await replace_message(user_id, text, reply_markup=instruction_keyboard(), delete_user_msg=message)

@router.message(F.text == "📢 Наш канал")
async def channel_link(message: Message):
    user_id = message.from_user.id
    text = "📢 <b>Наш канал</b>\n\nПодписывайтесь, чтобы быть в курсе новостей и акций!"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти в канал", url=Config.TG_CHANNEL)],
            [InlineKeyboardButton(text="Главное меню", callback_data="main_menu")]
        ]
    )
    await replace_message(user_id, text, reply_markup=keyboard, delete_user_msg=message)

@router.message(F.text == "💬 Отзывы")
async def reviews_link(message: Message):
    user_id = message.from_user.id
    text = "💬 <b>Отзывы о нашем сервисе</b>\n\nЧитайте отзывы и оставляйте свои впечатления!"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к отзывам", url=Config.TG_CHANNEL)],
            [InlineKeyboardButton(text="Главное меню", callback_data="main_menu")]
        ]
    )
    await replace_message(user_id, text, reply_markup=keyboard, delete_user_msg=message)

async def show_main_menu(user_id: int, delete_user_msg: Optional[Message] = None):
    db = get_db()
    """Показывает главное меню с актуальной статистикой."""
    total_users = await db.get_total_users()
    active_vpns = len(await db.get_subscribed_user_ids())
    text = (
        "👋 <b>Добро пожаловать в Какой-то VPN!</b>\n\n"
        f"Всего пользователей: <b>{total_users}</b>\n"
        f"Активных пользователей: <b>{active_vpns}</b>"
    )
    is_admin = user_id in Config.ADMIN_USER_IDS
    await replace_message(user_id, text, reply_markup=main_menu_keyboard(is_admin), delete_user_msg=delete_user_msg)

@router.message(F.text == "⬅️ Назад")
async def back_to_main(message: Message):
    await show_main_menu(message.from_user.id, delete_user_msg=message)

@router.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery):
    await callback.message.delete()
    await show_main_menu(callback.from_user.id)
    await callback.answer()


# --- Запуск ---


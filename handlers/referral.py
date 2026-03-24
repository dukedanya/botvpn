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
    get_all_active, is_trial_plan,
)
from keyboards import (
    back_keyboard, profile_keyboard, kb,
)
from utils.helpers import replace_message, notify_admins, notify_user, smart_answer, get_visible_plans, get_db, get_panel, get_itpay, get_ref_link, generate_ref_code
from services.subscriptions import create_subscription, is_active_subscription
from services.panel import PanelAPI
from services.itpay import ItpayAPI

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "🤝 Реферальная система")
async def referral_menu(message: Message, db: Database):
    user_id = message.from_user.id
    await db.add_user(user_id)
    await show_referral_menu(user_id, message)

@router.message(F.text == "🔄 Изменить тип реферальной системы")
async def change_ref_system(message: Message):
    user_id = message.from_user.id
    await show_ref_system_choice(user_id, message)

@router.message(F.text == "💸 Вывести средства")
async def withdraw_money(message: Message, db: Database):
    user_id = message.from_user.id
    balance = await db.get_balance(user_id)
    if balance < Config.MIN_WITHDRAW:
        await replace_message(user_id, f"❌ Минимальная сумма вывода: {Config.MIN_WITHDRAW} ₽. Ваш баланс: {balance:.2f} ₽.", reply_markup=back_keyboard(), delete_user_msg=message)
        return

    # Создаём запрос на вывод
    request_id = await db.create_withdraw_request(user_id, balance)
    if request_id:
        await notify_admins(f"💸 <b>Новый запрос на вывод средств!</b>\n\n"
            f"👤 Пользователь: <code>{user_id}</code>\n"
            f"💰 Сумма: {balance:.2f} ₽\n"
            f"🆔 ID запроса: {request_id}"
        )
        await replace_message(user_id, f"✅ Запрос на вывод {balance:.2f} ₽ отправлен администратору. Ожидайте подтверждения.", reply_markup=back_keyboard(), delete_user_msg=message)
    else:
        await replace_message(user_id, "❌ Ошибка при создании запроса. Попробуйте позже.", reply_markup=back_keyboard(), delete_user_msg=message)


# --- Вспомогательные функции для реферальной системы ---

async def show_ref_system_choice(user_id: int, user_msg: Optional[Message] = None):
    text = "🤝 <b>Выберите тип реферальной системы:</b>\n\n"
    text += f"1️⃣ <b>Бонус днями:</b> Вы будете получать {Config.REF_BONUS_DAYS} дней подписки за каждого приглашённого, который оплатит подписку.\n"
    text += f"2️⃣ <b>Проценты на баланс:</b> {Config.REF_PERCENT_LEVEL1}% от суммы оплаты реферала на баланс, и {Config.REF_PERCENT_LEVEL2}% от оплаты реферала вашего реферала.\n\n"
    text += "Выберите вариант:"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🎁 Бонус днями ({Config.REF_BONUS_DAYS} дней)", callback_data="set_ref_system:1")],
            [InlineKeyboardButton(text=f"💰 Проценты на баланс ({Config.REF_PERCENT_LEVEL1}%+{Config.REF_PERCENT_LEVEL2}%)", callback_data="set_ref_system:2")],
        ]
    )
    await replace_message(user_id, text, reply_markup=keyboard, delete_user_msg=user_msg)

async def show_referral_menu(user_id: int, user_msg: Optional[Message] = None):
    db = get_db()
    """Показывает реферальное меню для пользователя."""
    user = await db.get_user(user_id)
    system_type = user.get("ref_system_type", 1)
    balance = user.get("balance", 0.0)

    if user.get("ref_system_type") is None:
        await show_ref_system_choice(user_id, user_msg)
        return

    ref_code = await db.ensure_ref_code(user_id)
    if not ref_code:
        text = "❌ Не удалось сгенерировать реферальный код."
        await replace_message(user_id, text, reply_markup=profile_keyboard(await is_active_subscription(user_id, db=db, panel=panel), is_frozen=bool((await db.get_user(user_id)).get('frozen_until'))), delete_user_msg=user_msg)
        return

    link = get_ref_link(ref_code, system_type)
    total_refs = await db.count_referrals(user_id)
    paid_refs = await db.count_referrals_paid(user_id)

    if system_type == 1:
        system_desc = f"Бонус днями ({Config.REF_BONUS_DAYS} дней)"
        bonus_info = f"За каждого оплатившего реферала вы получаете +{Config.REF_BONUS_DAYS} дней подписки."
    else:
        system_desc = f"Проценты на баланс ({Config.REF_PERCENT_LEVEL1}% + {Config.REF_PERCENT_LEVEL2}%)"
        bonus_info = f"{Config.REF_PERCENT_LEVEL1}% от суммы оплаты реферала на баланс, и {Config.REF_PERCENT_LEVEL2}% от оплаты реферала вашего реферала."

    text = (
        "🤝 <b>Реферальная система VPN</b>\n\n"
        f"Ваша система: <b>{system_desc}</b>\n\n"
        f"{bonus_info}\n\n"
        f"Всего приглашено: <b>{total_refs}</b>\n"
        f"Оплатили подписку: <b>{paid_refs}</b>\n"
        f"Ваш баланс: <b>{balance:.2f} ₽</b>\n"
    )

    keyboard = []
    keyboard.append([KeyboardButton(text="🔗 Получить ссылку")])
    keyboard.append([KeyboardButton(text="🏆 Топ рефереров")])
    keyboard.append([KeyboardButton(text="👥 Мои рефералы")])
    keyboard.append([KeyboardButton(text="📊 История начислений")])
    if balance >= Config.MIN_WITHDRAW:
        keyboard.append([KeyboardButton(text="💸 Вывести средства")])
    keyboard.append([KeyboardButton(text="🔄 Изменить тип реферальной системы")])
    keyboard.append([KeyboardButton(text="⬅️ Назад")])

    await replace_message(user_id, text, reply_markup=ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True), delete_user_msg=user_msg)



@router.message(F.text == "🔗 Получить ссылку")
async def get_ref_link_handler(message: Message, db: Database):
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    system_type = user.get("ref_system_type", 1)

    ref_code = await db.ensure_ref_code(user_id)
    if not ref_code:
        await message.answer("❌ Не удалось сгенерировать реферальный код.")
        return

    link = get_ref_link(ref_code, system_type)

    if system_type == 1:
        bonus_text = f"Когда Ваш друг купит подписку в нашем сервисе,\nВы и Ваш друг получите <b>+{Config.REF_BONUS_DAYS} дней</b> к подписке бесплатно! 🎁"
    else:
        bonus_text = (
            f"Когда Ваш друг купит подписку в нашем сервисе,\n"
            f"Вы получите <b>{Config.REF_PERCENT_LEVEL1}%</b> от суммы оплаты на баланс,\n"
            f"и <b>{Config.REF_PERCENT_LEVEL2}%</b> от оплаты реферала вашего реферала."
        )

    text = (
        f"🕊️ Отправь своему другу ссылку:\n\n"
        f"<blockquote>{link}</blockquote>\n\n"
        f"{bonus_text}"
    )

    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(F.text == "🏆 Топ рефереров")
async def top_referrers_handler(message: Message, db: Database):
    top = await db.get_top_referrers(limit=10)
    if not top:
        await message.answer("😔 Пока никто не пригласил друзей.")
        return

    text = "🏆 <b>Топ рефереров</b>\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(top):
        medal = medals[i] if i < 3 else f"{i+1}."
        uid = row["ref_by"]
        count = row["paid_count"]
        text += f"{medal} <code>{uid}</code> — <b>{count}</b> оплативших рефералов\n"

    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(F.text == "👥 Мои рефералы")
async def my_referrals_handler(message: Message, db: Database):
    user_id = message.from_user.id
    refs = await db.get_referrals_list(user_id)
    if not refs:
        await message.answer("😔 Вы ещё никого не пригласили.")
        return

    text = "👥 <b>Ваши рефералы</b>\n\n"
    for r in refs:
        status = "✅ оплатил" if r.get("ref_rewarded") else "⏳ не оплатил"
        uid = r["user_id"]
        joined = str(r.get("join_date", ""))[:10]
        text += f"• <code>{uid}</code> — {status} (вступил {joined})\n"

    await message.answer(text, parse_mode=ParseMode.HTML)


@router.message(F.text == "📊 История начислений")
async def ref_history_handler(message: Message, db: Database):
    user_id = message.from_user.id
    history = await db.get_ref_history(user_id, limit=10)
    if not history:
        await message.answer("😔 История начислений пуста.")
        return

    text = "📊 <b>История начислений</b>\n\n"
    for row in history:
        date = str(row.get("created_at", ""))[:10]
        if row.get("bonus_days"):
            text += f"• {date} — <b>+{row['bonus_days']} дней</b> подписки\n"
        elif row.get("amount"):
            text += f"• {date} — <b>+{row['amount']:.2f} ₽</b> на баланс\n"

    await message.answer(text, parse_mode=ParseMode.HTML)

# --- Обработчики callback-запросов ---

@router.callback_query(F.data.startswith("set_ref_system:"))
async def set_ref_system(callback: CallbackQuery, db: Database):
    user_id = callback.from_user.id
    system_type = int(callback.data.split(":")[1])
    await db.update_user(user_id, ref_system_type=system_type)
    await callback.answer(f"✅ Выбран тип реферальной системы: {'Бонус днями' if system_type == 1 else 'Проценты на баланс'}", show_alert=True)
    # Удаляем сообщение с выбором типа
    try:
        await callback.message.delete()
    except:
        pass
    # Показываем обновлённое реферальное меню
    await show_referral_menu(user_id)


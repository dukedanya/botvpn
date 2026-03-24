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
    build_buy_text,
)
from keyboards import (
    admin_menu_keyboard, main_menu_keyboard, back_keyboard, kb,
)
from utils.helpers import replace_message, notify_admins, notify_user, smart_answer, get_visible_plans, get_db, get_panel, get_itpay, get_bot
from services.subscriptions import create_subscription, is_active_subscription
from services.panel import PanelAPI
from services.itpay import ItpayAPI

logger = logging.getLogger(__name__)
router = Router()

from services.subscriptions import create_subscription, reward_referrer_days, reward_referrer_percent
from handlers.profile import show_available_tariffs


@router.message(F.text == "🛠️ Админ меню")
async def admin_menu(message: Message):
    user_id = message.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        await replace_message(user_id, "⛔ У вас нет прав администратора.", reply_markup=main_menu_keyboard(False), delete_user_msg=message)
        return
    await replace_message(user_id, "🛠️ <b>Админ панель</b>\n\nВыберите действие:", reply_markup=admin_menu_keyboard(), delete_user_msg=message)

@router.message(F.text == "📊 Статистика")
async def admin_stats(message: Message):
    db = get_db()
    user_id = message.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        return
    total_users = await db.get_total_users()
    subscribed = len(await db.get_subscribed_user_ids())
    banned = await db.get_banned_users_count()
    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Активных VPN: {subscribed}\n"
        f"⛔ Заблокировано: {banned}"
    )
    await replace_message(user_id, text, reply_markup=admin_menu_keyboard(), delete_user_msg=message)

@router.message(F.text == "💸 Запросы на вывод")
async def admin_withdraw_requests(message: Message, db: Database):
    bot = get_bot()
    db = get_db()
    user_id = message.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        return
    requests = await db.get_pending_withdraw_requests()
    if not requests:
        await replace_message(user_id, "💸 Нет активных запросов на вывод.", reply_markup=admin_menu_keyboard(), delete_user_msg=message)
        return
    await replace_message(user_id, "💸 Активные запросы на вывод:", reply_markup=admin_menu_keyboard(), delete_user_msg=message)
    for req in requests:
        req_id = req["id"]
        req_user_id = req["user_id"]
        amount = req["amount"]
        created_at = req["created_at"]
        text = (
            f"📋 <b>Запрос #{req_id}</b>\n"
            f"👤 Пользователь: <code>{req_user_id}</code>\n"
            f"💰 Сумма: {amount} ₽\n"
            f"🕐 Создан: {created_at}"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"withdraw_accept:{req_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"withdraw_reject:{req_id}"),
                ]
            ]
        )
        await bot.send_message(user_id, text, reply_markup=keyboard)

@router.message(F.text == "📦 Создать тестовую подписку")
async def admin_test_subscription(message: Message):
    user_id = message.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        return
    plans = get_all_active()
    text = build_buy_text(plans)
    keyboard = []
    for plan in plans:
        name = plan.get("name", plan.get("id"))
        keyboard.append([InlineKeyboardButton(text=name, callback_data=f"test:{plan.get('id')}")])
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_admin")])
    await replace_message(user_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), delete_user_msg=message)

@router.callback_query(F.data == "back_to_admin")
async def back_to_admin(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in Config.ADMIN_USER_IDS:
        await callback.message.delete()
        await replace_message(user_id, "🛠️ <b>Админ панель</b>\n\nВыберите действие:", reply_markup=admin_menu_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("withdraw_accept:"))
async def withdraw_accept(callback: CallbackQuery):
    db = get_db()
    request_id = int(callback.data.split(":")[1])
    success = await db.process_withdraw_request(request_id, accept=True)
    if success:
        await callback.message.edit_text(callback.message.text + "\n\n✅ <b>ВЫВОД ПОДТВЕРЖДЁН</b>", parse_mode="HTML")
        await callback.answer("Вывод подтверждён")
    else:
        await callback.answer("Ошибка обработки запроса", show_alert=True)

@router.callback_query(F.data.startswith("withdraw_reject:"))
async def withdraw_reject(callback: CallbackQuery):
    db = get_db()
    request_id = int(callback.data.split(":")[1])
    success = await db.process_withdraw_request(request_id, accept=False)
    if success:
        await callback.message.edit_text(callback.message.text + "\n\n❌ <b>ВЫВОД ОТКЛОНЁН</b>", parse_mode="HTML")
        await callback.answer("Вывод отклонён")
    else:
        await callback.answer("Ошибка обработки запроса", show_alert=True)


@router.callback_query(F.data == "trial_decline")
async def trial_decline(callback: CallbackQuery):
    db = get_db()
    user_id = callback.from_user.id
    await db.add_user(user_id)
    await db.mark_trial_declined(user_id)
    await callback.message.edit_text("❌ Вы отказались от пробного периода. Вы можете приобрести платную подписку.", reply_markup=None)
    # Показываем доступные платные тарифы
    await show_available_tariffs(user_id, False)
    await callback.answer()

# ─── Редактор тарифов ──────────────────────────────────────────────

class TariffEditFSM(StatesGroup):
    choosing = State()
    field = State()
    value = State()

TARIFF_FIELDS = {
    "name":         ("Название", str),
    "price_rub":    ("Цена (руб)", int),
    "duration_days":("Дней", int),
    "ip_limit":     ("Устройств", int),
    "traffic_gb":   ("Трафик ГБ", float),
    "sort":         ("Порядок", int),
    "description":  ("Описание", str),
}


def tariffs_list_keyboard(plans):
    rows = []
    for p in plans:
        status = "✅" if p.get("active", True) else "❌"
        rows.append([
            InlineKeyboardButton(text=status + " " + p.get("name", p["id"]), callback_data="tedit:" + p["id"]),
            InlineKeyboardButton(text="🔀", callback_data="ttoggle:" + p["id"]),
        ])
    rows.append([InlineKeyboardButton(text="➕ Добавить тариф", callback_data="tadd")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tariff_fields_keyboard(plan_id):
    rows = []
    for key, (label, _) in TARIFF_FIELDS.items():
        rows.append([InlineKeyboardButton(text="✏️ " + label, callback_data="tfield:" + plan_id + ":" + key)])
    rows.append([InlineKeyboardButton(text="🗑 Удалить тариф", callback_data="tdelete:" + plan_id)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="tlist")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def save_tariffs(plans):
    import json, os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tarifs.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"plans": plans}, f, ensure_ascii=False, indent=2)
    from tariffs.loader import load_tariffs
    load_tariffs()


@router.message(F.text == "📋 Тарифы")
async def admin_tariffs_list(message: Message):
    user_id = message.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        return
    from tariffs.loader import TARIFFS_ALL
    plans = list(TARIFFS_ALL)
    text = "📋 <b>Редактор тарифов</b>\n\nВыберите тариф для редактирования:"
    await replace_message(user_id, text, reply_markup=tariffs_list_keyboard(plans), delete_user_msg=message)


@router.callback_query(F.data == "tlist")
async def tariffs_list_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        await callback.answer()
        return
    from tariffs.loader import TARIFFS_ALL
    plans = list(TARIFFS_ALL)
    text = "📋 <b>Редактор тарифов</b>\n\nВыберите тариф для редактирования:"
    await callback.message.edit_text(text, reply_markup=tariffs_list_keyboard(plans), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("tedit:"))
async def tariff_edit_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        await callback.answer()
        return
    plan_id = callback.data.split(":", 1)[1]
    plan = get_by_id(plan_id)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    lines = ["✏️ <b>Тариф: " + plan.get("name", plan_id) + "</b>\n"]
    for key, (label, _) in TARIFF_FIELDS.items():
        lines.append(label + ": <b>" + str(plan.get(key, "—")) + "</b>")
    await callback.message.edit_text("\n".join(lines), reply_markup=tariff_fields_keyboard(plan_id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("ttoggle:"))
async def tariff_toggle(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        await callback.answer()
        return
    from tariffs.loader import TARIFFS_ALL
    plan_id = callback.data.split(":", 1)[1]
    plans = list(TARIFFS_ALL)
    for p in plans:
        if p.get("id") == plan_id:
            p["active"] = not p.get("active", True)
            status = "включён" if p["active"] else "выключен"
            await callback.answer("Тариф " + status, show_alert=False)
            break
    save_tariffs(plans)
    from tariffs.loader import TARIFFS_ALL as T2
    text = "📋 <b>Редактор тарифов</b>\n\nВыберите тариф для редактирования:"
    await callback.message.edit_text(text, reply_markup=tariffs_list_keyboard(list(T2)), parse_mode="HTML")


@router.callback_query(F.data.startswith("tfield:"))
async def tariff_field_select(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        await callback.answer()
        return
    _, plan_id, field = callback.data.split(":", 2)
    label = TARIFF_FIELDS.get(field, (field,))[0]
    await state.set_state(TariffEditFSM.value)
    await state.update_data(plan_id=plan_id, field=field, msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "✏️ Введите новое значение для поля <b>" + label + "</b>:\n(отправьте /cancel для отмены)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="tedit:" + plan_id)]])
    )
    await callback.answer()


@router.message(TariffEditFSM.value)
async def tariff_field_value(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == "/cancel":
        await state.clear()
        await message.delete()
        return
    data = await state.get_data()
    plan_id = data["plan_id"]
    field = data["field"]
    _, cast = TARIFF_FIELDS[field]
    try:
        value = cast(message.text.strip())
    except Exception:
        await message.answer("❌ Неверный формат. Попробуйте ещё раз.")
        return

    from tariffs.loader import TARIFFS_ALL
    plans = list(TARIFFS_ALL)
    for p in plans:
        if p.get("id") == plan_id:
            p[field] = value
            break
    save_tariffs(plans)

    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass

    plan = get_by_id(plan_id)
    lines = ["✅ Сохранено!\n\n✏️ <b>Тариф: " + plan.get("name", plan_id) + "</b>\n"]
    for key, (label, _) in TARIFF_FIELDS.items():
        lines.append(label + ": <b>" + str(plan.get(key, "—")) + "</b>")
    bot = get_bot()
    await bot.send_message(user_id, "\n".join(lines), reply_markup=tariff_fields_keyboard(plan_id), parse_mode="HTML")


@router.callback_query(F.data.startswith("tdelete:"))
async def tariff_delete(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        await callback.answer()
        return
    from tariffs.loader import TARIFFS_ALL
    plan_id = callback.data.split(":", 1)[1]
    plans = [p for p in TARIFFS_ALL if p.get("id") != plan_id]
    save_tariffs(plans)
    from tariffs.loader import TARIFFS_ALL as T2
    await callback.message.edit_text(
        "🗑 Тариф удалён.\n\n📋 <b>Редактор тарифов</b>:",
        reply_markup=tariffs_list_keyboard(list(T2)),
        parse_mode="HTML"
    )
    await callback.answer("Удалено")


@router.callback_query(F.data == "tadd")
async def tariff_add(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in Config.ADMIN_USER_IDS:
        await callback.answer()
        return
    import uuid
    new_id = "plan_" + str(int(time.time()))
    from tariffs.loader import TARIFFS_ALL
    plans = list(TARIFFS_ALL)
    plans.append({
        "id": new_id, "name": "Новый тариф", "active": False,
        "price_rub": 0, "duration_days": 30, "ip_limit": 1,
        "traffic_gb": 50, "sort": 999, "description": ""
    })
    save_tariffs(plans)
    from tariffs.loader import TARIFFS_ALL as T2
    await callback.message.edit_text(
        "➕ Тариф создан (выключен). Отредактируйте его:",
        reply_markup=tariff_fields_keyboard(new_id),
        parse_mode="HTML"
    )
    await callback.answer()


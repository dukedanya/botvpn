from typing import Any, Dict, List, Optional
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from config import Config


def kb(rows: List[List[Dict[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(**button) for button in row] for row in rows
        ]
    )

def main_menu_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="👤 Личный кабинет")],
        [KeyboardButton(text="📦 Подписки"), KeyboardButton(text="🆘 Поддержка")],
        [KeyboardButton(text="📢 Наш канал"), KeyboardButton(text="💬 Отзывы")],
        [KeyboardButton(text="Инструкция")],
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="🛠️ Админ меню")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)

def profile_keyboard(has_subscription: bool = False, is_frozen: bool = False) -> ReplyKeyboardMarkup:
    button_text = "💰 Продлить подписку" if has_subscription else "💰 Оформить подписку"
    keyboard = [
        [KeyboardButton(text=button_text)],
        [KeyboardButton(text="🤝 Реферальная система")],
    ]
    if has_subscription:
        if is_frozen:
            keyboard.append([KeyboardButton(text="▶️ Разморозить подписку")])
        else:
            keyboard.append([KeyboardButton(text="⏸ Заморозить подписку")])
    keyboard.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def subscriptions_keyboard(has_subscription: bool = False) -> ReplyKeyboardMarkup:
    button_text = "💰 Продлить подписку" if has_subscription else "💰 Оформить подписку"
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=button_text)], [KeyboardButton(text="⬅️ Назад")]],
        resize_keyboard=True,
    )

def admin_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="💰 Ожидающие платежи")],
            [KeyboardButton(text="💸 Запросы на вывод"), KeyboardButton(text="📦 Создать тестовую подписку")],
            [KeyboardButton(text="📋 Тарифы")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )

def support_keyboard_reply() -> ReplyKeyboardMarkup:
    return back_keyboard()

def instruction_keyboard() -> ReplyKeyboardMarkup:
    return back_keyboard()


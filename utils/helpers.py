import asyncio
import html
import logging
import secrets
import string
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import Config
from db import Database
from tariffs import get_all_active, is_trial_plan

logger = logging.getLogger(__name__)

# Глобальный bot — устанавливается из main.py через set_bot()
_bot: Optional[Bot] = None
_db = None
_panel = None
_itpay = None
user_last_msg: Dict[int, int] = {}
BOT_USERNAME: str = ""


def set_bot(bot: Bot, username: str = "") -> None:
    global _bot, BOT_USERNAME
    _bot = bot
    BOT_USERNAME = username


def set_db(db) -> None:
    global _db
    _db = db


def set_panel(panel) -> None:
    global _panel
    _panel = panel


def set_itpay(itpay) -> None:
    global _itpay
    _itpay = itpay


def get_bot() -> Bot:
    if _bot is None:
        raise RuntimeError("Bot не инициализирован. Вызови set_bot() в main.py")
    return _bot


def get_db():
    if _db is None:
        raise RuntimeError("DB не инициализирована. Вызови set_db() в main.py")
    return _db


def get_panel():
    if _panel is None:
        raise RuntimeError("Panel не инициализирована")
    return _panel


def get_itpay():
    if _itpay is None:
        raise RuntimeError("Itpay не инициализирован")
    return _itpay


async def replace_message(
    user_id: int,
    text: str,
    reply_markup=None,
    parse_mode: Optional[str] = ParseMode.HTML,
    delete_user_msg: Optional[Message] = None,
    **kwargs,
) -> Optional[Message]:
    bot = get_bot()
    msg = await bot.send_message(
        user_id, text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
    )
    if user_id in user_last_msg:
        try:
            await bot.delete_message(user_id, user_last_msg[user_id])
        except Exception:
            pass
    if delete_user_msg:
        try:
            await delete_user_msg.delete()
        except Exception:
            pass
    user_last_msg[user_id] = msg.message_id
    return msg


async def safe_send_message(
    user_id: int,
    message: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    bot = get_bot()
    try:
        await bot.send_message(
            user_id, message, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
    except TelegramBadRequest as e:
        logger.warning(f"HTML parse error for {user_id}: {e}")
        try:
            await bot.send_message(
                user_id, html.escape(message), parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        except Exception:
            try:
                await bot.send_message(user_id, message, reply_markup=reply_markup)
            except Exception as e2:
                logger.error(f"Ошибка отправки {user_id}: {e2}")
    except Exception as e:
        logger.error(f"Ошибка отправки {user_id}: {e}")


async def notify_admins(
    message: str, reply_markup: Optional[InlineKeyboardMarkup] = None
) -> None:
    for admin_id in Config.ADMIN_USER_IDS:
        await safe_send_message(admin_id, message, reply_markup=reply_markup)


async def notify_user(
    user_id: int,
    message: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    await safe_send_message(user_id, message, reply_markup=reply_markup)


async def smart_answer(event, text, reply_markup=None, delete_origin=False) -> None:
    try:
        if isinstance(event, Message):
            await event.answer(text, reply_markup=reply_markup)
        elif isinstance(event, CallbackQuery):
            if event.message:
                await event.message.answer(text, reply_markup=reply_markup)
                if delete_origin:
                    try:
                        await event.message.delete()
                    except Exception:
                        pass
            try:
                await event.answer()
            except Exception:
                pass
    except Exception as e:
        logger.error(f"smart_answer error: {e}")


async def get_visible_plans(
    user_id: int, *, for_admin: bool, db: Database
) -> List[Dict[str, Any]]:
    plans = get_all_active()
    if for_admin:
        return [p for p in plans if not is_trial_plan(p)]
    user = await db.get_user(user_id)
    trial_used = bool(user.get("trial_used")) if user else False
    visible: List[Dict[str, Any]] = []
    for plan in plans:
        if is_trial_plan(plan):
            continue
        visible.append(plan)
    return visible


def generate_ref_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def get_ref_link(ref_code: str, system_type: int) -> str:
    prefix = "ref1" if system_type == 1 else "ref2"
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}?start={prefix}_{ref_code}"
    return f"https://t.me/?start={prefix}_{ref_code}"

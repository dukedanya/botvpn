import logging
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config

logger = logging.getLogger(__name__)


async def ban_middleware(handler, event, data):
    # Игнорируем сообщения и колбэки от ботов
    if isinstance(event, (Message, CallbackQuery)):
        if event.from_user.is_bot:
            return None

    if isinstance(event, Message):
        user_id = event.from_user.id
    elif isinstance(event, CallbackQuery):
        user_id = event.from_user.id
    else:
        return await handler(event, data)

    db = data.get("db")
    if not db:
        return await handler(event, data)

    user_data = await db.get_user(user_id)
    if user_data and user_data.get("banned"):
        ban_reason = user_data.get("ban_reason", "Не указана")
        support_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Поддержка", url=Config.SUPPORT_URL)]]
        )
        if isinstance(event, Message):
            await event.answer(
                "⛔ <b>Ваш аккаунт заблокирован!</b>\n\n"
                f"Причина: {ban_reason}\n\n"
                "Если вы считаете, что это ошибка, пожалуйста, свяжитесь с поддержкой.",
                reply_markup=support_kb,
            )
        elif isinstance(event, CallbackQuery):
            if event.message:
                await event.message.answer(
                    "⛔ <b>Ваш аккаунт заблокирован!</b>\n\n"
                    f"Причина: {ban_reason}\n\n"
                    "Если вы считаете, что это ошибка, пожалуйста, свяжитесь с поддержкой.",
                    reply_markup=support_kb,
                )
            await event.answer("⛔ Ваш аккаунт заблокирован.", show_alert=True)
        return None

    return await handler(event, data)

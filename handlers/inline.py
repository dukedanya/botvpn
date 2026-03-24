import logging
from aiogram import Router, F
from aiogram.types import (
    InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton,
)
from utils.helpers import get_db, get_ref_link, BOT_USERNAME

logger = logging.getLogger(__name__)
router = Router()


@router.inline_query()
async def inline_ref_link(query: InlineQuery):
    user_id = query.from_user.id
    db = get_db()

    user = await db.get_user(user_id)
    if not user:
        await db.add_user(user_id)
        user = await db.get_user(user_id)

    # Если тип не выбран — предлагаем зайти в бот
    if not user.get("ref_system_type"):
        result = InlineQueryResultArticle(
            id="no_system",
            title="⚠️ Сначала настройте реферальную систему",
            description="Откройте бота и выберите тип реферальной программы",
            input_message_content=InputTextMessageContent(
                message_text=f"👋 Присоединяйся к нашему VPN-сервису!\n\nhttps://t.me/{BOT_USERNAME}"
            ),
        )
        await query.answer([result], cache_time=10, is_personal=True)
        return

    ref_code = await db.ensure_ref_code(user_id)
    system_type = user.get("ref_system_type", 1)
    link = get_ref_link(ref_code, system_type)

    if system_type == 1:
        bonus_text = f"🎁 Мы оба получим бонусные дни подписки!"
    else:
        bonus_text = f"💰 Получи скидку по моей ссылке!"

    # Карточка для отправки другу
    share_text = (
        f"🔒 <b>Надёжный VPN-сервис</b>\n\n"
        f"Подключайся по моей реферальной ссылке:\n"
        f"{link}\n\n"
        f"{bonus_text}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Подключиться", url=link)]
    ])

    results = [
        InlineQueryResultArticle(
            id="ref_link",
            title="🔗 Отправить реферальную ссылку",
            description=f"{link}",
            input_message_content=InputTextMessageContent(
                message_text=share_text,
                parse_mode="HTML",
            ),
            reply_markup=keyboard,
            thumbnail_url="https://cdn-icons-png.flaticon.com/512/2716/2716051.png",
        ),
        InlineQueryResultArticle(
            id="ref_link_short",
            title="📨 Краткое приглашение",
            description="Короткое сообщение с кнопкой",
            input_message_content=InputTextMessageContent(
                message_text=f"👋 Присоединяйся к нашему VPN!\n{bonus_text}",
                parse_mode="HTML",
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Присоединиться", url=link)]
            ]),
        ),
    ]

    await query.answer(results, cache_time=30, is_personal=True)

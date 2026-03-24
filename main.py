import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.callback_answer import CallbackAnswerMiddleware

from config import Config
from db import Database
from services.panel import PanelAPI
from services.itpay import ItpayAPI
from services.webhook import start_webhook_server
from tariffs.loader import load_tariffs
from middlewares.ban import ban_middleware
from handlers import start, profile, buy, payment_admin, referral, admin, inline
from utils.helpers import set_bot, set_db, set_panel, set_itpay

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR  = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(LOG_DIR, f"{datetime.now().strftime('%Y-%m-%d')}.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

db    = Database(Config.DATA_FILE)
panel = PanelAPI()
itpay = ItpayAPI()


async def check_expired_subscriptions() -> None:
    from services.subscriptions import is_active_subscription
    while True:
        try:
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"check_expired_subscriptions: {e}")
            await asyncio.sleep(60)


async def cleanup_old_payments() -> None:
    while True:
        try:
            deleted = await db.cleanup_old_pending_payments(days=30)
            if deleted:
                logger.info(f"Удалено старых платежей: {deleted}")
            await asyncio.sleep(259200)
        except Exception as e:
            logger.error(f"cleanup_old_payments: {e}")
            await asyncio.sleep(3600)



async def remind_unpaid_referrals() -> None:
    """Напоминание рефералам, которые не купили подписку через 24ч."""
    from utils.helpers import notify_user
    while True:
        try:
            await asyncio.sleep(3600)  # проверяем каждый час
            users = await db.get_all_users()
            now = datetime.utcnow()
            for user in users:
                if user.get("ref_by") and not user.get("ref_rewarded") and not user.get("has_subscription"):
                    joined = user.get("join_date")
                    if joined:
                        try:
                            join_dt = datetime.fromisoformat(str(joined))
                        except:
                            continue
                        diff = (now - join_dt).total_seconds()
                        # Отправляем один раз — через 24ч после регистрации
                        if 86400 <= diff <= 90000:
                            await notify_user(
                                user["user_id"],
                                "👋 Привет! Вы пришли по реферальной ссылке.\n\n"
                                "Купите подписку и получите бонусные дни! 🎁\n"
                                "Нажмите /start чтобы начать."
                            )
        except Exception as e:
            logger.error(f"remind_unpaid_referrals: {e}")
            await asyncio.sleep(3600)


async def check_expiry_notifications() -> None:
    """Напоминания за 3 дня, 1 день и 1 час до истечения подписки."""
    from utils.helpers import notify_user
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    import time as _time

    kb_renew = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="main_menu")]
    ])

    while True:
        try:
            await asyncio.sleep(1800)  # каждые 30 минут
            users = await db.get_all_subscribers()
            now_ms = int(_time.time() * 1000)

            for user in users:
                uid = user["user_id"]
                base_email = f"user_{uid}@{Config.PANEL_EMAIL_DOMAIN}"
                clients = await panel.find_clients_by_base_email(base_email)
                if not clients:
                    continue

                expiry_ms = max((c.get("expiryTime", 0) or 0) for c in clients)
                if not expiry_ms or expiry_ms < now_ms:
                    continue

                diff_sec = (expiry_ms - now_ms) / 1000

                if diff_sec <= 3600 and not user.get("notified_1h"):
                    await notify_user(uid, "⏰ <b>До истечения подписки остался 1 час!</b>\n\nНе забудьте продлить.", reply_markup=kb_renew)
                    await db.update_user(uid, notified_1h=1)
                elif diff_sec <= 86400 and not user.get("notified_1d"):
                    await notify_user(uid, "⚠️ <b>До истечения подписки остался 1 день!</b>\n\nПродлите заранее.", reply_markup=kb_renew)
                    await db.update_user(uid, notified_1d=1)
                elif diff_sec <= 259200 and not user.get("notified_3d"):
                    await notify_user(uid, "📅 <b>До истечения подписки осталось 3 дня.</b>\n\nВы можете продлить прямо сейчас.", reply_markup=kb_renew)
                    await db.update_user(uid, notified_3d=1)

        except Exception as e:
            logger.error(f"check_expiry_notifications: {e}")
            await asyncio.sleep(1800)

async def main() -> None:
    load_tariffs()
    await db.connect()
    await panel.start()

    bot = Bot(
        token=Config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(ban_middleware)
    dp.callback_query.middleware(ban_middleware)
    dp.callback_query.middleware(CallbackAnswerMiddleware())

    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(buy.router)
    dp.include_router(payment_admin.router)
    dp.include_router(referral.router)
    dp.include_router(admin.router)
    dp.include_router(inline.router)

    # Глобальные зависимости для хендлеров
    dp["db"]    = db
    dp["panel"] = panel
    dp["itpay"] = itpay
    dp["bot"]   = bot

    asyncio.create_task(check_expired_subscriptions())
    asyncio.create_task(cleanup_old_payments())
    asyncio.create_task(remind_unpaid_referrals())
    asyncio.create_task(check_expiry_notifications())
    await start_webhook_server(bot, db, panel)

    me = await bot.get_me()
    set_bot(bot, me.username)
    set_db(db)
    set_panel(panel)
    set_itpay(itpay)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен")

    try:
        await dp.start_polling(bot)
    finally:
        await db.close()
        await panel.close()
        await itpay.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())


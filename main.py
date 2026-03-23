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
from handlers import start, profile, buy, payment_admin, referral, admin
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

    # Глобальные зависимости для хендлеров
    dp["db"]    = db
    dp["panel"] = panel
    dp["itpay"] = itpay
    dp["bot"]   = bot

    asyncio.create_task(check_expired_subscriptions())
    asyncio.create_task(cleanup_old_payments())
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


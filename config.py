import os
from typing import List
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def str_to_bool(val: str) -> bool:
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_USER_IDS: List[int] = [
        int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()
    ]
    PAYMENT_CARD_NUMBER: str = os.getenv("PAYMENT_CARD_NUMBER", "")
    PANEL_BASE: str = os.getenv("PANEL_BASE", "").rstrip("/")
    SUB_PANEL_BASE: str = os.getenv("SUB_PANEL_BASE", "")
    PANEL_LOGIN: str = os.getenv("PANEL_LOGIN", "")
    PANEL_PASSWORD: str = os.getenv("PANEL_PASSWORD", "")
    VERIFY_SSL: bool = str_to_bool(os.getenv("VERIFY_SSL", "true"))
    DATA_DIR: str = os.getenv("DATA_DIR", "/data")
    DATA_FILE: str = os.getenv("DATA_FILE", os.path.join(os.getenv("DATA_DIR", "/data"), "users.db"))
    SITE_URL: str = os.getenv("SITE_URL", "")
    TG_CHANNEL: str = os.getenv("TG_CHANNEL", "https://t.me/+XsoxseRgJa8yN2Ni")
    SUPPORT_URL: str = os.getenv("SUPPORT_URL", "")
    REF_BONUS_DAYS: int = int(os.getenv("REF_BONUS_DAYS", "7"))
    REF_PERCENT_LEVEL1: float = float(os.getenv("REF_PERCENT_LEVEL1", "25"))
    REF_PERCENT_LEVEL2: float = float(os.getenv("REF_PERCENT_LEVEL2", "10"))
    MIN_WITHDRAW: float = float(os.getenv("MIN_WITHDRAW", "300"))
    PANEL_EMAIL_DOMAIN: str = os.getenv("PANEL_EMAIL_DOMAIN", "vpnbot")
    ITPAY_PUBLIC_ID: str = os.getenv("ITPAY_PUBLIC_ID", "")
    ITPAY_API_SECRET: str = os.getenv("ITPAY_API_SECRET", "")
    ITPAY_WEBHOOK_SECRET: str = os.getenv("ITPAY_WEBHOOK_SECRET", "")
    WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "")


try:
    os.makedirs(Config.DATA_DIR, exist_ok=True)
except Exception:
    pass

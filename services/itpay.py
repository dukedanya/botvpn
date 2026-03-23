import base64
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

import aiohttp

from config import Config

logger = logging.getLogger(__name__)
ITPAY_API_BASE = "https://api.gw.itpay.ru"


class ItpayAPI:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self.session or self.session.closed:
            creds = base64.b64encode(
                f"{Config.ITPAY_PUBLIC_ID}:{Config.ITPAY_API_SECRET}".encode()
            ).decode()
            self.session = aiohttp.ClientSession(
                headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"},
                connector=aiohttp.TCPConnector(ssl=Config.VERIFY_SSL),
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def create_payment(
        self,
        amount: float,
        client_payment_id: str,
        user_id: int,
        plan_id: str,
        description: str = "Оплата подписки",
        success_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        session = await self._get_session()
        payload: Dict[str, Any] = {
            "amount": f"{amount:.2f}",
            "client_payment_id": client_payment_id,
            "description": description,
            "method": "sbp",
            "webhook_url": f"{Config.SITE_URL.rstrip('/')}/itpay/webhook",
            "metadata": {
                "user_id": str(user_id),
                "plan_id": plan_id,
            },
        }
        if success_url:
            payload["success_url"] = success_url
        try:
            async with session.post(f"{ITPAY_API_BASE}/v1/payments", json=payload) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("data"):
                    return data["data"]
                logger.error(f"ITPAY create_payment {resp.status}: {data}")
        except Exception as e:
            logger.error(f"ITPAY create_payment: {e}")
        return None

    async def get_payment(self, payment_id: str) -> Optional[Dict[str, Any]]:
        session = await self._get_session()
        try:
            async with session.get(f"{ITPAY_API_BASE}/v1/payments/{payment_id}") as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("data"):
                    return data["data"]
        except Exception as e:
            logger.error(f"ITPAY get_payment: {e}")
        return None

    @staticmethod
    def verify_webhook_signature(api_secret: str, raw_body: bytes, signature_header: str) -> bool:
        try:
            parts = dict(p.split("=", 1) for p in signature_header.split(","))
            timestamp, v1 = parts.get("t", ""), parts.get("v1", "")
            body_json = json.loads(raw_body.decode("utf-8"))
            data_str = json.dumps(body_json.get("data", {}), separators=(",", ":"), ensure_ascii=False)
            signed_payload = f"{timestamp}.{data_str}"
            expected = hmac.new(api_secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, v1)
        except Exception as e:
            logger.error(f"ITPAY signature verify: {e}")
            return False

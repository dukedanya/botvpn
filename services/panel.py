import asyncio
import uuid
import logging
import secrets
import string
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import aiohttp
import random
import time
import json

from config import Config

logger = logging.getLogger(__name__)


class PanelAPI:
    def __init__(self) -> None:
        self.apibase = Config.PANEL_BASE.rstrip("/")
        self.username = Config.PANEL_LOGIN
        self.password = Config.PANEL_PASSWORD
        self.verifyssl = Config.VERIFY_SSL
        self.session: Optional[aiohttp.ClientSession] = None
        self.token: Optional[str] = None
        self.logged_in: bool = False
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        connector = aiohttp.TCPConnector(ssl=self.verifyssl)
        timeout = aiohttp.ClientTimeout(total=15)
        self.session = aiohttp.ClientSession(
            connector=connector, timeout=timeout, cookie_jar=aiohttp.CookieJar(unsafe=True)
        )
        await self.login()

    async def close(self) -> None:
        if self.session:
            await self.session.close()
            self.session = None

    async def _request_json(self, method: str, url: str, **kwargs):
        if not self.session:
            return 0, {}, ""
        try:
            async with self.session.request(method, url, **kwargs) as resp:
                text = await resp.text()
                data = {}
                if text:
                    try:
                        data = json.loads(text)
                    except Exception:
                                data = {}
                return resp.status, data, text
        except aiohttp.ClientError as e:
            logger.error(f"HTTP ошибка запроса {url}: {e}")
            return 0, {}, ""

    @staticmethod
    def _needs_reauth(status: int, data: Dict[str, Any]) -> bool:
        if status in (401, 403, 404):
            return True
        if status == 200 and isinstance(data, dict) and data.get("success") is False:
            return True
        return False

    async def _request_json_with_reauth(self, method: str, url: str, **kwargs):
        status, data, text = await self._request_json(method, url, **kwargs)
        if self._needs_reauth(status, data):
            await self.login()
            status, data, text = await self._request_json(method, url, **kwargs)
        return status, data, text

    async def login(self) -> None:
        async with self.lock:
            if not self.session:
                return
            try:
                url = f"{self.apibase}/login"
                status, data, _ = await self._request_json(
                    "POST",
                    url,
                    json={"username": self.username, "password": self.password},
                )
                if status == 200 and data.get("success"):
                    # 3x-ui использует cookie-сессию, токен не нужен
                    self.logged_in = True
                    logger.info("Успешная аутентификация в панели 3X-UI")
                else:
                    self.logged_in = False
                    logger.error(
                        f"Ошибка аутентификации 3X-UI: status={status} msg={data.get('msg')}"
                    )
            except Exception as e:
                self.logged_in = False
                logger.error(f"Ошибка при аутентификации 3X-UI: {e}")

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def ensure_auth(self) -> None:
        if not self.logged_in:
            await self.login()

    async def get_inbounds(self) -> Optional[Dict[str, Any]]:
        await self.ensure_auth()
        url = f"{self.apibase}/panel/api/inbounds/list"
        status, data, _ = await self._request_json_with_reauth(
            "GET", url, headers=self._headers()
        )
        if status == 200 and data.get("success"):
            obj = data.get("obj") or []
            logger.info(f"Получено {len(obj)} inbounds")
            return data

        logger.error(
            f"Ошибка API getInbounds: url={url} status={status} msg={data.get('msg')}"
        )
        return None

    def _parse_inbound_clients(self, inbound: Dict[str, Any]) -> List[Dict[str, Any]]:
        clients: List[Dict[str, Any]] = []
        settings = inbound.get("settings")

        if isinstance(settings, str):
            try:
                settings_obj = json.loads(settings)
                s_clients = settings_obj.get("clients") or []
                if isinstance(s_clients, list):
                    clients.extend(s_clients)
            except Exception:
                pass
        elif isinstance(settings, dict):
            s_clients = settings.get("clients") or []
            if isinstance(s_clients, list):
                clients.extend(s_clients)

        protocol = inbound.get("protocol", "")
        for client in clients:
            client["protocol"] = protocol

        return clients

    @staticmethod
    def _is_base_email(email: str, base_email: str) -> bool:
        if not email or not base_email:
            return False
        return email.endswith(base_email)

    async def find_clients_by_base_email(self, base_email: str) -> List[Dict[str, Any]]:
        inbounds = await self.get_inbounds()
        if not inbounds or not inbounds.get("success"):
            return []

        result = []
        for inbound in inbounds.get("obj", []):
            inbound_id = inbound.get("id")
            for stat in inbound.get("clientStats", []) or []:
                email = stat.get("email", "")
                if self._is_base_email(email, base_email):
                    stat["inboundId"] = inbound_id
                    result.append(stat)
        return result

    async def find_clients_full_by_email(self, base_email: str) -> List[Dict[str, Any]]:
        inbounds = await self.get_inbounds()
        if not inbounds or not inbounds.get("success"):
            return []

        result: List[Dict[str, Any]] = []
        for inbound in inbounds.get("obj", []):
            inbound_id = inbound.get("id")
            protocol = inbound.get("protocol", "").lower()
            client_stats = inbound.get("clientStats", []) or []
            clients = self._parse_inbound_clients(inbound)

            for stat in client_stats:
                email = stat.get("email", "") or ""
                if not self._is_base_email(email, base_email):
                    continue

                client_id = None
                password = None
                sub_id = None
                client_obj = None

                for c in clients:
                    c_email = c.get("email", "") or ""
                    if c_email == email:
                        client_id = c.get("id") or c.get("clientId")
                        password = c.get("password")
                        sub_id = c.get("subId")
                        client_obj = c
                        break

                item = dict(stat)
                item["inboundId"] = inbound_id
                item["clientId"] = client_id
                item["password"] = password
                item["subId"] = sub_id
                item["protocol"] = protocol
                item["clientObj"] = client_obj
                result.append(item)

        logger.info(f"Найдено {len(result)} клиентов по base_email='{base_email}'")
        return result

    async def create_client(
        self,
        email: str,
        limit_ip: int,
        total_gb: int,
        days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        await self.ensure_auth()
        inbounds = await self.get_inbounds()
        if not inbounds or not inbounds.get("success"):
            logger.error("Не удалось получить inbounds для создания клиента")
            return None

        enabled_inbounds = [
            i for i in inbounds.get("obj", []) if i.get("enable", False)
        ]

        if not enabled_inbounds:
            logger.error("Нет включённых inbound для создания клиента")
            return None

        expiry_ms = int((time.time() + days * 86400) * 1000)
        total_bytes = int(total_gb * 1073741824)
        sub_id = f"user{random.randint(100000, 999999)}"
        created_inbounds = []
        last_client = None

        for inbound in enabled_inbounds:
            inbound_id = inbound.get("id")
            protocol = inbound.get("protocol", "").lower()
            prefix = "".join(
                secrets.choice(string.ascii_letters + string.digits) for _ in range(2)
            )

            if protocol == "trojan":
                client = {
                    "password": secrets.token_urlsafe(12),
                    "email": f"{prefix}{email}",
                    "enable": True,
                    "flow": "",
                    "limitIp": limit_ip,
                    "totalGB": total_bytes,
                    "expiryTime": expiry_ms,
                    "subId": sub_id,
                }
            else:
                client = {
                    "id": str(uuid.uuid4()),
                    "email": f"{prefix}{email}",
                    "enable": True,
                    "flow": "",
                    "limitIp": limit_ip,
                    "totalGB": total_bytes,
                    "expiryTime": expiry_ms,
                    "subId": sub_id,
                }

            payload = {
                "id": inbound_id,
                "settings": json.dumps({"clients": [client]}, ensure_ascii=False),
            }

            url = f"{self.apibase}/panel/api/inbounds/addClient"
            status, data, text = await self._request_json_with_reauth(
                "POST", url, headers=self._headers(), json=payload
            )

            if status in (200, 201) and data.get("success"):
                logger.info(
                    f"Клиент {email} успешно создан в inbound {inbound_id} ({protocol})"
                )
                created_inbounds.append(inbound_id)
                client["protocol"] = protocol
                last_client = client
            else:
                logger.error(
                    f"Ошибка addClient inbound {inbound_id}: status={status} msg={data.get('msg')}"
                )
                if text:
                    logger.error(text)

        if created_inbounds and last_client:
            return last_client
        return None

    async def delete_client(self, base_email: str) -> bool:
        await self.ensure_auth()
        clients = await self.find_clients_full_by_email(base_email)

        if not clients:
            logger.info(
                f"Клиенты с частью email '{base_email}' не найдены, ничего не удаляем"
            )
            return True

        success_count = 0

        for c in clients:
            inbound_id = c.get("inboundId")
            client_id = c.get("clientId")
            password = c.get("password")
            protocol = c.get("protocol", "").lower()
            email = c.get("email", "")

            if not inbound_id:
                logger.error(f"Пропускаем клиента email={email}: нет inboundId")
                continue

            if protocol == "trojan":
                delete_id = password
            else:
                delete_id = client_id

            if not delete_id:
                logger.error(f"Пропускаем клиента email={email}: нет delete_id")
                continue

            delete_url = (
                f"{self.apibase}/panel/api/inbounds/{inbound_id}/delClient/{delete_id}"
            )
            status, data, text = await self._request_json_with_reauth(
                "POST", delete_url, headers=self._headers()
            )

            if status == 200 and data.get("success"):
                logger.info(
                    f"Клиент email={email} (inboundId={inbound_id}, protocol={protocol}) успешно удалён"
                )
                success_count += 1
            else:
                logger.error(
                    f"Ошибка удаления клиента email={email} inbound={inbound_id}: status={status} msg={data.get('msg')}"
                )
                if text:
                    logger.error(text)

        return success_count > 0

    async def extend_client_expiry(self, base_email: str, add_days: int) -> bool:
        await self.ensure_auth()
        clients = await self.find_clients_full_by_email(base_email)
        if not clients:
            return False

        success = False
        update_url = f"{self.apibase}/panel/api/inbounds/updateClient"
        for c in clients:
            inbound_id = c.get("inboundId")
            client_obj = c.get("clientObj")
            if not inbound_id or not isinstance(client_obj, dict):
                continue

            current_expiry = c.get("expiryTime", 0) or 0
            if current_expiry and current_expiry > 0:
                new_expiry = int(current_expiry + add_days * 86400 * 1000)
            else:
                new_expiry = int((time.time() + add_days * 86400) * 1000)

            client_obj["expiryTime"] = new_expiry

            payload = {
                "id": inbound_id,
                "settings": json.dumps({"clients": [client_obj]}, ensure_ascii=False),
            }

            status, data, text = await self._request_json_with_reauth(
                "POST", update_url, headers=self._headers(), json=payload
            )

            if status in (200, 201) and data.get("success"):
                success = True
            else:
                logger.error(
                    f"Ошибка updateClient inbound {inbound_id}: status={status} msg={data.get('msg')}"
                )
                if text:
                    logger.error(text)

        return success

    async def get_client_stats(self, base_email: str) -> List[Dict[str, Any]]:
        return await self.find_clients_by_base_email(base_email)

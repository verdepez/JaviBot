import httpx

from app.core.config import settings


class WhatsAppClient:
    def __init__(self) -> None:
        self.base_url = f"https://graph.facebook.com/{settings.whatsapp_api_version}"
        self.headers = {"Authorization": f"Bearer {settings.meta_access_token}"}

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        async with httpx.AsyncClient(timeout=30) as client:
            metadata = await client.get(f"{self.base_url}/{media_id}", headers=self.headers)
            metadata.raise_for_status()
            media = await client.get(metadata.json()["url"], headers=self.headers)
            media.raise_for_status()
            return media.content, metadata.json().get("mime_type", "application/octet-stream")

    async def send_text(self, recipient: str, body: str) -> None:
        payload = {"messaging_product": "whatsapp", "to": recipient, "type": "text", "text": {"body": body}}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/{settings.whatsapp_phone_number_id}/messages",
                headers={**self.headers, "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()

    async def send_contact(
        self,
        recipient: str,
        phone_number: str,
        formatted_name: str = "Pam Anota 🐶",
        first_name: str = "Pam",
        last_name: str = "Anota 🐶",
        company: str = "Pam Anota",
    ) -> None:
        clean_p = phone_number.replace("+", "").strip()
        e164_phone = f"+{clean_p}"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "contacts",
            "contacts": [
                {
                    "name": {
                        "formatted_name": formatted_name,
                        "first_name": first_name,
                        "last_name": last_name,
                    },
                    "org": {
                        "company": company,
                        "title": "Asistente Financiera",
                    },
                    "phones": [
                        {
                            "phone": e164_phone,
                            "type": "WORK",
                            "wa_id": clean_p,
                        }
                    ],
                }
            ],
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/{settings.whatsapp_phone_number_id}/messages",
                headers={**self.headers, "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
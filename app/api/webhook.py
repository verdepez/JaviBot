import traceback
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import get_db
from app.services.expense_service import record_extraction
from app.services.gemini_service import GeminiExtractor
from app.services.whatsapp_service import WhatsAppClient

router = APIRouter(prefix="/webhook", tags=["whatsapp"])
extractor = GeminiExtractor()
whatsapp = WhatsAppClient()


@router.get("")
async def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    if hub_mode == "subscribe" and hub_verify_token == settings.meta_verify_token and hub_challenge:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Token de verificación inválido")


@router.post("")
async def receive_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    payload = await request.json()
    print(f"--> [WEBHOOK] Payload recibido: {payload}")
    phone = None
    try:
        entry = payload.get("entry", [])
        if not entry:
            print("--> [WEBHOOK] Sin 'entry' en payload")
            return {"status": "ignored"}
        changes = entry[0].get("changes", [])
        if not changes:
            print("--> [WEBHOOK] Sin 'changes' en payload")
            return {"status": "ignored"}
        value = changes[0].get("value", {})

        # Ignorar notificaciones de estado (sent, delivered, read)
        messages = value.get("messages")
        if not messages:
            print("--> [WEBHOOK] No contiene 'messages' (posible notificación de estado)")
            return {"status": "ignored"}

        message = messages[0]
        phone = message.get("from")
        if not phone:
            print("--> [WEBHOOK] Mensaje sin remitente 'from'")
            return {"status": "ignored"}

        input_type = message.get("type")
        print(f"--> [WEBHOOK] Procesando mensaje de {phone} tipo {input_type}")
        if input_type == "text":
            content, mime_type = message.get("text", {}).get("body", ""), None
            print(f"--> [WEBHOOK] Texto: {content}")
        elif input_type in {"audio", "image"}:
            media_id = message.get(input_type, {}).get("id")
            if not media_id:
                return {"status": "ignored"}
            content, mime_type = await whatsapp.download_media(media_id)
        else:
            print(f"--> [WEBHOOK] Tipo no soportado: {input_type}")
            return {"status": "ignored"}

        extraction = await extractor.extract(input_type, content, mime_type)
        print(f"--> [WEBHOOK] Extracción Gemini: {extraction}")
        result_type, value = await record_extraction(db, phone, input_type, extraction)
        if result_type == "budget":
            reply = f"✅ Presupuesto mensual configurado: ${value:,.2f}"
        elif result_type == "unrecognized":
            reply = (
                "👋 ¡Hola! No detecté un gasto ni configuración de presupuesto.\n\n"
                "📌 Puedes enviar:\n"
                "• Presupuesto: 'Mi presupuesto este mes es 500000'\n"
                "• Gastos: 'Almuerzo 4500' o '2 cafés por 3000'\n"
                "• Audios o fotos de tus boletas."
            )
        else:
            reply = f"✅ Gasto registrado: ${extraction.total_spent:,.2f}\nRestante disponible: ${value:,.2f}"

        print(f"--> [WEBHOOK] Enviando respuesta a {phone}: {reply}")
        await whatsapp.send_text(phone, reply)
        print(f"--> [WEBHOOK] Respuesta enviada con éxito a {phone}")
        return {"status": "processed"}
    except ValueError as exc:
        print(f"--> [WEBHOOK] ValueError: {exc}")
        if phone:
            try:
                await whatsapp.send_text(phone, f"⚠️ {exc}")
            except Exception as send_err:
                print(f"--> [WEBHOOK] Error al enviar mensaje de aviso: {send_err}")
        return {"status": f"handled_error: {exc}"}
    except Exception as exc:
        print(f"--> [WEBHOOK] ERROR CRÍTICO: {type(exc).__name__} - {exc}")
        traceback.print_exc()
        return {"status": f"error: {type(exc).__name__} - {exc}"}
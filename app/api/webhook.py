import traceback
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import get_db
from app.services.expense_service import get_monthly_summary, record_extraction
from app.services.gemini_service import GeminiExtractor
from app.services.whatsapp_service import WhatsAppClient

router = APIRouter(prefix="/webhook", tags=["whatsapp"])
extractor = GeminiExtractor()
whatsapp = WhatsAppClient()

HELP_MESSAGE = (
    "🤖 *¡Hola! Soy JaviBot, tu asistente de gastos.*\n\n"
    "Aquí tienes una guía rápida de cómo usarme:\n\n"
    "1️⃣ *Configurar tu presupuesto mensual:*\n"
    "• Escribe: `Presupuesto 500000`\n\n"
    "2️⃣ *Registrar gastos diarios:*\n"
    "• *Texto simple:* `Almuerzo 4500` o `Uber 3200`\n"
    "• *Con detalle:* `2 cafés por 3000`\n"
    "• *Audio 🎙️:* Manda una nota de voz diciendo lo que compraste.\n"
    "• *Foto 📸:* Envía una foto de tu boleta o ticket.\n\n"
    "3️⃣ *Consultar tu saldo y balance:*\n"
    "• Escribe: `saldo`, `¿cuánto me queda?` o `resumen`\n\n"
    "4️⃣ *Ver esta ayuda:*\n"
    "• Escribe: `ayuda` o `menu`\n\n"
    "¡Pruébame enviando un gasto o consultando tu saldo! 🚀"
)


def format_summary(summary: dict) -> str:
    if not summary.get("has_budget"):
        return (
            f"⚠️ *No tienes un presupuesto configurado para este mes ({summary.get('month')}).*\n\n"
            "Configúralo fácilmente enviando:\n"
            "👉 `Presupuesto 500000`"
        )
    return (
        f"📊 *Resumen Mensual ({summary['month']})*\n\n"
        f"💰 *Presupuesto:* ${summary['total_budget']:,.2f}\n"
        f"💸 *Total Gastado:* ${summary['spent']:,.2f}\n"
        f"🟢 *Restante Disponible:* ${summary['remaining']:,.2f}\n"
        f"🏦 *Bóveda de Ahorro:* ${summary['savings']:,.2f}\n"
        f"🧾 *Compras Registradas:* {summary['expense_count']}\n\n"
        "💡 *Tip:* Escribe 'ayuda' para ver todas las opciones."
    )


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
            return {"status": "ignored"}
        changes = entry[0].get("changes", [])
        if not changes:
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
            return {"status": "ignored"}

        input_type = message.get("type")
        print(f"--> [WEBHOOK] Procesando mensaje de {phone} tipo {input_type}")

        # Atajos rápidos de texto (sin llamar a Gemini: respuesta instantánea)
        if input_type == "text":
            content = message.get("text", {}).get("body", "").strip()
            mime_type = None
            print(f"--> [WEBHOOK] Texto: {content}")
            norm = content.lower().strip().strip("¿?¡!.,")

            if norm in {"ayuda", "help", "menu", "menú", "inicio", "start", "hola", "buenas", "como funciona", "cómo funciona", "que puedes hacer", "qué puedes hacer"}:
                print(f"--> [WEBHOOK] Enviando mensaje de ayuda a {phone}")
                await whatsapp.send_text(phone, HELP_MESSAGE)
                return {"status": "processed"}

            if norm in {"saldo", "cuanto me queda", "cuánto me queda", "cuanto tengo", "cuánto tengo", "resumen", "balance", "estado", "cuanto he gastado", "cuánto he gastado"}:
                print(f"--> [WEBHOOK] Consultando resumen directo para {phone}")
                summary = await get_monthly_summary(db, phone)
                await whatsapp.send_text(phone, format_summary(summary))
                return {"status": "processed"}
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

        if extraction.is_balance_inquiry:
            summary = await get_monthly_summary(db, phone)
            reply = format_summary(summary)
        else:
            result_type, value = await record_extraction(db, phone, input_type, extraction)
            if result_type == "budget":
                reply = f"✅ Presupuesto mensual configurado: ${value:,.2f}"
            elif result_type == "unrecognized":
                reply = (
                    "👋 ¡Hola! No detecté un gasto ni consulta.\n\n"
                    "📌 *Opciones útiles:*\n"
                    "• Consultar saldo: `saldo` o `¿cuánto me queda?`\n"
                    "• Registrar gasto: `Almuerzo 4500`\n"
                    "• Presupuesto: `Presupuesto 500000`\n"
                    "• Ver guía completa: `ayuda`"
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
        if phone and ("503" in str(exc) or "UNAVAILABLE" in str(exc)):
            try:
                await whatsapp.send_text(phone, "⏳ En este momento el servicio de IA tiene alta demanda momentánea. Por favor, reenvía tu mensaje en unos segundos.")
            except Exception:
                pass
        return {"status": f"error: {type(exc).__name__} - {exc}"}
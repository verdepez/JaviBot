import traceback
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import get_db
from app.services.expense_service import (
    get_expense_list,
    get_monthly_summary,
    get_or_create_user,
    record_extraction,
    update_user_name,
)
from app.services.gemini_service import GeminiExtractor
from app.services.whatsapp_service import WhatsAppClient

router = APIRouter(prefix="/webhook", tags=["whatsapp"])
extractor = GeminiExtractor()
whatsapp = WhatsAppClient()


def get_help_message(user_name: str, user_code: str) -> str:
    return (
        f"🤖 *¡Hola {user_name}! Soy JaviBot, tu asistente de gastos.*\n"
        f"🆔 Tu código único: `{user_code}`\n\n"
        "Aquí tienes una guía rápida de cómo usarme:\n\n"
        "1️⃣ *Configurar tu presupuesto mensual:*\n"
        "• Escribe: `Presupuesto 500000`\n\n"
        "2️⃣ *Registrar gastos diarios:*\n"
        "• *Texto simple:* `Almuerzo 4500` o `Uber 3200`\n"
        "• *Con detalle:* `2 cafés por 3000`\n"
        "• *Audio 🎙️:* Manda una nota de voz diciendo lo que compraste.\n"
        "• *Foto 📸:* Envía una foto de tu boleta o ticket.\n\n"
        "3️⃣ *Consultar tus compras y en qué gastaste:*\n"
        "• Mes actual: `¿cuáles son mis compras?`, `ver compras` o `¿en qué gasté?`\n"
        "• Otro mes: `compras agosto` o `gastos 2026-08`\n\n"
        "4️⃣ *Consultar tu saldo y balance:*\n"
        "• Escribe: `saldo`, `¿cuánto me queda?` o `resumen`\n\n"
        "5️⃣ *Personalizar tu nombre:*\n"
        "• Escribe: `Me llamo Carlos` (o tu nombre preferido)\n\n"
        "6️⃣ *Ver esta ayuda:*\n"
        "• Escribe: `ayuda` o `menu`\n\n"
        "¡Pruébame enviando un gasto o consultando tu saldo! 🚀"
    )


def format_summary(summary: dict) -> str:
    name = summary.get("user_name", "Amigo")
    code = summary.get("user_code", "")
    if not summary.get("has_budget"):
        return (
            f"⚠️ *{name}, no tienes un presupuesto configurado para este mes ({summary.get('month')}).*\n\n"
            "Configúralo fácilmente enviando:\n"
            "👉 `Presupuesto 500000`\n\n"
            f"🆔 ID Usuario: `{code}`"
        )
    return (
        f"📊 *Resumen Mensual de {name} ({summary['month']})*\n\n"
        f"💰 *Presupuesto:* ${summary['total_budget']:,.2f}\n"
        f"💸 *Total Gastado:* ${summary['spent']:,.2f}\n"
        f"🟢 *Restante Disponible:* ${summary['remaining']:,.2f}\n"
        f"🏦 *Bóveda de Ahorro:* ${summary['savings']:,.2f}\n"
        f"🧾 *Compras Registradas:* {summary['expense_count']}\n"
        f"🆔 *ID Usuario:* `{code}`\n\n"
        "💡 *Tip:* Escribe 'compras' para ver el detalle de cada compra o 'ayuda' para más opciones."
    )


def format_expense_list(data: dict) -> str:
    name = data.get("user_name", "Amigo")
    month = data.get("month", "")
    is_current = data.get("is_current_month", True)

    month_label = f"este mes actual ({month})" if is_current else f"el mes {month}"

    if not data.get("has_budget"):
        return (
            f"📋 *Hola {name}, no encontré registros de gastos para {month_label}.*\n\n"
            "💡 *¿Consultar otro mes?*\n"
            "Escribe por ejemplo: `compras agosto` o `gastos 2026-08`."
        )

    expenses = data.get("expenses", [])
    if not expenses:
        return (
            f"📋 *{name}, para {month_label} no tienes compras registradas aún.*\n"
            f"💰 Presupuesto: ${data['total_budget']:,.2f}\n"
            f"🟢 Disponible: ${data['remaining']:,.2f}\n\n"
            "💡 *¿Consultar otro mes?* Escribe por ejemplo: `compras 2026-08`."
        )

    lines = [f"🧾 *Compras de {name} ({month_label}):*\n"]
    for exp in expenses:
        fecha = exp.created_at.strftime("%d/%m %H:%M") if exp.created_at else ""
        if exp.items:
            items_str = ", ".join(f"{it.item_name} (${it.total_price:,.0f})" for it in exp.items[:3])
            if len(exp.items) > 3:
                items_str += f" (+{len(exp.items)-3} más)"
            lines.append(f"• *{fecha}* {items_str} -> *${exp.total_amount:,.2f}*")
        else:
            lines.append(f"• *{fecha}* Gasto registrado -> *${exp.total_amount:,.2f}*")

    lines.append(f"\n💸 *Total gastado en {month}:* ${data['total_spent']:,.2f}")
    lines.append(f"🟢 *Saldo disponible:* ${data['remaining']:,.2f}")

    if is_current:
        lines.append(
            "\n🗓️ *¿Buscabas otro mes?*\n"
            "Estás viendo el *mes actual*. Para consultar meses anteriores, escribe por ejemplo:\n"
            "👉 `compras agosto` o `gastos 2026-08`"
        )
    else:
        lines.append(
            "\n🗓️ *Para volver al mes actual*, escribe simplemente: `compras` o `saldo`."
        )

    return "\n".join(lines)



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

        # Extraer nombre del perfil de WhatsApp si viene en el webhook
        contacts = value.get("contacts", [])
        profile_name = None
        if contacts and isinstance(contacts, list):
            profile_name = contacts[0].get("profile", {}).get("name")

        # Obtener o crear usuario de forma segura (cifrado + hash)
        user, is_new_user = await get_or_create_user(db, phone, profile_name)

        input_type = message.get("type")
        print(f"--> [WEBHOOK] Procesando mensaje de {user.name} ({user.user_code}) tipo {input_type}")

        # Atajos rápidos de texto (sin llamar a Gemini: respuesta instantánea)
        if input_type == "text":
            content = message.get("text", {}).get("body", "").strip()
            mime_type = None
            print(f"--> [WEBHOOK] Texto de {user.name}: {content}")
            norm = content.lower().strip().strip("¿?¡!.,")

            # Cambio de nombre del usuario
            for prefix in ("me llamo ", "mi nombre es ", "llamame ", "llámame ", "cambiar nombre a "):
                if norm.startswith(prefix):
                    new_name_input = content[len(prefix) :].strip()
                    if new_name_input:
                        user, updated_name = await update_user_name(db, phone, new_name_input)
                        confirm_msg = (
                            f"✨ ¡Listo! A partir de ahora te llamaré *{updated_name}*.\n"
                            f"🆔 Tu identificador único es `{user.user_code}`."
                        )
                        await whatsapp.send_text(phone, confirm_msg)
                        return {"status": "processed"}

            # Comandos de ayuda
            if norm in {"ayuda", "help", "menu", "menú", "inicio", "start", "hola", "buenas", "como funciona", "cómo funciona", "que puedes hacer", "qué puedes hacer"}:
                print(f"--> [WEBHOOK] Enviando mensaje de ayuda a {user.name}")
                await whatsapp.send_text(phone, get_help_message(user.name, user.user_code))
                return {"status": "processed"}

            # Comandos de lista de compras / gastos
            expense_list_triggers = (
                "cuales son mis compras", "cuáles son mis compras", "mis compras",
                "muestra los gastos", "muestra mis gastos", "mostrar gastos",
                "en que gaste", "en qué gasté", "que he comprado", "qué he comprado",
                "ver compras", "ver gastos", "detalle de gastos", "lista de compras", "compras", "gastos"
            )
            if any(norm == trig or norm.startswith(trig + " ") for trig in expense_list_triggers):
                print(f"--> [WEBHOOK] Consultando lista de compras directa para {user.name}")
                data = await get_expense_list(db, phone, raw_month=content, profile_name=profile_name)
                await whatsapp.send_text(phone, format_expense_list(data))
                return {"status": "processed"}

            # Comandos de saldo / resumen
            if norm in {"saldo", "cuanto me queda", "cuánto me queda", "cuanto tengo", "cuánto tengo", "resumen", "balance", "estado", "cuanto he gastado", "cuánto he gastado"}:
                print(f"--> [WEBHOOK] Consultando resumen directo para {user.name}")
                summary = await get_monthly_summary(db, phone, profile_name)
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
            result_type = "balance"
            summary = await get_monthly_summary(db, phone, profile_name)
            reply = format_summary(summary)
        elif extraction.is_expense_list_inquiry:
            result_type = "expense_list"
            data = await get_expense_list(db, phone, raw_month=extraction.target_month or content, profile_name=profile_name)
            reply = format_expense_list(data)
        else:
            result_type, value, user = await record_extraction(db, phone, input_type, extraction, profile_name)
            if result_type == "budget":
                reply = f"✅ Presupuesto mensual configurado, {user.name}: ${value:,.2f}"
            elif result_type == "unrecognized":
                reply = (
                    f"👋 ¡Hola {user.name}! No detecté un gasto ni consulta.\n\n"
                    "📌 *Opciones útiles:*\n"
                    "• Ver tus compras: `¿cuáles son mis compras?` o `compras agosto`\n"
                    "• Consultar saldo: `saldo` o `¿cuánto me queda?`\n"
                    "• Registrar gasto: `Almuerzo 4500`\n"
                    "• Presupuesto: `Presupuesto 500000`\n"
                    "• Cambiar tu nombre: `Me llamo [Nombre]`\n"
                    "• Ver guía completa: `ayuda`"
                )
            else:
                reply = f"✅ Gasto registrado, {user.name}: ${extraction.total_spent:,.2f}\nRestante disponible: ${value:,.2f}"

        # Si es un usuario recién creado, darle una bienvenida introductoria
        if is_new_user and result_type != "budget":
            reply = (
                f"👋 *¡Mucho gusto, {user.name}!* Te he registrado en JaviBot con el ID `{user.user_code}`.\n\n"
                + reply
            )

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
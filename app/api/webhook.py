import time
import traceback
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import get_db
from app.models import ProcessedMessage
from app.services.admin_service import handle_admin_command, is_admin_phone
from app.services.expense_service import (
    delete_last_expense,
    get_expense_list,
    get_monthly_summary,
    get_or_create_user,
    get_user_by_phone,
    record_extraction,
    update_user_name,
)
from app.services.gemini_service import GeminiExtractor
from app.services.whatsapp_service import WhatsAppClient

router = APIRouter(prefix="/webhook", tags=["whatsapp"])
extractor = GeminiExtractor()
whatsapp = WhatsAppClient()

# Cache en memoria para deduplicación instantánea de reintentos concurrentes de Meta
_seen_message_ids: OrderedDict[str, float] = OrderedDict()
_CACHE_MAX_SIZE = 2000
_CACHE_TTL_SECONDS = 3600  # 1 hora


def is_duplicate_message_id(msg_id: str) -> bool:
    now = time.time()
    if len(_seen_message_ids) > _CACHE_MAX_SIZE:
        cutoff = now - _CACHE_TTL_SECONDS
        while _seen_message_ids and next(iter(_seen_message_ids.values())) < cutoff:
            _seen_message_ids.popitem(last=False)

    if msg_id in _seen_message_ids:
        return True
    _seen_message_ids[msg_id] = now
    return False


def get_help_message(user_name: str, user_code: str) -> str:
    return (
        f"■ *JAVIBOT* | Control de Gastos\n"
        f"▪ ID Usuario: `{user_code}`\n"
        f"▪ Usuario: *{user_name}*\n"
        "──────────────────────────\n\n"
        "[1] *Configurar presupuesto:*\n"
        "• Enviar: `Presupuesto 500000`\n\n"
        "[2] *Registrar gastos diarios:*\n"
        "• *Texto simple:* `Almuerzo 4500` o `Uber 3200`\n"
        "• *Con detalle:* `2 cafés por 3000`\n"
        "• *Audio:* Nota de voz indicando tu compra.\n"
        "• *Boleta:* Foto de tu ticket o boleta.\n\n"
        "[3] *Consultar compras y detalle:*\n"
        "• *Mes actual:* `mis compras`, `ver compras` o `en que gaste`\n"
        "• *Otro mes:* `compras agosto` o `gastos 2026-08`\n\n"
        "[4] *Consultar saldo:*\n"
        "• Enviar: `saldo`, `cuanto me queda` o `resumen`\n\n"
        "[5] *Personalizar tu nombre:*\n"
        "• Enviar: `Me llamo Carlos` (o tu nombre preferido)\n\n"
        "[6] *Ver esta ayuda:*\n"
        "• Enviar: `ayuda` o `menu`\n\n"
        "[7] *Deshacer último gasto:*\n"
        "• Enviar: `deshacer` o `eliminar ultimo gasto`"
    )


def format_summary(summary: dict) -> str:
    name = summary.get("user_name", "Amigo")
    code = summary.get("user_code", "")
    if not summary.get("has_budget"):
        return (
            f"[!] *{name}, no tienes un presupuesto configurado para este mes ({summary.get('month')}).*\n\n"
            "Configúralo enviando:\n"
            "▸ `Presupuesto 500000`\n\n"
            f"▪ ID Usuario: `{code}`"
        )
    return (
        f"■ *RESUMEN MENSUAL* | {name}\n"
        f"▪ Periodo: {summary['month']}\n"
        f"▪ ID Usuario: `{code}`\n"
        "──────────────────────────\n"
        f"▪ Presupuesto: ${summary['total_budget']:,.2f}\n"
        f"▪ Total Gastado: ${summary['spent']:,.2f}\n"
        f"▪ Saldo Disponible: ${summary['remaining']:,.2f}\n"
        f"▪ Ahorro en Bóveda: ${summary['savings']:,.2f}\n"
        f"▪ Compras Registradas: {summary['expense_count']}\n\n"
        "▸ *Tip:* Escribe 'compras' para ver el detalle de cada compra o 'ayuda' para más opciones."
    )


def format_expense_list(data: dict) -> str:
    name = data.get("user_name", "Amigo")
    month = data.get("month", "")
    is_current = data.get("is_current_month", True)

    month_label = f"este mes actual ({month})" if is_current else f"el mes {month}"

    if not data.get("has_budget"):
        return (
            f"[!] *{name}, no se encontraron registros para {month_label}.*\n\n"
            "▸ *¿Consultar otro mes?*\n"
            "Escribe por ejemplo: `compras agosto` o `gastos 2026-08`."
        )

    expenses = data.get("expenses", [])
    if not expenses:
        return (
            f"■ *COMPRAS* | {name} ({month_label})\n"
            "──────────────────────────\n"
            "No tienes compras registradas en este período.\n"
            f"▪ Presupuesto: ${data['total_budget']:,.2f}\n"
            f"▪ Saldo disponible: ${data['remaining']:,.2f}\n\n"
            "▸ *¿Consultar otro mes?* Escribe: `compras 2026-08`."
        )

    lines = [
        f"■ *DETALLE DE COMPRAS* | {name} ({month_label})",
        "──────────────────────────",
    ]
    for exp in expenses:
        fecha = exp.created_at.strftime("%d/%m %H:%M") if exp.created_at else ""
        if exp.items:
            items_str = ", ".join(f"{it.item_name} (${it.total_price:,.0f})" for it in exp.items[:3])
            if len(exp.items) > 3:
                items_str += f" (+{len(exp.items)-3} más)"
            lines.append(f"• {fecha} | {items_str} -> *${exp.total_amount:,.2f}*")
        else:
            lines.append(f"• {fecha} | Gasto registrado -> *${exp.total_amount:,.2f}*")

    lines.append("──────────────────────────")
    lines.append(f"▪ Total gastado en {month}: ${data['total_spent']:,.2f}")
    lines.append(f"▪ Saldo disponible: ${data['remaining']:,.2f}")

    if is_current:
        lines.append(
            "\n▸ *¿Buscabas otro mes?*\n"
            "Estás viendo el *mes actual*. Para consultar meses anteriores, escribe por ejemplo:\n"
            "▸ `compras agosto` o `gastos 2026-08`"
        )
    else:
        lines.append(
            "\n▸ *Para volver al mes actual*, escribe: `compras` o `saldo`."
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

        # Deduplicación por WhatsApp Message ID (wamid) para ignorar reintentos de Meta
        msg_id = message.get("id")
        if msg_id:
            # 1. Chequeo rápido en memoria (evita carreras y reintentos concurrentes en milisegundos)
            if is_duplicate_message_id(msg_id):
                print(f"--> [WEBHOOK] Mensaje duplicado detectado en memoria ({msg_id}). Ignorando.")
                return {"status": "duplicate_ignored"}

            # 2. Chequeo persistente en base de datos
            existing_pm = await db.scalar(select(ProcessedMessage).where(ProcessedMessage.message_id == msg_id))
            if existing_pm is not None:
                print(f"--> [WEBHOOK] Mensaje duplicado detectado en BD ({msg_id}). Ignorando.")
                return {"status": "duplicate_ignored"}

            db.add(ProcessedMessage(message_id=msg_id))
            await db.commit()

        # Extraer nombre del perfil de WhatsApp si viene en el webhook
        contacts = value.get("contacts", [])
        profile_name = None
        if contacts and isinstance(contacts, list):
            profile_name = contacts[0].get("profile", {}).get("name")

        # Obtener o crear usuario de forma segura (cifrado + hash)
        user, is_new_user = await get_or_create_user(db, phone, profile_name)

        input_type = message.get("type")
        print(f"--> [WEBHOOK] Procesando mensaje de {user.name} ({user.user_code}) tipo {input_type}")

        # 1. Comandos de Administrador / Dueño (si el remitente es admin)
        if input_type == "text":
            raw_text = message.get("text", {}).get("body", "").strip()
            if is_admin_phone(phone) or user.is_admin:
                admin_res = await handle_admin_command(db, phone, raw_text)
                if admin_res:
                    admin_reply, target_u, target_num = admin_res
                    print(f"--> [WEBHOOK] Comando admin ejecutado por {phone}: {admin_reply}")
                    await whatsapp.send_text(phone, admin_reply)

                    # Si se autorizó exitosamente a un usuario, enviar mensaje de bienvenida directo a su WhatsApp
                    if target_u and target_num:
                        try:
                            client_welcome = (
                                f"✓ *¡Hola {target_u.name}! Tu acceso a JaviBot ha sido activado.*\n"
                                f"▪ ID Usuario: `{target_u.user_code}`\n\n"
                                "Ya puedes comenzar a usar el servicio:\n"
                                "• *Configura tu presupuesto:* `Presupuesto 500000`\n"
                                "• *Registra un gasto:* `Almuerzo 4500` (o envía audio/foto)\n"
                                "• *Consulta tu saldo:* `saldo`\n"
                                "• *Ver ayuda:* `ayuda`"
                            )
                            await whatsapp.send_text(target_num, client_welcome)
                        except Exception as notify_err:
                            print(f"--> [WEBHOOK] Error al notificar bienvenida al cliente: {notify_err}")

                    return {"status": "processed"}

        # 2. Control de Acceso: Verificar si el usuario está bloqueado
        if user.status == "BLOCKED":
            print(f"--> [WEBHOOK] Usuario bloqueado intentó acceder: {phone}")
            await whatsapp.send_text(
                phone,
                "[!] *Acceso inactivo*\n"
                "──────────────────────────\n"
                f"Hola {user.name}, tu cuenta se encuentra suspendida o inactiva.\n"
                "▪ Para reactivar tu servicio o resolver dudas, por favor comunícate con el administrador."
            )
            return {"status": "processed"}

        # 3. Control de Acceso: Si el usuario NO está activo y NO es admin
        is_in_trial = False
        if user.status != "ACTIVE" and not user.is_admin:
            is_hire_request = False
            if input_type == "text":
                text_content = message.get("text", {}).get("body", "").strip()
                norm_text = text_content.lower().strip().strip("¿?¡!.,")
                hire_triggers = {
                    "si", "sí", "quiero", "contratar", "quiero contratar", "me interesa",
                    "suscribir", "suscribirme", "deseo contratar", "si quiero", "sí quiero",
                    "comprar", "activar", "plan"
                }
                if norm_text in hire_triggers or any(norm_text.startswith(t + " ") for t in ("si", "sí", "quiero", "contratar")):
                    is_hire_request = True

            # Si el prospecto confirma interés en contratar
            if is_hire_request:
                user.status = "PENDING"
                await db.commit()
                print(f"--> [WEBHOOK] Prospecto registrado: {user.name} ({phone})")

                prospect_reply = (
                    "■ *SOLICITUD RECIBIDA* | JaviBot\n"
                    "──────────────────────────\n"
                    f"✓ ¡Gracias por tu interés, *{user.name}*!\n"
                    "▪ Hemos registrado tu solicitud de activación.\n"
                    "▪ Nos comunicaremos contigo a la brevedad para coordinar el pago y activar tu servicio.\n\n"
                    f"▪ ID Solicitud: `{user.user_code}`"
                )
                await whatsapp.send_text(phone, prospect_reply)

                # Notificación automática al Dueño / Administrador
                if settings.admin_phone:
                    clean_adm = settings.admin_phone.strip().lstrip("+")
                    admin_lead_msg = (
                        "■ *NUEVO CLIENTE INTERESADO*\n"
                        "──────────────────────────\n"
                        f"▪ Nombre: *{user.name}*\n"
                        f"▪ Teléfono: `{phone}`\n"
                        f"▪ ID Código: `{user.user_code}`\n\n"
                        "▸ Para activar su acceso tras recibir el pago, responde:\n"
                        f"`autorizar {phone}`"
                    )
                    try:
                        await whatsapp.send_text(clean_adm, admin_lead_msg)
                    except Exception as adm_err:
                        print(f"--> [WEBHOOK] Error al notificar al admin sobre prospecto: {adm_err}")

                return {"status": "processed"}

            # Modelo C: Verificar período de prueba gratuita si está activado
            if settings.allow_free_trial:
                if user.trial_expense_count >= settings.free_trial_max_expenses:
                    trial_ended_msg = (
                        f"[!] *Período de prueba finalizado*, {user.name}.\n"
                        "──────────────────────────\n"
                        f"Has alcanzado el límite de {settings.free_trial_max_expenses} registros de prueba gratuita.\n\n"
                        "▸ Para continuar utilizando JaviBot de forma ilimitada, responde *SI* para contratar tu plan mensual."
                    )
                    await whatsapp.send_text(phone, trial_ended_msg)
                    return {"status": "processed"}
                else:
                    is_in_trial = True

            if not is_in_trial:
                # Modo Lista Blanca Estricto: Mensaje de presentación de servicio (Cero llamadas a Gemini)
                pitch_msg = (
                    "■ *JAVIBOT* | Control de Gastos Inteligente\n"
                    "──────────────────────────\n"
                    f"Hola *{user.name}*, este es un servicio privado de gestión financiera y control de gastos personales vía WhatsApp asistido por IA.\n\n"
                    "▪ *Características principales:*\n"
                    "• Registro inmediato por texto, nota de voz o foto de boleta\n"
                    "• Control de presupuesto mensual y saldo disponible\n"
                    "• Detalle de compras mensuales y consultas históricas\n"
                    "• Privacidad y datos cifrados\n\n"
                    "▸ *¿Deseas contratar el servicio?*\n"
                    "Responde *SI* para coordinar tu activación y método de pago."
                )
                await whatsapp.send_text(phone, pitch_msg)
                return {"status": "processed"}

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
                            f"✓ Nombre actualizado: *{updated_name}*\n"
                            f"▪ ID Usuario: `{user.user_code}`"
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

            # Comandos para deshacer o borrar el último gasto
            undo_triggers = (
                "deshacer", "eliminar gasto", "borrar gasto", "eliminar ultimo gasto",
                "eliminar último gasto", "borrar ultimo gasto", "borrar último gasto",
                "cancelar gasto", "anular gasto"
            )
            if norm in undo_triggers:
                print(f"--> [WEBHOOK] Deshaciendo último gasto para {user.name}")
                success, msg = await delete_last_expense(db, phone)
                await whatsapp.send_text(phone, msg)
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

        # Una imagen es SIEMPRE un comprobante de gasto, nunca una consulta de lista ni saldo
        if input_type == "image":
            extraction.is_expense_list_inquiry = False
            extraction.is_balance_inquiry = False
            extraction.is_budget_setup = False

        # Si se extrajo un monto o ítems, tiene prioridad absoluta como registro de gasto
        if extraction.total_spent > 0 or extraction.items:
            extraction.is_expense_list_inquiry = False
            extraction.is_balance_inquiry = False

        if extraction.is_balance_inquiry:
            result_type = "balance"
            summary = await get_monthly_summary(db, phone, profile_name)
            reply = format_summary(summary)
        elif extraction.is_expense_list_inquiry:
            result_type = "expense_list"
            month_param = extraction.target_month or (content if isinstance(content, str) else None)
            data = await get_expense_list(db, phone, raw_month=month_param, profile_name=profile_name)
            reply = format_expense_list(data)
        else:
            result_type, value, user = await record_extraction(db, phone, input_type, extraction, profile_name)
            if result_type == "budget":
                reply = f"✓ Presupuesto mensual configurado, {user.name}: ${value:,.2f}"
            elif result_type == "unrecognized":
                reply = (
                    f"[!] Hola {user.name}, no detecté un gasto ni consulta.\n\n"
                    "▪ *Opciones disponibles:*\n"
                    "• Ver compras: `mis compras` o `compras agosto`\n"
                    "• Consultar saldo: `saldo` o `cuanto me queda`\n"
                    "• Registrar gasto: `Almuerzo 4500`\n"
                    "• Presupuesto: `Presupuesto 500000`\n"
                    "• Cambiar nombre: `Me llamo [Nombre]`\n"
                    "• Ver ayuda: `ayuda`"
                )
            else:
                reply = (
                    f"✓ Gasto registrado, {user.name}: ${extraction.total_spent:,.2f}\n"
                    f"▪ Saldo disponible: ${value:,.2f}"
                )
                if is_in_trial:
                    user.trial_expense_count += 1
                    await db.commit()
                    reply += f"\n\n▸ *Uso de prueba:* {user.trial_expense_count}/{settings.free_trial_max_expenses} registros."

        # Si es un usuario recién creado, darle una bienvenida introductoria
        if is_new_user and result_type != "budget":
            reply = (
                f"✓ *Bienvenido/a, {user.name}* (ID: `{user.user_code}`)\n\n"
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
                await whatsapp.send_text(phone, f"[!] {exc}")
            except Exception as send_err:
                print(f"--> [WEBHOOK] Error al enviar mensaje de aviso: {send_err}")
        return {"status": f"handled_error: {exc}"}
    except Exception as exc:
        print(f"--> [WEBHOOK] ERROR CRÍTICO: {type(exc).__name__} - {exc}")
        traceback.print_exc()
        if phone:
            err_str = str(exc)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                try:
                    await whatsapp.send_text(
                        phone,
                        "[!] El servicio de IA está con límite de cuota diario superado.\n"
                        "▪ Puedes registrar gastos en texto simple sin límites (ej: `Almuerzo 4500` o `Insumos 12000`)."
                    )
                except Exception:
                    pass
            elif "503" in err_str or "UNAVAILABLE" in err_str:
                try:
                    await whatsapp.send_text(phone, "[!] El servicio de IA tiene alta demanda momentánea. Por favor, reenvía tu mensaje en unos segundos.")
                except Exception:
                    pass
        return {"status": f"error: {type(exc).__name__} - {exc}"}
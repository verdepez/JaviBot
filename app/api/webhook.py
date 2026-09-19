import time
import traceback
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.formatters import format_currency
from app.core.security import mask_phone, normalize_phone
from app.core.timezone import get_now
from app.db import get_db
from app.models import Company, ProcessedMessage, User
from app.schemas import ExtractionResult
from app.services.admin_service import handle_admin_command, is_admin_phone, list_active_users
from app.services.analytics_service import format_spending_analysis, get_spending_analysis
from app.services.dialogue_engine import DialogueEngine
from app.services.company_service import (
    create_company,
    get_active_company,
    get_and_clear_pending_action,
    is_company_timeout_exceeded,
    list_user_companies,
    save_pending_action,
    set_company_exempt_status,
    switch_mode,
    touch_company_action,
)
from app.services.expense_service import (
    delete_last_expense,
    get_expense_list,
    get_monthly_summary,
    get_or_create_user,
    record_extraction,
    update_user_name,
)
from app.services.gemini_service import GeminiExtractor
from app.services.tax_service import (
    format_tax_summary,
    get_monthly_tax_summary,
    record_tax_document,
)
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


def get_help_message(
    user_name: str,
    user_code: str,
    active_mode: str = "PERSONAL",
    company_name: str | None = None,
) -> str:
    if active_mode == "EMPRESA" and company_name:
        mode_str = f"EMPRESA (*{company_name}*)"
    else:
        mode_str = "PERSONAL (Hogar)"

    return (
        f"■ *PAM ANOTA* | Control de Gastos & F29\n"
        f"▪ ID Usuario: `{user_code}`\n"
        f"▪ Usuario: *{user_name}*\n"
        f"▪ Modo Activo: *{mode_str}*\n"
        "──────────────────────────\n\n"
        "[1] *MODO Y CONTEXTO:*\n"
        "• *Cambiar a personal:* `modo personal`\n"
        "• *Cambiar a empresa:* `modo [nombre de tu empresa]`\n"
        "• *Ver tus empresas:* `mis empresas`\n"
        "• *Crear nueva empresa:*\n"
        "  `crear empresa [Nombre] rut [RUT] remanente [Monto]`\n"
        "  _Ej: `crear empresa Consultora rut 76.123.456-7 remanente 0 exenta`_\n\n"
        "[2] *GESTIÓN EMPRESA Y F29 (Chile / SII):*\n"
        "• *Factura de Venta (Débito Fiscal 19%):*\n"
        "  `Emití factura por 1.190.000 a Cliente X`\n"
        "• *Factura Exenta Venta (DTE 34, 0% IVA):*\n"
        "  `Emití factura exenta por 500.000`\n"
        "• *Factura de Compra (Recupera 19% IVA):*\n"
        "  `Factura compra insumos 238.000`\n"
        "• *Factura Exenta Compra (Gasto deducible, $0 crédito IVA):*\n"
        "  `Factura exenta compra 150.000`\n"
        "• *Boleta de Compra (Gasto sin crédito IVA):*\n"
        "  `Boleta materiales 45.000`\n"
        "• *Definir emisor exento:* `empresa exenta si` / `empresa exenta no`\n"
        "• *Presupuesto mensual empresa:* `Presupuesto 3000000`\n"
        "• *Liquidación y cálculo de impuestos:* `iva`, `impuestos` o `f29`\n\n"
        "[3] *GASTOS PERSONALES (en Modo Personal):*\n"
        "• *Registrar gasto:* `Almuerzo 4500` o `Uber 3200`\n"
        "• *Audio o Foto:* Envía nota de voz o foto de comprobante.\n"
        "• *Presupuesto mensual:* `Presupuesto 500000`\n"
        "• *Abono al presupuesto:* `Agregar presupuesto 100000`\n"
        "• *Consultar saldo:* `saldo`, `cuanto me queda` o `resumen`\n"
        "• *Diagnóstico y consejos:* `ahorro`, `consejos` o `analisis`\n\n"
        "[4] *OTROS COMANDOS:*\n"
        "• *Actualizar mi nombre en tu teléfono:* `contacto`\n"
        "• *Deshacer último registro:* `deshacer`\n"
        "• *Consultar compras:* `mis compras` o `compras agosto`\n"
        "• *Personalizar tu nombre:* `Me llamo Carlos`\n"
        "• *Ver esta ayuda:* `ayuda` o `menu`"
    )


def format_summary(summary: dict) -> str:
    name = summary.get("user_name", "Amigo")
    code = summary.get("user_code", "")
    mode = summary.get("active_mode", "PERSONAL")
    comp = summary.get("company_name")
    header_ctx = f"EMPRESA ({comp})" if (mode == "EMPRESA" and comp) else "PERSONAL"

    if not summary.get("has_budget"):
        return (
            f"[!] *{name}, no tienes un presupuesto configurado para {header_ctx} en este mes ({summary.get('month')}).*\n\n"
            "Configúralo enviando:\n"
            "▸ `Presupuesto 500000`\n\n"
            f"▪ ID Usuario: `{code}`"
        )

    msg = (
        f"■ *RESUMEN MENSUAL* | {header_ctx}\n"
        f"▪ Titular: {name}\n"
        f"▪ Periodo: {summary['month']}\n"
        f"▪ ID Usuario: `{code}`\n"
        "──────────────────────────\n"
        f"▪ Presupuesto: {format_currency(summary['total_budget'])}\n"
        f"▪ Total Gastado: {format_currency(summary['spent'])}\n"
        f"▪ Saldo Disponible: {format_currency(summary['remaining'])}\n"
        f"▪ Compras Registradas: {summary['expense_count']}\n"
    )

    if mode == "PERSONAL":
        msg += f"▪ Ahorro en Bóveda: {format_currency(summary.get('savings', 0))}\n"
        msg += "\n▸ *Tip:* Escribe 'compras' para ver el detalle o 'ahorro' para diagnóstico."
    else:
        msg += "\n▸ *Tip:* Escribe 'iva' o 'f29' para ver tu cálculo mensual de impuestos."

    cur_day = get_now().day
    if summary["total_budget"] > 0:
        pct_spent = summary["spent"] / summary["total_budget"]
        if pct_spent >= Decimal("0.70") and cur_day <= 20:
            msg += "\n\n▸ *Alerta:* Has consumido más del 70% de este presupuesto."

    return msg


def format_expense_list(data: dict) -> str:
    name = data.get("user_name", "Amigo")
    month = data.get("month", "")
    is_current = data.get("is_current_month", True)
    mode = data.get("active_mode", "PERSONAL")
    comp = data.get("company_name")
    header_ctx = f"EMPRESA ({comp})" if (mode == "EMPRESA" and comp) else "PERSONAL"

    month_label = f"este mes actual ({month})" if is_current else f"el mes {month}"

    if not data.get("has_budget"):
        return (
            f"[!] *{name}, no se encontraron registros de {header_ctx} para {month_label}.*\n\n"
            "▸ *¿Consultar otro mes?*\n"
            "Escribe por ejemplo: `compras agosto` o `gastos 2026-08`."
        )

    expenses = data.get("expenses", [])
    if not expenses:
        return (
            f"■ *COMPRAS* | {header_ctx} ({month_label})\n"
            "──────────────────────────\n"
            "No tienes compras registradas en este período.\n"
            f"▪ Presupuesto: {format_currency(data['total_budget'])}\n"
            f"▪ Saldo disponible: {format_currency(data['remaining'])}\n\n"
            "▸ *¿Consultar otro mes?* Escribe: `compras 2026-08`."
        )

    lines = [
        f"■ *DETALLE DE COMPRAS* | {header_ctx} ({month_label})",
        "──────────────────────────",
    ]
    for exp in expenses:
        fecha = exp.created_at.strftime("%d/%m %H:%M") if exp.created_at else ""
        if exp.items:
            items_str = ", ".join(f"{it.item_name} ({format_currency(it.total_price)})" for it in exp.items[:3])
            if len(exp.items) > 3:
                items_str += f" (+{len(exp.items)-3} más)"
            lines.append(f"• {fecha} | {items_str} -> *{format_currency(exp.total_amount)}*")
        else:
            lines.append(f"• {fecha} | Gasto registrado -> *{format_currency(exp.total_amount)}*")

    lines.append("──────────────────────────")
    lines.append(f"▪ Total gastado en {month}: {format_currency(data['total_spent'])}")
    lines.append(f"▪ Saldo disponible: {format_currency(data['remaining'])}")

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
    print(f"--> [WEBHOOK] Evento recibido (object: {payload.get('object', 'unknown')})")
    phone = None
    try:
        entry = payload.get("entry", [])
        if not entry:
            return {"status": "ignored"}
        changes = entry[0].get("changes", [])
        if not changes:
            return {"status": "ignored"}
        value = changes[0].get("value", {})
        metadata = value.get("metadata", {})
        bot_phone_raw = metadata.get("display_phone_number", "")
        bot_number = settings.bot_phone_number or normalize_phone(bot_phone_raw)

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
                    if admin_reply == "BROADCAST_CARD":
                        users_list = await list_active_users(db)
                        bot_contact_phone = bot_number or phone
                        sent_count = 0
                        for u_data in users_list:
                            u_phone = u_data.get("phone")
                            if u_phone and u_phone != "Cifrado" and not is_admin_phone(u_phone):
                                try:
                                    b_msg = (
                                        f"¡Hola {u_data.get('name', 'Amigo')}! 🐶🐾\n\n"
                                        "Me cambié el nombre oficialmente a *Pam Anota* 🐾.\n"
                                        "Te comparto mi tarjeta de contacto oficial aquí abajo:\n\n"
                                        "▸ Toca la tarjeta y selecciona *'Actualizar contacto existente'* "
                                        "(o *'Guardar contacto'*) para que quede guardado con mi nuevo nombre "
                                        "en tu teléfono con un solo toque."
                                    )
                                    await whatsapp.send_text(u_phone, b_msg)
                                    await whatsapp.send_contact(
                                        recipient=u_phone,
                                        phone_number=bot_contact_phone,
                                        formatted_name="Pam Anota 🐶",
                                        first_name="Pam",
                                        last_name="Anota 🐶",
                                        company="Pam Anota",
                                    )
                                    sent_count += 1
                                except Exception as b_err:
                                    print(f"--> [ADMIN BROADCAST] Error enviando a {mask_phone(u_phone)}: {b_err}")
                        await whatsapp.send_text(phone, f"✓ Difusión completada: Tarjeta enviada a {sent_count} usuarios activos.")
                        return {"status": "processed"}

                    print(f"--> [WEBHOOK] Comando admin ejecutado por {mask_phone(phone)}: {admin_reply}")
                    await whatsapp.send_text(phone, admin_reply)

                    # Si se autorizó exitosamente a un usuario, enviar mensaje de bienvenida directo a su WhatsApp
                    if target_u and target_num:
                        try:
                            client_welcome = (
                                f"✓ *¡Hola {target_u.name}! Tu acceso a Pam Anota ha sido activado.*\n"
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
            print(f"--> [WEBHOOK] Usuario bloqueado intentó acceder: {mask_phone(phone)}")
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
                print(f"--> [WEBHOOK] Prospecto registrado: {user.name} ({mask_phone(phone)})")

                prospect_reply = (
                    "■ *SOLICITUD RECIBIDA* | Pam Anota\n"
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
                        "▸ Para continuar utilizando Pam Anota de forma ilimitada, responde *SI* para contratar tu plan mensual."
                    )
                    await whatsapp.send_text(phone, trial_ended_msg)
                    return {"status": "processed"}
                else:
                    is_in_trial = True

            if not is_in_trial:
                pitch_msg = (
                    "■ *PAM ANOTA* | Control de Gastos Inteligente\n"
                    "──────────────────────────\n"
                    f"Hola *{user.name}*, este es un servicio privado de gestión financiera personal y tributaria para empresas vía WhatsApp.\n\n"
                    "▪ *Características principales:*\n"
                    "• Control dual: Gastos personales y Presupuesto Empresa\n"
                    "• Registro de Facturas (débito/crédito IVA) y Boletas operacionales\n"
                    "• Liquidación mensual estimada de impuestos F29 y PPM\n"
                    "• Registro inmediato por texto, nota de voz o foto de boleta/factura\n"
                    "• Privacidad y datos cifrados\n\n"
                    "▸ *¿Deseas contratar el servicio?*\n"
                    "Responde *SI* para coordinar tu activación."
                )
                await whatsapp.send_text(phone, pitch_msg)
                return {"status": "processed"}

        # 4. Manejo de Confirmación por Timeout de 5 Minutos (si hay acción pendiente retenida)
        if user.pending_action_data and input_type == "text":
            raw_text = message.get("text", {}).get("body", "").strip()
            norm_confirm = raw_text.lower().strip().strip("¿?¡!.,")
            active_comp = await get_active_company(db, user)
            comp_norm = active_comp.name_normalized if active_comp else ""

            # Cancelar acción
            if norm_confirm in {"cancelar", "anular", "cancel", "no"}:
                await get_and_clear_pending_action(db, user)
                await whatsapp.send_text(phone, "✓ Registro cancelado.")
                return {"status": "processed"}

            # Opción 1: Empresa activa
            if norm_confirm in {"1", "empresa", "si", "sí", "confirmar"} or (comp_norm and norm_confirm == comp_norm):
                pending = await get_and_clear_pending_action(db, user)
                if pending and active_comp:
                    await touch_company_action(db, user)
                    if pending.get("kind") == "tax_doc":
                        doc, summary = await record_tax_document(
                            db=db,
                            user=user,
                            company=active_comp,
                            doc_direction=pending.get("doc_direction", "RECEIVED"),
                            doc_type=pending.get("doc_type", "FACTURA"),
                            total_amount=pending.get("total_amount", 0),
                            net_amount=pending.get("net_amount"),
                            counterpart=pending.get("counterpart", ""),
                            description=pending.get("description", ""),
                            is_exempt=pending.get("is_exempt", False),
                            raw_input_type=pending.get("raw_input_type", "text"),
                        )
                        f29_str = f"🏛️ Saldo F29 proyectado: {format_currency(summary['iva_a_pagar'])} a pagar." if summary['iva_a_pagar'] > 0 else f"💰 Remanente F29 a favor: {format_currency(summary['remanente_nuevo'])}."
                        reply = (
                            f"✓ *{doc.doc_type.capitalize()} registrada en {active_comp.name}*\n"
                            f"▪ Monto Total: {format_currency(doc.total_amount)}\n"
                            f"▪ {f29_str}"
                        )
                    else:
                        ext_dict = pending.get("extraction", {})
                        ext_obj = ExtractionResult.model_validate(ext_dict)
                        _, val, _ = await record_extraction(db, phone, pending.get("raw_input_type", "text"), ext_obj, profile_name)
                        reply = f"✓ Gasto registrado en *{active_comp.name}*: {format_currency(ext_obj.total_spent)}\n▪ Presupuesto empresa disponible: {format_currency(val)}"

                    await whatsapp.send_text(phone, reply)
                    return {"status": "processed"}

            # Opción 2: Cuenta Personal
            if norm_confirm in {"2", "personal", "mi cuenta", "hogar", "casa"}:
                pending = await get_and_clear_pending_action(db, user)
                if pending:
                    # Forzar registro en presupuesto personal
                    original_mode = user.active_mode
                    user.active_mode = "PERSONAL"
                    await db.commit()

                    if pending.get("kind") == "tax_doc":
                        tot = pending.get("total_amount", 0)
                        ext_personal = ExtractionResult(total_spent=tot)
                    else:
                        ext_personal = ExtractionResult.model_validate(pending.get("extraction", {}))

                    _, val, _ = await record_extraction(db, phone, pending.get("raw_input_type", "text"), ext_personal, profile_name)
                    # Restaurar modo original
                    user.active_mode = original_mode
                    await db.commit()

                    reply = (
                        f"✓ *Gasto registrado en tu cuenta Personal*: {format_currency(ext_personal.total_spent)}\n"
                        f"▪ Saldo disponible personal: {format_currency(val)}\n"
                        f"▸ *Nota:* Al registrarse en Personal, no recupera crédito fiscal IVA."
                    )
                    await whatsapp.send_text(phone, reply)
                    return {"status": "processed"}

        # 5. Atajos directos de texto (sin llamar a Gemini: respuesta instantánea en < 2 ms)
        active_comp = await get_active_company(db, user)
        comp_name = active_comp.name if active_comp else None

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

            # Comandos de ayuda explícita
            if norm in {"ayuda", "help", "menu", "menú", "inicio", "start", "como funciona", "cómo funciona", "comandos"}:
                print(f"--> [WEBHOOK] Enviando mensaje de ayuda a {user.name}")
                await whatsapp.send_text(phone, get_help_message(user.name, user.user_code, user.active_mode, comp_name))
                return {"status": "processed"}

            # Saludos, agradecimientos y personalidad conversacional Pam Anota (0 tokens IA)
            chitchat_reply = DialogueEngine.try_respond_chitchat(content, user.name)
            if chitchat_reply:
                await whatsapp.send_text(phone, chitchat_reply)
                return {"status": "processed"}

            # Tarjeta de contacto interactiva para actualizar / guardar el contacto como Pam Anota
            contact_triggers = (
                "contacto", "tarjeta", "tarjeta de contacto", "actualizar contacto",
                "guardar contacto", "mi contacto", "como te guardo", "cómo te guardo",
                "tu contacto", "tu numero", "tu número", "guardar numero", "guardar número"
            )
            if any(norm == ct or norm.startswith(ct + " ") for ct in contact_triggers):
                bot_contact_phone = bot_number or phone
                intro_card_msg = (
                    "¡Aquí tienes mi tarjeta de contacto oficial! 🐶🐾\n\n"
                    "▸ Presiona la tarjeta que aparece abajo y selecciona *'Actualizar contacto existente'* "
                    "(o *'Guardar contacto'*) para que mi nombre quede actualizado automáticamente como "
                    "*Pam Anota 🐶* en tu teléfono con un solo toque."
                )
                await whatsapp.send_text(phone, intro_card_msg)
                await whatsapp.send_contact(
                    recipient=phone,
                    phone_number=bot_contact_phone,
                    formatted_name="Pam Anota 🐶",
                    first_name="Pam",
                    last_name="Anota 🐶",
                    company="Pam Anota",
                )
                return {"status": "processed"}

            # Listado de empresas
            if norm in {"mis empresas", "ver empresas", "empresas", "lista empresas", "mis companias", "mis compañías"}:
                companies = await list_user_companies(db, user.id)
                if not companies:
                    msg = (
                        "■ *TUS EMPRESAS*\n"
                        "──────────────────────────\n"
                        "No tienes ninguna empresa registrada aún.\n\n"
                        "▸ Para registrar tu primera empresa, escribe:\n"
                        "`crear empresa [Nombre] rut [RUT] remanente [Monto]`\n\n"
                        "Ej: `crear empresa TecnoSpA rut 76.123.456-7 remanente 80000`"
                    )
                else:
                    lines = ["■ *TUS EMPRESAS REGISTRADAS*", "──────────────────────────"]
                    for c in companies:
                        active_mark = " ✓ *(Activa)*" if (user.active_mode == "EMPRESA" and user.active_company_id == c.id) else ""
                        lines.append(f"• *{c.name}*{active_mark}")
                        lines.append(f"  RUT: `{c.rut}` | Remanente IVA: {format_currency(c.initial_tax_credit)}")
                        lines.append(f"  ▸ Para activar escribe: `modo {c.name.lower()}`")
                    lines.append("──────────────────────────")
                    lines.append("• Para volver a gastos del hogar: `modo personal`")
                    msg = "\n".join(lines)

                await whatsapp.send_text(phone, msg)
                return {"status": "processed"}

            # Liquidación F29 / Impuestos
            if norm in {"iva", "impuestos", "impuesto", "f29", "formulario 29", "mi iva", "balance tributario", "cuanto iva debo", "cuánto iva debo", "cuanto debo de iva", "cuánto debo de iva"}:
                if user.active_mode != "EMPRESA" or not active_comp:
                    await whatsapp.send_text(
                        phone,
                        "[!] Para consultar tu liquidación de impuestos F29 debes estar en Modo Empresa.\n\n"
                        "▸ Escribe `modo [nombre de tu empresa]` (ej: `modo tecnospa`).\n"
                        "O escribe `mis empresas` para ver tus empresas registradas."
                    )
                else:
                    await touch_company_action(db, user)
                    tax_summary = await get_monthly_tax_summary(db, active_comp)
                    await whatsapp.send_text(phone, format_tax_summary(tax_summary))
                return {"status": "processed"}

            # Comandos de lista de compras / gastos
            expense_list_triggers = (
                "cuales son mis compras", "cuáles son mis compras", "mis compras",
                "muestra los gastos", "muestra mis gastos", "mostrar gastos",
                "en que gaste", "en qué gasté", "que he comprado", "qué he comprado",
                "ver compras", "ver gastos", "detalle de gastos", "lista de compras", "compras", "gastos"
            )
            if any(norm == trig or norm.startswith(trig + " ") for trig in expense_list_triggers):
                data = await get_expense_list(db, phone, raw_month=content, profile_name=profile_name)
                await whatsapp.send_text(phone, format_expense_list(data))
                return {"status": "processed"}

            # Comandos de saldo / resumen
            if norm in {"saldo", "cuanto me queda", "cuánto me queda", "cuanto tengo", "cuánto tengo", "resumen", "balance", "estado", "cuanto he gastado", "cuánto he gastado"}:
                summary = await get_monthly_summary(db, phone, profile_name)
                await whatsapp.send_text(phone, format_summary(summary))
                return {"status": "processed"}

            # Comandos de diagnóstico financiero y consejos de ahorro
            analytics_triggers = (
                "ahorro", "como ahorrar", "cómo ahorrar", "consejos", "consejo",
                "tips", "tips de ahorro", "analisis", "análisis", "en que gasto mas",
                "en qué gasto más", "donde gasto mas", "dónde gasto más", "diagnostico", "diagnóstico"
            )
            if any(norm == trig or norm.startswith(trig + " ") for trig in analytics_triggers):
                analysis = await get_spending_analysis(db, phone, profile_name=profile_name)
                await whatsapp.send_text(phone, format_spending_analysis(analysis))
                return {"status": "processed"}

            # Deshacer o borrar el último gasto
            undo_triggers = (
                "deshacer", "eliminar gasto", "borrar gasto", "eliminar ultimo gasto",
                "eliminar último gasto", "borrar ultimo gasto", "borrar último gasto",
                "cancelar gasto", "anular gasto"
            )
            if norm in undo_triggers:
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

        # 6. Extracción (Base de conocimiento local de 0 tokens prioritaria + motor ML local + fallback Gemini/Groq)
        extraction = await extractor.extract(input_type, content, mime_type, db=db)
        print(f"--> [WEBHOOK] Extracción: {extraction}")

        # Si el usuario solicitó crear una empresa
        if extraction.is_company_creation and extraction.company_name and extraction.company_rut:
            comp_obj, is_new = await create_company(
                db=db,
                user=user,
                name=extraction.company_name,
                rut=extraction.company_rut,
                initial_tax_credit=extraction.initial_credit or 0.0,
                is_exempt_issuer=extraction.company_is_exempt or False,
            )
            action_label = "creada y activada" if is_new else "actualizada y activada"
            exempt_badge = " _(Emisor Exento DTE 34)_" if comp_obj.is_exempt_issuer else ""
            reply = (
                f"✓ *Empresa {action_label} con éxito*{exempt_badge}\n"
                f"▪ Nombre: *{comp_obj.name}*\n"
                f"▪ RUT: `{comp_obj.rut}`\n"
                f"▪ Emite Exento: {'Sí (DTE 34: 0% Débito IVA)' if comp_obj.is_exempt_issuer else 'No (DTE 33: 19% Débito IVA)'}\n"
                f"▪ Remanente Inicial IVA: {format_currency(comp_obj.initial_tax_credit)}\n"
                f"▪ *Modo Activo:* Has entrado en *Modo Empresa ({comp_obj.name})*.\n\n"
                "▸ Para volver a tus gastos personales en cualquier momento, escribe `modo personal`."
            )
            await whatsapp.send_text(phone, reply)
            return {"status": "processed"}

        # Si el usuario configuró el estado de emisor exento para su empresa
        if extraction.set_company_exempt is not None:
            if user.active_mode != "EMPRESA" or not active_comp:
                reply = "[!] Debes activar una empresa antes de configurar si es exenta. Escribe `modo [nombre de tu empresa]`."
            else:
                await touch_company_action(db, user)
                active_comp = await set_company_exempt_status(db, active_comp, extraction.set_company_exempt)
                status_txt = "EMISOR EXENTO (DTE 34: 0% IVA Débito)" if active_comp.is_exempt_issuer else "EMISOR AFECTO (DTE 33: 19% IVA Débito)"
                reply = (
                    f"✓ Configuración actualizada para *{active_comp.name}*.\n"
                    f"▪ Estado: *{status_txt}*.\n"
                    f"▪ A partir de ahora, sus facturas emitidas por defecto serán {'exentas de IVA' if active_comp.is_exempt_issuer else 'con 19% de IVA'}."
                )
            await whatsapp.send_text(phone, reply)
            return {"status": "processed"}

        # Si el usuario solicitó cambiar de modo
        if extraction.target_mode:
            switch_msg, _ = await switch_mode(db, user, extraction.target_mode, extraction.target_company_name)
            await whatsapp.send_text(phone, switch_msg)
            return {"status": "processed"}

        # Si consultó empresas
        if extraction.is_companies_list_inquiry:
            companies = await list_user_companies(db, user.id)
            if not companies:
                reply = "No tienes empresas registradas. Crea una con: `crear empresa [Nombre] rut [RUT] remanente [Monto]`"
            else:
                lines = ["■ *TUS EMPRESAS*", "──────────────────────────"]
                for c in companies:
                    ex_badge = " _[Exenta]_" if getattr(c, "is_exempt_issuer", False) else ""
                    lines.append(f"• *{c.name}*{ex_badge} (RUT: `{c.rut}`) -> `modo {c.name.lower()}`")
                reply = "\n".join(lines)
            await whatsapp.send_text(phone, reply)
            return {"status": "processed"}

        # Si consultó impuestos
        if extraction.is_tax_inquiry:
            if user.active_mode != "EMPRESA" or not active_comp:
                reply = "[!] Para consultar impuestos F29 debes activar tu empresa. Escribe `modo [nombre de tu empresa]`."
            else:
                await touch_company_action(db, user)
                tax_summary = await get_monthly_tax_summary(db, active_comp)
                reply = format_tax_summary(tax_summary)
            await whatsapp.send_text(phone, reply)
            return {"status": "processed"}

        # Una imagen es SIEMPRE un comprobante de gasto/factura, nunca una consulta
        if input_type == "image":
            extraction.is_expense_list_inquiry = False
            extraction.is_balance_inquiry = False
            extraction.is_budget_setup = False

        if extraction.total_spent > 0 or extraction.items:
            extraction.is_expense_list_inquiry = False
            extraction.is_balance_inquiry = False

        if extraction.is_balance_inquiry:
            summary = await get_monthly_summary(db, phone, profile_name)
            reply = format_summary(summary)
            await whatsapp.send_text(phone, reply)
            return {"status": "processed"}

        if extraction.is_expense_list_inquiry:
            month_param = extraction.target_month or (content if isinstance(content, str) else None)
            data = await get_expense_list(db, phone, raw_month=month_param, profile_name=profile_name)
            reply = format_expense_list(data)
            await whatsapp.send_text(phone, reply)
            return {"status": "processed"}

        # 7. Procesamiento de Facturas / Boletas y Gastos con Timeout de 5 Minutos
        is_in_company_mode = (user.active_mode == "EMPRESA" and active_comp is not None)

        # Regla de Timeout de 5 minutos: si está en modo empresa y pasaron >300s de inactividad
        if is_in_company_mode and is_company_timeout_exceeded(user, timeout_seconds=300):
            item_desc = extraction.items[0].name if extraction.items else "Registro"
            amt_display = format_currency(extraction.total_spent)

            if extraction.tax_doc_type:
                pending_payload = {
                    "kind": "tax_doc",
                    "doc_direction": extraction.tax_doc_direction or "RECEIVED",
                    "doc_type": extraction.tax_doc_type,
                    "total_amount": extraction.total_spent,
                    "net_amount": extraction.net_amount,
                    "is_net_amount": extraction.is_net_amount,
                    "counterpart": extraction.counterpart or "",
                    "description": item_desc,
                    "is_exempt": extraction.is_exempt,
                    "raw_input_type": input_type,
                }
            else:
                pending_payload = {
                    "kind": "expense",
                    "extraction": extraction.model_dump(),
                    "raw_input_type": input_type,
                }

            await save_pending_action(db, user, pending_payload)

            timeout_confirm_msg = (
                f"⚠️ *Han pasado más de 5 minutos desde tu última acción en {active_comp.name}.*\n\n"
                f"¿Dónde deseas registrar este movimiento de *{amt_display}* ({item_desc})?\n\n"
                f"• Responde *1* para *{active_comp.name}* (Modo Empresa)\n"
                f"• Responde *2* para *Personal* (Cuenta Personal)\n"
                f"• O escribe *cancelar*"
            )
            await whatsapp.send_text(phone, timeout_confirm_msg)
            return {"status": "processed"}

        # Si es un documento tributario explícito (Factura, Factura Exenta o Boleta)
        if extraction.tax_doc_type in {"FACTURA", "FACTURA_EXENTA", "BOLETA"}:
            if is_in_company_mode:
                await touch_company_action(db, user)
                doc, tax_sum = await record_tax_document(
                    db=db,
                    user=user,
                    company=active_comp,
                    doc_direction=extraction.tax_doc_direction or "RECEIVED",
                    doc_type=extraction.tax_doc_type,
                    total_amount=extraction.total_spent,
                    net_amount=extraction.net_amount,
                    counterpart=extraction.counterpart or "",
                    description=extraction.items[0].name if extraction.items else "",
                    is_exempt=extraction.is_exempt,
                    raw_input_type=input_type,
                )

                if doc.doc_direction == "EMITTED":
                    if doc.is_exempt or doc.doc_type == "FACTURA_EXENTA":
                        reply = (
                            f"✓ *Factura Exenta de Venta emitida ({active_comp.name})*\n"
                            f"▪ Total Facturado: *{format_currency(doc.total_amount)}*\n"
                            f"▪ *Débito Fiscal IVA:* $0 (DTE 34: Operación no afecta a IVA)\n"
                        )
                    else:
                        reply = (
                            f"✓ *Factura de Venta emitida ({active_comp.name})*\n"
                            f"▪ Total Facturado: *{format_currency(doc.total_amount)}*\n"
                            f"▪ Monto Neto: {format_currency(doc.net_amount)}\n"
                            f"▪ *Débito Fiscal (+IVA):* `+{format_currency(doc.iva_amount)}`\n"
                        )
                elif doc.is_exempt or doc.doc_type == "FACTURA_EXENTA":
                    reply = (
                        f"✓ *Factura Exenta de Compra registrada ({active_comp.name})*\n"
                        f"▪ Gasto total: *{format_currency(doc.total_amount)}* (Gasto operacional deducible)\n"
                        f"▪ *Crédito Fiscal IVA:* $0 (Sin crédito fiscal)\n"
                    )
                elif doc.doc_type == "FACTURA":
                    reply = (
                        f"✓ *Factura de Compra registrada ({active_comp.name})*\n"
                        f"▪ Total Proveedor: {format_currency(doc.total_amount)}\n"
                        f"▪ Gasto Neto empresa: {format_currency(doc.net_amount)}\n"
                        f"▪ *Crédito Fiscal IVA recuperado:* `+{format_currency(doc.iva_amount)}`\n"
                    )
                else:  # BOLETA
                    reply = (
                        f"✓ *Boleta de compra registrada ({active_comp.name})*\n"
                        f"▪ Gasto total: *{format_currency(doc.total_amount)}* (Gasto operacional)\n"
                        f"▪ *Crédito Fiscal IVA:* $0 (Sin crédito fiscal)\n"
                    )

                f29_part = f"🏛️ Saldo F29 actual: {format_currency(tax_sum['iva_a_pagar'])} a pagar." if tax_sum['iva_a_pagar'] > 0 else f"💰 Remanente F29 a favor: {format_currency(tax_sum['remanente_nuevo'])}."
                reply += f"▪ {f29_part}"

                await whatsapp.send_text(phone, reply)
                return {"status": "processed"}
            else:
                # Está en modo PERSONAL: registrar como gasto personal común
                result_type, value, user = await record_extraction(db, phone, input_type, extraction, profile_name)
                reply = (
                    f"✓ Gasto registrado en *Modo Personal*, {user.name}: {format_currency(extraction.total_spent)}\n"
                    f"▪ Saldo disponible: {format_currency(value)}\n\n"
                    "▸ *Aviso:* Al estar en Modo Personal, este documento no genera crédito fiscal IVA. "
                    "Si pertenecía a tu empresa, escribe `modo [nombre de tu empresa]` y vuelve a registrarlo."
                )
                await whatsapp.send_text(phone, reply)
                return {"status": "processed"}

        # 8. Registro de Gasto o Presupuesto General
        result_type, value, user = await record_extraction(db, phone, input_type, extraction, profile_name)
        ctx_label = f" ({active_comp.name})" if is_in_company_mode else ""

        if is_in_company_mode:
            await touch_company_action(db, user)

        if result_type == "budget":
            reply = DialogueEngine.format_budget_reply(
                user_name=user.name,
                total_budget=float(value),
                remaining=float(value),
                is_addition=False,
                is_company=is_in_company_mode,
                company_name=active_comp.name if active_comp else None,
            )
        elif result_type == "budget_added":
            summary = await get_monthly_summary(db, phone, profile_name)
            reply = DialogueEngine.format_budget_reply(
                user_name=user.name,
                total_budget=float(summary["total_budget"]),
                remaining=float(summary["remaining"]),
                is_addition=True,
                added_amount=float(value),
                is_company=is_in_company_mode,
                company_name=active_comp.name if active_comp else None,
            )
        elif result_type == "unrecognized":
            reply = (
                f"[!] Hola {user.name} 🐾, no detecté un gasto ni consulta clara.\n\n"
                "▪ *Opciones disponibles:*\n"
                "• Registrar gasto: `Almuerzo 4500` o `10 lucas bencina`\n"
                "• Factura venta: `Emití factura por 1.190.000`\n"
                "• Factura compra: `Factura insumos 238.000`\n"
                "• Boleta: `Boleta 45.000`\n"
                "• Liquidación impuestos: `iva` o `f29`\n"
                "• Cambiar modo: `modo [empresa]` o `modo personal`\n"
                "• Ver ayuda completa: `ayuda`"
            )
        else:
            item_name = extraction.items[0].name if extraction.items else "Gasto"
            reply = DialogueEngine.format_expense_reply(
                user_name=user.name,
                item_name=item_name,
                amount=extraction.total_spent,
                remaining=float(value),
                is_company=is_in_company_mode,
                company_name=active_comp.name if active_comp else None,
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

        print(f"--> [WEBHOOK] Enviando respuesta a {mask_phone(phone)}: {reply}")
        await whatsapp.send_text(phone, reply)
        print(f"--> [WEBHOOK] Respuesta enviada con éxito a {mask_phone(phone)}")
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
                        "▪ Puedes registrar gastos y facturas en texto simple sin límites."
                    )
                except Exception:
                    pass
            elif "503" in err_str or "UNAVAILABLE" in err_str:
                try:
                    await whatsapp.send_text(phone, "[!] El servicio de IA tiene alta demanda momentánea. Por favor, reenvía tu mensaje en unos segundos.")
                except Exception:
                    pass
        return {"status": f"error: {type(exc).__name__} - {exc}"}
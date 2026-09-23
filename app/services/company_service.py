import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.formatters import format_currency
from app.core.timezone import get_now
from app.models import Budget, Company, Expense, User
from app.services.expense_service import active_month



def normalize_company_name(name: str) -> str:
    """Normaliza el nombre de una empresa para comparaciones insensibles a mayúsculas, tildes y espacios."""
    n = name.lower().strip()
    n = n.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    n = re.sub(r"[^a-z0-9]", "", n)
    return n


def clean_rut(raw_rut: str) -> str:
    """Limpia y formatea un RUT chileno (ej: 76.123.456-7 o 76123456-K)."""
    cleaned = raw_rut.strip().upper().replace(" ", "").replace(".", "")
    if "-" in cleaned:
        body, dv = cleaned.split("-", 1)
    elif len(cleaned) >= 2:
        body, dv = cleaned[:-1], cleaned[-1]
    else:
        return raw_rut.strip()

    body_digits = "".join(c for c in body if c.isdigit())
    if not body_digits:
        return raw_rut.strip()

    # Formatear con puntos
    rev = body_digits[::-1]
    chunks = [rev[i : i + 3] for i in range(0, len(rev), 3)]
    formatted_body = ".".join(chunks)[::-1]
    return f"{formatted_body}-{dv}"


async def create_company(
    db: AsyncSession,
    user: User,
    name: str,
    rut: str,
    initial_tax_credit: float | Decimal = Decimal("0.00"),
    ppm_rate: float | Decimal = Decimal("0.0100"),
    is_exempt_issuer: bool = False,
    initial_budget: float | Decimal = Decimal("0.00"),
) -> tuple[Company, bool, Budget]:
    """Crea una empresa asociada al usuario o actualiza sus datos si ya existe."""
    norm_name = normalize_company_name(name)
    if not norm_name:
        raise ValueError("El nombre de la empresa no es válido.")

    clean_r = clean_rut(rut)
    init_credit = Decimal(str(initial_tax_credit or 0))
    ppm = Decimal(str(ppm_rate or 0.01))
    init_budget_dec = Decimal(str(initial_budget or 0))
    cur_month = active_month()

    # Verificar si ya existe una empresa con nombre similar para este usuario
    existing = await db.scalar(
        select(Company).where(Company.user_id == user.id, Company.name_normalized == norm_name)
    )

    if existing is not None:
        existing.name = name.strip()
        existing.rut = clean_r
        if is_exempt_issuer:
            existing.is_exempt_issuer = True
        if init_credit > 0:
            existing.initial_tax_credit = init_credit

        comp_budget = await db.scalar(
            select(Budget).where(
                Budget.user_id == user.id,
                Budget.company_id == existing.id,
                Budget.month_year == cur_month,
                Budget.budget_type == "EMPRESA",
            )
        )
        if comp_budget is None:
            comp_budget = Budget(
                user_id=user.id,
                company_id=existing.id,
                month_year=cur_month,
                budget_type="EMPRESA",
                total_budget=init_budget_dec,
            )
            db.add(comp_budget)
        elif init_budget_dec > 0:
            comp_budget.total_budget = init_budget_dec

        user.active_mode = "EMPRESA"
        user.active_company_id = existing.id
        user.last_company_action_at = get_now()
        await db.commit()
        return existing, False, comp_budget

    company = Company(
        user_id=user.id,
        name=name.strip(),
        name_normalized=norm_name,
        rut=clean_r,
        is_exempt_issuer=is_exempt_issuer,
        initial_tax_credit=init_credit,
        ppm_rate=ppm,
    )
    db.add(company)
    await db.flush()

    # Crear presupuesto de empresa para el mes en curso
    comp_budget = Budget(
        user_id=user.id,
        company_id=company.id,
        month_year=cur_month,
        budget_type="EMPRESA",
        total_budget=init_budget_dec,
    )
    db.add(comp_budget)

    # Establecer inmediatamente como empresa activa
    user.active_mode = "EMPRESA"
    user.active_company_id = company.id
    user.last_company_action_at = get_now()
    await db.commit()

    return company, True, comp_budget


async def update_company_remanente(
    db: AsyncSession,
    company: Company,
    new_remanente: float | Decimal,
) -> Company:
    """Actualiza el remanente de crédito fiscal IVA asignado a una empresa."""
    rem_val = Decimal(str(new_remanente or 0))
    company.initial_tax_credit = rem_val
    await db.commit()
    return company


async def update_company_rut(
    db: AsyncSession,
    company: Company,
    new_rut: str,
) -> Company:
    """Actualiza el RUT asignado a una empresa."""
    clean_r = clean_rut(new_rut)
    company.rut = clean_r
    await db.commit()
    await db.refresh(company)
    return company


async def set_company_exempt_status(
    db: AsyncSession,
    company: Company,
    is_exempt: bool,
) -> Company:
    """Configura si la empresa emite facturas exentas por defecto (DTE 34)."""
    company.is_exempt_issuer = is_exempt
    await db.commit()
    await db.refresh(company)
    return company


async def list_user_companies(db: AsyncSession, user_id: int) -> list[Company]:
    """Lista todas las empresas del usuario ordenadas por ID."""
    stmt = select(Company).where(Company.user_id == user_id).order_by(Company.id.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_company_by_name(db: AsyncSession, user_id: int, raw_name: str) -> Company | None:
    """Busca una empresa por nombre exacto o normalizado."""
    norm = normalize_company_name(raw_name)
    if not norm:
        return None

    # Coincidencia exacta normalizada
    company = await db.scalar(
        select(Company).where(Company.user_id == user_id, Company.name_normalized == norm)
    )
    if company is not None:
        return company

    # Coincidencia parcial si el usuario escribió solo una parte del nombre
    companies = await list_user_companies(db, user_id)
    for c in companies:
        if norm in c.name_normalized or c.name_normalized in norm:
            return c

    return None


async def get_active_company(db: AsyncSession, user: User) -> Company | None:
    """Obtiene la empresa activa del usuario si está en modo EMPRESA."""
    if user.active_mode != "EMPRESA" or not user.active_company_id:
        return None

    company = await db.get(Company, user.active_company_id)
    if not company:
        user.active_mode = "PERSONAL"
        user.active_company_id = None
        await db.commit()
        return None

    return company


async def switch_mode(
    db: AsyncSession,
    user: User,
    target_mode: str,
    target_company_name: str | None = None,
) -> tuple[str, Company | None]:
    """
    Cambia entre 'PERSONAL' y 'EMPRESA (Nombre)'.
    Retorna mensaje descriptivo y la empresa activa (si aplica).
    """
    clean_target = target_mode.lower().strip()
    if clean_target in {"personal", "hogar", "casa", "familia", "modo personal", "cuenta personal", "gastos personales"}:
        user.active_mode = "PERSONAL"
        user.active_company_id = None
        user.pending_action_data = None
        await db.commit()
        msg = (
            "✓ Has cambiado a *Modo Personal* 🏠.\n"
            "──────────────────────────\n"
            "▪ A partir de ahora, todos los gastos y compras que registres serán grabados en tus *gastos personales del hogar* (no afectarán a tu empresa ni al cálculo del F29).\n\n"
            "▸ Para volver a gestionar una empresa en cualquier momento, escribe `modo [nombre de tu empresa]`."
        )
        return msg, None

    # Si es modo empresa
    company_name_query = target_company_name or target_mode
    # Si viene con prefijo 'modo ' o 'empresa '
    if company_name_query.lower().startswith("modo "):
        company_name_query = company_name_query[5:].strip()
    if company_name_query.lower().startswith("empresa "):
        company_name_query = company_name_query[8:].strip()

    company = await get_company_by_name(db, user.id, company_name_query)
    if not company:
        # Listar las que tiene para orientarlo
        user_companies = await list_user_companies(db, user.id)
        if not user_companies:
            return (
                f"[!] No tienes empresas registradas todavía.\n\n"
                "Para registrar tu primera empresa, escribe:\n"
                "▸ `crear empresa [Nombre] rut [RUT] remanente [Monto]`\n"
                "Ej: `crear empresa TecnoSpA rut 76.123.456-7 remanente 80000`",
                None,
            )

        comp_list_str = "\n".join(f"• `modo {c.name.lower()}` ({c.name} - RUT: `{c.rut}`)" for c in user_companies)
        return (
            f"[!] No encontramos la empresa *'{company_name_query}'*.\n\n"
            f"▪ Tus empresas disponibles:\n{comp_list_str}\n\n"
            "▸ O escribe `modo personal` para volver a tus gastos personales.",
            None,
        )

    user.active_mode = "EMPRESA"
    user.active_company_id = company.id
    user.last_company_action_at = get_now()
    user.pending_action_data = None
    await db.commit()

    exempt_label = " _(Emisor Exento DTE 34)_" if getattr(company, "is_exempt_issuer", False) else ""
    msg = (
        f"✓ Has cambiado a *Modo Empresa* 🏢 (*{company.name}*{exempt_label} - RUT: `{company.rut}`).\n"
        "──────────────────────────\n"
        f"▪ A partir de ahora, todos los gastos, compras y boletas que registres serán grabados en los gastos operacionales de *{company.name}*.\n"
        f"▪ Tus facturas de venta abonarán al presupuesto de *{company.name}* y tus compras afectarán al cálculo de IVA y F29.\n\n"
        "▸ Para volver a tus gastos personales en cualquier momento, escribe `modo personal`."
    )
    return msg, company


async def transfer_company_budget_to_personal(
    db: AsyncSession,
    user: User,
    amount: float | Decimal,
    target_company_name: str | None = None,
) -> tuple[bool, str, dict | None]:
    """
    Traspasa un monto del presupuesto operacional de una empresa hacia el presupuesto personal del usuario.
    Genera un comprobante formal con folio y mensaje de respaldo contable.
    """
    amt = Decimal(str(amount))
    if amt <= Decimal("0"):
        return False, "[!] El monto a transferir debe ser mayor a cero.", None

    # Identificar la empresa origen
    company = None
    if target_company_name:
        clean_target = target_company_name.lower().strip()
        if clean_target.startswith("modo "):
            clean_target = clean_target[5:].strip()
        if clean_target.startswith("empresa "):
            clean_target = clean_target[8:].strip()
        company = await get_company_by_name(db, user.id, clean_target)
        if not company:
            return False, f"[!] No encontré ninguna empresa llamada *'{target_company_name}'*. Escribe `mis empresas` para ver la lista.", None
    elif user.active_mode == "EMPRESA" and user.active_company_id:
        company = await db.get(Company, user.active_company_id)

    if not company:
        companies = await list_user_companies(db, user.id)
        if len(companies) == 1:
            company = companies[0]
        elif len(companies) > 1:
            return False, (
                "[!] Tienes más de una empresa registrada. Por favor indica desde cuál transferir:\n"
                "▸ `mover presupuesto empresa [Nombre] a personal [Monto]`\n"
                "Ej: `mover presupuesto empresa TecnoSpA a personal 50000`"
            ), None
        else:
            return False, "[!] No tienes ninguna empresa registrada para transferir presupuesto.", None

    month = active_month()

    # 1. Obtener presupuesto de la empresa para el mes actual
    company_budget = await db.scalar(
        select(Budget).where(
            Budget.user_id == user.id,
            Budget.company_id == company.id,
            Budget.month_year == month,
            Budget.budget_type == "EMPRESA",
        )
    )

    if not company_budget or company_budget.total_budget <= Decimal("0"):
        return False, f"[!] La empresa *{company.name}* no tiene presupuesto asignado para este mes ({month}).", None

    # Calcular gastos ejecutados en la empresa para validar disponibilidad
    company_spent = await db.scalar(
        select(func.coalesce(func.sum(Expense.total_amount), Decimal("0"))).where(Expense.budget_id == company_budget.id)
    ) or Decimal("0")
    company_available = company_budget.total_budget - company_spent

    if amt > company_available:
        return False, (
            f"[!] Fondos insuficientes en el presupuesto operacional de *{company.name}*:\n"
            f"▪ Presupuesto total empresa: {format_currency(company_budget.total_budget)}\n"
            f"▪ Gastos ejecutados en el mes: {format_currency(company_spent)}\n"
            f"▪ Saldo disponible transferible: *{format_currency(company_available)}*\n"
            f"▪ Monto solicitado a transferir: {format_currency(amt)}\n\n"
            "▸ No es posible transferir un monto mayor al disponible para no dejar a la empresa en déficit."
        ), None

    # 2. Obtener o crear presupuesto personal para el mes actual
    personal_budget = await db.scalar(
        select(Budget).where(
            Budget.user_id == user.id,
            Budget.month_year == month,
            Budget.budget_type == "PERSONAL",
        )
    )

    personal_prev_total = personal_budget.total_budget if personal_budget else Decimal("0")

    if not personal_budget:
        personal_budget = Budget(
            user_id=user.id,
            month_year=month,
            budget_type="PERSONAL",
            company_id=None,
            total_budget=amt,
        )
        db.add(personal_budget)
        await db.flush()
    else:
        personal_budget.total_budget += amt

    # 3. Descontar del presupuesto de la empresa
    company_prev_total = company_budget.total_budget
    company_budget.total_budget -= amt
    company_new_available = company_available - amt

    # Calcular saldo disponible actualizado personal
    personal_spent = await db.scalar(
        select(func.coalesce(func.sum(Expense.total_amount), Decimal("0"))).where(Expense.budget_id == personal_budget.id)
    ) or Decimal("0")
    personal_available = personal_budget.total_budget - personal_spent

    await db.commit()

    # 4. Generar comprobante y mensaje de respaldo
    now = get_now()
    unique_suffix = uuid.uuid4().hex[:6].upper()
    folio = f"TRF-{now.strftime('%Y%m%d')}-{unique_suffix}"

    receipt_msg = (
        "📄🐾 *COMPROBANTE DE TRASPASO DE PRESUPUESTO*\n"
        "──────────────────────────\n"
        f"▪ *Folio de Respaldo:* `{folio}`\n"
        f"▪ *Fecha y Hora:* {now.strftime('%d/%m/%Y %H:%M:%S')}\n"
        f"▪ *Origen:* 🏢 *{company.name}* (RUT: `{company.rut}`)\n"
        f"▪ *Destino:* 🏠 *Presupuesto Personal* ({user.name})\n"
        f"▪ *Monto Traspasado:* *{format_currency(amt)}*\n"
        "──────────────────────────\n"
        f"📊 *ACTUALIZACIÓN DE SALDOS ({month})*:\n\n"
        f"🏢 *Empresa ({company.name})*:\n"
        f"  • Presupuesto anterior: {format_currency(company_prev_total)}\n"
        f"  • Débito por traspaso: -{format_currency(amt)}\n"
        f"  • Nuevo presupuesto total: {format_currency(company_budget.total_budget)}\n"
        f"  • Saldo disponible restante: *{format_currency(company_new_available)}*\n\n"
        f"🏠 *Presupuesto Personal*:\n"
        f"  • Presupuesto anterior: {format_currency(personal_prev_total)}\n"
        f"  • Abono por traspaso: +{format_currency(amt)}\n"
        f"  • Nuevo presupuesto total: {format_currency(personal_budget.total_budget)}\n"
        f"  • Saldo disponible actualizado: *{format_currency(personal_available)}*\n"
        "──────────────────────────\n"
        "ℹ️ *Respaldo Contable:* Traspaso interno de fondos presupuestarios registrado en Pam Anota. "
        "Este movimiento reasigna presupuesto operacional y no genera débito ni crédito fiscal IVA."
    )

    data = {
        "folio": folio,
        "amount": amt,
        "company_name": company.name,
        "company_rut": company.rut,
        "company_new_total": company_budget.total_budget,
        "company_new_available": company_new_available,
        "personal_new_total": personal_budget.total_budget,
        "personal_new_available": personal_available,
    }
    return True, receipt_msg, data


def is_company_timeout_exceeded(user: User, timeout_seconds: int = 300) -> bool:

    """Verifica si han transcurrido más de 5 minutos (300s) desde la última acción en modo empresa."""
    if user.active_mode != "EMPRESA" or not user.last_company_action_at:
        return False

    now = get_now()
    last = user.last_company_action_at
    # Asegurar que ambos tengan o no timezone
    if last.tzinfo is None and now.tzinfo is not None:
        last = last.replace(tzinfo=now.tzinfo)
    elif last.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=last.tzinfo)

    diff = (now - last).total_seconds()
    return diff > timeout_seconds


async def touch_company_action(db: AsyncSession, user: User) -> None:
    """Actualiza la marca de tiempo de interacción reciente en modo empresa."""
    user.last_company_action_at = get_now()
    await db.commit()


async def save_pending_action(db: AsyncSession, user: User, action_data: dict) -> None:
    """Guarda un registro retenido a la espera de confirmación de destino (Empresa vs Personal)."""
    user.pending_action_data = json.dumps(action_data, ensure_ascii=False)
    await db.commit()


async def get_and_clear_pending_action(db: AsyncSession, user: User) -> dict | None:
    """Recupera la acción pendiente y limpia el estado."""
    if not user.pending_action_data:
        return None

    try:
        data = json.loads(user.pending_action_data)
    except Exception:
        data = None

    user.pending_action_data = None
    await db.commit()
    return data


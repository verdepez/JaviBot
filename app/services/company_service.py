import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.formatters import format_currency
from app.core.timezone import get_now
from app.models import Budget, Company, User
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
    if clean_target in {"personal", "hogar", "casa", "familia", "modo personal"}:
        user.active_mode = "PERSONAL"
        user.pending_action_data = None
        await db.commit()
        return "✓ Has cambiado a *Modo Personal*. Tus registros ahora se imputarán a tus gastos personales sin crédito fiscal.", None

    # Si es modo empresa
    company_name_query = target_company_name or target_mode
    # Si viene con prefijo 'modo '
    if company_name_query.lower().startswith("modo "):
        company_name_query = company_name_query[5:].strip()

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

    return (
        f"✓ Has cambiado a *Modo Empresa* con *{company.name}* (RUT: `{company.rut}`).\n"
        f"▪ Las facturas recibidas recuperarán IVA (19% crédito fiscal) y las boletas se imputarán como gasto operacional.\n"
        f"▪ Escribe `modo personal` en cualquier momento para volver a tus gastos de hogar.",
        company,
    )


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


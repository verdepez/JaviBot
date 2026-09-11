from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.security import clean_first_name, encrypt_phone, generate_user_code, hash_phone
from app.models import Budget, Expense, ExpenseItem, SavingsVault, User
from app.schemas import ExtractionResult

MONTHS_MAP = {
    "enero": "01",
    "febrero": "02",
    "marzo": "03",
    "abril": "04",
    "mayo": "05",
    "junio": "06",
    "julio": "07",
    "agosto": "08",
    "septiembre": "09",
    "setiembre": "09",
    "octubre": "10",
    "noviembre": "11",
    "diciembre": "12",
}


def active_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def resolve_target_month(raw: str | None) -> str:
    """Convierte texto como 'agosto', 'mes pasado' o '2026-08' en 'YYYY-MM'."""
    current = active_month()
    current_year, current_m = current.split("-")
    if not raw:
        return current

    clean = raw.lower().strip()

    # Formato directo YYYY-MM
    if len(clean) == 7 and clean[:4].isdigit() and clean[4] == "-" and clean[5:].isdigit():
        return clean

    # 'mes pasado' o 'anterior'
    if "pasado" in clean or "anterior" in clean:
        m_int = int(current_m) - 1
        y_int = int(current_year)
        if m_int == 0:
            m_int = 12
            y_int -= 1
        return f"{y_int:04d}-{m_int:02d}"

    for m_name, m_num in MONTHS_MAP.items():
        if m_name in clean:
            for part in clean.split():
                if part.isdigit() and len(part) == 4:
                    return f"{part}-{m_num}"
            return f"{current_year}-{m_num}"

    return current



async def get_user_by_phone(db: AsyncSession, phone_number: str) -> User | None:
    clean_p = phone_number.strip().lstrip("+")
    p_hash = hash_phone(clean_p)
    user = await db.scalar(select(User).where(User.phone_hash == p_hash))
    if user is None:
        user = await db.scalar(select(User).where(User.phone_number == clean_p))
    return user


async def get_or_create_user(
    db: AsyncSession,
    phone_number: str,
    profile_name: str | None = None,
    initial_status: str | None = None,
) -> tuple[User, bool]:
    clean_p = phone_number.strip().lstrip("+")
    clean_admin = settings.admin_phone.strip().lstrip("+") if settings.admin_phone else ""
    is_admin_user = bool(clean_admin and clean_p == clean_admin)

    p_hash = hash_phone(clean_p)
    user = await db.scalar(select(User).where(User.phone_hash == p_hash))

    # Retrocompatibilidad: buscar por phone_number anterior si no se encontró por hash
    if user is None:
        user = await db.scalar(select(User).where(User.phone_number == clean_p))
        if user is not None:
            user.phone_hash = p_hash
            user.encrypted_phone = encrypt_phone(clean_p)
            if not user.name:
                user.name = clean_first_name(profile_name)
            if not user.user_code:
                user.user_code = generate_user_code(clean_p, user.name)
            if is_admin_user:
                user.is_admin = True
                user.status = "ACTIVE"
            await db.flush()
            return user, False

    is_new = False
    if user is None:
        is_new = True
        name = clean_first_name(profile_name)
        code = generate_user_code(clean_p, name)

        if is_admin_user:
            user_status = "ACTIVE"
        elif initial_status:
            user_status = initial_status
        elif settings.access_mode == "whitelist":
            user_status = "PENDING"
        else:
            user_status = "ACTIVE"

        user = User(
            phone_hash=p_hash,
            encrypted_phone=encrypt_phone(clean_p),
            name=name,
            user_code=code,
            phone_number=None,
            status=user_status,
            is_admin=is_admin_user,
        )
        db.add(user)
        await db.flush()
    else:
        if is_admin_user and (not user.is_admin or user.status != "ACTIVE"):
            user.is_admin = True
            user.status = "ACTIVE"
            await db.flush()
        if profile_name and user.name in ("Amigo", "", None):
            user.name = clean_first_name(profile_name)
            user.user_code = generate_user_code(clean_p, user.name)
            await db.flush()

    return user, is_new


async def update_user_name(db: AsyncSession, phone_number: str, new_name: str) -> tuple[User, str]:
    clean_name = clean_first_name(new_name)
    user, _ = await get_or_create_user(db, phone_number)
    user.name = clean_name
    user.user_code = generate_user_code(phone_number, clean_name)
    await db.commit()
    return user, user.name


async def record_extraction(
    db: AsyncSession,
    phone_number: str,
    input_type: str,
    extraction: ExtractionResult,
    profile_name: str | None = None,
) -> tuple[str, Decimal, User]:
    user, _ = await get_or_create_user(db, phone_number, profile_name)

    month = active_month()
    budget = await db.scalar(select(Budget).where(Budget.user_id == user.id, Budget.month_year == month))
    if extraction.is_budget_setup:
        if extraction.budget_amount is None or extraction.budget_amount <= 0:
            raise ValueError(f"Por favor indica un monto válido para tu presupuesto mensual, {user.name} (ej: 'Presupuesto 500000').")
        if budget is None:
            budget = Budget(user_id=user.id, month_year=month, total_budget=Decimal(str(extraction.budget_amount)))
            db.add(budget)
        else:
            budget.total_budget = Decimal(str(extraction.budget_amount))
        await db.commit()
        return "budget", budget.total_budget, user

    if extraction.is_balance_inquiry:
        return "balance", Decimal("0"), user

    if extraction.is_expense_list_inquiry:
        return "expense_list", Decimal("0"), user

    # Si no es configuración de presupuesto ni se detectó un gasto real
    if not extraction.items and extraction.total_spent <= 0:
        return "unrecognized", Decimal("0"), user

    if budget is None:
        raise ValueError(f"Primero configura tu presupuesto mensual, {user.name} (ej: 'Mi presupuesto este mes es 500000').")

    total = Decimal(str(extraction.total_spent))
    expense = Expense(budget_id=budget.id, raw_input_type=input_type, total_amount=total)
    db.add(expense)
    await db.flush()
    for item in extraction.items:
        db.add(
            ExpenseItem(
                expense_id=expense.id,
                item_name=item.name,
                quantity=Decimal(str(item.quantity)),
                unit_price=Decimal(str(item.unit_price)),
                total_price=Decimal(str(item.total)),
                category=item.category,
            )
        )

    # A positive difference is treated as an optimization saving supplied by extraction.
    estimated_saving = sum(
        max(
            Decimal("0"),
            Decimal(str(item.quantity)) * Decimal(str(item.unit_price)) - Decimal(str(item.total)),
        )
        for item in extraction.items
    )
    if estimated_saving > 0:
        db.add(SavingsVault(user_id=user.id, amount_saved=estimated_saving, reason="Compra optimizada o al por mayor"))

    await db.commit()
    spent = await db.scalar(
        select(func.coalesce(func.sum(Expense.total_amount), Decimal("0"))).where(Expense.budget_id == budget.id)
    )
    remaining = budget.total_budget - (spent or Decimal("0"))
    return "expense", remaining, user


async def get_monthly_summary(db: AsyncSession, phone_number: str, profile_name: str | None = None) -> dict:
    user, _ = await get_or_create_user(db, phone_number, profile_name)
    month = active_month()
    budget = await db.scalar(select(Budget).where(Budget.user_id == user.id, Budget.month_year == month))
    if budget is None:
        return {
            "has_budget": False,
            "month": month,
            "user_name": user.name,
            "user_code": user.user_code,
        }

    spent = await db.scalar(
        select(func.coalesce(func.sum(Expense.total_amount), Decimal("0"))).where(Expense.budget_id == budget.id)
    ) or Decimal("0")

    expense_count = await db.scalar(
        select(func.count(Expense.id)).where(Expense.budget_id == budget.id)
    ) or 0

    savings = await db.scalar(
        select(func.coalesce(func.sum(SavingsVault.amount_saved), Decimal("0"))).where(SavingsVault.user_id == user.id)
    ) or Decimal("0")

    remaining = budget.total_budget - spent

    return {
        "has_budget": True,
        "month": month,
        "user_name": user.name,
        "user_code": user.user_code,
        "total_budget": budget.total_budget,
        "spent": spent,
        "remaining": remaining,
        "savings": savings,
        "expense_count": expense_count,
    }


async def get_expense_list(
    db: AsyncSession,
    phone_number: str,
    raw_month: str | None = None,
    profile_name: str | None = None,
) -> dict:
    user, _ = await get_or_create_user(db, phone_number, profile_name)
    target = resolve_target_month(raw_month)
    current = active_month()
    is_current = (target == current)

    budget = await db.scalar(select(Budget).where(Budget.user_id == user.id, Budget.month_year == target))
    if budget is None:
        return {
            "has_budget": False,
            "month": target,
            "is_current_month": is_current,
            "user_name": user.name,
            "user_code": user.user_code,
            "expenses": [],
            "total_spent": Decimal("0"),
            "total_budget": Decimal("0"),
            "remaining": Decimal("0"),
        }

    stmt = (
        select(Expense)
        .options(selectinload(Expense.items))
        .where(Expense.budget_id == budget.id)
        .order_by(Expense.created_at.desc())
        .limit(20)
    )
    expenses = (await db.scalars(stmt)).all()

    total_spent = await db.scalar(
        select(func.coalesce(func.sum(Expense.total_amount), Decimal("0"))).where(Expense.budget_id == budget.id)
    ) or Decimal("0")

    remaining = budget.total_budget - total_spent

    return {
        "has_budget": True,
        "month": target,
        "is_current_month": is_current,
        "user_name": user.name,
        "user_code": user.user_code,
        "total_budget": budget.total_budget,
        "total_spent": total_spent,
        "remaining": remaining,
        "expenses": expenses,
    }


async def delete_last_expense(db: AsyncSession, phone_number: str) -> tuple[bool, str]:
    user = await get_user_by_phone(db, phone_number)
    if not user:
        return False, "[!] No se encontró tu cuenta de usuario."

    current = active_month()
    budget = await db.scalar(select(Budget).where(Budget.user_id == user.id, Budget.month_year == current))
    if not budget:
        return False, f"[!] No tienes un presupuesto activo para este mes ({current})."

    stmt = (
        select(Expense)
        .options(selectinload(Expense.items))
        .where(Expense.budget_id == budget.id)
        .order_by(Expense.id.desc())
        .limit(1)
    )
    last_exp = await db.scalar(stmt)
    if not last_exp:
        return False, "[!] No tienes compras registradas en este mes para deshacer."

    deleted_amount = last_exp.total_amount
    items_desc = ", ".join(it.item_name for it in last_exp.items) if last_exp.items else "Gasto"
    await db.delete(last_exp)
    await db.commit()

    summary = await get_monthly_summary(db, phone_number)
    return (
        True,
        f"✓ *Gasto eliminado con éxito*, {user.name}:\n"
        f"▪ Detalle: {items_desc} (${deleted_amount:,.2f})\n"
        f"▪ Saldo disponible actualizado: *${summary['remaining']:,.2f}*"
    )
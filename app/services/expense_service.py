from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import clean_first_name, encrypt_phone, generate_user_code, hash_phone
from app.models import Budget, Expense, ExpenseItem, SavingsVault, User
from app.schemas import ExtractionResult


def active_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def get_or_create_user(
    db: AsyncSession,
    phone_number: str,
    profile_name: str | None = None,
) -> tuple[User, bool]:
    p_hash = hash_phone(phone_number)
    user = await db.scalar(select(User).where(User.phone_hash == p_hash))

    # Retrocompatibilidad: buscar por phone_number anterior si no se encontró por hash
    if user is None:
        user = await db.scalar(select(User).where(User.phone_number == phone_number))
        if user is not None:
            user.phone_hash = p_hash
            user.encrypted_phone = encrypt_phone(phone_number)
            if not user.name:
                user.name = clean_first_name(profile_name)
            if not user.user_code:
                user.user_code = generate_user_code(phone_number, user.name)
            await db.flush()
            return user, False

    is_new = False
    if user is None:
        is_new = True
        name = clean_first_name(profile_name)
        code = generate_user_code(phone_number, name)
        user = User(
            phone_hash=p_hash,
            encrypted_phone=encrypt_phone(phone_number),
            name=name,
            user_code=code,
            phone_number=None,
        )
        db.add(user)
        await db.flush()
    elif profile_name and user.name in ("Amigo", "", None):
        user.name = clean_first_name(profile_name)
        user.user_code = generate_user_code(phone_number, user.name)
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
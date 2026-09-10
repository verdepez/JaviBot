from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Budget, Expense, ExpenseItem, SavingsVault, User
from app.schemas import ExtractionResult


def active_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def record_extraction(
    db: AsyncSession,
    phone_number: str,
    input_type: str,
    extraction: ExtractionResult,
) -> tuple[str, Decimal]:
    user = await db.scalar(select(User).where(User.phone_number == phone_number))
    if user is None:
        user = User(phone_number=phone_number)
        db.add(user)
        await db.flush()

    month = active_month()
    budget = await db.scalar(select(Budget).where(Budget.user_id == user.id, Budget.month_year == month))
    if extraction.is_budget_setup:
        if extraction.budget_amount is None or extraction.budget_amount <= 0:
            raise ValueError("Por favor indica un monto válido para tu presupuesto mensual (ej: 'Presupuesto 500000').")
        if budget is None:
            budget = Budget(user_id=user.id, month_year=month, total_budget=Decimal(str(extraction.budget_amount)))
            db.add(budget)
        else:
            budget.total_budget = Decimal(str(extraction.budget_amount))
        await db.commit()
        return "budget", budget.total_budget

    # Si no es configuración de presupuesto ni se detectó un gasto real
    if not extraction.items and extraction.total_spent <= 0:
        return "unrecognized", Decimal("0")

    if budget is None:
        raise ValueError("Primero configura tu presupuesto mensual (ej: 'Mi presupuesto este mes es 500000').")

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
    return "expense", remaining
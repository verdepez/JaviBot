import calendar
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.formatters import format_currency
from app.core.timezone import get_now
from app.models import Budget, Expense
from app.services.expense_service import active_month, get_or_create_user

CATEGORY_LABELS = {
    "alimentos": "Alimentos y Supermercado",
    "mascotas": "Mascotas y Veterinaria",
    "hogar_servicios": "Hogar, Cuentas y Servicios",
    "transporte": "Transporte y Combustible",
    "salud": "Salud y Farmacia",
    "educacion": "Educación y Colegios",
    "familia": "Familia y Cuidado",
    "ocio": "Ocio y Entretenimiento",
    "trabajo_insumos": "Trabajo e Insumos",
    "otros": "Otros Gastos",
}

CATEGORY_ADVICE = {
    "alimentos": "Tu mayor gasto está en alimentación. Una lista planificada de supermercado y limitar pedidos de delivery/comida al paso a 1 vez por semana puede reducir este gasto hasta un 20%.",
    "mascotas": "Tu gasto veterinario y de mascotas es tu principal centro de costo. Comprar alimento en sacos grandes (15kg+) y adquirir antiparasitarios en distribuidoras mayoristas suele ahorrar entre 15% y 25%.",
    "hogar_servicios": "Cuentas y hogar son tu mayor costo. Audita suscripciones mensuales que ya no utilices y controla el consumo de gas/calefacción y electricidad en horarios punta.",
    "transporte": "El transporte representa una porción importante. Combinar trayectos o preferir Metro/bip frente a tarifas dinámicas de aplicaciones en horas punta amortiguará este desembolso.",
    "salud": "En farmacias y salud, consulta por medicamentos bioequivalentes y aprovecha los días de descuento por cadenas de farmacias o convenios de seguros.",
    "ocio": "Tus salidas y entretenimiento concentran gran parte de tus egresos. Fija un tope semanal para diversión sin comprometer tus obligaciones fijas.",
    "educacion": "Revisa los gastos educativos recurrentes y cotiza compras conjuntas de útiles o libros escolares para acceder a precios mayoristas.",
    "familia": "En compras familiares frecuentes (pañales, artículos infantiles, aseo), comprar por bulto cerrado en distribuidoras reduce notoriamente el costo por unidad.",
    "trabajo_insumos": "Separa estrictamente los insumos de trabajo para que no distorsionen tu presupuesto de gastos personales del hogar.",
    "otros": "Tienes varios consumos generales sin categorizar. Intenta detallar el concepto de tus compras (ej: 'Farmacia 15000') para afinar tus recomendaciones de ahorro.",
}


async def get_spending_analysis(
    db: AsyncSession,
    phone_number: str,
    profile_name: str | None = None,
) -> dict:
    user, _ = await get_or_create_user(db, phone_number, profile_name)
    month = active_month()

    budget = await db.scalar(
        select(Budget).where(Budget.user_id == user.id, Budget.month_year == month)
    )
    if budget is None:
        return {
            "status": "no_budget",
            "user_name": user.name,
            "user_code": user.user_code,
            "month": month,
        }

    stmt = (
        select(Expense)
        .options(selectinload(Expense.items))
        .where(Expense.budget_id == budget.id)
        .order_by(Expense.created_at.desc())
    )
    result = await db.execute(stmt)
    expenses = list(result.scalars().all())

    if len(expenses) < 5:
        return {
            "status": "insufficient_data",
            "user_name": user.name,
            "user_code": user.user_code,
            "month": month,
            "count": len(expenses),
            "required": 5,
            "total_budget": budget.total_budget,
            "spent": sum((e.total_amount for e in expenses), Decimal("0")),
        }

    total_spent = sum((e.total_amount for e in expenses), Decimal("0"))
    remaining = budget.total_budget - total_spent

    # Desglose por categoría y detección de gastos hormiga (< $10.000 CLP)
    cat_totals: dict[str, Decimal] = {}
    micro_expenses: list[dict] = []

    for exp in expenses:
        if exp.items:
            for it in exp.items:
                c = (it.category or "otros").lower()
                cat_totals[c] = cat_totals.get(c, Decimal("0")) + it.total_price
                if it.total_price < Decimal("10000"):
                    micro_expenses.append({
                        "name": it.item_name or "Gasto menor",
                        "amount": it.total_price,
                        "category": c,
                    })
        else:
            cat_totals["otros"] = cat_totals.get("otros", Decimal("0")) + exp.total_amount
            if exp.total_amount < Decimal("10000"):
                micro_expenses.append({
                    "name": "Compra menor",
                    "amount": exp.total_amount,
                    "category": "otros",
                })

    # Ranking Top Categorías
    sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)
    top_categories = []
    for cat_name, amt in sorted_cats[:3]:
        pct = (amt / total_spent * Decimal("100")) if total_spent > 0 else Decimal("0")
        label = CATEGORY_LABELS.get(cat_name, cat_name.capitalize())
        top_categories.append({
            "key": cat_name,
            "label": label,
            "amount": amt,
            "percentage": float(pct),
        })

    # Gastos Hormiga
    micro_total = sum((m["amount"] for m in micro_expenses), Decimal("0"))
    micro_count = len(micro_expenses)
    micro_pct = (micro_total / total_spent * Decimal("100")) if total_spent > 0 else Decimal("0")

    # Burn Rate y Proyección de fin de mes
    now = get_now()
    current_day = max(1, now.day)
    _, days_in_month = calendar.monthrange(now.year, now.month)

    daily_burn_rate = total_spent / Decimal(str(current_day))
    projected_total = daily_burn_rate * Decimal(str(days_in_month))
    diff_from_budget = budget.total_budget - projected_total
    is_overspending = projected_total > budget.total_budget

    # Consejo accionable según categoría #1
    top_cat_key = top_categories[0]["key"] if top_categories else "alimentos"
    primary_advice = CATEGORY_ADVICE.get(top_cat_key, CATEGORY_ADVICE["alimentos"])

    return {
        "status": "ok",
        "user_name": user.name,
        "user_code": user.user_code,
        "month": month,
        "current_day": current_day,
        "days_in_month": days_in_month,
        "total_budget": budget.total_budget,
        "total_spent": total_spent,
        "remaining": remaining,
        "spent_percentage": float((total_spent / budget.total_budget * Decimal("100")) if budget.total_budget > 0 else 0),
        "expense_count": len(expenses),
        "top_categories": top_categories,
        "micro_count": micro_count,
        "micro_total": micro_total,
        "micro_percentage": float(micro_pct),
        "daily_burn_rate": daily_burn_rate,
        "projected_total": projected_total,
        "diff_from_budget": diff_from_budget,
        "is_overspending": is_overspending,
        "primary_advice": primary_advice,
    }


def format_spending_analysis(analysis: dict) -> str:
    name = analysis.get("user_name", "Amigo")
    status = analysis.get("status")

    if status == "no_budget":
        return (
            f"[!] *{name}, no tienes un presupuesto configurado este mes.*\n\n"
            "Configura tu presupuesto para poder analizar tus gastos y proyectar tu ahorro:\n"
            "▸ Envía: `Presupuesto 500000`"
        )

    if status == "insufficient_data":
        count = analysis.get("count", 0)
        required = analysis.get("required", 5)
        spent = analysis.get("spent", Decimal("0"))
        return (
            f"■ *DIAGNÓSTICO FINANCIERO* | {name}\n"
            f"▪ Período: {analysis.get('month')}\n"
            "──────────────────────────\n"
            f"Llevas *{count} de {required} compras* registradas este mes (Total: {format_currency(spent)}).\n\n"
            f"▸ *¿Cómo funciona?*\n"
            f"Al completar al menos {required} registros, se desbloqueará tu reporte estadístico con:\n"
            "• Desglose Pareto de tus 3 mayores centros de costo.\n"
            "• Detección de gastos hormiga acumulados.\n"
            "• Proyección de ritmo de gasto (Burn Rate) a fin de mes.\n"
            "• Oportunidades concretas de ahorro en pesos chilenos."
        )

    # Status OK (>= 5 compras)
    top_cats_lines = []
    for idx, c in enumerate(analysis["top_categories"], 1):
        top_cats_lines.append(
            f"• {idx}. *{c['label']}:* {format_currency(c['amount'])} ({c['percentage']:.1f}% de tus gastos)"
        )
    top_cats_str = "\n".join(top_cats_lines) if top_cats_lines else "• Sin categorías suficientes."

    # Bloque de gastos hormiga
    micro_count = analysis["micro_count"]
    micro_total = analysis["micro_total"]
    micro_pct = analysis["micro_percentage"]
    if micro_count >= 2 and micro_total > 0:
        half_saved = micro_total * Decimal("0.5")
        micro_block = (
            f"■ *Fugas Detectadas (Gastos Hormiga):*\n"
            f"• Detectamos *{micro_count} compras menores a $10.000* (delivery, cafés, snacks, mini-viajes) que suman *{format_currency(micro_total)}* ({micro_pct:.1f}% de tu gasto total).\n"
            f"▸ *Potencial de ahorro:* Reduciendo a la mitad estos micro-gastos liberarías *{format_currency(half_saved)}* extra para tu saldo."
        )
    else:
        micro_block = (
            "■ *Fugas Detectadas (Gastos Hormiga):*\n"
            "• ¡Excelente control! No registras fugas significativas por compras menores recurrentes."
        )

    # Proyección y Burn Rate
    daily_rate = analysis["daily_burn_rate"]
    proj_total = analysis["projected_total"]
    diff = analysis["diff_from_budget"]
    is_over = analysis["is_overspending"]

    if is_over:
        rate_block = (
            f"▸ *Alerta de Ritmo de Gasto:* Tu gasto diario promedio es de {format_currency(daily_rate)}/día. "
            f"Al ritmo actual, proyectas gastar *{format_currency(proj_total)}*, superando tu presupuesto mensual por *{format_currency(abs(diff))}*."
        )
    else:
        rate_block = (
            f"✓ *Ritmo de Gasto Saludable:* Tu gasto diario promedio es de {format_currency(daily_rate)}/día. "
            f"Al ritmo actual terminarás el mes dentro del presupuesto con un margen favorable de *{format_currency(diff)}*."
        )

    primary_advice = analysis["primary_advice"]

    return (
        f"■ *DIAGNÓSTICO Y CONSEJOS DE AHORRO* | {name}\n"
        f"▪ Período: {analysis['month']} (Día {analysis['current_day']} de {analysis['days_in_month']})\n"
        "──────────────────────────\n"
        f"▪ Presupuesto: *{format_currency(analysis['total_budget'])}*\n"
        f"▪ Gastado a la fecha: *{format_currency(analysis['total_spent'])}* ({analysis['spent_percentage']:.1f}%)\n"
        f"▪ Saldo disponible: *{format_currency(analysis['remaining'])}*\n\n"
        f"■ *Tus Mayores Centros de Costo:*\n"
        f"{top_cats_str}\n\n"
        f"{micro_block}\n\n"
        f"▸ *Consejo Accionable Personalizado:*\n"
        f"{primary_advice}\n\n"
        f"{rate_block}"
    )


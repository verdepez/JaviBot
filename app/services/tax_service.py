from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.formatters import format_currency
from app.core.timezone import get_current_month, get_now
from app.models import Budget, Company, Expense, ExpenseItem, TaxDocument, User
from app.services.expense_service import active_month


async def get_or_create_company_budget(
    db: AsyncSession,
    user_id: int,
    company_id: int,
    month: str,
    default_amount: Decimal = Decimal("0.00"),
) -> Budget:
    """Obtiene o crea un presupuesto de empresa para el mes."""
    budget = await db.scalar(
        select(Budget).where(
            Budget.user_id == user_id,
            Budget.company_id == company_id,
            Budget.month_year == month,
            Budget.budget_type == "EMPRESA",
        )
    )
    if budget is None:
        budget = Budget(
            user_id=user_id,
            company_id=company_id,
            month_year=month,
            budget_type="EMPRESA",
            total_budget=default_amount,
        )
        db.add(budget)
        await db.flush()
    return budget


async def record_tax_document(
    db: AsyncSession,
    user: User,
    company: Company,
    doc_direction: str,  # "EMITTED" (Venta) o "RECEIVED" (Compra)
    doc_type: str,  # "FACTURA" o "BOLETA"
    total_amount: float | Decimal,
    net_amount: float | Decimal | None = None,
    counterpart: str = "",
    description: str = "",
    raw_input_type: str = "text",
) -> tuple[TaxDocument, dict]:
    """
    Registra un documento tributario según la normativa chilena:
    - FACTURA EMITIDA: Genera Débito Fiscal IVA (19%).
    - FACTURA RECIBIDA: Genera Crédito Fiscal IVA (19% recuperable), el Neto se imputa como gasto.
    - BOLETA RECIBIDA: 100% Gasto Operacional deducible de renta, $0 Crédito Fiscal IVA.
    """
    month = active_month()
    doc_dir = doc_direction.upper()
    doc_t = doc_type.upper()

    # Cálculo matemático de montos en estándar chileno (CLP)
    if doc_t == "FACTURA":
        if net_amount and float(net_amount) > 0:
            net = Decimal(str(net_amount)).quantize(Decimal("1"))
            iva = (net * Decimal("0.19")).quantize(Decimal("1"))
            total = net + iva
        else:
            total = Decimal(str(total_amount)).quantize(Decimal("1"))
            # En Chile Total = Neto * 1.19
            net = (total / Decimal("1.19")).quantize(Decimal("1"))
            iva = total - net
    else:  # BOLETA
        total = Decimal(str(total_amount)).quantize(Decimal("1"))
        net = total
        iva = Decimal("0")

    # Asociar presupuesto de empresa si existe para el mes
    budget = await db.scalar(
        select(Budget).where(
            Budget.user_id == user.id,
            Budget.company_id == company.id,
            Budget.month_year == month,
            Budget.budget_type == "EMPRESA",
        )
    )

    doc = TaxDocument(
        company_id=company.id,
        user_id=user.id,
        budget_id=budget.id if budget else None,
        month_year=month,
        doc_direction=doc_dir,
        doc_type=doc_t,
        counterpart_name=counterpart.strip(),
        net_amount=net,
        iva_amount=iva,
        total_amount=total,
        description=description.strip() or ("Venta" if doc_dir == "EMITTED" else "Compra"),
        raw_input_type=raw_input_type,
    )
    db.add(doc)

    # Si es una compra recibida y hay presupuesto de empresa, registrar el egreso operacional
    if doc_dir == "RECEIVED" and budget is not None:
        # En contabilidad, el gasto real de la factura es el valor Neto (ya que el IVA se recupera)
        expense_amt = net if doc_t == "FACTURA" else total
        expense = Expense(
            budget_id=budget.id,
            raw_input_type=raw_input_type,
            total_amount=expense_amt,
        )
        db.add(expense)
        await db.flush()
        db.add(
            ExpenseItem(
                expense_id=expense.id,
                item_name=f"[{doc_t}] {description or counterpart or 'Compra empresa'}",
                quantity=Decimal("1"),
                unit_price=expense_amt,
                total_price=expense_amt,
                category="trabajo_insumos",
            )
        )

    await db.commit()

    # Obtener el saldo mensual proyectado de IVA tras este registro
    tax_summary = await get_monthly_tax_summary(db, company, month)

    return doc, tax_summary


async def get_monthly_tax_summary(
    db: AsyncSession,
    company: Company,
    month_year: str | None = None,
) -> dict:
    """Calcula la liquidación mensual de IVA y F29 de la empresa."""
    month = month_year or active_month()

    docs_res = await db.execute(
        select(TaxDocument).where(
            TaxDocument.company_id == company.id,
            TaxDocument.month_year == month,
        )
    )
    docs = list(docs_res.scalars().all())

    debito_fiscal = Decimal("0")
    ventas_neto = Decimal("0")
    ventas_total = Decimal("0")
    facturas_emitidas_count = 0

    credito_fiscal = Decimal("0")
    compras_factura_neto = Decimal("0")
    compras_factura_total = Decimal("0")
    facturas_recibidas_count = 0

    gastos_boleta_total = Decimal("0")
    boletas_count = 0

    for d in docs:
        if d.doc_direction == "EMITTED":
            debito_fiscal += d.iva_amount
            ventas_neto += d.net_amount
            ventas_total += d.total_amount
            facturas_emitidas_count += 1
        elif d.doc_direction == "RECEIVED":
            if d.doc_type == "FACTURA":
                credito_fiscal += d.iva_amount
                compras_factura_neto += d.net_amount
                compras_factura_total += d.total_amount
                facturas_recibidas_count += 1
            else:  # BOLETA
                gastos_boleta_total += d.total_amount
                boletas_count += 1

    # Remanente inicial/anterior de crédito fiscal
    remanente_previo = company.initial_tax_credit or Decimal("0")

    # Fórmula oficial F29: Débito Fiscal - Crédito Fiscal - Remanente Previo
    diferencia_iva = debito_fiscal - credito_fiscal - remanente_previo

    if diferencia_iva > 0:
        iva_a_pagar = diferencia_iva
        remanente_nuevo = Decimal("0")
    else:
        iva_a_pagar = Decimal("0")
        remanente_nuevo = abs(diferencia_iva)

    # PPM (Pago Provisional Mensual): tasa % sobre las ventas netas
    ppm_rate = company.ppm_rate or Decimal("0.01")
    ppm_estimado = (ventas_neto * ppm_rate).quantize(Decimal("1"))

    total_f29_estimado = iva_a_pagar + ppm_estimado

    # Presupuesto de empresa si existe
    budget = await db.scalar(
        select(Budget).where(
            Budget.user_id == company.user_id,
            Budget.company_id == company.id,
            Budget.month_year == month,
            Budget.budget_type == "EMPRESA",
        )
    )
    budget_total = budget.total_budget if budget else Decimal("0")
    budget_spent = (
        await db.scalar(
            select(func.coalesce(func.sum(Expense.total_amount), Decimal("0"))).where(
                Expense.budget_id == budget.id
            )
        )
        if budget
        else Decimal("0")
    )
    budget_remaining = budget_total - budget_spent if budget else Decimal("0")

    return {
        "company_name": company.name,
        "company_rut": company.rut,
        "month": month,
        "debito_fiscal": debito_fiscal,
        "ventas_neto": ventas_neto,
        "ventas_total": ventas_total,
        "facturas_emitidas_count": facturas_emitidas_count,
        "credito_fiscal": credito_fiscal,
        "compras_factura_neto": compras_factura_neto,
        "compras_factura_total": compras_factura_total,
        "facturas_recibidas_count": facturas_recibidas_count,
        "gastos_boleta_total": gastos_boleta_total,
        "boletas_count": boletas_count,
        "remanente_previo": remanente_previo,
        "iva_a_pagar": iva_a_pagar,
        "remanente_nuevo": remanente_nuevo,
        "ppm_rate": ppm_rate,
        "ppm_estimado": ppm_estimado,
        "total_f29_estimado": total_f29_estimado,
        "has_budget": budget is not None,
        "budget_total": budget_total,
        "budget_spent": budget_spent,
        "budget_remaining": budget_remaining,
    }


def format_tax_summary(summary: dict) -> str:
    """Genera la plantilla de liquidación tributaria F29 para WhatsApp."""
    comp = summary["company_name"]
    rut = summary["company_rut"]
    month = summary["month"]

    debito = summary["debito_fiscal"]
    credito = summary["credito_fiscal"]
    remanente = summary["remanente_previo"]
    iva_pagar = summary["iva_a_pagar"]
    rem_nuevo = summary["remanente_nuevo"]
    ppm = summary["ppm_estimado"]
    total_f29 = summary["total_f29_estimado"]

    lines = [
        f"■ *LIQUIDACIÓN TRIBUTARIA F29 (ESTIMADA)*",
        f"▪ Empresa: *{comp}* (RUT: `{rut}`)",
        f"▪ Período: *{month}*",
        "──────────────────────────",
        f"📈 *VENTAS Y DÉBITO FISCAL (+IVA):*",
        f"• Facturas emitidas: {summary['facturas_emitidas_count']}",
        f"• Total facturado: {format_currency(summary['ventas_total'])}",
        f"• Neto: {format_currency(summary['ventas_neto'])}",
        f"• *Débito Fiscal (IVA Ventas):* `+{format_currency(debito)}`",
        "──────────────────────────",
        f"📉 *COMPRAS Y CRÉDITO FISCAL (-IVA):*",
        f"• Facturas recibidas: {summary['facturas_recibidas_count']}",
        f"• Total proveedores: {format_currency(summary['compras_factura_total'])}",
        f"• *Crédito Fiscal (IVA Compras):* `-{format_currency(credito)}`",
        f"• Boletas de gasto operacional: {format_currency(summary['gastos_boleta_total'])} (sin crédito IVA)",
    ]

    if remanente > 0:
        lines.append(f"• Remanente a favor anterior: `-{format_currency(remanente)}`")

    lines.append("──────────────────────────")

    if iva_pagar > 0:
        lines.append(f"🏛️ *IVA por Pagar al SII:* *{format_currency(iva_pagar)}*")
    else:
        lines.append(f"✓ *IVA por Pagar:* $0 (Sin pago de IVA este mes)")
        lines.append(f"💰 *Nuevo Remanente a Favor para el próximo mes:* *{format_currency(rem_nuevo)}*")

    lines.append(f"📌 *PPM Estimado ({float(summary['ppm_rate']*100):.1f}% s/Neto):* {format_currency(ppm)}")
    lines.append(f"💵 *TOTAL ESTIMADO F29:* *{format_currency(total_f29)}*")

    if summary["has_budget"]:
        lines.append("──────────────────────────")
        lines.append(f"▪ Presupuesto mensual empresa: {format_currency(summary['budget_total'])}")
        lines.append(f"▪ Gastos operacionales acumulados: {format_currency(summary['budget_spent'])}")
        lines.append(f"▪ Presupuesto disponible: *{format_currency(summary['budget_remaining'])}*")

    return "\n".join(lines)


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
    doc_type: str,  # "FACTURA", "FACTURA_EXENTA" o "BOLETA"
    total_amount: float | Decimal,
    net_amount: float | Decimal | None = None,
    counterpart: str = "",
    description: str = "",
    is_exempt: bool = False,
    raw_input_type: str = "text",
) -> tuple[TaxDocument, dict]:
    """
    Registra un documento tributario según la normativa chilena:
    - FACTURA AFECTA EMITIDA: Genera Débito Fiscal IVA (19%).
    - FACTURA EXENTA EMITIDA (DTE 34): $0 Débito Fiscal IVA.
    - FACTURA AFECTA RECIBIDA: Genera Crédito Fiscal IVA (19% recuperable), el Neto se imputa como gasto.
    - FACTURA EXENTA RECIBIDA (DTE 34): 100% Gasto Operacional deducible, $0 Crédito Fiscal IVA.
    - BOLETA RECIBIDA: 100% Gasto Operacional deducible de renta, $0 Crédito Fiscal IVA.
    """
    month = active_month()
    doc_dir = doc_direction.upper()
    doc_t = doc_type.upper()

    # Determinar si el documento es exento
    is_doc_exempt = is_exempt or (doc_t == "FACTURA_EXENTA")
    if not is_doc_exempt and doc_dir == "EMITTED" and getattr(company, "is_exempt_issuer", False) and doc_t != "FACTURA_AFECTA":
        is_doc_exempt = True

    if is_doc_exempt:
        doc_t = "FACTURA_EXENTA"

    # Cálculo matemático de montos en estándar chileno (CLP)
    if is_doc_exempt:
        total = Decimal(str(total_amount)).quantize(Decimal("1"))
        net = total
        iva = Decimal("0")
    elif doc_t in {"FACTURA", "FACTURA_AFECTA"}:
        doc_t = "FACTURA"
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

    # Obtener o crear presupuesto de empresa para el mes
    budget = await get_or_create_company_budget(
        db=db,
        user_id=user.id,
        company_id=company.id,
        month=month,
        default_amount=Decimal("0.00"),
    )

    doc = TaxDocument(
        company_id=company.id,
        user_id=user.id,
        budget_id=budget.id if budget else None,
        month_year=month,
        doc_direction=doc_dir,
        doc_type=doc_t,
        counterpart_name=counterpart.strip(),
        is_exempt=is_doc_exempt,
        net_amount=net,
        iva_amount=iva,
        total_amount=total,
        description=description.strip() or ("Venta" if doc_dir == "EMITTED" else "Compra"),
        raw_input_type=raw_input_type,
    )
    db.add(doc)

    # Si es una venta emitida por la empresa:
    # Las facturas emitidas por la empresa son abono a presupuesto empresa descontando el IVA que va a IVA débito
    if doc_dir == "EMITTED" and budget is not None:
        net_credit = net if not is_doc_exempt else total
        budget.total_budget += net_credit

    # Si es una compra recibida y hay presupuesto de empresa, registrar el egreso operacional
    elif doc_dir == "RECEIVED" and budget is not None:
        # En contabilidad chilena:
        # - Factura afecta: El gasto neto es imputable (el IVA 19% se recupera vía crédito fiscal)
        # - Factura exenta: 100% es gasto operacional deducible (no genera crédito fiscal)
        # - Boleta: 100% es gasto operacional deducible
        if is_doc_exempt:
            expense_amt = total
            concept_tag = "[FACTURA EXENTA]"
        elif doc_t == "FACTURA":
            expense_amt = net
            concept_tag = "[FACTURA]"
        else:
            expense_amt = total
            concept_tag = "[BOLETA]"

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
                item_name=f"{concept_tag} {description or counterpart or 'Compra empresa'}",
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
    ventas_afectas_neto = Decimal("0")
    ventas_afectas_total = Decimal("0")
    facturas_emitidas_afectas_count = 0

    ventas_exentas_total = Decimal("0")
    facturas_emitidas_exentas_count = 0

    credito_fiscal = Decimal("0")
    compras_factura_neto = Decimal("0")
    compras_factura_total = Decimal("0")
    facturas_recibidas_afectas_count = 0

    compras_exentas_total = Decimal("0")
    facturas_recibidas_exentas_count = 0

    gastos_boleta_total = Decimal("0")
    boletas_count = 0

    for d in docs:
        if d.doc_direction == "EMITTED":
            if getattr(d, "is_exempt", False) or d.doc_type == "FACTURA_EXENTA":
                ventas_exentas_total += d.total_amount
                facturas_emitidas_exentas_count += 1
            else:
                debito_fiscal += d.iva_amount
                ventas_afectas_neto += d.net_amount
                ventas_afectas_total += d.total_amount
                facturas_emitidas_afectas_count += 1
        elif d.doc_direction == "RECEIVED":
            if getattr(d, "is_exempt", False) or d.doc_type == "FACTURA_EXENTA":
                compras_exentas_total += d.total_amount
                facturas_recibidas_exentas_count += 1
            elif d.doc_type in {"FACTURA", "FACTURA_AFECTA"}:
                credito_fiscal += d.iva_amount
                compras_factura_neto += d.net_amount
                compras_factura_total += d.total_amount
                facturas_recibidas_afectas_count += 1
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

    # PPM (Pago Provisional Mensual): tasa % sobre los ingresos operacionales brutos (neto afecto + exento)
    ppm_rate = company.ppm_rate or Decimal("0.01")
    base_ppm = ventas_afectas_neto + ventas_exentas_total
    ppm_estimado = (base_ppm * ppm_rate).quantize(Decimal("1"))

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
        "is_exempt_issuer": getattr(company, "is_exempt_issuer", False),
        "month": month,
        "debito_fiscal": debito_fiscal,
        "ventas_afectas_neto": ventas_afectas_neto,
        "ventas_afectas_total": ventas_afectas_total,
        "ventas_neto": ventas_afectas_neto,
        "ventas_exentas_total": ventas_exentas_total,
        "ventas_total": ventas_afectas_total + ventas_exentas_total,
        "facturas_emitidas_count": facturas_emitidas_afectas_count,
        "facturas_emitidas_exentas_count": facturas_emitidas_exentas_count,
        "credito_fiscal": credito_fiscal,
        "compras_factura_neto": compras_factura_neto,
        "compras_factura_total": compras_factura_total,
        "facturas_recibidas_count": facturas_recibidas_afectas_count,
        "compras_exentas_total": compras_exentas_total,
        "facturas_recibidas_exentas_count": facturas_recibidas_exentas_count,
        "gastos_boleta_total": gastos_boleta_total,
        "boletas_count": boletas_count,
        "remanente_previo": remanente_previo,
        "iva_a_pagar": iva_a_pagar,
        "remanente_nuevo": remanente_nuevo,
        "ppm_rate": ppm_rate,
        "base_ppm": base_ppm,
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
    is_ex_issuer = summary.get("is_exempt_issuer", False)

    debito = summary["debito_fiscal"]
    credito = summary["credito_fiscal"]
    remanente = summary["remanente_previo"]
    iva_pagar = summary["iva_a_pagar"]
    rem_nuevo = summary["remanente_nuevo"]
    ppm = summary["ppm_estimado"]
    total_f29 = summary["total_f29_estimado"]

    issuer_badge = " _[Emisor Exento DTE 34]_" if is_ex_issuer else ""

    lines = [
        f"■ *LIQUIDACIÓN TRIBUTARIA F29 (ESTIMADA)*",
        f"▪ Empresa: *{comp}* (RUT: `{rut}`){issuer_badge}",
        f"▪ Período: *{month}*",
        "──────────────────────────",
        f"📈 *VENTAS Y DÉBITO FISCAL:*",
    ]

    has_ventas_afectas = summary.get("facturas_emitidas_count", 0) > 0
    has_ventas_exentas = summary.get("facturas_emitidas_exentas_count", 0) > 0

    if has_ventas_afectas or has_ventas_exentas:
        if has_ventas_afectas:
            v_af_total = summary.get("ventas_afectas_total", summary.get("ventas_total", Decimal("0")))
            v_af_neto = summary.get("ventas_afectas_neto", summary.get("ventas_neto", Decimal("0")))
            lines.append(f"• Facturas afectas (19% IVA): {summary.get('facturas_emitidas_count', 0)} ({format_currency(v_af_total)})")
            lines.append(f"  └ Neto: {format_currency(v_af_neto)} | Débito: `+{format_currency(debito)}`")
        if has_ventas_exentas:
            v_ex_total = summary.get("ventas_exentas_total", Decimal("0"))
            lines.append(f"• Facturas exentas (DTE 34): {summary.get('facturas_emitidas_exentas_count', 0)} ({format_currency(v_ex_total)}) [0% IVA]")
        lines.append(f"• *Total Débito Fiscal:* `+{format_currency(debito)}`")
    else:
        lines.append("• Sin ventas registradas en el período.")

    lines.append("──────────────────────────")
    lines.append(f"📉 *COMPRAS Y CRÉDITO FISCAL:*")

    has_compras_afectas = summary.get("facturas_recibidas_count", 0) > 0
    has_compras_exentas = summary.get("facturas_recibidas_exentas_count", 0) > 0
    has_boletas = summary.get("boletas_count", 0) > 0

    if has_compras_afectas or has_compras_exentas or has_boletas:
        if has_compras_afectas:
            c_af_total = summary.get("compras_factura_total", Decimal("0"))
            lines.append(f"• Facturas afectas: {summary.get('facturas_recibidas_count', 0)} ({format_currency(c_af_total)})")
            lines.append(f"  └ *Crédito Fiscal (19%):* `-{format_currency(credito)}`")
        if has_compras_exentas:
            c_ex_total = summary.get("compras_exentas_total", Decimal("0"))
            lines.append(f"• Facturas exentas recibidas: {summary.get('facturas_recibidas_exentas_count', 0)} ({format_currency(c_ex_total)}) [$0 Crédito]")
        if has_boletas:
            b_total = summary.get("gastos_boleta_total", Decimal("0"))
            lines.append(f"• Boletas gasto operacional: {summary.get('boletas_count', 0)} ({format_currency(b_total)}) [Sin Crédito]")
    else:
        lines.append("• Sin compras ni gastos registrados en el período.")

    if remanente > 0:
        lines.append(f"• Remanente a favor anterior: `-{format_currency(remanente)}`")

    lines.append("──────────────────────────")

    if iva_pagar > 0:
        lines.append(f"🏛️ *IVA por Pagar al SII:* *{format_currency(iva_pagar)}*")
    else:
        lines.append(f"✓ *IVA por Pagar:* {format_currency(iva_pagar)} (Sin pago de IVA este mes)")
        lines.append(f"💰 *Nuevo Remanente a Favor para el próximo mes:* *{format_currency(rem_nuevo)}*")

    lines.append(f"📌 *PPM Estimado ({float(summary['ppm_rate']*100):.1f}% s/Ventas):* {format_currency(ppm)}")
    lines.append(f"💵 *TOTAL ESTIMADO F29:* *{format_currency(total_f29)}*")

    if summary["has_budget"]:
        lines.append("──────────────────────────")
        lines.append(f"▪ Presupuesto mensual empresa: {format_currency(summary['budget_total'])}")
        lines.append(f"▪ Gastos operacionales acumulados: {format_currency(summary['budget_spent'])}")
        lines.append(f"▪ Presupuesto disponible: *{format_currency(summary['budget_remaining'])}*")

    return "\n".join(lines)


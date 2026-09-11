from decimal import Decimal


def format_currency(amount: float | Decimal | int | None, phone: str | None = None) -> str:
    """
    Formatea el monto en formato monetario chileno:
    - Símbolo '$' al inicio a la izquierda.
    - Miles separados con punto '.'.
    - Decimales separados con coma ',' (ej: $1.000.000,00).
    """
    if amount is None:
        val = 0.0
    else:
        val = float(amount)

    us_str = f"{val:,.2f}"
    cl_str = us_str.replace(".", "@").replace(",", ".").replace("@", ",")
    return f"${cl_str}"

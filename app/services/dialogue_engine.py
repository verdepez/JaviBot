import random
from app.core.formatters import format_currency


class DialogueEngine:
    """
    Motor de Diálogo y Personalidad de Pam Anota 🐶:
    Gestiona saludos, agradecimientos, despedidas y respuestas conversacionales locales (0 tokens)
    junto con confirmaciones naturales y variadas para evitar respuestas rígidas o mecánicas.
    """

    EXPENSE_TEMPLATES = [
        "¡Anotadísimo 🐾! Registré *{item}* por *{amount}*.\n▪ Saldo disponible: *{remaining}*",
        "¡Listo, {user_name}! Ya dejé anotado *{item}* ({amount}) 🦴.\n▪ Te van quedando: *{remaining}*",
        "Guardado 🐾. Desconté *{amount}* en *{item}*.\n▪ Saldo actual: *{remaining}*",
        "¡Listo el registro 🐶! *{item}* por *{amount}*.\n▪ Saldo disponible: *{remaining}*",
        "¡Anotado en la libreta 🐾! *{item}* ({amount}).\n▪ Saldo restante: *{remaining}*",
    ]

    COMPANY_EXPENSE_TEMPLATES = [
        "✓ Registrado para *{company_name}* 🐾: *{item}* por *{amount}*.\n▪ Saldo operacional: *{remaining}*",
        "✓ ¡Anotado en *{company_name}* 🐶! *{item}* ({amount}).\n▪ Saldo disponible: *{remaining}*",
        "✓ Guardado para *{company_name}* 🦴: *{amount}* en *{item}*.\n▪ Saldo disponible: *{remaining}*",
    ]

    BUDGET_SETUP_TEMPLATES = [
        "✓ ¡Presupuesto mensual configurado{ctx_label}, {user_name} 🐶! Cuentas con *{budget}* para este mes.",
        "✓ ¡Anotadísimo tu presupuesto{ctx_label} 🐾! Total disponible: *{budget}*.",
        "✓ ¡Listo, {user_name}! He fijado tu presupuesto{ctx_label} en *{budget}* 🦴. Estaré atenta a cada gasto.",
    ]

    BUDGET_ADDED_TEMPLATES = [
        "✓ ¡Presupuesto ampliado con éxito{ctx_label}, {user_name} 🐾!\n▪ Sumé: *+{added}*\n▪ Nuevo total: *{total}*\n▪ Saldo disponible: *{remaining}*",
        "✓ ¡Abono registrado{ctx_label} 🐶!\n▪ Agregaste: *+{added}*\n▪ Presupuesto total: *{total}*\n▪ Te quedan: *{remaining}* 🦴",
        "✓ ¡Listo! Amplié tu presupuesto{ctx_label} en *+{added}* 🐾.\n▪ Total del mes: *{total}*\n▪ Saldo disponible: *{remaining}*",
    ]

    GREETING_RESPONSES = [
        "¡Hola {user_name}! 🐾 Soy *Pam Anota*, tu perrita guardiana de finanzas. ¿Qué anotamos hoy? Puedes dictarme un gasto, enviarme una boleta o consultar tu `saldo`.",
        "¡Wena {user_name}! 🐶 Aquí Pam lista con su libreta. ¿Hiciste alguna compra o quieres revisar cómo va tu `saldo`?",
        "¡Hola, {user_name}! 🐾 Lista para anotar. Escríbeme cualquier gasto (ej: `Almuerzo 5000` o `10 lucas bencina`), boleta o escribe `ayuda` para ver opciones.",
    ]

    THANK_YOU_RESPONSES = [
        "¡De nada, {user_name}! 🐶🐾 Aquí sigo vigilando tus números para que no se escape ni un peso.",
        "¡Para eso estoy, {user_name}! 🦴 Cualquier otra compra o boleta, solo dímela y la anoto al tiro.",
        "¡Un gusto ayudarte! 🐾 Si necesitas saber cuánto te queda, solo escríbeme `saldo`.",
    ]

    WHO_ARE_YOU_RESPONSES = [
        "¡Soy *Pam Anota*! 🐶🐾 Tu asistente financiera inteligente de WhatsApp. Te ayudo a controlar tu presupuesto personal, registrar boletas y facturas DTE para tu empresa y calcular tu IVA F29 automáticamente.\n\n▸ _Si aún me tienes guardada con mi nombre anterior, escribe `contacto` y te enviaré mi tarjeta para actualizarme con un toque._",
    ]

    GOODBYE_RESPONSES = [
        "¡Hasta luego, {user_name}! 🐾 Que tengas un excelente día. Aquí estaré cuando hagas tu próxima compra.",
        "¡Chao {user_name}! 🐶 Recuerda avisarme si compras algo para mantener tu saldo al día.",
    ]

    @classmethod
    def try_respond_chitchat(cls, text_str: str, user_name: str = "Amigo") -> str | None:
        """Detecta saludos, agradecimientos y preguntas de identidad sin gastar tokens de IA."""
        if not text_str:
            return None

        clean = text_str.lower().strip()
        # Remover puntuaciones terminales
        clean = clean.rstrip(".!?,;: ")

        # 1. Saludos
        greeting_keywords = {
            "hola", "ola", "buenas", "buen dia", "buenos dias", "buenas tardes",
            "buenas noches", "wena", "wena pam", "hola pam", "que tal", "cómo estás",
            "como estas", "que onda", "holi", "holis"
        }
        if clean in greeting_keywords or any(clean.startswith(f"{g} ") for g in ("hola", "wena", "buenas")):
            tpl = random.choice(cls.GREETING_RESPONSES)
            return tpl.format(user_name=user_name)

        # 2. Agradecimientos
        thanks_keywords = {
            "gracias", "muchas gracias", "muchas grax", "grax", "vale", "vale pam",
            "agradecido", "te pasaste", "buena pam", "gracias pam", "te amo", "linda pam",
            "buenisima", "buenísima", "bakan", "bacan", "genial pam"
        }
        if clean in thanks_keywords or any(clean.startswith(f"{t} ") for t in ("gracias", "muchas gracias", "vale")):
            tpl = random.choice(cls.THANK_YOU_RESPONSES)
            return tpl.format(user_name=user_name)

        # 3. Identidad / Quién eres
        identity_keywords = {
            "quien eres", "quién eres", "como te llamas", "cómo te llamas",
            "que haces", "qué haces", "cual es tu nombre", "cuál es tu nombre",
            "presentate", "preséntate", "quien sos", "quién sos"
        }
        if clean in identity_keywords or any(clean.startswith(f"{ik} ") for ik in ("quien eres", "quién eres", "como te llamas", "cómo te llamas")):
            return random.choice(cls.WHO_ARE_YOU_RESPONSES)

        # 4. Despedidas
        goodbye_keywords = {
            "chao", "adios", "adiós", "hasta luego", "nos vemos", "chaito", "bye"
        }
        if clean in goodbye_keywords:
            tpl = random.choice(cls.GOODBYE_RESPONSES)
            return tpl.format(user_name=user_name)

        return None

    @classmethod
    def format_expense_reply(
        cls,
        user_name: str,
        item_name: str,
        amount: float,
        remaining: float,
        is_company: bool = False,
        company_name: str | None = None,
    ) -> str:
        """Genera una confirmación de gasto dinámica y variada con personalidad."""
        amt_str = format_currency(amount)
        rem_str = format_currency(remaining)
        clean_item = item_name or "Gasto"

        if is_company and company_name:
            tpl = random.choice(cls.COMPANY_EXPENSE_TEMPLATES)
            return tpl.format(
                company_name=company_name,
                item=clean_item,
                amount=amt_str,
                remaining=rem_str,
            )

        tpl = random.choice(cls.EXPENSE_TEMPLATES)
        return tpl.format(
            user_name=user_name,
            item=clean_item,
            amount=amt_str,
            remaining=rem_str,
        )

    @classmethod
    def format_budget_reply(
        cls,
        user_name: str,
        total_budget: float,
        remaining: float,
        is_addition: bool = False,
        added_amount: float = 0.0,
        is_company: bool = False,
        company_name: str | None = None,
    ) -> str:
        """Genera una confirmación de presupuesto configurado o ampliado."""
        ctx_label = f" para *{company_name}*" if (is_company and company_name) else ""

        if is_addition:
            tpl = random.choice(cls.BUDGET_ADDED_TEMPLATES)
            return tpl.format(
                user_name=user_name,
                ctx_label=ctx_label,
                added=format_currency(added_amount),
                total=format_currency(total_budget),
                remaining=format_currency(remaining),
            )

        tpl = random.choice(cls.BUDGET_SETUP_TEMPLATES)
        return tpl.format(
            user_name=user_name,
            ctx_label=ctx_label,
            budget=format_currency(total_budget),
        )


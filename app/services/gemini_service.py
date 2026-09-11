import re
from typing import Any
import asyncio
from google import genai
from google.genai import errors, types

from app.core.config import settings
from app.schemas import ExtractionResult, ExtractedItem


SYSTEM_INSTRUCTION = """Eres un extractor financiero para un bot de WhatsApp en español operando primordialmente en Chile (CLP, código país 56).
Analiza texto, audio o imágenes de compras. Devuelve únicamente el JSON que cumple el esquema indicado.
- CONVENCIÓN MONETARIA DE CHILE (CLP):
  - La moneda es el Peso Chileno (símbolo $).
  - En Chile los miles se separan con punto '.' (ej: 45.983 o 1.000.000) o coma ',' en teclados de smartphones (ej: 45,983).
  - Si un monto tiene 3 dígitos tras un punto o coma (ej: 45.983 o 45,983 o 12,000 o 3,500), son cuarenta y cinco mil pesos (45983), doce mil pesos (12000), tres mil quinientos (3500). NUNCA interpretes esos 3 dígitos como decimales.
  - El peso chileno no usa centavos en transacciones diarias. Si aparecen decimales, van tras una coma con 1 o 2 dígitos.
  - Devuelve siempre los montos 'total_spent', 'budget_amount' y 'unit_price' en su valor real completo (ej: 45983.0, 12000.0).
- Las imágenes de boletas, recibos, facturas, tickets o vouchers son SIEMPRE compras o gastos realizados (NUNCA consultas). Para cualquier imagen, fija siempre is_expense_list_inquiry=false e is_balance_inquiry=false.
- Si el mensaje de texto o audio configura presupuesto, marca is_budget_setup=true y extrae budget_amount.
- Si el usuario pregunta expresamente por la lista, detalle o desglose de sus compras/gastos (ej: 'cuales son mis compras', 'muestra los gastos', 'en que gaste', 'mis compras', 'ver gastos'), marca is_expense_list_inquiry=true. Si menciona un mes específico (ej: 'de agosto', 'del mes pasado', '2026-08'), extrae target_month en formato 'YYYY-MM'. Si no menciona mes, deja target_month=null.
- Si el usuario pregunta por su saldo general, balance o cuánto le queda (ej: 'cuanto me queda', 'saldo', 'resumen', 'cuanto tengo'), marca is_balance_inquiry=true.
- Para gastos o compras realizadas, desglosa cada ítem y calcula total_spent. No inventes precios: usa 0 cuando un dato no sea legible. Categoriza en alimentos, hogar, transporte, salud, ocio, servicios u otros. Palabras como 'insumos', 'máquinas', 'herramientas', 'materiales' o 'equipos' son gastos de compra válidos (categoría otros). Si un total existe, úsalo como total_spent.
- Si el mensaje no contiene gastos, presupuesto ni consultas, devuelve total_spent=0, items=[], is_budget_setup=false, is_balance_inquiry=false, is_expense_list_inquiry=false."""


def parse_amount(val_str: str) -> float | None:
    clean = val_str.replace("$", "").replace(" ", "").strip()
    if not clean:
        return None

    # Caso 1: Tiene tanto puntos como comas. Ej: "1.000.000,50" o "1,000,000.50"
    if "." in clean and "," in clean:
        last_dot = clean.rfind(".")
        last_comma = clean.rfind(",")
        if last_comma > last_dot:
            # Formato chileno/latino: puntos son miles, coma es decimal (1.000.000,50)
            clean = clean.replace(".", "").replace(",", ".")
        else:
            # Formato anglosajón: comas son miles, punto es decimal (1,000,000.50)
            clean = clean.replace(",", "")

    # Caso 2: Solo tiene puntos. Ej: "1.000.000" o "45.983" o "45.50"
    elif "." in clean:
        parts = clean.split(".")
        if len(parts) > 2:
            # Múltiples puntos son miles: 1.000.000 -> 1000000
            clean = "".join(parts)
        elif len(parts) == 2:
            # Si tras el punto hay 3 dígitos: 45.000 o 45.983 -> Son miles en Chile
            if len(parts[1]) == 3:
                clean = parts[0] + parts[1]
            else:
                clean = clean

    # Caso 3: Solo tiene comas. Ej: "1,000,000" o "45,983" o "45,50"
    elif "," in clean:
        parts = clean.split(",")
        if len(parts) > 2:
            # Múltiples comas son miles: 1,000,000 -> 1000000
            clean = "".join(parts)
        elif len(parts) == 2:
            # Si tras la coma hay exactamente 3 dígitos: 45,983 o 12,000 -> Son miles (teclado móvil o formato miles)
            if len(parts[1]) == 3:
                clean = parts[0] + parts[1]
            else:
                # 1 o 2 dígitos tras la coma son decimales: 45,50 -> 45.50
                clean = parts[0] + "." + parts[1]

    try:
        val = float(clean)
        return val if val > 0 else None
    except ValueError:
        return None


def categorize_item(name: str) -> str:
    n = name.lower()
    if any(w in n for w in ["almuerzo", "cena", "comida", "desayuno", "pan", "super", "supermercado", "carne", "verdura", "bebida", "cafe", "café", "restaurant"]):
        return "alimentos"
    if any(w in n for w in ["uber", "taxi", "bencina", "gasolina", "combustible", "metro", "micro", "bus", "peaje", "estacionamiento", "pasaje"]):
        return "transporte"
    if any(w in n for w in ["farmacia", "medico", "médico", "doctor", "remedio", "medicamento", "clinica", "clínica", "hospital", "dentista"]):
        return "salud"
    if any(w in n for w in ["luz", "agua", "gas", "internet", "arriendo", "plan", "celular", "cuenta", "gastos comunes"]):
        return "servicios"
    if any(w in n for w in ["insumo", "maquina", "máquina", "herramienta", "material", "taller", "repuesto"]):
        return "otros"
    if any(w in n for w in ["cine", "bar", "cerveza", "carrete", "fiesta", "juego", "salida"]):
        return "ocio"
    return "otros"


def try_parse_text_locally(text: str) -> ExtractionResult | None:
    raw = text.strip()
    norm = raw.lower().strip(".,¡!¿?")

    # 1. Configuración de Presupuesto
    # Ej: "presupuesto 500000", "mi presupuesto mensual es de 500.000", "presupuesto: $400.000"
    m_budget = re.search(r"^(?:mi\s+)?presupuesto(?:\s+mensual)?(?:\s+(?:es\s+de|es|de|:))?\s*\$?\s*([0-9][0-9.,\s]*)$", norm)
    if m_budget:
        amt = parse_amount(m_budget.group(1))
        if amt:
            return ExtractionResult(
                is_budget_setup=True,
                budget_amount=amt,
                total_spent=0,
                items=[],
            )

    # 2. Compras con verbos: "Compré insumos por 12000 pesos", "Compre maquinas por 3500", "Gaste 15000 en bencina", "Pagué 20000 de luz"
    m_verb1 = re.search(r"^(?:compr[eé]|pagu[eé]|gast[eé])\s+(.+?)\s+(?:por|en|de)\s+\$?\s*([0-9][0-9.,\s]*?)(?:\s*(?:pesos|clp|\$))?$", norm)
    if m_verb1:
        desc = m_verb1.group(1).strip()
        amt = parse_amount(m_verb1.group(2))
        if amt and desc:
            cat = categorize_item(desc)
            return ExtractionResult(
                is_budget_setup=False,
                total_spent=amt,
                items=[ExtractedItem(name=desc.capitalize(), quantity=1, unit_price=amt, total=amt, category=cat)],
            )

    m_verb2 = re.search(r"^(?:compr[eé]|pagu[eé]|gast[eé])\s+\$?\s*([0-9][0-9.,\s]*?)(?:\s*(?:pesos|clp|\$))?\s+(?:por|en|de)\s+(.+?)$", norm)
    if m_verb2:
        amt = parse_amount(m_verb2.group(1))
        desc = m_verb2.group(2).strip()
        if amt and desc:
            cat = categorize_item(desc)
            return ExtractionResult(
                is_budget_setup=False,
                total_spent=amt,
                items=[ExtractedItem(name=desc.capitalize(), quantity=1, unit_price=amt, total=amt, category=cat)],
            )

    # 3. Formato simple: "Almuerzo 4500", "Insumos 12000", "Uber 5200"
    m_simple1 = re.search(r"^([a-záéíóúñA-ZÁÉÍÓÚÑ\s]{2,30})\s+\$?\s*([0-9][0-9.,]*)(?:\s*(?:pesos|clp|\$))?$", raw)
    if m_simple1:
        desc = m_simple1.group(1).strip()
        if desc.lower() not in {"presupuesto", "saldo", "compras", "gastos", "ayuda", "admin", "autorizar", "bloquear"}:
            amt = parse_amount(m_simple1.group(2))
            if amt:
                cat = categorize_item(desc)
                return ExtractionResult(
                    is_budget_setup=False,
                    total_spent=amt,
                    items=[ExtractedItem(name=desc.capitalize(), quantity=1, unit_price=amt, total=amt, category=cat)],
                )

    # 4. Formato inverso: "4500 almuerzo", "12000 en insumos"
    m_simple2 = re.search(r"^\$?\s*([0-9][0-9.,]*)\s+(?:en\s+|de\s+)?([a-záéíóúñA-ZÁÉÍÓÚÑ\s]{2,30})(?:\s*(?:pesos|clp|\$))?$", raw)
    if m_simple2:
        amt = parse_amount(m_simple2.group(1))
        desc = m_simple2.group(2).strip()
        if amt and desc.lower() not in {"pesos", "clp", "dolares"}:
            cat = categorize_item(desc)
            return ExtractionResult(
                is_budget_setup=False,
                total_spent=amt,
                items=[ExtractedItem(name=desc.capitalize(), quantity=1, unit_price=amt, total=amt, category=cat)],
            )

    return None


class GeminiExtractor:
    def __init__(self) -> None:
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def extract(
        self,
        input_type: str,
        content: str | bytes,
        mime_type: str | None = None,
    ) -> ExtractionResult:
        contents: Any
        if input_type == "text":
            contents = content if isinstance(content, str) else content.decode()
            # 1. Intentar extracción local inmediata (0 cuota de API, respuesta en milisegundos)
            local_res = try_parse_text_locally(contents)
            if local_res is not None:
                print(f"--> [EXTRACTOR] Extracción local rápida (0 consumo Gemini): gasto={local_res.total_spent}, presupuesto={local_res.budget_amount}")
                return local_res
        else:
            if not isinstance(content, bytes) or not mime_type:
                raise ValueError("El contenido multimodal requiere bytes y mime_type")
            clean_mime = mime_type.split(";")[0].strip()
            contents = [types.Part.from_bytes(data=content, mime_type=clean_mime)]

        # 2. Si no es texto simple o requiere multimodal, consultar Gemini con tolerancia a fallos y fallback
        candidate_models = [
            settings.gemini_model,
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-2.0-flash-lite",
        ]
        models_to_try = []
        for m in candidate_models:
            if m and m not in models_to_try:
                models_to_try.append(m)

        last_err = None
        for model_name in models_to_try:
            for attempt in range(2):
                try:
                    response = await self.client.aio.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_INSTRUCTION,
                            response_mime_type="application/json",
                            response_schema=ExtractionResult,
                        ),
                    )
                    if response.parsed:
                        return ExtractionResult.model_validate(response.parsed)
                    if response.text:
                        return ExtractionResult.model_validate_json(response.text)
                    raise ValueError("Gemini no devolvió una extracción válida")
                except (errors.APIError, Exception) as err:
                    last_err = err
                    err_msg = str(err)
                    print(f"--> [EXTRACTOR] Error con modelo {model_name} (intento {attempt+1}): {err_msg}")
                    # Si es 429 Quota Exceeded, saltar inmediatamente al siguiente modelo de la cadena
                    if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "quota" in err_msg.lower():
                        print(f"--> [EXTRACTOR] Cuota agotada en {model_name}. Probando modelo alternativo...")
                        break
                    if attempt == 0 and ("503" in err_msg or "UNAVAILABLE" in err_msg):
                        await asyncio.sleep(1.5)
                        continue
                    break

        raise last_err or ValueError("Gemini no devolvió una extracción válida")
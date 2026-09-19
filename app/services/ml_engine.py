import math
import re
import unicodedata
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import get_now
from app.models import LearnedPattern, LearnedVocabulary
from app.schemas import ExtractedItem, ExtractionResult

# Umbral de similitud coseno para considerar coincidencia segura (0.0 a 1.0)
SIMILARITY_THRESHOLD = 0.82

# Cache en memoria de patrones y vocabulario para ejecuciones < 1 ms
_PATTERNS_CACHE: list[dict[str, Any]] = []
_VOCABULARY_CACHE: dict[str, str] = {}
_CACHE_LAST_LOADED: datetime | None = None


def normalize_text_for_ml(text_str: str) -> str:
    """Normaliza texto eliminando puntuación superflua, acentos y espacios repetidos."""
    if not text_str:
        return ""
    # Descomposición unicode para remover tildes preservando caracteres base
    nfkd = unicodedata.normalize("NFKD", text_str.lower())
    without_accents = "".join([c for c in nfkd if not unicodedata.combining(c)])
    # Limpiar caracteres que no sean alfanuméricos ni espacios
    cleaned = re.sub(r"[^\w\s]", " ", without_accents)
    return re.sub(r"\s+", " ", cleaned).strip()


class SubwordVectorizer:
    """
    Vectorizador TF-IDF subpalabra ligero basado en n-gramas de caracteres (3-4) y palabras.
    Permite tolerancia a faltas ortográficas, modismos chilenos y variaciones tipográficas.
    """

    @staticmethod
    def get_features(text_str: str) -> list[str]:
        norm = normalize_text_for_ml(text_str)
        tokens = norm.split()
        features: list[str] = []

        # 1. Unigramas de palabras
        for t in tokens:
            features.append(f"w:{t}")

        # 2. Bigramas de palabras
        for i in range(len(tokens) - 1):
            features.append(f"bi:{tokens[i]}_{tokens[i+1]}")

        # 3. N-gramas de caracteres (3 y 4)
        for t in tokens:
            t_padded = f"<{t}>"
            for n in (3, 4):
                for i in range(len(t_padded) - n + 1):
                    features.append(f"c{n}:{t_padded[i:i+n]}")

        return features

    @classmethod
    def vectorize(cls, text_str: str) -> dict[str, float]:
        """Genera un vector TF normalizado por norma L2 (vector unitario)."""
        features = cls.get_features(text_str)
        if not features:
            return {}
        counts = Counter(features)
        # Calcular norma L2
        norm = math.sqrt(sum(cnt * cnt for cnt in counts.values()))
        if norm == 0:
            return {}
        return {feat: cnt / norm for feat, cnt in counts.items()}

    @staticmethod
    def cosine_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
        """Calcula el producto punto entre dos vectores unitarios (similitud coseno)."""
        if not vec1 or not vec2:
            return 0.0
        # Iterar sobre el vector más pequeño para máxima velocidad
        if len(vec1) > len(vec2):
            vec1, vec2 = vec2, vec1
        return sum(val * vec2.get(feat, 0.0) for feat, val in vec1.items())


def parse_chilean_amount(text_str: str) -> tuple[float | None, str]:
    """
    Detecta y extrae montos monetarios cotidianos en Chile (números, lucas, gambas, palos)
    y devuelve una tupla con (monto_extraido, texto_reemplazado_con_{amount}).
    """
    norm = text_str.strip()

    # 1. Jerga chilena explícita con multiplicadores
    # Palos (millones): "2 palos", "1 palo", "un palo"
    palo_match = re.search(r"\b(?:un|(\d+))\s*(?:palos?)\b", norm, re.IGNORECASE)
    if palo_match:
        qty = float(palo_match.group(1)) if palo_match.group(1) else 1.0
        amt = qty * 1_000_000.0
        replaced = re.sub(r"\b(?:un|\d+)\s*(?:palos?)\b", "{amount}", norm, count=1, flags=re.IGNORECASE)
        return amt, replaced

    # Lucas (miles): "15 lucas", "1 luca", "una luca", "15lucas"
    luca_match = re.search(r"\b(?:una|(\d+(?:[.,]\d+)?))\s*(?:lucas?)\b", norm, re.IGNORECASE)
    if luca_match:
        qty_str = luca_match.group(1)
        if qty_str:
            qty = float(qty_str.replace(",", "."))
        else:
            qty = 1.0
        amt = qty * 1000.0
        replaced = re.sub(r"\b(?:una|\d+(?:[.,]\d+)?)\s*(?:lucas?)\b", "{amount}", norm, count=1, flags=re.IGNORECASE)
        return amt, replaced

    # Gambas (cientos): "5 gambas", "una gamba"
    gamba_match = re.search(r"\b(?:una|(\d+))\s*(?:gambas?)\b", norm, re.IGNORECASE)
    if gamba_match:
        qty = float(gamba_match.group(1)) if gamba_match.group(1) else 1.0
        amt = qty * 100.0
        replaced = re.sub(r"\b(?:una|\d+)\s*(?:gambas?)\b", "{amount}", norm, count=1, flags=re.IGNORECASE)
        return amt, replaced

    # 2. Formato con separador de miles: "15.000", "1.500.000", "45,983"
    # Captura números con separadores
    miles_match = re.search(r"(?:\$|\b)(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?)(?:\b|$)", norm)
    if miles_match:
        raw_num = miles_match.group(1)
        # En Chile, si hay exactamente 3 dígitos tras punto/coma, son miles
        clean_num = raw_num.replace(".", "").replace(",", "")
        # Si tiene decimales reales (ej: 15.000,50 -> 15000.50)
        if "," in raw_num and len(raw_num.split(",")[-1]) <= 2:
            parts = raw_num.split(",")
            clean_num = parts[0].replace(".", "") + "." + parts[1]
        try:
            amt = float(clean_num)
            replaced = norm[:miles_match.start()] + "{amount}" + norm[miles_match.end():]
            return amt, replaced
        except ValueError:
            pass

    # 3. Entero estándar: "15000", "$4500"
    num_match = re.search(r"(?:\$|\b)(\d{2,9})(?:\b|$)", norm)
    if num_match:
        try:
            amt = float(num_match.group(1))
            replaced = norm[:num_match.start()] + "{amount}" + norm[num_match.end():]
            return amt, replaced
        except ValueError:
            pass

    return None, norm


def extract_item_concept(text_with_amount_token: str) -> str:
    """Extrae el concepto o ítem limpiando palabras de parada y el token {amount}."""
    cleaned = text_with_amount_token.replace("{amount}", "").strip()

    # 1. Si hay preposiciones clave ("en", "de", "por", "para"), el ítem suele estar inmediatamente después
    prep_match = re.search(r"\b(?:en|de|por|para)\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ0-9\s]+)$", cleaned, re.IGNORECASE)
    if prep_match:
        cand = prep_match.group(1).strip()
        if cand:
            return cand

    # 2. Si hay vocabulario conocido en el texto, priorizarlo
    tokens = normalize_text_for_ml(cleaned).split()
    for t in tokens:
        if t in _VOCABULARY_CACHE:
            return t

    # 3. Quitar palabras de acción cotidianas y de parada
    stop_words = {
        "se", "me", "le", "nos", "les", "fueron", "fue", "iba", "iban", "gaste", "gasté",
        "compre", "compré", "pague", "pagué", "pago", "anota", "anotame", "anótame", "anotar",
        "transferi", "transferí", "en", "de", "por", "para", "un", "una", "unos", "unas",
        "el", "la", "los", "las", "al", "del", "factura", "boleta", "emitida", "recibida",
        "exenta", "compra", "venta", "puse", "costo", "costó", "salio", "salió"
    }
    cand_tokens = [w for w in tokens if w not in stop_words and len(w) > 1]
    item_name = " ".join(cand_tokens).strip()
    return item_name or "Varios"


async def load_patterns_from_db(db: AsyncSession) -> list[dict[str, Any]]:
    """Carga patrones y precomputa vectores para matching instantáneo."""
    global _PATTERNS_CACHE, _CACHE_LAST_LOADED
    stmt = select(LearnedPattern)
    result = await db.execute(stmt)
    patterns = result.scalars().all()

    loaded = []
    vectorizer = SubwordVectorizer()
    for p in patterns:
        vec = vectorizer.vectorize(p.pattern_template)
        loaded.append({
            "id": p.id,
            "pattern_template": p.pattern_template,
            "intent": p.intent,
            "category_default": p.category_default,
            "doc_direction": p.doc_direction,
            "doc_type": p.doc_type,
            "is_exempt": p.is_exempt,
            "confidence_score": p.confidence_score,
            "hit_count": p.hit_count,
            "vector": vec,
        })
    _PATTERNS_CACHE = loaded
    _CACHE_LAST_LOADED = get_now()
    return loaded


async def load_vocabulary_from_db(db: AsyncSession) -> dict[str, str]:
    """Carga vocabulario aprendido en cache de categorías."""
    global _VOCABULARY_CACHE
    stmt = select(LearnedVocabulary)
    result = await db.execute(stmt)
    vocab = result.scalars().all()
    _VOCABULARY_CACHE = {v.term.lower(): v.canonical_category for v in vocab}
    return _VOCABULARY_CACHE


def resolve_category_from_vocab(item_name: str, fallback: str = "otros") -> str:
    """Busca en el vocabulario adquirido la categoría más probable."""
    tokens = normalize_text_for_ml(item_name).split()
    for t in tokens:
        if t in _VOCABULARY_CACHE:
            return _VOCABULARY_CACHE[t]
    return fallback


async def try_parse_text_ml(text_str: str, db: AsyncSession | None) -> ExtractionResult | None:
    """
    Motor ML local (Zero External Tokens):
    1. Extrae monto y generaliza el texto a una plantilla con {amount}.
    2. Vectoriza la plantilla con n-gramas de subpalabras TF-IDF.
    3. Compara con los patrones aprendidos en la base de datos mediante similitud coseno.
    4. Si similitud >= SIMILARITY_THRESHOLD, resuelve la intención y llena slots.
    """
    if not text_str or not db:
        return None

    # Extraer monto
    amount, template_candidate = parse_chilean_amount(text_str)
    if amount is None:
        return None

    # Normalizar plantilla candidata
    # Reemplazar posibles ítems por {item} para matching
    item_concept = extract_item_concept(template_candidate)

    # Construir variante con {item} si se extrajo un concepto
    template_with_item = template_candidate
    if item_concept and item_concept.lower() != "varios":
        for word in item_concept.split():
            template_with_item = re.sub(re.escape(word), "{item}", template_with_item, flags=re.IGNORECASE)
        template_with_item = re.sub(r"(\{item\}\s*)+", "{item} ", template_with_item).strip()

    # Asegurar que el cache de patrones esté cargado
    global _PATTERNS_CACHE, _VOCABULARY_CACHE
    if not _PATTERNS_CACHE:
        await load_patterns_from_db(db)
        await load_vocabulary_from_db(db)

    cand_vec = SubwordVectorizer.vectorize(template_candidate)
    cand_item_vec = SubwordVectorizer.vectorize(template_with_item)

    best_match: dict[str, Any] | None = None
    highest_sim = 0.0

    for pat in _PATTERNS_CACHE:
        pat_vec = pat["vector"]
        sim1 = SubwordVectorizer.cosine_similarity(cand_vec, pat_vec)
        sim2 = SubwordVectorizer.cosine_similarity(cand_item_vec, pat_vec)
        sim = max(sim1, sim2)

        if sim > highest_sim:
            highest_sim = sim
            best_match = pat

    if best_match and highest_sim >= SIMILARITY_THRESHOLD:
        intent = best_match["intent"]
        category = resolve_category_from_vocab(item_concept, best_match.get("category_default") or "otros")

        print(
            f"--> [ML ENGINE] Coincidencia local ({highest_sim:.2f} >= {SIMILARITY_THRESHOLD}): "
            f"Patrón='{best_match['pattern_template']}' Intent={intent} Monto={amount} Ítem='{item_concept}'"
        )

        # Actualizar contador de uso del patrón en segundo plano
        pat_id = best_match["id"]
        try:
            await db.execute(
                text("UPDATE learned_patterns SET hit_count = hit_count + 1, last_used_at = NOW() WHERE id = :pid"),
                {"pid": pat_id}
            )
            await db.commit()
        except Exception as e:
            print(f"--> [ML ENGINE] Error menor actualizando hit_count: {e}")

        if intent == "EXPENSE":
            return ExtractionResult(
                is_budget_setup=False,
                is_budget_addition=False,
                total_spent=amount,
                items=[
                    ExtractedItem(
                        name=item_concept or "Varios",
                        quantity=1,
                        unit_price=amount,
                        total=amount,
                        category=category,
                    )
                ],
            )
        elif intent == "BUDGET":
            is_addition = any(kw in text_str.lower() for kw in ("agregar", "sumar", "abono", "mas", "aumentar"))
            return ExtractionResult(
                is_budget_setup=True,
                is_budget_addition=is_addition,
                budget_amount=amount,
                total_spent=0.0,
                items=[],
            )
        elif intent == "TAX_DOC":
            return ExtractionResult(
                is_budget_setup=False,
                total_spent=amount,
                tax_doc_direction=best_match.get("doc_direction") or "RECEIVED",
                tax_doc_type=best_match.get("doc_type") or "FACTURA",
                is_exempt=best_match.get("is_exempt", False),
                items=[
                    ExtractedItem(
                        name=item_concept or best_match.get("doc_type", "Documento"),
                        quantity=1,
                        unit_price=amount,
                        total=amount,
                        category="empresa",
                    )
                ],
            )

    return None


async def learn_from_external_result(
    db: AsyncSession,
    raw_text: str,
    result: ExtractionResult,
) -> None:
    """
    Bucle de Retroalimentación y Adquisición de Lenguaje Continuo:
    Cuando la IA externa (Groq o Gemini) procesa con éxito un mensaje que no fue reconocido
    localmente, abstrae la estructura del mensaje y la guarda en learned_patterns y
    learned_vocabulary para que las próximas veces se responda localmente en 0 tokens.
    """
    if not raw_text or not isinstance(raw_text, str):
        return

    # Si es consulta simple de saldo o lista, no es un patrón de registro
    if result.is_balance_inquiry or result.is_expense_list_inquiry or result.is_tax_inquiry:
        return

    # 1. Determinar intención
    intent: str
    doc_dir = result.tax_doc_direction
    doc_type = result.tax_doc_type
    is_exempt = result.is_exempt
    cat_default = "otros"

    if result.is_budget_setup or result.is_budget_addition:
        intent = "BUDGET"
        amount = result.budget_amount or 0.0
    elif doc_type:
        intent = "TAX_DOC"
        amount = result.total_spent
        cat_default = "empresa"
    elif result.total_spent > 0:
        intent = "EXPENSE"
        amount = result.total_spent
        if result.items:
            cat_default = result.items[0].category
    else:
        return

    if amount <= 0:
        return

    # 2. Generalizar el patrón reemplazando el monto y el ítem
    _, template = parse_chilean_amount(raw_text)
    if "{amount}" not in template:
        return

    # Reemplazar nombre de ítems por {item}
    if result.items and result.items[0].name and result.items[0].name.lower() != "varios":
        item_name = result.items[0].name.lower()
        # Aprender término en el vocabulario si tiene categoría válida
        try:
            await db.execute(
                text(
                    """
                    INSERT INTO learned_vocabulary (term, canonical_category, multiplier)
                    VALUES (:term, :cat, 1.0)
                    ON CONFLICT (term) DO NOTHING;
                    """
                ),
                {"term": item_name, "cat": result.items[0].category},
            )
            # Actualizar cache en memoria
            _VOCABULARY_CACHE[item_name] = result.items[0].category
        except Exception:
            pass

        for word in item_name.split():
            if len(word) > 2:
                template = re.sub(re.escape(word), "{item}", template, flags=re.IGNORECASE)
        template = re.sub(r"(\{item\}\s*)+", "{item} ", template).strip()

    # Normalizar espacios del template
    clean_template = re.sub(r"\s+", " ", template.strip().lower())
    if len(clean_template) < 5 or clean_template == "{amount}":
        return

    # 3. Guardar o actualizar en learned_patterns
    try:
        await db.execute(
            text(
                """
                INSERT INTO learned_patterns (
                    pattern_template, intent, category_default, doc_direction, doc_type, is_exempt, hit_count, confidence_score
                )
                VALUES (
                    :template, :intent, :cat, :doc_dir, :doc_type, :is_exempt, 1, 0.90
                )
                ON CONFLICT (pattern_template) DO UPDATE SET
                    hit_count = learned_patterns.hit_count + 1,
                    last_used_at = NOW();
                """
            ),
            {
                "template": clean_template,
                "intent": intent,
                "cat": cat_default,
                "doc_dir": doc_dir,
                "doc_type": doc_type,
                "is_exempt": is_exempt,
            },
        )
        await db.commit()
        print(f"--> [ML ENGINE] Nuevo patrón aprendido y absorbido: '{clean_template}' [{intent}]")

        # Recargar patrones en cache para el próximo mensaje
        await load_patterns_from_db(db)
    except Exception as exc:
        print(f"--> [ML ENGINE] Error guardando patrón aprendido: {exc}")

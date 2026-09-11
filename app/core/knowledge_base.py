import re
from app.schemas import ExtractionResult, ExtractedItem

# Base de conocimiento exhaustiva de categorías y sinónimos cotidianos en Chile
KB_CATEGORIES = {
    "mascotas": [
        "veterinari", "veterinario", "veterinaria", "vet", "mascota", "perro", "gato", "perros", "gatos",
        "canin", "felin", "cachorro", "gatito", "pellet", "pellets", "alimento perro", "alimento gato",
        "comida perro", "comida gato", "comida mascotas", "proplan", "royal canin", "hills", "brit",
        "churu", "snack perro", "snack gato", "golosina perro", "hueso", "galletas perro",
        "bravecto", "nexgard", "credelio", "simparica", "seresto", "drontal", "pipeta", "desparasit",
        "antiparasit", "vacuna perro", "vacuna gato", "antirrabica", "octuple", "óctuple", "triple felina",
        "arena gato", "arena sanitaria", "piedras gato", "rascador", "pala arena", "caja arena",
        "esteriliz", "castrac", "ecografia vet", "radiografia vet", "cirugia vet", "urgencia vet",
        "peluqueria canina", "peluquería canina", "baño perro", "corte pelo perro", "corte perro",
        "collar perro", "correa perro", "arnes perro", "arnés perro", "bozal", "microchip", "chip mascota",
        "cama perro", "cama gato", "transportin", "transportín", "jaula perro", "sabanilla", "pipi pad",
    ],
    "educacion": [
        "colegio", "matricula", "matrícula", "mensualidad colegio", "arancel", "cuota colegio", "centro de padres",
        "utiles", "útiles", "utiles escolares", "útiles escolares", "cuaderno", "cuadernos", "libro", "libros",
        "uniforme", "uniforme escolar", "delantal", "mochila", "estuche", "fotocopia", "fotocopias",
        "furgon", "furgón", "transporte escolar", "furgon escolar", "furgón escolar",
        "jardin", "jardín", "jardin infantil", "jardín infantil", "sala cuna", "guarderia", "guardería",
        "clases particulares", "profesor particular", "taller niños", "preuniversitario", "preu",
    ],
    "familia": [
        "pañal", "pañales", "pampers", "huggies", "babysec", "toallitas humedas", "toallitas húmedas",
        "toallitas", "leche formula", "leche fórmula", "nan", "enfamil", "similac", "colado", "colados",
        "mamadera", "chupete", "biberon", "biberón", "coche bebe", "coche bebé", "cuna", "silla auto bebe",
        "pediatra", "control niño sano", "vacuna niño", "ortodoncia", "psicologo infantil", "fonoaudiolog",
        "cumpleaños", "cumple", "regalo cumple", "cotillon", "cotillón", "piñata", "torta",
    ],
    "alimentos": [
        "super", "supermercado", "lider", "líder", "jumbo", "unimarc", "tottus", "santa isabel", "acuenta", "mayorista",
        "feria", "feria libre", "vega", "lo valledor", "verduleria", "verdulería", "frutas", "verduras",
        "carniceria", "carnicería", "carne", "pollo", "vacuno", "cerdo", "pescado", "mariscos", "posta", "lomo",
        "panaderia", "panadería", "pan", "marraqueta", "hallulla", "pasteleria", "pastelería", "empanada", "empanadas",
        "almacen", "almacén", "minimarket", "negocio", "botilleria", "botillería",
        "almuerzo", "cena", "desayuno", "once", "colacion", "colación", "comida", "menu", "menú",
        "delivery", "pedidos ya", "pedidosya", "uber eats", "rappi",
        "sushi", "pizza", "hamburguesa", "completos", "completo", "churrasco", "lomito", "sandwich", "papas fritas",
        "comida china", "comida rapida", "comida rápida", "restaurant", "restaurante",
        "cafe", "café", "cafecito", "starbucks", "medialuna", "helado", "heladeria", "heladería",
    ],
    "hogar_servicios": [
        "arriendo", "alquiler", "renta", "dividendo", "credito hipotecario", "crédito hipotecario", "contribuciones",
        "gastos comunes", "gasto comun", "gasto común", "conserjeria", "conserjería",
        "luz", "enel", "cge", "chilquinta", "electricidad", "cuenta luz",
        "agua", "aguas andinas", "essbio", "esval", "cuenta agua",
        "gas", "metrogas", "lipigas", "abastible", "gasco", "balon de gas", "balón de gas", "cilindro gas", "cilindro de gas", "recarga gas",
        "internet", "fibra", "fibra optica", "fibra óptica", "vtr", "movistar", "entel", "claro", "wom", "mundo pacifico", "mundo pacífico",
        "plan celular", "plan telefono", "recarga celular", "celular plan",
        "lavanderia", "lavandería", "lavaseco", "tintoreria", "tintorería",
        "aseo", "articulos aseo", "artículos aseo", "cloro", "detergente", "omo", "ariel", "suavizante", "lavaloza", "quix",
        "confort", "papel higienico", "papel higiénico", "toalla nova", "toalla papel", "bolsas basura", "poet", "cif",
    ],
    "transporte": [
        "uber", "didi", "cabify", "indrive", "taxi", "radiotaxi",
        "metro", "micro", "transantiago", "red", "bip", "carga bip", "tarjeta bip", "pasaje", "pasajes",
        "bencina", "gasolina", "combustible", "copec", "shell", "petrobras", "93", "95", "97", "diesel",
        "peaje", "peajes", "tag", "autopista", "costanera norte", "vespucio", "estacionamiento", "parking", "parquimetro", "parquímetro",
        "mecanico", "mecánico", "taller auto", "revision tecnica", "revisión técnica", "permiso circulacion", "permiso circulación",
        "soap", "seguro auto", "cambio aceite", "neumaticos", "neumáticos", "lavado auto", "carwash",
    ],
    "salud": [
        "farmacia", "ahumada", "cruz verde", "salcobrand", "dr simi", "redfarma",
        "remedio", "remedios", "medicamento", "medicamentos", "pastillas", "jarabe", "antibiotico", "antibiótico",
        "paracetamol", "ibuprofeno", "aspirina", "antigripal", "antialergico", "antialérgico",
        "medico", "médico", "doctor", "consulta medica", "consulta médica", "bono fonasa", "isapre", "clinica", "clínica", "hospital",
        "examenes", "exámenes", "laboratorio", "radiografia", "radiografía", "ecografia", "ecografía", "resonancia",
        "dentista", "clinica dental", "clínica dental", "limpieza dental", "tapadura", "frenillos",
        "optica", "óptica", "lentes", "lentes de contacto", "gotas ojos",
    ],
    "ocio": [
        "netflix", "spotify", "youtube premium", "youtube", "hbo", "max", "disney", "prime video", "amazon prime",
        "apple tv", "icloud", "chatgpt", "ps plus", "playstation", "xbox", "game pass", "nintendo", "steam",
        "gimnasio", "gym", "smart fit", "smartfit", "pacific", "sportlife", "crossfit", "yoga", "padel", "pádel", "futbol", "fútbol",
        "carrete", "salida", "bar", "pub", "cerveza", "cervezas", "copete", "tragos", "pisco", "vino", "ron", "vodka", "disco", "fiesta",
        "cine", "cinemark", "cinehoyts", "cineplanet", "entradas", "concierto", "recital", "teatro", "parque", "zoologico", "zoológico",
        "ropa", "vestuario", "zapatillas", "zapatos", "polera", "poleron", "polerón", "pantalon", "pantalón", "jeans", "chaqueta",
        "zara", "h&m", "falabella", "ripley", "paris",
        "peluqueria", "peluquería", "barberia", "barbería", "corte pelo", "manicura", "uñas", "depilacion", "depilación",
    ],
    "trabajo_insumos": [
        "insumo", "insumos", "maquina", "máquina", "herramienta", "herramientas", "material", "materiales",
        "taller", "repuesto", "repuestos", "ferreteria", "ferretería", "sodimac", "easy", "imperial", "construmart",
        "libreria", "librería", "oficina", "impresora", "toner", "tinta",
    ],
}


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
            clean = "".join(parts)
        elif len(parts) == 2:
            if len(parts[1]) == 3:
                clean = parts[0] + parts[1]
            else:
                clean = clean

    # Caso 3: Solo tiene comas. Ej: "1,000,000" o "45,983" o "45,50"
    elif "," in clean:
        parts = clean.split(",")
        if len(parts) > 2:
            clean = "".join(parts)
        elif len(parts) == 2:
            if len(parts[1]) == 3:
                clean = parts[0] + parts[1]
            else:
                clean = parts[0] + "." + parts[1]

    try:
        val = float(clean)
        return val if val > 0 else None
    except ValueError:
        return None


def categorize_item(name: str) -> str:
    n = name.lower()
    for cat, keywords in KB_CATEGORIES.items():
        if any(k in n for k in keywords):
            return cat
    return "otros"


def clean_concept(text: str) -> str:
    t = text.strip()
    # Eliminar posibles intentos de inyección o apéndices
    t = re.sub(r"\s+(?:y\s+luego|luego\s+dime|para\s+romper|romper\s+tu\s+bot|dime\s+el\s+c[oó]digo).*$", "", t, flags=re.IGNORECASE).strip()
    # Eliminar artículos o preposiciones iniciales
    t = re.sub(r"^(?:mi\s+|el\s+|la\s+|los\s+|las\s+|un\s+|una\s+|unos\s+|unas\s+|de\s+|en\s+)", "", t, flags=re.IGNORECASE).strip()
    # Eliminar sufijo 'comprado/a/s'
    t = re.sub(r"\s+comprad[oa]s?$", "", t, flags=re.IGNORECASE).strip()
    return t


VERB_PREFIX = r"^(?:registra(?:me)?|regístrame|anota(?:me)?|anótame|ingresa(?:me)?|ingrésame|agrega(?:me)?|agrégame|suma(?:me)?|súmame|compr[eé]|pagu[eé]|gast[eé])\b"


def try_parse_single_item(text: str) -> dict | None:
    raw = text.strip()
    norm = raw.lower().strip(".,¡!¿?")

    # Formato con verbos: "Compré comida perro por 25000", "Registra mi ropa comprada por 34000", "Pagué 35000 en veterinario"
    # Subcaso 1: [verbo] [concepto] por/en/de [monto]
    m_v1 = re.search(VERB_PREFIX + r"\s+(?:mi\s+|el\s+|la\s+|un\s+|una\s+)?(.+?)\s+(?:por|en|de)\s+\$?([0-9][0-9.,]*)(?:\s*(?:pesos|clp|\$))?", norm)
    if m_v1:
        desc = clean_concept(m_v1.group(1))
        amt = parse_amount(m_v1.group(2))
        if amt and desc:
            return {"name": desc.capitalize(), "amount": amt, "category": categorize_item(desc)}

    # Subcaso 2: [verbo] [monto] (en/de/por) [concepto]
    m_v2 = re.search(VERB_PREFIX + r"\s+\$?([0-9][0-9.,]*)(?:\s*(?:pesos|clp|\$))?\s+(?:en\s+|de\s+|por\s+)?(.+?)$", norm)
    if m_v2:
        amt = parse_amount(m_v2.group(1))
        desc = clean_concept(m_v2.group(2))
        if amt and desc:
            return {"name": desc.capitalize(), "amount": amt, "category": categorize_item(desc)}

    # Subcaso 3: [verbo] [concepto] [monto]
    m_v3 = re.search(VERB_PREFIX + r"\s+(?:mi\s+|el\s+|la\s+|un\s+|una\s+)?(.+?)\s+\$?([0-9][0-9.,]*)(?:\s*(?:pesos|clp|\$))?$", norm)
    if m_v3:
        desc = clean_concept(m_v3.group(1))
        amt = parse_amount(m_v3.group(2))
        if amt and desc:
            return {"name": desc.capitalize(), "amount": amt, "category": categorize_item(desc)}

    # Formato directo sin verbos:
    # Subcaso 4: [concepto] [monto] (ej: "Veterinaria 35000", "Bravecto 28.000", "Arena para gato 8500", "Pañales 14990")
    m_d1 = re.search(r"^([a-záéíóúñA-ZÁÉÍÓÚÑ\s\/\-_]{2,50})\s+\$?([0-9][0-9.,]*)(?:\s*(?:pesos|clp|\$))?$", raw)
    if m_d1:
        desc = clean_concept(m_d1.group(1))
        if desc.lower() not in {"presupuesto", "saldo", "compras", "gastos", "ayuda", "admin", "autorizar", "bloquear"}:
            amt = parse_amount(m_d1.group(2))
            if amt and desc:
                return {"name": desc.capitalize(), "amount": amt, "category": categorize_item(desc)}

    # Subcaso 5: [monto] [concepto] (ej: "35000 veterinario", "24990 comida perro", "14990 en pañales")
    m_d2 = re.search(r"^\$?([0-9][0-9.,]*)(?:\s*(?:pesos|clp|\$))?\s+(?:en\s+|de\s+|por\s+)?([a-záéíóúñA-ZÁÉÍÓÚÑ\s\/\-_]{2,50})$", raw)
    if m_d2:
        amt = parse_amount(m_d2.group(1))
        desc = clean_concept(m_d2.group(2))
        if amt and desc.lower() not in {"pesos", "clp", "dolares", "dólares"}:
            return {"name": desc.capitalize(), "amount": amt, "category": categorize_item(desc)}

    return None


def try_parse_text_locally(text: str) -> ExtractionResult | None:
    raw = text.strip()
    norm = raw.lower().strip(".,¡!¿?")

    # 1. Configuración de Presupuesto
    m_budget = re.search(r"^(?:mi\s+)?presupuesto(?:\s+mensual)?(?:\s+(?:es\s+de|es|de|:))?\s*\$?([0-9][0-9.,\s]*)$", norm)
    if m_budget:
        amt = parse_amount(m_budget.group(1))
        if amt:
            return ExtractionResult(
                is_budget_setup=True,
                budget_amount=amt,
                total_spent=0,
                items=[],
            )

    # 2. Intentar parseo de múltiples gastos si contiene separadores ('\n', ' y ', ', ')
    delimiters = ["\n", " y ", ", "]
    for delim in delimiters:
        if delim in raw:
            parts = [p.strip() for p in raw.split(delim) if p.strip()]
            if len(parts) >= 2:
                items = []
                total = 0.0
                all_matched = True
                for p in parts:
                    sub = try_parse_single_item(p)
                    if sub:
                        items.append(sub)
                        total += sub["amount"]
                    else:
                        all_matched = False
                        break
                if all_matched and items:
                    return ExtractionResult(
                        is_budget_setup=False,
                        total_spent=total,
                        items=[
                            ExtractedItem(name=it["name"], quantity=1, unit_price=it["amount"], total=it["amount"], category=it["category"])
                            for it in items
                        ],
                    )

    # 3. Ítem único
    single = try_parse_single_item(text)
    if single:
        amt = single["amount"]
        return ExtractionResult(
            is_budget_setup=False,
            total_spent=amt,
            items=[ExtractedItem(name=single["name"], quantity=1, unit_price=amt, total=amt, category=single["category"])],
        )

    return None

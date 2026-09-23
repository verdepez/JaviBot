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


def parse_amount(val_str: str, allow_zero: bool = False) -> float | None:
    clean = val_str.replace("$", "").replace(" ", "").strip()
    if not clean:
        return None

    # Escudo Anti-RUT: un formato de RUT chileno (ej: 8670330-0 o termina en -[0-9kK]) NUNCA es un monto monetario
    if "-" in clean and re.search(r"-[0-9kK]$", clean, re.IGNORECASE):
        return None

    clean_l = clean.lower()
    if "luca" in clean_l:

        sub = clean_l.replace("lucas", "").replace("luca", "").strip()
        try:
            val_lucas = (float(sub) if sub else 1.0) * 1000.0
            return val_lucas if val_lucas > 0 or allow_zero else None
        except ValueError:
            pass

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
        if allow_zero:
            return val if val >= 0 else None
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

    # Escudo Anti-RUT y Comandos Administrativos/Tributarios
    # Un texto con formato de RUT chileno o que gestiona empresas jamás debe registrarse como gasto
    if re.search(r"\b[0-9]{1,2}(?:\.[0-9]{3}){2}-[0-9kK]\b|\b[0-9]{7,8}-[0-9kK]\b", raw):
        return None
    if any(k in norm for k in ("empresa", "remanente", "presupuesto", "factura emitida", "factura exenta", "corregir", "corrige", "modificar", "modifica")):
        return None

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
        if desc.lower() not in {"presupuesto", "saldo", "compras", "gastos", "ayuda", "admin", "autorizar", "bloquear", "factura", "boleta", "empresa", "modo", "iva", "f29"}:
            amt = parse_amount(m_d1.group(2))
            if amt and desc:
                return {"name": desc.capitalize(), "amount": amt, "category": categorize_item(desc)}

    # Subcaso 5: [monto] [concepto] (ej: "35000 veterinario", "24990 comida perro", "14990 en pañales")
    m_d2 = re.search(r"^\$?([0-9][0-9.,]*)(?:\s*(?:pesos|clp|\$))?\s+(?:en\s+|de\s+|por\s+)?([a-záéíóúñA-ZÁÉÍÓÚÑ\s\/\-_]{2,50})$", raw)
    if m_d2:
        amt = parse_amount(m_d2.group(1))
        desc = clean_concept(m_d2.group(2))
        if amt and desc.lower() not in {"pesos", "clp", "dolares", "dólares", "factura", "boleta", "empresa", "modo", "iva", "f29"}:
            return {"name": desc.capitalize(), "amount": amt, "category": categorize_item(desc)}

    return None


def try_parse_company_command(raw: str) -> ExtractionResult | None:
    """
    Parsea comandos de creación o edición de empresa de forma ultra flexible (0 tokens).
    Soporta variantes como:
    - 'crear empresa PomPomSpA rut 8.670.330-0 remanente 100000 presupuesto 750000'
    - 'crear nueva empresa PomPomSpA 8670330-0 remanente 100000 presupuesto 750000'
    - 'corrige empresa PomPomSpA rut 8.670.330-0 remanente 100000 presupuesto 750000'
    - 'modificar empresa PomPomSpA 8670330-0 presupuesto 800000'
    - 'nueva empresa MiPyme rut 76123456-K'
    """
    norm = raw.strip()
    prefix_match = re.match(
        r"^(?:crear(?:\s+(?:una\s+)?nueva|\s+una)?|agregar(?:\s+nueva)?|nueva|registrar(?:\s+nueva)?|corregir|corrige|modificar|modifica|editar|edita|actualizar|actualiza)\s+empresa\s+(.+)$",
        norm,
        re.IGNORECASE,
    )
    if not prefix_match:
        return None

    body = prefix_match.group(1).strip()

    # 1. Buscar si es exenta
    c_exempt = bool(re.search(r"\b(?:es\s+)?(?:exenta|no\s+afecta)\b", body, re.IGNORECASE))

    # 2. Buscar remanente
    m_rem = re.search(
        r"\b(?:remanente|cr[eé]dito(?:\s+fiscal)?)\s*(?:inicial)?(?:\s+(?:es\s+de|es|de|:))?\s*\$?([0-9][0-9\.,\s]*(?:\s*lucas?)?)",
        body,
        re.IGNORECASE,
    )
    c_rem = parse_amount(m_rem.group(1), allow_zero=True) if m_rem else 0.0

    # 3. Buscar presupuesto
    m_bud = re.search(
        r"\bpresupuesto(?:\s+mensual)?(?:\s+(?:es\s+de|es|de|:))?\s*\$?([0-9][0-9\.,\s]*(?:\s*lucas?)?)",
        body,
        re.IGNORECASE,
    )
    c_bud = parse_amount(m_bud.group(1), allow_zero=True) if m_bud else None

    # 4. Buscar RUT
    c_rut = None
    rut_start_idx = None
    m_rut_kw = re.search(r"\brut\s*:?\s*([0-9kK\.\-]+)", body, re.IGNORECASE)
    if m_rut_kw:
        c_rut = m_rut_kw.group(1).strip()
        rut_start_idx = m_rut_kw.start()
    else:
        m_rut_pat = re.search(r"\b([0-9]{1,2}(?:\.[0-9]{3}){2}-[0-9kK]|[0-9]{7,8}-[0-9kK])\b", body)
        if m_rut_pat:
            c_rut = m_rut_pat.group(1).strip()
            rut_start_idx = m_rut_pat.start()

    # 5. Extraer nombre de la empresa (texto antes del RUT o parámetros)
    cut_indices = []
    if rut_start_idx is not None:
        cut_indices.append(rut_start_idx)
    if m_rem:
        cut_indices.append(m_rem.start())
    if m_bud:
        cut_indices.append(m_bud.start())
    m_ex = re.search(r"\b(?:es\s+)?(?:exenta|no\s+afecta)\b", body, re.IGNORECASE)
    if m_ex:
        cut_indices.append(m_ex.start())

    if cut_indices:
        first_cut = min(cut_indices)
        c_name = body[:first_cut].strip()
    else:
        c_name = body.strip()

    c_name = re.sub(r"\s+(?:rut|con\s+rut|r\.u\.t\.)\s*$", "", c_name, flags=re.IGNORECASE).strip()

    if not c_name:
        return None

    return ExtractionResult(
        is_company_creation=True,
        company_name=c_name,
        company_rut=c_rut,
        initial_credit=c_rem if c_rem is not None else 0.0,
        budget_amount=c_bud,
        company_is_exempt=c_exempt,
    )


def try_parse_text_locally(text: str) -> ExtractionResult | None:
    raw = text.strip()
    norm = raw.lower().strip(".,¡!¿?")

    # 0. Creación y gestión/edición de empresas
    comp_res = try_parse_company_command(raw)
    if comp_res:
        return comp_res

    # 0a. Configurar empresa activa como exenta o afecta
    m_set_exempt = re.search(
        r"^(?:empresa|mi\s+empresa|configurar\s+empresa|configurar)?\s*(?:es\s+)?(?:factura\s+)?exenta\s+(si|no|sí|true|false)$",
        norm,
    )
    if not m_set_exempt:
        m_set_exempt = re.search(
            r"^(?:mi\s+empresa\s+)(es\s+exenta|no\s+es\s+exenta)$",
            norm,
        )
    if m_set_exempt:
        val_str = m_set_exempt.group(1).strip().lower()
        is_ex = val_str in {"si", "sí", "true", "es exenta"}
        return ExtractionResult(set_company_exempt=is_ex)

    # 0a-2. Corregir o actualizar remanente de IVA de la empresa
    # Con empresa explícita: "corregir remanente empresa Consultora 350000", "corrige remanente Consultora 350000"
    m_rem_comp = re.search(
        r"^(?:corregir|corrige|modificar|modifica|cambiar|cambia|fijar|fija|ajustar|ajusta|actualizar|actualiza)?\s*(?:mi\s+)?(?:remanente|cr[eé]dito\s+fiscal)(?:\s+inicial)?\s+(?:de\s+la\s+empresa\s+|de\s+empresa\s+|empresa\s+)(.+?)(?:\s+(?:es\s+de|es|de|a|en|por|:))?\s*\$?([0-9][0-9\.,\s]*(?:\s*lucas?)?)$",
        norm,
    )
    if m_rem_comp:
        comp_target = m_rem_comp.group(1).strip()
        amt = parse_amount(m_rem_comp.group(2), allow_zero=True)
        if amt is not None:
            return ExtractionResult(
                set_company_remanente=amt,
                target_company_name=comp_target,
            )

    # Directo para empresa activa / por defecto: "corregir remanente 150000", "corrige remanente 150000"
    m_rem_direct = re.search(
        r"^(?:corregir|corrige|modificar|modifica|cambiar|cambia|fijar|fija|ajustar|ajusta|actualizar|actualiza)?\s*(?:mi\s+)?(?:remanente|cr[eé]dito\s+fiscal)(?:\s+inicial)?(?:\s+(?:es\s+de|es|de|a|en|por|:))?\s*\$?([0-9][0-9\.,\s]*(?:\s*lucas?)?)$",
        norm,
    )
    if m_rem_direct:
        amt = parse_amount(m_rem_direct.group(1), allow_zero=True)
        if amt is not None:
            return ExtractionResult(set_company_remanente=amt)


    # 0a-3. Traspaso de presupuesto de Empresa a Personal
    # Caso 1: Empresa explícita con monto al final
    # Ej: "mover presupuesto empresa TecnoSpA a personal 50000", "pasar de empresa Consultora a personal 100000"
    m_trf_comp1 = re.search(
        r"^(?:mover|pasar|traspasar|transferir)\s+(?:(?:el\s+)?presupuesto\s+)?(?:de\s+(?:la\s+)?)?empresa\s+([a-zA-Z0-9\._\-]+)\s+(?:a|hacia|al)\s+(?:(?:mi\s+)?(?:presupuesto\s+)?personal|cuenta\s+personal)(?:\s+(?:es\s+de|es|de|a|en|por|:))?\s*\$?([0-9][0-9\.,\s]*(?:\s*lucas?)?)$",
        norm,
    )
    if m_trf_comp1:
        comp_target = m_trf_comp1.group(1).strip()
        amt = parse_amount(m_trf_comp1.group(2))
        if amt:
            return ExtractionResult(
                is_budget_transfer=True,
                transfer_amount=amt,
                transfer_from="EMPRESA",
                transfer_to="PERSONAL",
                target_company_name=comp_target,
            )

    # Caso 2: Empresa explícita con monto al inicio
    # Ej: "mover 50000 de empresa TecnoSpA a personal", "pasar 100000 del presupuesto de la empresa Consultora al personal"
    m_trf_comp2 = re.search(
        r"^(?:mover|pasar|traspasar|transferir)\s+\$?([0-9][0-9\.,\s]*(?:\s*lucas?)?)\s+(?:del?\s+)?(?:presupuesto\s+)?(?:de\s+(?:la\s+)?)?empresa\s+([a-zA-Z0-9\._\-]+)\s+(?:a|hacia|al)\s+(?:(?:mi\s+)?(?:presupuesto\s+)?personal|cuenta\s+personal)$",
        norm,
    )
    if m_trf_comp2:
        amt = parse_amount(m_trf_comp2.group(1))
        comp_target = m_trf_comp2.group(2).strip()
        if amt:
            return ExtractionResult(
                is_budget_transfer=True,
                transfer_amount=amt,
                transfer_from="EMPRESA",
                transfer_to="PERSONAL",
                target_company_name=comp_target,
            )

    # Caso 3: Sin empresa explícita (usa empresa activa o registrada), monto al final
    # Ej: "mover presupuesto empresa a personal 50000", "mover de empresa a personal 50000", "pasar presupuesto a personal 50000", "mover a personal 50000"
    m_trf_gen1 = re.search(
        r"^(?:mover|pasar|traspasar|transferir)\s+(?:(?:el\s+)?presupuesto\s+)?(?:(?:de\s+(?:la\s+)?)?empresa\s+)?(?:a|hacia|al)\s+(?:(?:mi\s+)?(?:presupuesto\s+)?personal|cuenta\s+personal)(?:\s+(?:es\s+de|es|de|a|en|por|:))?\s*\$?([0-9][0-9\.,\s]*(?:\s*lucas?)?)$",
        norm,
    )
    if m_trf_gen1:
        amt = parse_amount(m_trf_gen1.group(1))
        if amt:
            return ExtractionResult(
                is_budget_transfer=True,
                transfer_amount=amt,
                transfer_from="EMPRESA",
                transfer_to="PERSONAL",
            )

    # Caso 4: Sin empresa explícita, monto al inicio
    # Ej: "mover 50000 de empresa a personal", "mover 50000 de presupuesto empresa a personal", "mover 50000 a personal", "pasar 80000 a personal"
    m_trf_gen2 = re.search(
        r"^(?:mover|pasar|traspasar|transferir)\s+\$?([0-9][0-9\.,\s]*(?:\s*lucas?)?)\s+(?:(?:del?\s+)?(?:presupuesto\s+)?(?:de\s+(?:la\s+)?)?empresa\s+)?(?:a|hacia|al)\s+(?:(?:mi\s+)?(?:presupuesto\s+)?personal|cuenta\s+personal)$",
        norm,
    )
    if m_trf_gen2:
        amt = parse_amount(m_trf_gen2.group(1))
        if amt:
            return ExtractionResult(
                is_budget_transfer=True,
                transfer_amount=amt,
                transfer_from="EMPRESA",
                transfer_to="PERSONAL",
            )


    # 0b. Conmutación de modo (Personal vs Empresa)

    if norm in {"personal", "modo personal", "cambiar a personal", "volver a personal", "ir a personal", "cuenta personal"}:
        return ExtractionResult(target_mode="personal")

    m_mode = re.search(r"^(?:modo|cambiar\s+a\s+modo|cambiar\s+a|ir\s+a|pasar\s+a|volver\s+a)\s+(.+)$", norm)
    if m_mode:
        target = m_mode.group(1).strip()
        if target in {"personal", "hogar", "casa", "familia", "normal", "cuenta personal"}:
            return ExtractionResult(target_mode="personal")
        else:
            return ExtractionResult(target_mode=target, target_company_name=target)

    # 0c. Consulta de empresas
    if norm in {"mis empresas", "ver empresas", "empresas", "lista empresas", "mis companias", "mis compañías"}:
        return ExtractionResult(is_companies_list_inquiry=True)

    # 0d. Consulta tributaria F29 / IVA
    if norm in {"iva", "impuestos", "impuesto", "f29", "formulario 29", "mi iva", "balance tributario", "cuanto iva debo", "cuánto iva debo", "cuanto debo de iva", "cuánto debo de iva"}:
        return ExtractionResult(is_tax_inquiry=True)

    # 0e. Facturas Emitidas (Ventas con IVA Débito Fiscal o Exentas)
    is_emit_ex = bool(re.search(r"\b(?:exenta|no\s+afecta)\b", raw, re.IGNORECASE))
    m_f_emit = re.search(
        r"^(?:emit[ií]|hice|venta(?:\s+en)?)\s+(?:una\s+)?factura(?:\s+exenta|\s+no\s+afecta|\s+afecta)?(?:\s+(?:por|de))?\s+\$?([0-9][0-9\.,]*)(?:\s*(?:pesos|clp|\$))?(?:\s+(neto|con\s+iva|exenta|no\s+afecta|afecta))?(?:\s+(?:a|para)\s+(.+))?$",
        raw,
        re.IGNORECASE,
    )
    if not m_f_emit:
        m_f_emit = re.search(
            r"^factura(?:\s+exenta|\s+no\s+afecta|\s+afecta)?\s+emitida(?:\s+(?:por|de))?\s+\$?([0-9][0-9\.,]*)(?:\s*(?:pesos|clp|\$))?(?:\s+(neto|con\s+iva|exenta|no\s+afecta|afecta))?(?:\s+(?:a|para)\s+(.+))?$",
            raw,
            re.IGNORECASE,
        )
    if m_f_emit:
        amt = parse_amount(m_f_emit.group(1))
        tag = (m_f_emit.group(2) or "").lower()
        counterpart = m_f_emit.group(3).strip() if m_f_emit.group(3) else ""
        is_net = bool("neto" in tag)
        if amt:
            doc_type = "FACTURA_EXENTA" if is_emit_ex else "FACTURA"
            return ExtractionResult(
                tax_doc_direction="EMITTED",
                tax_doc_type=doc_type,
                is_exempt=is_emit_ex,
                total_spent=amt,
                is_net_amount=is_net,
                net_amount=amt if is_net else None,
                counterpart=counterpart,
                items=[ExtractedItem(name=f"Factura {'exenta ' if is_emit_ex else ''}emitida {counterpart}".strip(), quantity=1, unit_price=amt, total=amt, category="trabajo_insumos")],
            )

    # 0f. Facturas Recibidas (Compras con IVA Crédito Fiscal o Exentas)
    is_rec_ex = bool(re.search(r"\b(?:exenta|no\s+afecta)\b", raw, re.IGNORECASE))
    m_f_rec = re.search(
        r"^(?:recib[ií]\s+factura(?:\s+exenta|\s+no\s+afecta)?|factura(?:\s+exenta|\s+no\s+afecta)?\s+compra|compra\s+con\s+factura(?:\s+exenta|\s+no\s+afecta)?|factura\s+exenta|factura\s+no\s+afecta|factura)(?:\s+(?:de|por))?\s+(?:(.+?)\s+)?\$?([0-9][0-9\.,]*)(?:\s*(?:pesos|clp|\$))?(?:\s+(neto|con\s+iva|exenta|no\s+afecta))?$",
        raw,
        re.IGNORECASE,
    )
    if m_f_rec:
        desc = clean_concept(m_f_rec.group(1) or "")
        amt = parse_amount(m_f_rec.group(2))
        tag = (m_f_rec.group(3) or "").lower()
        is_net = bool("neto" in tag)
        if amt:
            concept = desc.capitalize() if desc else ("Factura exenta" if is_rec_ex else "Insumos y servicios")
            doc_type = "FACTURA_EXENTA" if is_rec_ex else "FACTURA"
            return ExtractionResult(
                tax_doc_direction="RECEIVED",
                tax_doc_type=doc_type,
                is_exempt=is_rec_ex,
                total_spent=amt,
                is_net_amount=is_net,
                net_amount=amt if is_net else None,
                counterpart="",
                items=[ExtractedItem(name=concept, quantity=1, unit_price=amt, total=amt, category=categorize_item(concept))],
            )

    # 0g. Boletas Recibidas (Gasto Operacional sin Crédito Fiscal)
    m_bol = re.search(
        r"^(?:boleta|boleta\s+gasto)(?:\s+de|\s+por)?\s+(?:(.+?)\s+)?\$?([0-9][0-9\.,]*)(?:\s*(?:pesos|clp|\$))?$",
        raw,
        re.IGNORECASE,
    )
    if m_bol:
        desc = clean_concept(m_bol.group(1) or "")
        amt = parse_amount(m_bol.group(2))
        if amt:
            concept = desc.capitalize() if desc else "Gasto en boleta"
            return ExtractionResult(
                tax_doc_direction="RECEIVED",
                tax_doc_type="BOLETA",
                total_spent=amt,
                items=[ExtractedItem(name=concept, quantity=1, unit_price=amt, total=amt, category=categorize_item(concept))],
            )

    # 1. Configuración o Ampliación de Presupuesto
    # 1a. Ampliación / Abono parcial al presupuesto existente
    m_budget_add1 = re.search(
        r"^(?:agregar|sumar|añadir|anadir|abono|abonar|ingreso|aumentar|mas|más)(?:\s+al|\s+a)?(?:\s+mi)?\s+presupuesto(?:\s+mensual)?(?:\s+(?:es\s+de|es|de|:))?\s*\$?([0-9][0-9.,\s]*)$",
        norm,
    )
    if m_budget_add1:
        amt = parse_amount(m_budget_add1.group(1))
        if amt:
            return ExtractionResult(
                is_budget_setup=True,
                is_budget_addition=True,
                budget_amount=amt,
                total_spent=0,
                items=[],
            )

    m_budget_add2 = re.search(
        r"^(?:agregar|sumar|añadir|anadir|abono|abonar|ingreso|aumentar)\s+(?:de\s+)?\$?([0-9][0-9.,\s]*)\s+(?:al|a\s+mi|al\s+mi|a)?\s*presupuesto(?:\s+mensual)?$",
        norm,
    )
    if m_budget_add2:
        amt = parse_amount(m_budget_add2.group(1))
        if amt:
            return ExtractionResult(
                is_budget_setup=True,
                is_budget_addition=True,
                budget_amount=amt,
                total_spent=0,
                items=[],
            )

    # 1b. Configuración o corrección de Presupuesto total
    m_budget = re.search(
        r"^(?:corregir|corrige|modificar|modifica|cambiar|cambia|fijar|fija|ajustar|ajusta|actualizar|actualiza)?\s*(?:mi\s+)?presupuesto(?:\s+mensual)?(?:\s+(?:es\s+de|es|de|a|en|por|:))?\s*\$?([0-9][0-9.,\s]*(?:\s*lucas?)?)$",
        norm,
    )

    if m_budget:
        amt = parse_amount(m_budget.group(1))
        if amt:
            return ExtractionResult(
                is_budget_setup=True,
                is_budget_addition=False,
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

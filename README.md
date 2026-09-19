# Pam Anota 🐶💰

<p align="center">
  <img src="assets/avatar.png" width="160" height="160" alt="Pam Anota Avatar" style="border-radius: 50%;">
</p>

Bot de WhatsApp para control de gastos personales y familiares en Chile (CLP), con arquitectura híbrida de alta velocidad (Base de Conocimiento local de 0 tokens + IA multimodal con rotación de modelos y respaldo en Groq).

---

## Características Principales

* 🇨🇱 **Estándar Monetario Chileno:** Formato `$1.000.000,00` con separación de miles con punto y centavos con coma.
* 🤖 **Machine Learning Local & Adquisición de Lenguaje (0 Tokens):** Vectorizador TF-IDF subpalabra (< 1 ms) que aprende continuamente de las respuestas externas para absorber patrones en memoria (`learned_patterns` y `learned_vocabulary`) y reducir el uso de APIs externas al mínimo.
* 🐾 **Personalidad Dinámica Pam Anota 🐶:** Motor de diálogo que atiende saludos, agradecimientos, preguntas de identidad y despedidas localmente, proporcionando confirmaciones variadas y naturales para evitar respuestas mecánicas.
* 🏢 **Soporte Multi-Empresa:** Creación y alternancia entre múltiples empresas (`crear empresa`, `modo [empresa]`, `modo personal`).
* 🏛️ **Módulo Tributario IVA (F29):** Diferenciación contable entre Facturas emitidas (débito IVA), Facturas recibidas (crédito IVA recuperable), Facturas exentas (DTE 34), Boletas (gasto operacional), remanente histórico y estimación de PPM.
* ⏱️ **Protección de Inactividad (5 min):** Si pasan más de 5 minutos en modo empresa, el bot retiene el registro y solicita confirmación interactiva de destino (Empresa vs. Personal).
* ⚡ **Zero-Token Priority:** Reconocimiento determinista y ML en Python (< 2 ms) para gastos, facturas, empresas y comandos cotidianos, minimizando consumo de cuota y costos de API.
* 🎙️ **Audio con Respaldo Gratuito:** Transcripción mediante **Groq Whisper Large v3** (~250 ms) y fallback con **Llama 3.3 70B** o **Gemini** multimodal.
* 📊 **Presupuesto Flexible:** Presupuesto personal y de empresa independientes, con ampliación mediante abonos parciales (`Agregar presupuesto 150.000`).
* 💡 **Inteligencia Financiera y Consejos:** Desglose Pareto de los 3 mayores centros de costo, detector de "gastos hormiga" (< $10.000 CLP), cálculo de *burn rate* diario y recomendaciones prácticas de ahorro.
* 🔒 **Seguridad y Privacidad:** Hashing HMAC-SHA256 para indexación de teléfonos, cifrado simétrico Fernet (AES-128-CBC) de números y roles de administrador.
* 🚀 **PostgreSQL Optimizado:** Índices cubrientes (*Index-Only Scans*) para suma de gastos, conteo y ordenamiento sin lecturas innecesarias de disco.

---

## Estructura del Proyecto

```
botGastos/
├── app/
│   ├── api/
│   │   └── webhook.py              # Verificación, webhook de WhatsApp Meta Cloud API y router
│   ├── core/
│   │   ├── config.py               # Configuración central y sanitizador de modelos
│   │   ├── formatters.py           # Formateador monetario chileno
│   │   ├── knowledge_base.py       # Base de conocimiento local (0 tokens) para gastos, empresas y facturas
│   │   ├── security.py             # Normalización, hashing y cifrado de teléfonos
│   │   └── timezone.py             # Zona horaria unificada (America/Santiago)
│   ├── models.py                   # Modelos SQLAlchemy 2.0 (User, Company, Budget, Expense, TaxDocument)
│   ├── schemas.py                  # Esquemas Pydantic
│   └── services/
│       ├── admin_service.py        # Panel y comandos de administrador por WhatsApp
│       ├── analytics_service.py    # Diagnóstico financiero y recomendaciones
│       ├── company_service.py      # Gestión multi-empresa, cambio de modo y timeout de 5 minutos
│       ├── dialogue_engine.py      # Personalidad Pam Anota 🐶, chit-chat local (0 tokens) y confirmaciones dinámicas
│       ├── expense_service.py      # Gestión de presupuestos y registros de gastos
│       ├── gemini_service.py       # Extracción multimodal Gemini con cascada de modelos y bucle de aprendizaje
│       ├── groq_service.py         # Whisper y Llama 3.3 en Groq Cloud
│       ├── ml_engine.py            # Motor ML local (0 tokens): TF-IDF n-gram, cosine similarity y feedback loop
│       ├── tax_service.py          # Registro tributario chileno, crédito/débito IVA y liquidación F29
│       └── whatsapp_service.py     # Cliente HTTP Meta Graph API
├── scripts/
    └── init_db.py                  # Inicializador de base de datos, tablas e índices
├── AGENTS.md                       # Memoria del proyecto para asistentes IA
└── Procfile / railway.json         # Despliegue en Railway
```

---

## Comandos Disponibles en WhatsApp

### 🏢 Gestión de Empresas y Tributario (Chile / SII)
* **Crear empresa:** `crear empresa TecnoSpA rut 76.123.456-7 remanente 80000` (o `... exenta`).
* **Activar empresa:** `modo tecnospa`.
* **Activar modo personal:** `modo personal`.
* **Ver empresas:** `mis empresas`.
* **Definir emisor exento:** `empresa exenta si` / `empresa exenta no`.
* **Factura Afecta de Venta (Débito Fiscal 19%):** `Emití factura por 1.190.000 a Cliente X`.
* **Factura Exenta de Venta (DTE 34, 0% IVA Débito):** `Emití factura exenta por 500.000 a Cliente Y`.
* **Factura Afecta de Compra (Crédito Fiscal 19%):** `Factura compra insumos 238.000` o `Recibí factura de 500.000`.
* **Factura Exenta de Compra (Gasto deducible, $0 Crédito):** `Factura exenta compra 150.000`.
* **Boleta de Compra (Gasto sin crédito IVA):** `Boleta materiales 45.000`.
* **Liquidación de Impuestos F29:** `iva`, `impuestos` o `f29`.

### 👤 Gastos Personales y Presupuestos
* **Registrar gasto:** `Almuerzo 4500`, `15 lucas bencina`, `Uber 3200`, `Veterinaria 35000` (o nota de voz / foto de boleta).
* **Configurar presupuesto:** `Presupuesto 500000`.
* **Abonar al presupuesto:** `Agregar presupuesto 150000` o `Sumar al presupuesto 50000`.
* **Consultar saldo:** `saldo`, `cuanto me queda` o `resumen`.
* **Diagnóstico de ahorro:** `ahorro`, `consejos` o `analisis`.
* **Listar compras:** `mis compras` o `compras agosto`.
* **Deshacer último gasto:** `deshacer` o `eliminar ultimo gasto`.
* **Menú y ayuda:** `ayuda` o `menu`.

### 🐾 Diálogo y Modismos (0 Tokens de IA)
* **Saludos:** `hola pam`, `wena`, `buenos días`, `hola`.
* **Agradecimientos:** `gracias pam`, `muchas gracias`, `vale pam`, `te amo`.
* **Identidad:** `quién eres`, `cómo te llamas`.
* **Despedidas:** `chao`, `hasta luego`.

---

## Ejecución Local

1. Crea y activa tu entorno virtual:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. Configura tu archivo `.env` basado en `.env.example`.
3. Inicializa las tablas e índices en PostgreSQL:
   ```bash
   PYTHONPATH=. python3 scripts/init_db.py
   ```
4. Inicia el servidor:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```
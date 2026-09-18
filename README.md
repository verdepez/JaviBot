# JaviBot (botGastos) 🤖💰

Bot de WhatsApp para control de gastos personales y familiares en Chile (CLP), con arquitectura híbrida de alta velocidad (Base de Conocimiento local de 0 tokens + IA multimodal con rotación de modelos y respaldo en Groq).

---

## Características Principales

* 🇨🇱 **Estándar Monetario Chileno:** Formato `$1.000.000,00` con separación de miles con punto y centavos con coma.
* 🏢 **Soporte Multi-Empresa:** Creación y alternancia entre múltiples empresas (`crear empresa`, `modo [empresa]`, `modo personal`).
* 🏛️ **Módulo Tributario IVA (F29):** Diferenciación contable entre Facturas emitidas (débito IVA), Facturas recibidas (crédito IVA recuperable), Boletas (gasto operacional sin crédito fiscal), remanente histórico y estimación de PPM.
* ⏱️ **Protección de Inactividad (5 min):** Si pasan más de 5 minutos en modo empresa, el bot retiene el registro y solicita confirmación interactiva de destino (Empresa vs. Personal).
* ⚡ **Zero-Token Priority:** Reconocimiento determinista ultra-rápido en Python (< 2 ms) para gastos, facturas, empresas y comandos cotidianos, minimizando consumo de cuota y costos de API.
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
│       ├── expense_service.py      # Gestión de presupuestos y registros de gastos
│       ├── gemini_service.py       # Extracción multimodal Gemini con cascada de modelos y DTEs
│       ├── groq_service.py         # Whisper y Llama 3.3 en Groq Cloud
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
* **Crear empresa:** `crear empresa TecnoSpA rut 76.123.456-7 remanente 80000`.
* **Activar empresa:** `modo tecnospa`.
* **Activar modo personal:** `modo personal`.
* **Ver empresas:** `mis empresas`.
* **Factura de Venta (Débito Fiscal):** `Emití factura por 1.190.000 a Cliente X`.
* **Factura de Compra (Crédito Fiscal 19%):** `Factura compra insumos 238.000` o `Recibí factura de 500.000`.
* **Boleta de Compra (Gasto sin crédito IVA):** `Boleta materiales 45.000`.
* **Liquidación de Impuestos F29:** `iva`, `impuestos` o `f29`.

### 👤 Gastos Personales y Presupuestos
* **Registrar gasto:** `Almuerzo 4500`, `Uber 3200`, `Veterinaria 35000` (o nota de voz / foto de boleta).
* **Configurar presupuesto:** `Presupuesto 500000`.
* **Abonar al presupuesto:** `Agregar presupuesto 150000` o `Sumar al presupuesto 50000`.
* **Consultar saldo:** `saldo`, `cuanto me queda` o `resumen`.
* **Diagnóstico de ahorro:** `ahorro`, `consejos` o `analisis`.
* **Listar compras:** `mis compras` o `compras agosto`.
* **Deshacer último gasto:** `deshacer` o `eliminar ultimo gasto`.
* **Menú y ayuda:** `ayuda` o `menu`.

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
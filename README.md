# botGastos

Backend MVP para registrar gastos desde WhatsApp mediante texto, audio e imágenes de boletas.

## Estructura

- `app/api/webhook.py`: verificación y recepción del webhook de Meta.
- `app/services/gemini_service.py`: extracción multimodal con JSON estructurado.
- `app/services/expense_service.py`: presupuesto, gastos y bóveda de ahorro.
- `app/models.py`: modelos SQLAlchemy para PostgreSQL.

## Ejecución local

1. Crea un entorno virtual e instala `requirements.txt`.
2. Copia `.env.example` a `.env` y completa las credenciales.
4. Crea la base `botgastos` en PostgreSQL.
5. Inicializa las tablas con `PYTHONPATH=. python3 scripts/init_db.py`.
6. Ejecuta `uvicorn app.main:app --reload`.

Configura en Meta la URL pública `https://TU_DOMINIO/webhook` y usa `META_VERIFY_TOKEN` para la verificación.

La respuesta estructurada de Gemini se valida con `ExtractionResult`; el audio se envía directamente como bytes al modelo y no pasa por un STT intermedio.
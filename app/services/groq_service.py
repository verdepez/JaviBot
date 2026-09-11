import json
import httpx
from app.schemas import ExtractionResult


async def extract_with_groq(
    text: str,
    api_key: str,
    model: str = "llama-3.3-70b-versatile",
) -> ExtractionResult | None:
    """
    Extracción de gastos con Groq Cloud (Llama 3.3 70B).
    Totalmente gratuita (14.400 peticiones diarias).
    """
    if not api_key:
        return None

    system_prompt = (
        "Eres un extractor financiero para WhatsApp en Chile (moneda CLP, símbolo $). "
        "Devuelve ÚNICAMENTE un objeto JSON válido que cumpla estrictamente este esquema:\n"
        "{\n"
        '  "is_budget_setup": false,\n'
        '  "is_balance_inquiry": false,\n'
        '  "is_expense_list_inquiry": false,\n'
        '  "target_month": null,\n'
        '  "budget_amount": null,\n'
        '  "items": [{"name": "string", "quantity": 1, "unit_price": 0, "total": 0, "category": "string"}],\n'
        '  "total_spent": 0\n'
        "}\n"
        "Categorías válidas: mascotas, educacion, familia, alimentos, hogar_servicios, transporte, salud, ocio, trabajo_insumos, otros.\n"
        "En Chile no se usan centavos en transacciones diarias, los números enteros representan pesos (ej: 45000 son $45.000). "
        "Si el usuario pide saldo o presupuesto, marca el booleano correspondiente. Si es un gasto, calcula total_spent y los ítems."
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key.strip()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return ExtractionResult.model_validate_json(content)
            else:
                print(f"--> [GROQ] Error {resp.status_code}: {resp.text}")
                return None
    except Exception as exc:
        print(f"--> [GROQ] Excepción al consultar Groq: {exc}")
        return None


async def transcribe_audio_groq(
    audio_bytes: bytes,
    mime_type: str,
    api_key: str,
) -> str | None:
    """
    Transcribe notas de voz de WhatsApp usando Whisper Large v3 en Groq.
    Latencia ultra baja (~300ms) y capa gratuita de 14.400 peticiones/día.
    """
    if not api_key:
        return None

    clean_mime = (mime_type or "").split(";")[0].strip().lower()
    ext = "ogg"
    if "mp4" in clean_mime or "m4a" in clean_mime:
        ext = "m4a"
    elif "mp3" in clean_mime:
        ext = "mp3"
    elif "wav" in clean_mime:
        ext = "wav"

    filename = f"voice_note.{ext}"

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            files = {
                "file": (filename, audio_bytes, clean_mime or "audio/ogg"),
            }
            data = {
                "model": "whisper-large-v3",
                "language": "es",
                "response_format": "json",
                "temperature": "0.0",
            }
            resp = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key.strip()}"},
                files=files,
                data=data,
            )
            if resp.status_code == 200:
                result = resp.json()
                transcription = result.get("text", "").strip()
                print(f"--> [GROQ WHISPER] Transcripción exitosa: '{transcription}'")
                return transcription
            else:
                print(f"--> [GROQ WHISPER] Error {resp.status_code}: {resp.text}")
                return None
    except Exception as exc:
        print(f"--> [GROQ WHISPER] Excepción al transcribir: {exc}")
        return None

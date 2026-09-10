from google import genai
from google.genai import types

from app.core.config import settings
from app.schemas import ExtractionResult


SYSTEM_INSTRUCTION = """Eres un extractor financiero para un bot de WhatsApp en español.
Analiza texto, audio o imágenes de compras. Devuelve únicamente el JSON que cumple el
esquema indicado. Si el mensaje configura presupuesto, marca is_budget_setup=true y
extrae budget_amount. Para gastos, desglosa cada ítem y calcula total_spent. No inventes
precios: usa 0 cuando un dato no sea legible. Categoriza en alimentos, hogar, transporte,
salud, ocio, servicios u otros. Si un total de boleta existe, úsalo como total_spent.
Si el mensaje no contiene gastos ni configuración de presupuesto (por ejemplo un saludo o pregunta),
devuelve total_spent=0, items=[] e is_budget_setup=false."""


class GeminiExtractor:
    def __init__(self) -> None:
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def extract(
        self,
        input_type: str,
        content: str | bytes,
        mime_type: str | None = None,
    ) -> ExtractionResult:
        if input_type == "text":
            contents: str | list[types.Part] = content if isinstance(content, str) else content.decode()
        else:
            if not isinstance(content, bytes) or not mime_type:
                raise ValueError("El contenido multimodal requiere bytes y mime_type")
            clean_mime = mime_type.split(";")[0].strip()
            contents = [types.Part.from_bytes(data=content, mime_type=clean_mime)]

        response = await self.client.aio.models.generate_content(
            model=settings.gemini_model,
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
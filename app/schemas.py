from pydantic import BaseModel, Field


class ExtractedItem(BaseModel):
    name: str = ""
    quantity: float = Field(default=1, ge=0)
    unit_price: float = Field(default=0, ge=0)
    total: float = Field(default=0, ge=0)
    category: str = "otros"


class ExtractionResult(BaseModel):
    is_budget_setup: bool = False
    is_budget_addition: bool = False
    is_balance_inquiry: bool = False
    is_expense_list_inquiry: bool = False
    target_month: str | None = None
    budget_amount: float | None = Field(default=None, ge=0)
    items: list[ExtractedItem] = Field(default_factory=list)
    total_spent: float = Field(default=0, ge=0)

    # Nuevos campos para Multi-Empresa y Módulo Tributario SII (IVA / F29)
    is_tax_inquiry: bool = False
    is_company_creation: bool = False
    company_name: str | None = None
    company_rut: str | None = None
    initial_credit: float | None = Field(default=None, ge=0)
    target_mode: str | None = None  # "personal" o nombre de empresa
    target_company_name: str | None = None
    is_companies_list_inquiry: bool = False
    tax_doc_direction: str | None = None  # "EMITTED" o "RECEIVED"
    tax_doc_type: str | None = None  # "FACTURA" o "BOLETA"
    counterpart: str | None = None
    is_net_amount: bool = False
    net_amount: float | None = Field(default=None, ge=0)
    iva_amount: float | None = Field(default=None, ge=0)
    is_exempt: bool = False
    company_is_exempt: bool | None = None
    set_company_exempt: bool | None = None
    set_company_remanente: float | None = Field(default=None, ge=0)
    is_budget_transfer: bool = False
    transfer_amount: float | None = Field(default=None, ge=0)
    transfer_from: str | None = None
    transfer_to: str | None = None
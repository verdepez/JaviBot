from pydantic import BaseModel, Field


class ExtractedItem(BaseModel):
    name: str = ""
    quantity: float = Field(default=1, ge=0)
    unit_price: float = Field(default=0, ge=0)
    total: float = Field(default=0, ge=0)
    category: str = "otros"


class ExtractionResult(BaseModel):
    is_budget_setup: bool = False
    is_balance_inquiry: bool = False
    budget_amount: float | None = Field(default=None, ge=0)
    items: list[ExtractedItem] = Field(default_factory=list)
    total_spent: float = Field(default=0, ge=0)
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_hash: Mapped[str] = mapped_column(String(64), unique=True)
    encrypted_phone: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100), default="Amigo")
    user_code: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)  # ACTIVE, PENDING, BLOCKED, TRIAL
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    trial_expense_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Campo opcional para retrocompatibilidad con bases de datos anteriores
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Soporte de modo y contexto empresa
    active_mode: Mapped[str] = mapped_column(String(20), default="PERSONAL")  # PERSONAL, EMPRESA
    active_company_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_company_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_action_data: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    budgets: Mapped[list["Budget"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    savings: Mapped[list["SavingsVault"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    companies: Mapped[list["Company"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("user_id", "name_normalized", name="uq_company_user_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    name_normalized: Mapped[str] = mapped_column(String(100), index=True)
    rut: Mapped[str] = mapped_column(String(20))
    is_exempt_issuer: Mapped[bool] = mapped_column(Boolean, default=False)
    initial_tax_credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    ppm_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.0100"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="companies")
    budgets: Mapped[list["Budget"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    tax_documents: Mapped[list["TaxDocument"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "month_year", "budget_type", "company_id", name="uq_budget_user_month_type_company"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    month_year: Mapped[str] = mapped_column(String(7), index=True)
    budget_type: Mapped[str] = mapped_column(String(20), default="PERSONAL")  # PERSONAL, EMPRESA
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=True)
    total_budget: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    user: Mapped[User] = relationship(back_populates="budgets")
    company: Mapped[Company | None] = relationship(back_populates="budgets")
    expenses: Mapped[list["Expense"]] = relationship(back_populates="budget", cascade="all, delete-orphan")
    tax_documents: Mapped[list["TaxDocument"]] = relationship(back_populates="budget")


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (
        Index("ix_expenses_budget_created", "budget_id", "created_at"),
        Index("ix_expenses_budget_amount", "budget_id", "total_amount"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    budget_id: Mapped[int] = mapped_column(ForeignKey("budgets.id", ondelete="CASCADE"))
    raw_input_type: Mapped[str] = mapped_column(String(20))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    budget: Mapped[Budget] = relationship(back_populates="expenses")
    items: Mapped[list["ExpenseItem"]] = relationship(back_populates="expense", cascade="all, delete-orphan")


class ExpenseItem(Base):
    __tablename__ = "expense_items"
    __table_args__ = (
        Index("ix_expense_items_expense_category", "expense_id", "category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(ForeignKey("expenses.id", ondelete="CASCADE"))
    item_name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    total_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    category: Mapped[str] = mapped_column(String(80), default="otros", index=True)

    expense: Mapped[Expense] = relationship(back_populates="items")


class TaxDocument(Base):
    __tablename__ = "tax_documents"
    __table_args__ = (
        Index("ix_tax_docs_company_month", "company_id", "month_year", "doc_direction"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    budget_id: Mapped[int | None] = mapped_column(ForeignKey("budgets.id", ondelete="SET NULL"), nullable=True)
    month_year: Mapped[str] = mapped_column(String(7), index=True)
    doc_direction: Mapped[str] = mapped_column(String(10))  # EMITTED, RECEIVED
    doc_type: Mapped[str] = mapped_column(String(20))  # FACTURA, BOLETA, NOTA_CREDITO
    counterpart_name: Mapped[str] = mapped_column(String(150), default="")
    counterpart_rut: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    iva_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    description: Mapped[str] = mapped_column(String(255), default="")
    raw_input_type: Mapped[str] = mapped_column(String(20), default="text")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    company: Mapped[Company] = relationship(back_populates="tax_documents")
    budget: Mapped[Budget | None] = relationship(back_populates="tax_documents")


class SavingsVault(Base):
    __tablename__ = "savings_vault"
    __table_args__ = (
        Index("ix_savings_vault_user_amount", "user_id", "amount_saved"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    amount_saved: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reason: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="savings")


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class LearnedPattern(Base):
    __tablename__ = "learned_patterns"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern_template: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    intent: Mapped[str] = mapped_column(String(50), index=True)  # EXPENSE, BUDGET, TAX_DOC, INQUIRY, CHITCHAT
    category_default: Mapped[str] = mapped_column(String(80), default="otros")
    doc_direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    doc_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.90)
    response_template: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LearnedVocabulary(Base):
    __tablename__ = "learned_vocabulary"

    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    canonical_category: Mapped[str] = mapped_column(String(80), index=True)
    multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
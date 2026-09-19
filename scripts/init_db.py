import asyncio
from sqlalchemy import text

from app.db import Base, engine
from app import models  # noqa: F401
from app.core.config import settings
from app.core.security import encrypt_phone, generate_user_code, hash_phone


async def init_db() -> None:
    async with engine.begin() as connection:
        # Crea tablas si no existen
        await connection.run_sync(Base.metadata.create_all)

        # Migración suave de columnas para tabla users existente
        await connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'users') THEN
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_hash VARCHAR(64);
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS encrypted_phone VARCHAR(255);
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS name VARCHAR(100) DEFAULT 'Amigo';
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS user_code VARCHAR(32);
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'ACTIVE';
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_expense_count INTEGER DEFAULT 0;
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS notes VARCHAR(255);
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS active_mode VARCHAR(20) DEFAULT 'PERSONAL';
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS active_company_id INTEGER;
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS last_company_action_at TIMESTAMP WITH TIME ZONE;
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS pending_action_data VARCHAR(1000);
                        -- Permitir nulo en phone_number anterior
                        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'phone_number') THEN
                            ALTER TABLE users ALTER COLUMN phone_number DROP NOT NULL;
                        END IF;
                    END IF;

                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'budgets') THEN
                        ALTER TABLE budgets ADD COLUMN IF NOT EXISTS budget_type VARCHAR(20) DEFAULT 'PERSONAL';
                        ALTER TABLE budgets ADD COLUMN IF NOT EXISTS company_id INTEGER;
                    END IF;

                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'companies') THEN
                        ALTER TABLE companies ADD COLUMN IF NOT EXISTS is_exempt_issuer BOOLEAN DEFAULT FALSE;
                    END IF;

                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'tax_documents') THEN
                        ALTER TABLE tax_documents ADD COLUMN IF NOT EXISTS is_exempt BOOLEAN DEFAULT FALSE;
                    END IF;
                END $$;
                """
            )
        )

        # Migrar filas anteriores que tengan phone_number pero no phone_hash
        rows = (await connection.execute(text("SELECT id, phone_number, name FROM users WHERE phone_hash IS NULL"))).fetchall()
        for row in rows:
            uid, p_num, u_name = row[0], row[1], row[2] or "Amigo"
            if p_num:
                p_hash = hash_phone(p_num)
                p_enc = encrypt_phone(p_num)
                u_code = generate_user_code(p_num, u_name)
                await connection.execute(
                    text(
                        "UPDATE users SET phone_hash = :hash, encrypted_phone = :enc, user_code = :code, name = :name, phone_number = NULL WHERE id = :id"
                    ),
                    {"hash": p_hash, "enc": p_enc, "code": u_code, "name": u_name, "id": uid},
                )

        # Garantizar privacidad: asegurar que ninguna fila con hash conserve teléfono en texto plano
        await connection.execute(text("UPDATE users SET phone_number = NULL WHERE phone_hash IS NOT NULL AND phone_number IS NOT NULL;"))

        # Crear índices para optimización de consultas de alto rendimiento (Index-Only Scans y ordenamiento veloz)
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone_hash ON users (phone_hash);"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_user_code ON users (user_code);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_users_status ON users (status);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_users_phone_number ON users (phone_number);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_expenses_budget_created ON expenses (budget_id, created_at DESC);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_expenses_budget_amount ON expenses (budget_id, total_amount);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_expense_items_expense_category ON expense_items (expense_id, category);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_expense_items_category ON expense_items (category);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_savings_vault_user_amount ON savings_vault (user_id, amount_saved);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_budgets_month_year ON budgets (month_year);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_budgets_company_id ON budgets (company_id);"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_companies_user_norm_name ON companies (user_id, name_normalized);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_companies_user_id ON companies (user_id);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tax_docs_company_month ON tax_documents (company_id, month_year, doc_direction);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tax_docs_user_id ON tax_documents (user_id);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_learned_patterns_intent ON learned_patterns (intent);"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_learned_patterns_template ON learned_patterns (pattern_template);"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_learned_vocab_term ON learned_vocabulary (term);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_learned_vocab_cat ON learned_vocabulary (canonical_category);"))

        # Eliminar índices redundantes si existen (ya cubiertos por los índices compuestos)
        await connection.execute(text("DROP INDEX IF EXISTS ix_expenses_budget_id;"))
        await connection.execute(text("DROP INDEX IF EXISTS ix_expenses_created_at;"))
        await connection.execute(text("DROP INDEX IF EXISTS ix_budgets_user_id;"))
        await connection.execute(text("DROP INDEX IF EXISTS ix_savings_vault_user_id;"))

        # Asegurar privilegios de admin para settings.admin_phone si está configurado
        if settings.admin_phone:
            clean_admin = settings.admin_phone.strip().lstrip("+")
            admin_hash = hash_phone(clean_admin)
            await connection.execute(
                text("UPDATE users SET is_admin = TRUE, status = 'ACTIVE' WHERE phone_hash = :hash"),
                {"hash": admin_hash},
            )

        # Asegurar tabla processed_messages
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS processed_messages (
                    id SERIAL PRIMARY KEY,
                    message_id VARCHAR(128) UNIQUE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
                """
            )
        )
        await connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_processed_messages_message_id ON processed_messages (message_id);")
        )
        await connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_processed_messages_created_at ON processed_messages (created_at);")
        )

        # Limpieza automática de gastos duplicados producidos por reintentos de webhook
        # (mismo presupuesto, mismo monto, creados con menos de 180 segundos de diferencia)
        await connection.execute(
            text(
                """
                DELETE FROM expenses e1
                USING expenses e2
                WHERE e1.budget_id = e2.budget_id
                  AND e1.total_amount = e2.total_amount
                  AND e1.id > e2.id
                  AND ABS(EXTRACT(EPOCH FROM (e1.created_at - e2.created_at))) < 180;
                """
            )
        )

        # Corregir activación de Niki (56961124124) y limpiar usuario fantasma por error de tipeo (5691124124)
        niki_hash = hash_phone("56961124124")
        await connection.execute(
            text(
                "UPDATE users SET status = 'ACTIVE', name = 'Niki' "
                "WHERE phone_hash = :hash OR user_code = 'JB-1455D61E'"
            ),
            {"hash": niki_hash},
        )
        ghost_hash = hash_phone("5691124124")
        await connection.execute(
            text(
                "DELETE FROM users WHERE (phone_hash = :hash OR phone_number = '5691124124') AND name = 'Amigo'"
            ),
            {"hash": ghost_hash},
        )

        # Corrector de datos monetarios chilenos mal registrados:
        # 1. Caso específico del ítem 'Insumos 45,983' registrado erróneamente como 45.98
        await connection.execute(
            text(
                """
                UPDATE expense_items
                SET unit_price = 45983.00, total_price = 45983.00
                WHERE total_price = 45.98 AND item_name ILIKE '%insumo%';
                """
            )
        )
        await connection.execute(
            text(
                """
                UPDATE expenses
                SET total_amount = 45983.00
                WHERE total_amount = 45.98;
                """
            )
        )
        # 2. Corrector general para montos chilenos donde la coma de miles fue interpretada como punto decimal
        # (en Chile los gastos no tienen centavos y son de miles de pesos)
        await connection.execute(
            text(
                """
                UPDATE expense_items
                SET unit_price = ROUND(unit_price * 1000, 2),
                    total_price = ROUND(total_price * 1000, 2)
                WHERE total_price > 0 AND total_price < 1000 AND (total_price != ROUND(total_price, 0))
                  AND NOT (total_price = 45983.00 AND item_name ILIKE '%insumo%');
                """
            )
        )
        await connection.execute(
            text(
                """
                UPDATE expenses
                SET total_amount = (
                    SELECT COALESCE(SUM(total_price), expenses.total_amount)
                    FROM expense_items
                    WHERE expense_items.expense_id = expenses.id
                )
                WHERE total_amount > 0 AND total_amount < 1000 AND (total_amount != ROUND(total_amount, 0))
                  AND total_amount != 45983.00;
                """
            )
        # 3. Inicializar patrones base de lenguaje y vocabulario chileno (Cold-Start)
        await connection.execute(
            text(
                """
                INSERT INTO learned_patterns (pattern_template, intent, category_default, doc_direction, doc_type, is_exempt, hit_count, confidence_score)
                VALUES
                    ('gaste {amount} en {item}', 'EXPENSE', 'otros', NULL, NULL, FALSE, 10, 0.98),
                    ('compre {item} en {amount}', 'EXPENSE', 'otros', NULL, NULL, FALSE, 10, 0.98),
                    ('pague {amount} de {item}', 'EXPENSE', 'otros', NULL, NULL, FALSE, 10, 0.98),
                    ('anota {amount} en {item}', 'EXPENSE', 'otros', NULL, NULL, FALSE, 10, 0.98),
                    ('anota {item} {amount}', 'EXPENSE', 'otros', NULL, NULL, FALSE, 10, 0.98),
                    ('transferi {amount} por {item}', 'EXPENSE', 'otros', NULL, NULL, FALSE, 10, 0.98),
                    ('{item} {amount}', 'EXPENSE', 'otros', NULL, NULL, FALSE, 5, 0.90),
                    ('factura emitida {amount}', 'TAX_DOC', 'otros', 'EMITTED', 'FACTURA', FALSE, 10, 0.98),
                    ('emiti factura {amount}', 'TAX_DOC', 'otros', 'EMITTED', 'FACTURA', FALSE, 10, 0.98),
                    ('factura recibida {amount}', 'TAX_DOC', 'otros', 'RECEIVED', 'FACTURA', FALSE, 10, 0.98),
                    ('factura de compra {amount}', 'TAX_DOC', 'otros', 'RECEIVED', 'FACTURA', FALSE, 10, 0.98),
                    ('factura exenta emitida {amount}', 'TAX_DOC', 'otros', 'EMITTED', 'FACTURA', TRUE, 10, 0.98),
                    ('factura exenta recibida {amount}', 'TAX_DOC', 'otros', 'RECEIVED', 'FACTURA', TRUE, 10, 0.98),
                    ('boleta de compra {amount}', 'TAX_DOC', 'otros', 'RECEIVED', 'BOLETA', FALSE, 10, 0.98),
                    ('presupuesto {amount}', 'BUDGET', 'otros', NULL, NULL, FALSE, 10, 0.98),
                    ('agregar presupuesto {amount}', 'BUDGET', 'otros', NULL, NULL, FALSE, 10, 0.98),
                    ('sumar al presupuesto {amount}', 'BUDGET', 'otros', NULL, NULL, FALSE, 10, 0.98)
                ON CONFLICT (pattern_template) DO NOTHING;
                """
            )
        )

        await connection.execute(
            text(
                """
                INSERT INTO learned_vocabulary (term, canonical_category, multiplier)
                VALUES
                    ('lucas', 'otros', 1000.0),
                    ('luca', 'otros', 1000.0),
                    ('gamba', 'otros', 100.0),
                    ('gambas', 'otros', 100.0),
                    ('palo', 'otros', 1000000.0),
                    ('palos', 'otros', 1000000.0),
                    ('bencina', 'transporte', 1.0),
                    ('combustible', 'transporte', 1.0),
                    ('metro', 'transporte', 1.0),
                    ('micro', 'transporte', 1.0),
                    ('uber', 'transporte', 1.0),
                    ('colectivo', 'transporte', 1.0),
                    ('estacionamiento', 'transporte', 1.0),
                    ('almuerzo', 'alimentacion', 1.0),
                    ('supermercado', 'alimentacion', 1.0),
                    ('comida', 'alimentacion', 1.0),
                    ('pan', 'alimentacion', 1.0),
                    ('feria', 'alimentacion', 1.0),
                    ('cafe', 'alimentacion', 1.0),
                    ('desayuno', 'alimentacion', 1.0),
                    ('once', 'alimentacion', 1.0),
                    ('baltilocas', 'ocio', 1.0),
                    ('cerveza', 'ocio', 1.0),
                    ('carrete', 'ocio', 1.0),
                    ('cine', 'ocio', 1.0),
                    ('bar', 'ocio', 1.0),
                    ('farmacia', 'salud', 1.0),
                    ('remedios', 'salud', 1.0),
                    ('doctor', 'salud', 1.0),
                    ('arriendo', 'hogar', 1.0),
                    ('luz', 'cuentas', 1.0),
                    ('agua', 'cuentas', 1.0),
                    ('gas', 'cuentas', 1.0),
                    ('internet', 'cuentas', 1.0),
                    ('gasto comun', 'hogar', 1.0)
                ON CONFLICT (term) DO NOTHING;
                """
            )
        )


if __name__ == "__main__":
    asyncio.run(init_db())
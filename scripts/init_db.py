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
                        -- Permitir nulo en phone_number anterior
                        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'phone_number') THEN
                            ALTER TABLE users ALTER COLUMN phone_number DROP NOT NULL;
                        END IF;
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
                        "UPDATE users SET phone_hash = :hash, encrypted_phone = :enc, user_code = :code, name = :name WHERE id = :id"
                    ),
                    {"hash": p_hash, "enc": p_enc, "code": u_code, "name": u_name, "id": uid},
                )

        # Crear índices para optimización de consultas de alto rendimiento
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone_hash ON users (phone_hash);"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_user_code ON users (user_code);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_users_status ON users (status);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_users_phone_number ON users (phone_number);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_expenses_budget_created ON expenses (budget_id, created_at DESC);"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_expense_items_category ON expense_items (category);"))

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
        )


if __name__ == "__main__":
    asyncio.run(init_db())
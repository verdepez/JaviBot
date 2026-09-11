import asyncio
from sqlalchemy import text

from app.db import Base, engine
from app import models  # noqa: F401
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

        # Crear índices únicos
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone_hash ON users (phone_hash);"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_user_code ON users (user_code);"))


if __name__ == "__main__":
    asyncio.run(init_db())
import re
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import clean_first_name, decrypt_phone, encrypt_phone, generate_user_code, hash_phone
from app.models import User


def clean_phone_str(raw: str) -> str:
    """Elimina caracteres no numéricos excepto si viene con +."""
    digits = re.sub(r"[^\d]", "", raw)
    return digits


def is_admin_phone(phone: str) -> bool:
    clean_p = clean_phone_str(phone)
    clean_admin = clean_phone_str(settings.admin_phone) if settings.admin_phone else ""
    return bool(clean_admin and clean_p == clean_admin)


async def authorize_user(
    db: AsyncSession,
    target_phone: str,
    custom_name: str | None = None,
) -> tuple[User, str]:
    clean_target = clean_phone_str(target_phone)
    if len(clean_target) < 8:
        raise ValueError(f"Número de teléfono inválido: {target_phone}")

    p_hash = hash_phone(clean_target)
    user = await db.scalar(select(User).where(User.phone_hash == p_hash))
    if user is None:
        user = await db.scalar(select(User).where(User.phone_number == clean_target))

    if user is None:
        name = clean_first_name(custom_name) if custom_name else "Amigo"
        code = generate_user_code(clean_target, name)
        user = User(
            phone_hash=p_hash,
            encrypted_phone=encrypt_phone(clean_target),
            name=name,
            user_code=code,
            phone_number=None,
            status="ACTIVE",
            is_admin=False,
        )
        db.add(user)
    else:
        user.status = "ACTIVE"
        if custom_name:
            user.name = clean_first_name(custom_name)
            user.user_code = generate_user_code(clean_target, user.name)

    await db.commit()
    return user, clean_target


async def block_user(db: AsyncSession, target_phone: str) -> tuple[User | None, str]:
    clean_target = clean_phone_str(target_phone)
    p_hash = hash_phone(clean_target)
    user = await db.scalar(select(User).where(User.phone_hash == p_hash))
    if user is None:
        user = await db.scalar(select(User).where(User.phone_number == clean_target))

    if user is not None:
        user.status = "BLOCKED"
        await db.commit()

    return user, clean_target


async def list_active_users(db: AsyncSession) -> list[dict]:
    result = await db.execute(select(User).where(User.status == "ACTIVE").order_by(User.id.desc()))
    users = result.scalars().all()
    out = []
    for u in users:
        phone_display = u.phone_number or ""
        if not phone_display and u.encrypted_phone:
            try:
                phone_display = decrypt_phone(u.encrypted_phone)
            except Exception:
                phone_display = "Cifrado"
        out.append({
            "name": u.name,
            "code": u.user_code,
            "phone": phone_display,
            "is_admin": u.is_admin,
        })
    return out


async def list_pending_leads(db: AsyncSession) -> list[dict]:
    result = await db.execute(select(User).where(User.status == "PENDING").order_by(User.id.desc()))
    users = result.scalars().all()
    out = []
    for u in users:
        phone_display = u.phone_number or ""
        if not phone_display and u.encrypted_phone:
            try:
                phone_display = decrypt_phone(u.encrypted_phone)
            except Exception:
                phone_display = "Cifrado"
        created = u.created_at.strftime("%d/%m %H:%M") if u.created_at else ""
        out.append({
            "name": u.name,
            "code": u.user_code,
            "phone": phone_display,
            "date": created,
        })
    return out


async def handle_admin_command(db: AsyncSession, admin_phone: str, text: str) -> str | None:
    """
    Evalúa si el texto recibido corresponde a un comando de administrador.
    Si es así, lo procesa y retorna la respuesta. Si no es un comando de admin, retorna None.
    """
    if not is_admin_phone(admin_phone):
        # Verificar si en BD está marcado como is_admin
        clean_p = clean_phone_str(admin_phone)
        p_hash = hash_phone(clean_p)
        u = await db.scalar(select(User).where(User.phone_hash == p_hash))
        if not (u and u.is_admin):
            return None

    raw = text.strip()
    norm = raw.lower()

    # Menú de ayuda admin
    if norm in {"admin", "ayuda admin", "menu admin", "comandos admin"}:
        return (
            "■ *PANEL DE ADMINISTRADOR* | JaviBot\n"
            "──────────────────────────\n"
            "Comandos disponibles:\n\n"
            "• `autorizar [teléfono] [nombre]`\n"
            "  Habilita acceso a un usuario.\n"
            "  Ej: `autorizar 56912345678 Carlos`\n\n"
            "• `bloquear [teléfono]`\n"
            "  Revoca acceso a un usuario.\n"
            "  Ej: `bloquear 56912345678`\n\n"
            "• `usuarios`\n"
            "  Lista todos los usuarios activos.\n\n"
            "• `prospectos` o `pendientes`\n"
            "  Lista prospectos que quieren contratar.\n\n"
            "▸ Si envías un gasto normal (ej: 'Almuerzo 4500'), se registrará en tu cuenta personal."
        )

    # Comando Autorizar
    if norm.startswith("autorizar ") or norm.startswith("activar "):
        parts = raw.split()
        if len(parts) < 2:
            return "[!] Uso incorrecto. Formato: `autorizar [teléfono] [nombre opcional]`"
        target = parts[1]
        name = " ".join(parts[2:]) if len(parts) > 2 else None
        try:
            user, clean_target = await authorize_user(db, target, name)
            return (
                f"✓ *Usuario autorizado con éxito*\n"
                f"▪ Nombre: *{user.name}*\n"
                f"▪ Teléfono: `{clean_target}`\n"
                f"▪ Código: `{user.user_code}`\n"
                f"▪ Estado: *ACTIVO*"
            )
        except Exception as e:
            return f"[!] Error al autorizar: {e}"

    # Comando Bloquear
    if norm.startswith("bloquear ") or norm.startswith("desactivar "):
        parts = raw.split()
        if len(parts) < 2:
            return "[!] Uso incorrecto. Formato: `bloquear [teléfono]`"
        target = parts[1]
        try:
            user, clean_target = await block_user(db, target)
            if user:
                return (
                    f"✓ *Usuario bloqueado con éxito*\n"
                    f"▪ Nombre: *{user.name}*\n"
                    f"▪ Teléfono: `{clean_target}`\n"
                    f"▪ Estado: *BLOQUEADO*"
                )
            else:
                return f"[!] No se encontró un usuario con el teléfono `{clean_target}`."
        except Exception as e:
            return f"[!] Error al bloquear: {e}"

    # Listar Usuarios Activos
    if norm in {"usuarios", "lista usuarios", "ver usuarios", "clientes"}:
        users = await list_active_users(db)
        if not users:
            return "■ *USUARIOS ACTIVOS*\n──────────────────────────\nNo hay usuarios activos registrados aún."
        lines = [
            f"■ *USUARIOS ACTIVOS* ({len(users)})",
            "──────────────────────────",
        ]
        for u in users:
            role = " (Admin)" if u["is_admin"] else ""
            lines.append(f"• *{u['name']}*{role} | {u['phone']} (`{u['code']}`)")
        return "\n".join(lines)

    # Listar Prospectos / Pendientes
    if norm in {"prospectos", "pendientes", "lista prospectos", "leads"}:
        leads = await list_pending_leads(db)
        if not leads:
            return "■ *PROSPECTOS EN ESPERA*\n──────────────────────────\nNo hay prospectos pendientes de pago."
        lines = [
            f"■ *PROSPECTOS EN ESPERA* ({len(leads)})",
            "──────────────────────────",
        ]
        for l in leads:
            lines.append(f"• *{l['name']}* | {l['phone']} | {l['date']}")
            lines.append(f"  ▸ Autorizar: `autorizar {l['phone']}`")
        return "\n".join(lines)

    return None


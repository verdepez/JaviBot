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


def is_similar_phone(p1: str, p2: str) -> bool:
    """Comprueba si dos números son idénticos o difieren por prefijo o error de tipeo menor."""
    if p1 == p2:
        return True
    if len(p1) >= 8 and (p2.endswith(p1) or p1.endswith(p2)):
        return True
    if abs(len(p1) - len(p2)) <= 2 and min(len(p1), len(p2)) >= 8:
        dp = [[0] * (len(p2) + 1) for _ in range(len(p1) + 1)]
        for i in range(len(p1) + 1):
            dp[i][0] = i
        for j in range(len(p2) + 1):
            dp[0][j] = j
        for i in range(1, len(p1) + 1):
            for j in range(1, len(p2) + 1):
                cost = 0 if p1[i - 1] == p2[j - 1] else 1
                dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
        return dp[len(p1)][len(p2)] <= 2
    return False


async def authorize_user(
    db: AsyncSession,
    target: str,
    custom_name: str | None = None,
) -> tuple[User, str]:
    """
    Busca y activa un usuario.
    Permite autorizar por:
    - 'ultimo' o '': el prospecto en espera más reciente.
    - Código de usuario (ej: 'JB-1455D61E').
    - Nombre del prospecto (ej: 'Niki' o 'Nicole').
    - Teléfono exacto o aproximado (con tolerancia a dígitos faltantes).
    - Si no existe y se suministra un teléfono válido de >= 9 dígitos, crea y activa al usuario.
    """
    target_clean = target.strip()
    target_digits = clean_phone_str(target_clean)
    found_user: User | None = None
    real_phone: str = ""

    # 1. Caso 'ultimo' o vacío: autorizar al prospecto más reciente
    if target_clean.lower() in {"ultimo", "último", ""}:
        stmt = select(User).where(User.status == "PENDING").order_by(User.id.desc()).limit(1)
        found_user = await db.scalar(stmt)
        if not found_user:
            raise ValueError("No hay ningún prospecto pendiente de pago para autorizar.")

    # 2. Buscar por código de usuario (user_code)
    if not found_user and (target_clean.upper().startswith("JB-") or (len(target_clean) >= 6 and not target_clean.isdigit())):
        code_search = target_clean.upper()
        if not code_search.startswith("JB-") and len(code_search) >= 6:
            code_search = f"JB-{code_search}"
        found_user = await db.scalar(select(User).where(User.user_code == code_search))

    # 3. Buscar por teléfono exacto (hash o número plano)
    if not found_user and len(target_digits) >= 8:
        p_hash = hash_phone(target_digits)
        found_user = await db.scalar(select(User).where(User.phone_hash == p_hash))
        if not found_user:
            found_user = await db.scalar(select(User).where(User.phone_number == target_digits))

    # 4. Buscar entre los prospectos PENDING (por nombre o similitud de teléfono)
    if not found_user:
        pending_result = await db.execute(select(User).where(User.status == "PENDING").order_by(User.id.desc()))
        pending_users = pending_result.scalars().all()

        # 4a. Coincidencia por nombre en prospectos
        for u in pending_users:
            if u.name and target_clean.lower() in u.name.lower():
                found_user = u
                break

        # 4b. Coincidencia difusa por número de teléfono en prospectos
        if not found_user and len(target_digits) >= 6:
            for u in pending_users:
                p_dec = ""
                if u.phone_number:
                    p_dec = u.phone_number
                elif u.encrypted_phone:
                    try:
                        p_dec = decrypt_phone(u.encrypted_phone)
                    except Exception:
                        pass
                if p_dec and is_similar_phone(target_digits, p_dec):
                    found_user = u
                    real_phone = p_dec
                    break

    # 5. Si no se encontró ningún usuario existente
    if not found_user:
        # Verificar si hay prospectos en espera para orientar al administrador
        pending_result = await db.execute(select(User).where(User.status == "PENDING").order_by(User.id.desc()))
        pending_list = pending_result.scalars().all()
        if pending_list:
            leads_preview = []
            for pu in pending_list:
                p_disp = pu.phone_number or ""
                if not p_disp and pu.encrypted_phone:
                    try:
                        p_disp = decrypt_phone(pu.encrypted_phone)
                    except Exception:
                        p_disp = "Cifrado"
                leads_preview.append(f"• *{pu.name}* | `{p_disp}` (Código: `{pu.user_code}`)")
            leads_str = "\n".join(leads_preview)
            raise ValueError(
                f"No se encontró ningún prospecto con '{target_clean}'.\n\n"
                f"▪ Prospectos pendientes:\n{leads_str}\n\n"
                f"▸ Responde con su nombre, código o teléfono para autorizar."
            )

        # Si no hay prospectos y es un número de teléfono válido nuevo
        if len(target_digits) < 9:
            raise ValueError(f"No se encontró usuario y '{target_clean}' no es un teléfono válido (mínimo 9 dígitos).")

        name = clean_first_name(custom_name) if custom_name else "Cliente"
        code = generate_user_code(target_digits, name)
        found_user = User(
            phone_hash=hash_phone(target_digits),
            encrypted_phone=encrypt_phone(target_digits),
            name=name,
            user_code=code,
            phone_number=None,
            status="ACTIVE",
            is_admin=False,
        )
        db.add(found_user)
        real_phone = target_digits

    # Activar al usuario encontrado
    found_user.status = "ACTIVE"
    if custom_name and custom_name.lower() not in {"amigo", "cliente"}:
        found_user.name = clean_first_name(custom_name)

    # Obtener el teléfono real para notificación
    if not real_phone:
        if found_user.phone_number:
            real_phone = found_user.phone_number
        elif found_user.encrypted_phone:
            try:
                real_phone = decrypt_phone(found_user.encrypted_phone)
            except Exception:
                real_phone = target_digits

    await db.commit()
    return found_user, real_phone


async def block_user(db: AsyncSession, target: str) -> tuple[User | None, str]:
    target_clean = target.strip()
    target_digits = clean_phone_str(target_clean)

    found_user: User | None = None
    real_phone: str = ""

    if target_clean.upper().startswith("JB-") or (len(target_clean) >= 6 and not target_clean.isdigit()):
        found_user = await db.scalar(select(User).where(User.user_code == target_clean.upper()))

    if not found_user and len(target_digits) >= 8:
        p_hash = hash_phone(target_digits)
        found_user = await db.scalar(select(User).where(User.phone_hash == p_hash))
        if not found_user:
            found_user = await db.scalar(select(User).where(User.phone_number == target_digits))

    if not found_user:
        stmt = select(User).where(User.name.ilike(f"%{target_clean}%")).limit(1)
        found_user = await db.scalar(stmt)

    if found_user is not None:
        found_user.status = "BLOCKED"
        await db.commit()
        if found_user.phone_number:
            real_phone = found_user.phone_number
        elif found_user.encrypted_phone:
            try:
                real_phone = decrypt_phone(found_user.encrypted_phone)
            except Exception:
                real_phone = target_digits

    return found_user, real_phone or target_clean


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


async def handle_admin_command(
    db: AsyncSession,
    admin_phone: str,
    text: str,
) -> tuple[str, User | None, str | None] | None:
    """
    Evalúa si el texto recibido corresponde a un comando de administrador.
    Retorna (mensaje_para_admin, usuario_autorizado_o_None, telefono_usuario_o_None) o None.
    """
    if not is_admin_phone(admin_phone):
        clean_p = clean_phone_str(admin_phone)
        p_hash = hash_phone(clean_p)
        u = await db.scalar(select(User).where(User.phone_hash == p_hash))
        if not (u and u.is_admin):
            return None

    raw = text.strip()
    norm = raw.lower()

    # Menú de ayuda admin
    if norm in {"admin", "ayuda admin", "menu admin", "comandos admin"}:
        msg = (
            "■ *PANEL DE ADMINISTRADOR* | JaviBot\n"
            "──────────────────────────\n"
            "Comandos disponibles:\n\n"
            "• `autorizar [teléfono, nombre o código]`\n"
            "  Habilita acceso a un usuario.\n"
            "  Ejemplos:\n"
            "  - `autorizar` (activa al último prospecto en espera)\n"
            "  - `autorizar Niki` (activa por nombre)\n"
            "  - `autorizar 56961124124` (activa por teléfono)\n"
            "  - `autorizar JB-1455D61E` (activa por código)\n\n"
            "• `bloquear [teléfono, nombre o código]`\n"
            "  Revoca acceso a un usuario.\n\n"
            "• `usuarios`\n"
            "  Lista todos los usuarios activos.\n\n"
            "• `prospectos` o `pendientes`\n"
            "  Lista prospectos que quieren contratar.\n\n"
            "▸ Si envías un gasto normal (ej: 'Almuerzo 4500'), se registrará en tu cuenta personal."
        )
        return msg, None, None

    # Comando Autorizar
    if norm == "autorizar" or norm == "activar" or norm.startswith("autorizar ") or norm.startswith("activar "):
        parts = raw.split()
        target = parts[1] if len(parts) > 1 else "ultimo"
        name = " ".join(parts[2:]) if len(parts) > 2 else None
        try:
            user, real_phone = await authorize_user(db, target, name)
            reply = (
                f"✓ *Usuario autorizado con éxito*\n"
                f"▪ Nombre: *{user.name}*\n"
                f"▪ Teléfono: `{real_phone}`\n"
                f"▪ Código: `{user.user_code}`\n"
                f"▪ Estado: *ACTIVO*"
            )
            return reply, user, real_phone
        except Exception as e:
            return f"[!] Error al autorizar: {e}", None, None

    # Comando Bloquear
    if norm.startswith("bloquear ") or norm.startswith("desactivar "):
        parts = raw.split()
        if len(parts) < 2:
            return "[!] Uso incorrecto. Formato: `bloquear [teléfono, nombre o código]`", None, None
        target = parts[1]
        try:
            user, real_phone = await block_user(db, target)
            if user:
                reply = (
                    f"✓ *Usuario bloqueado con éxito*\n"
                    f"▪ Nombre: *{user.name}*\n"
                    f"▪ Teléfono: `{real_phone}`\n"
                    f"▪ Estado: *BLOQUEADO*"
                )
                return reply, None, None
            else:
                return f"[!] No se encontró un usuario para '{target}'.", None, None
        except Exception as e:
            return f"[!] Error al bloquear: {e}", None, None

    # Listar Usuarios Activos
    if norm in {"usuarios", "lista usuarios", "ver usuarios", "clientes"}:
        users = await list_active_users(db)
        if not users:
            return "■ *USUARIOS ACTIVOS*\n──────────────────────────\nNo hay usuarios activos registrados aún.", None, None
        lines = [
            f"■ *USUARIOS ACTIVOS* ({len(users)})",
            "──────────────────────────",
        ]
        for u in users:
            role = " (Admin)" if u["is_admin"] else ""
            lines.append(f"• *{u['name']}*{role} | {u['phone']} (`{u['code']}`)")
        return "\n".join(lines), None, None

    # Listar Prospectos / Pendientes
    if norm in {"prospectos", "pendientes", "lista prospectos", "leads"}:
        leads = await list_pending_leads(db)
        if not leads:
            return "■ *PROSPECTOS EN ESPERA*\n──────────────────────────\nNo hay prospectos pendientes de pago.", None, None
        lines = [
            f"■ *PROSPECTOS EN ESPERA* ({len(leads)})",
            "──────────────────────────",
        ]
        for l in leads:
            lines.append(f"• *{l['name']}* | {l['phone']} | {l['date']}")
            lines.append(f"  ▸ Autorizar: `autorizar {l['phone']}`")
        return "\n".join(lines), None, None

    return None

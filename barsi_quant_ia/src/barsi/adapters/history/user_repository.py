"""Repositório de Usuários e Controle de Acesso Baseado em Perfis (RBAC) no SQLite."""
import sqlite3
import os
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple
from contextlib import contextmanager
from barsi.infra.security import (
    hash_password,
    verify_password,
    generate_totp_secret,
    verify_totp_code,
    get_totp_uri,
)

class UserRepository:
    def __init__(self, db_path: Optional[str] = None):
        from barsi.adapters.history.budget_repository import resolve_db_path

        self.db_path = resolve_db_path(db_path)
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'leitor',
                permissoes TEXT NOT NULL,
                criado_em TEXT NOT NULL,
                totp_secret TEXT DEFAULT NULL,
                totp_enabled INTEGER NOT NULL DEFAULT 0,
                email TEXT DEFAULT NULL,
                google_id TEXT DEFAULT NULL
            );
            """)
            try:
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(usuarios)").fetchall()]
                if "totp_secret" not in cols:
                    conn.execute("ALTER TABLE usuarios ADD COLUMN totp_secret TEXT DEFAULT NULL")
                if "totp_enabled" not in cols:
                    conn.execute("ALTER TABLE usuarios ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0")
                if "email" not in cols:
                    conn.execute("ALTER TABLE usuarios ADD COLUMN email TEXT DEFAULT NULL")
                if "google_id" not in cols:
                    conn.execute("ALTER TABLE usuarios ADD COLUMN google_id TEXT DEFAULT NULL")
            except Exception:
                pass
            conn.commit()

    def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled, email, google_id FROM usuarios WHERE LOWER(username) = LOWER(?)", (username.strip(),))
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def get_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled, email, google_id FROM usuarios WHERE id = ?", (user_id,))
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        if not email:
            return None
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled, email, google_id FROM usuarios WHERE LOWER(email) = LOWER(?)", (email.strip(),))
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def get_by_google_id(self, google_id: str) -> Optional[Dict[str, Any]]:
        if not google_id:
            return None
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled, email, google_id FROM usuarios WHERE google_id = ?", (google_id.strip(),))
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def get_by_username_or_email(self, identifier: str) -> Optional[Dict[str, Any]]:
        if not identifier:
            return None
        clean_id = identifier.strip().lower()
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled, email, google_id "
                "FROM usuarios WHERE LOWER(username) = ? OR LOWER(email) = ?",
                (clean_id, clean_id)
            )
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def create_user(self, username: str, password_plain: str, role: str = "leitor", email: Optional[str] = None) -> bool:
        """Cria um usuário com senha hasheada com salt criptográfico."""
        permissoes_map = {
            "admin": "read,edit,run,sync,admin",
            "investidor": "read,edit,run,sync",
            "leitor": "read"
        }
        perms = permissoes_map.get(role, "read")
        pwd_hash = hash_password(password_plain)
        now_str = datetime.now().isoformat()
        clean_email = email.strip() if email else None

        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO usuarios (username, password_hash, role, permissoes, criado_em, email) VALUES (?, ?, ?, ?, ?, ?)",
                    (username.strip(), pwd_hash, role, perms, now_str, clean_email)
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def list_users(self) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, role, permissoes, criado_em, totp_enabled, email, google_id FROM usuarios")
            return [dict(r) for r in cur.fetchall()]

    def authenticate(self, username_or_email: str, password_plain: str) -> Optional[Dict[str, Any]]:
        """Autentica o usuário por username ou email, validando a criptografia da senha."""
        user = self.get_by_username_or_email(username_or_email)
        if not user:
            return None
        if verify_password(password_plain, user["password_hash"]):
            return user
        return None

    def update_password(self, user_id: int, new_password_plain: str) -> bool:
        """Atualiza a senha de um usuário no banco de dados."""
        if not new_password_plain or len(new_password_plain) < 6:
            return False
        pwd_hash = hash_password(new_password_plain)
        with self._connection() as conn:
            cur = conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (pwd_hash, user_id))
            conn.commit()
            return cur.rowcount > 0

    def update_email(self, user_id: int, email: Optional[str]) -> bool:
        """Atualiza o e-mail do usuário para permitir vínculo com OAuth."""
        clean_email = email.strip().lower() if email else None
        if clean_email == "":
            clean_email = None
        try:
            with self._connection() as conn:
                cur = conn.execute("UPDATE usuarios SET email = ? WHERE id = ?", (clean_email, user_id))
                conn.commit()
                return cur.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    def link_google_account(self, user_id: int, google_id: str, email: Optional[str] = None) -> bool:
        """Vincula um ID de conta Google e opcionalmente o email ao usuário."""
        if not google_id:
            return False
        with self._connection() as conn:
            if email:
                cur = conn.execute("UPDATE usuarios SET google_id = ?, email = COALESCE(email, ?) WHERE id = ?", (google_id.strip(), email.strip(), user_id))
            else:
                cur = conn.execute("UPDATE usuarios SET google_id = ? WHERE id = ?", (google_id.strip(), user_id))
            conn.commit()
            return cur.rowcount > 0

    def init_admin_if_empty(self, admin_user: str, admin_pass: str):
        """Se o banco não tiver nenhum usuário, cria o administrador inicial."""
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM usuarios")
            count = cur.fetchone()[0]
            if count == 0 and admin_user and admin_pass:
                self.create_user(admin_user, admin_pass, role="admin")

    def setup_totp(self, user_id: int) -> Tuple[str, str]:
        """Gera e registra provisoriamente um novo segredo TOTP para o usuário."""
        user = self.get_by_id(user_id)
        if not user:
            raise ValueError(f"Usuário id={user_id} não encontrado")
        secret_b32 = generate_totp_secret()
        with self._connection() as conn:
            conn.execute(
                "UPDATE usuarios SET totp_secret = ?, totp_enabled = 0 WHERE id = ?",
                (secret_b32, user_id)
            )
            conn.commit()
        uri = get_totp_uri(secret_b32, user["username"])
        return secret_b32, uri

    def confirm_and_enable_totp(self, user_id: int, code: str) -> bool:
        """Confirma o código de 6 dígitos gerado pelo app e ativa o 2FA."""
        user = self.get_by_id(user_id)
        if not user or not user.get("totp_secret"):
            return False
        if verify_totp_code(user["totp_secret"], code):
            with self._connection() as conn:
                conn.execute(
                    "UPDATE usuarios SET totp_enabled = 1 WHERE id = ?",
                    (user_id,)
                )
                conn.commit()
            return True
        return False

    def disable_totp(self, user_id: int) -> bool:
        """Desativa o 2FA e limpa o segredo TOTP."""
        with self._connection() as conn:
            conn.execute(
                "UPDATE usuarios SET totp_secret = NULL, totp_enabled = 0 WHERE id = ?",
                (user_id,)
            )
            conn.commit()
        return True

    def verify_totp(self, user_id: int, code: str) -> bool:
        """Valida o código 2FA informado para um usuário que tem 2FA ativo."""
        user = self.get_by_id(user_id)
        if not user or not user.get("totp_enabled") or not user.get("totp_secret"):
            return False
        return verify_totp_code(user["totp_secret"], code)

    def is_totp_enabled(self, user_id: int) -> bool:
        user = self.get_by_id(user_id)
        return bool(user and user.get("totp_enabled"))


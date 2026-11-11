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
    def __init__(self, db_path: str = "./memory/barsi_memory.db"):
        self.db_path = db_path
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
                totp_enabled INTEGER NOT NULL DEFAULT 0
            );
            """)
            try:
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(usuarios)").fetchall()]
                if "totp_secret" not in cols:
                    conn.execute("ALTER TABLE usuarios ADD COLUMN totp_secret TEXT DEFAULT NULL")
                if "totp_enabled" not in cols:
                    conn.execute("ALTER TABLE usuarios ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            conn.commit()

    def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled FROM usuarios WHERE username = ?", (username,))
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def get_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled FROM usuarios WHERE id = ?", (user_id,))
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def create_user(self, username: str, password_plain: str, role: str = "leitor") -> bool:
        """Cria um usuário com senha hasheada com salt criptográfico."""
        permissoes_map = {
            "admin": "read,edit,run,sync,admin",
            "investidor": "read,edit,run,sync",
            "leitor": "read"
        }
        perms = permissoes_map.get(role, "read")
        pwd_hash = hash_password(password_plain)
        now_str = datetime.now().isoformat()

        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO usuarios (username, password_hash, role, permissoes, criado_em) VALUES (?, ?, ?, ?, ?)",
                    (username, pwd_hash, role, perms, now_str)
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def list_users(self) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, role, permissoes, criado_em, totp_enabled FROM usuarios")
            return [dict(r) for r in cur.fetchall()]

    def authenticate(self, username: str, password_plain: str) -> Optional[Dict[str, Any]]:
        """Autentica o usuário validando a criptografia da senha."""
        user = self.get_by_username(username)
        if not user:
            return None
        if verify_password(password_plain, user["password_hash"]):
            return user
        return None

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


"""Repositório de Usuários e Controle de Acesso Baseado em Perfis (RBAC) no SQLite."""
import sqlite3
import os
from datetime import datetime
from typing import Optional, Dict, List, Any
from contextlib import contextmanager
from barsi.infra.security import hash_password, verify_password

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
                criado_em TEXT NOT NULL
            );
            """)
            conn.commit()

    def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, password_hash, role, permissoes FROM usuarios WHERE username = ?", (username,))
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
            cur.execute("SELECT id, username, role, permissoes, criado_em FROM usuarios")
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

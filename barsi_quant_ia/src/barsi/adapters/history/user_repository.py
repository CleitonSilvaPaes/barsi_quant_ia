"""Repositório de Usuários e Controle de Acesso Baseado em Perfis (RBAC) no SQLite."""
import sqlite3
import os
import json
import time
import socket
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple
from contextlib import contextmanager
from barsi.infra.security import (
    hash_password,
    verify_password,
    generate_totp_secret,
    verify_totp_code,
    get_totp_uri,
    generate_backup_codes_pair,
    verify_backup_code,
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
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
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
                google_id TEXT DEFAULT NULL,
                backup_codes_hash TEXT DEFAULT NULL
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
                if "backup_codes_hash" not in cols:
                    conn.execute("ALTER TABLE usuarios ADD COLUMN backup_codes_hash TEXT DEFAULT NULL")
            except Exception:
                pass

            conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id       INTEGER,
                actor_username TEXT,
                action         TEXT NOT NULL,
                target         TEXT,
                reason         TEXT,
                ip             TEXT,
                hostname       TEXT,
                created_at     TEXT DEFAULT (datetime('now'))
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action, created_at);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS session_revocation (
                jti         TEXT PRIMARY KEY,
                user_id     INTEGER,
                revoked_at  REAL NOT NULL,
                expires_at  REAL NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_revocation_exp ON session_revocation(expires_at);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ip            TEXT NOT NULL,
                attempted_at  REAL NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip, attempted_at);")
            conn.commit()

    def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled, email, google_id, backup_codes_hash "
                "FROM usuarios WHERE LOWER(username) = LOWER(?)",
                (username.strip(),)
            )
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def get_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled, email, google_id, backup_codes_hash "
                "FROM usuarios WHERE id = ?",
                (user_id,)
            )
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        if not email:
            return None
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled, email, google_id, backup_codes_hash "
                "FROM usuarios WHERE LOWER(email) = LOWER(?)",
                (email.strip(),)
            )
            row = cur.fetchone()
            if row:
                return dict(row)
        return None

    def get_by_google_id(self, google_id: str) -> Optional[Dict[str, Any]]:
        if not google_id:
            return None
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled, email, google_id, backup_codes_hash "
                "FROM usuarios WHERE google_id = ?",
                (google_id.strip(),)
            )
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
                "SELECT id, username, password_hash, role, permissoes, totp_secret, totp_enabled, email, google_id, backup_codes_hash "
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

    def disable_totp(self, user_id: int, *, bypass_verification: bool = True) -> bool:
        """Desativa o 2FA e limpa o segredo TOTP e códigos de recuperação."""
        if not bypass_verification:
            user = self.get_by_id(user_id)
            if user and user.get("totp_enabled"):
                raise ValueError("disable_totp requer bypass_verification=True para chamadas diretas.")
        with self._connection() as conn:
            conn.execute(
                "UPDATE usuarios SET totp_secret = NULL, totp_enabled = 0, backup_codes_hash = NULL WHERE id = ?",
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

    def generate_backup_codes(self, user_id: int, count: int = 8) -> List[str]:
        """Gera e salva hashes de códigos de backup únicos para o usuário."""
        plain_codes, hashed_codes = generate_backup_codes_pair(count=count)
        with self._connection() as conn:
            conn.execute(
                "UPDATE usuarios SET backup_codes_hash = ? WHERE id = ?",
                (json.dumps(hashed_codes), user_id)
            )
            conn.commit()
        return plain_codes

    def verify_and_consume_backup_code(self, user_id: int, code: str) -> bool:
        """Verifica se o código fornecido é um backup code válido e o consome se for."""
        user = self.get_by_id(user_id)
        if not user or not user.get("backup_codes_hash"):
            return False
        try:
            hashes = json.loads(user["backup_codes_hash"])
            if not isinstance(hashes, list) or not hashes:
                return False
        except Exception:
            return False

        ok, remaining = verify_backup_code(code, hashes)
        if ok:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE usuarios SET backup_codes_hash = ? WHERE id = ?",
                    (json.dumps(remaining) if remaining else None, user_id)
                )
                conn.commit()
            return True
        return False

    def verify_totp_or_backup_code(self, user_id: int, code: str) -> Tuple[bool, str]:
        """Valida se o código informado é um TOTP ativo ou um Backup Code válido."""
        if self.verify_totp(user_id, code):
            return True, "totp"
        if self.verify_and_consume_backup_code(user_id, code):
            return True, "backup_code"
        return False, ""

    def get_backup_codes_count(self, user_id: int) -> int:
        """Retorna a quantidade de códigos de backup restantes para o usuário."""
        user = self.get_by_id(user_id)
        if not user or not user.get("backup_codes_hash"):
            return 0
        try:
            hashes = json.loads(user["backup_codes_hash"])
            return len(hashes) if isinstance(hashes, list) else 0
        except Exception:
            return 0

    def log_audit(
        self,
        action: str,
        actor_id: Optional[int] = None,
        actor_username: Optional[str] = None,
        target: Optional[str] = None,
        reason: Optional[str] = None,
        ip: Optional[str] = None,
        hostname: Optional[str] = None
    ) -> int:
        """Registra um evento com carimbo de tempo na tabela audit_log."""
        if not hostname:
            try:
                hostname = socket.gethostname()
            except Exception:
                hostname = None
        with self._connection() as conn:
            cur = conn.execute(
                "INSERT INTO audit_log (actor_id, actor_username, action, target, reason, ip, hostname) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (actor_id, actor_username, action, target, reason, ip, hostname)
            )
            conn.commit()
            return cur.lastrowid

    def list_audit_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retorna os registros mais recentes da tabela audit_log."""
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, actor_id, actor_username, action, target, reason, ip, hostname, created_at "
                "FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            return [dict(r) for r in cur.fetchall()]

    def revoke_session(self, jti: str, user_id: Optional[int] = None, expires_at: Optional[float] = None) -> bool:
        """Registra um JTI na lista de revogação imediata."""
        if not jti:
            return False
        now = time.time()
        exp = float(expires_at or (now + 86400 * 7))
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO session_revocation (jti, user_id, revoked_at, expires_at) VALUES (?, ?, ?, ?)",
                (jti, user_id, now, exp)
            )
            conn.commit()
        return True

    def is_session_revoked(self, jti: str) -> bool:
        """Verifica se um JTI foi revogado."""
        if not jti:
            return False
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM session_revocation WHERE jti = ?", (jti,))
            return cur.fetchone() is not None

    def clean_expired_session_revocations(self) -> int:
        """Exclui revogações cujos tokens já expiraram naturalmente."""
        now = time.time()
        with self._connection() as conn:
            cur = conn.execute("DELETE FROM session_revocation WHERE expires_at < ?", (now,))
            conn.commit()
            return cur.rowcount

    def record_login_attempt(self, ip: str) -> None:
        """Registra tentativa de login mal sucedida no SQLite (inserção rápida O(1))."""
        now = time.time()
        with self._connection() as conn:
            conn.execute("INSERT INTO login_attempts (ip, attempted_at) VALUES (?, ?)", (ip, now))
            conn.commit()

    def clean_expired_login_attempts(self, max_age_sec: float = 86400.0) -> int:
        """Remove tentativas de login anteriores ao período configurado (padrão 24h)."""
        cutoff = time.time() - max_age_sec
        with self._connection() as conn:
            cur = conn.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (cutoff,))
            conn.commit()
            return cur.rowcount

    def get_recent_login_attempts(self, ip: str, window_sec: float = 60.0) -> int:
        """Retorna número de tentativas para o IP na janela especificada."""
        cutoff = time.time() - window_sec
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND attempted_at >= ?", (ip, cutoff))
            return cur.fetchone()[0]

    def clear_login_attempts(self, ip: str) -> None:
        """Limpa tentativas de login após autenticação bem-sucedida."""
        with self._connection() as conn:
            conn.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
            conn.commit()


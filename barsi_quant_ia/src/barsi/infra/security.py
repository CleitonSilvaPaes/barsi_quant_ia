"""Módulo de Criptografia, Hashing de Senhas e Tokens Seguros de Sessão."""
import os
import hmac
import hashlib
import json
import base64
import time
import struct
import secrets
import urllib.parse
from typing import Optional, Dict, Any

def generate_totp_secret() -> str:
    """Gera um segredo Base32 criptograficamente seguro de 160 bits (20 bytes) para 2FA TOTP."""
    raw = secrets.token_bytes(20)
    return base64.b32encode(raw).decode("utf-8").rstrip("=")

def generate_totp_code(secret_b32: str, timestamp: Optional[int] = None, interval: int = 30) -> str:
    """Calcula o token TOTP RFC 6238 de 6 dígitos para o segredo fornecido."""
    if timestamp is None:
        timestamp = int(time.time())
    counter = int(timestamp // interval)
    
    # Corrige padding base32 se ausente
    secret_clean = secret_b32.strip().replace(" ", "").upper()
    pad_len = (8 - (len(secret_clean) % 8)) % 8
    secret_clean += "=" * pad_len
    key = base64.b32decode(secret_clean, casefold=True)
    
    # 8 bytes em big-endian
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    
    # Dynamic truncation (RFC 4226)
    offset = h[-1] & 0x0F
    code_int = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{code_int % 1_000_000:06d}"

def verify_totp_code(secret_b32: str, code: str, window: int = 1) -> bool:
    """Valida o código de 6 dígitos com janela de tolerância de +/- window intervalos (padrão 30s)."""
    if not secret_b32 or not code:
        return False
    code_clean = code.strip().replace(" ", "")
    if len(code_clean) != 6 or not code_clean.isdigit():
        return False
        
    now = int(time.time())
    for offset in range(-window, window + 1):
        expected = generate_totp_code(secret_b32, timestamp=now + (offset * 30))
        if hmac.compare_digest(code_clean, expected):
            return True
    return False

def get_totp_uri(secret_b32: str, username: str, issuer: str = "BarsiQuantIA") -> str:
    """Gera a URI otpauth:// padrão para aplicativos autenticadores (Google Authenticator, Authy, 1Password)."""
    encoded_issuer = urllib.parse.quote(issuer)
    encoded_user = urllib.parse.quote(username)
    label = f"{encoded_issuer}:{encoded_user}"
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={encoded_issuer}&algorithm=SHA1&digits=6&period=30"


def hash_password(plain_password: str) -> str:
    """Gera um hash criptográfico seguro usando PBKDF2-HMAC-SHA256 com Salt aleatório."""
    salt = os.urandom(16)
    iterations = 120_000
    derived = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, iterations)
    salt_hex = salt.hex()
    hash_hex = derived.hex()
    return f"{salt_hex}${iterations}${hash_hex}"

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica se a senha em texto plano confere com o hash armazenado (protegido contra timing attacks)."""
    try:
        salt_hex, iter_str, expected_hash_hex = hashed_password.split("$")
        salt = bytes.fromhex(salt_hex)
        iterations = int(iter_str)
        derived = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(derived.hex(), expected_hash_hex)
    except Exception:
        return False

def create_session_token(user_id: int, username: str, role: str, secret_key: str, expires_in_sec: int = 86400 * 7) -> str:
    """Cria um token assinado criptograficamente com HMAC-SHA256 contendo ID, usuário, perfil e expiração."""
    payload = {
        "uid": user_id,
        "user": username,
        "role": role,
        "exp": int(time.time() + expires_in_sec)
    }
    payload_json = json.dumps(payload, separators=(',', ':')).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode("utf-8").rstrip("=")
    
    signature = hmac.new(secret_key.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"

def decode_session_token(token: str, secret_key: str) -> Optional[Dict[str, Any]]:
    """Valida a assinatura criptográfica e a data de expiração do token de sessão."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, signature = token.split(".", 1)
        expected_sig = hmac.new(secret_key.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        
        # Corrige padding base64
        padding = 4 - (len(payload_b64) % 4)
        if padding != 4:
            payload_b64 += "=" * padding
            
        payload_json = base64.urlsafe_b64decode(payload_b64).decode("utf-8")
        data = json.loads(payload_json)
        
        if data.get("exp", 0) < time.time():
            return None  # Token expirado
            
        return data
    except Exception:
        return None


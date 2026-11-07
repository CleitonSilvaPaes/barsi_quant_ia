"""Módulo de Criptografia, Hashing de Senhas e Tokens Seguros de Sessão."""
import os
import hmac
import hashlib
import json
import base64
import time
from typing import Optional, Dict, Any

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


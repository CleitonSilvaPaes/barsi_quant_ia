"""Rotas de autenticação, sessão, rate-limiting, 2FA (TOTP) e perfil de usuário."""
import os
import time
import secrets
import urllib.parse
from datetime import datetime
from typing import Optional
import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from barsi.config import settings
from barsi.infra.logging import logger
from barsi.adapters.factory import AdapterFactory
from barsi.infra.security import (
    create_session_token,
    decode_session_token,
    generate_csrf_token,
    create_2fa_pending_token,
    decode_2fa_pending_token,
)
from barsi.api.common import (
    TEMPLATES_DIR,
    _login_attempts,
    LOGIN_RATE_LIMIT_MAX,
    LOGIN_RATE_LIMIT_WINDOW,
    _cleanup_login_attempts,
    _get_client_ip,
    set_barsi_cookie,
    get_user_repo,
    get_current_user,
    ChangePasswordRequest,
    TotpConfirmRequest,
)

router = APIRouter(tags=["auth"])


async def _send_security_alert(title: str, body: str, level: str = "alert") -> None:
    """Dispara alerta proativo de segurança via adaptador de notificações configurado."""
    try:
        notifier = AdapterFactory.create_notification_adapter(settings)
        await notifier.notify(title=title, body=body, level=level)
    except Exception as exc:
        logger.warning("security_push_notification_failed", title=title, error=str(exc))

# ----------------- ROTAS DE LOGIN, LOGOUT & SESSÃO -----------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    login_path = os.path.join(TEMPLATES_DIR, "login.html")
    with open(login_path, "r", encoding="utf-8") as f:
        html = f.read()
    err_html = f'<div class="err">{error}</div>' if error else ""
    session_token = request.cookies.get("barsi_session", "")
    csrf_token = generate_csrf_token(session_token, settings.secret_key)
    html = html.replace("{{ERROR_MSG}}", err_html).replace("{{CSRF_TOKEN}}", csrf_token)
    res = HTMLResponse(content=html)
    set_barsi_cookie(response=res, key="barsi_csrf", value=csrf_token, request=request, httponly=False, samesite="lax", path="/")
    return res

@router.post("/login")
async def do_login(request: Request):
    client_ip = _get_client_ip(request)
    now = time.time()
    repo = get_user_repo()
    _cleanup_login_attempts(now)

    recent_attempts = [t for t in _login_attempts.get(client_ip, []) if now - t < LOGIN_RATE_LIMIT_WINDOW]
    if len(recent_attempts) >= LOGIN_RATE_LIMIT_MAX:
        logger.warning("login_rate_limited_memory", ip=client_ip, count=len(recent_attempts))
        return RedirectResponse(url="/login?error=Muitas+tentativas.+Aguarde+1+minuto.", status_code=303)

    db_attempts = repo.get_recent_login_attempts(client_ip, LOGIN_RATE_LIMIT_WINDOW)
    if db_attempts >= LOGIN_RATE_LIMIT_MAX:
        logger.warning("login_rate_limited_sqlite", ip=client_ip, count=db_attempts)
        return RedirectResponse(url="/login?error=Muitas+tentativas.+Aguarde+1+minuto.", status_code=303)

    body = (await request.body()).decode("utf-8", errors="ignore")
    params = urllib.parse.parse_qs(body)
    username = params.get("username", [""])[0].strip()
    password = params.get("password", [""])[0].strip()
    totp_code = params.get("totp_code", [""])[0].strip()

    user = repo.authenticate(username, password)
    if user:
        if user.get("totp_enabled"):
            if totp_code:
                ok, method_used = repo.verify_totp_or_backup_code(user["id"], totp_code)
                if not ok:
                    recent_attempts.append(now)
                    _login_attempts[client_ip] = recent_attempts
                    repo.record_login_attempt(client_ip)
                    repo.log_audit("login_totp_failed", actor_id=user["id"], actor_username=username, ip=client_ip, reason="invalid_totp_or_backup")
                    logger.warning("login_totp_failed", ip=client_ip, username=username, attempts=len(recent_attempts))
                    if len(recent_attempts) == LOGIN_RATE_LIMIT_MAX:
                        await _send_security_alert(
                            title="Alerta de Segurança: Tentativas Excessivas de 2FA",
                            body=(
                                f"• *Tentativas Falhas:* `{len(recent_attempts)}`\n"
                                f"• *IP de Origem:* `{client_ip}`\n"
                                f"• *Usuário Visado:* `{username}`\n"
                                f"• *Ação:* Bloqueio temporário por taxa limite ativado.\n"
                                f"• *Data/Hora:* `{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}`"
                            ),
                            level="alert"
                        )
                    return RedirectResponse(url="/login?error=Código+2FA+(TOTP)+ou+de+recuperação+inválido", status_code=303)
                if method_used == "backup_code":
                    repo.log_audit("backup_code_used", actor_id=user["id"], actor_username=username, ip=client_ip, reason="login_recovery")
            else:
                pending_token = create_2fa_pending_token(user["id"], user["username"], settings.secret_key)
                res = RedirectResponse(url="/login/2fa", status_code=303)
                set_barsi_cookie(response=res, key="barsi_2fa_pending", value=pending_token, request=request, httponly=True, max_age=300, samesite="lax")
                return res

        _login_attempts.pop(client_ip, None)
        repo.clear_login_attempts(client_ip)
        repo.log_audit("login_success", actor_id=user["id"], actor_username=username, ip=client_ip)
        logger.info("login_success", ip=client_ip, username=username, role=user["role"])
        jti = secrets.token_hex(16)
        token = create_session_token(
            user["id"],
            user["username"],
            user["role"],
            settings.secret_key,
            jti=jti,
            session_version=user.get("session_version", 1)
        )

        # 2FA obrigatório para administradores: se não tiver TOTP configurado, redireciona para setup no settings
        redirect_target = (
            "/settings?warn=Ative+o+2FA+para+garantir+a+seguranca+da+sua+conta#2fa"
            if (user.get("role") == "admin" and not user.get("totp_enabled"))
            else "/"
        )
        res = RedirectResponse(url=redirect_target, status_code=303)
        set_barsi_cookie(response=res, key="barsi_session", value=token, request=request, httponly=True, max_age=86400 * 7, samesite="lax")
        csrf_token = repo.rotate_session_csrf(jti, user_id=user["id"])
        set_barsi_cookie(response=res, key="barsi_csrf", value=csrf_token, request=request, httponly=False, max_age=86400 * 7, samesite="lax", path="/")
        return res

    recent_attempts.append(now)
    _login_attempts[client_ip] = recent_attempts
    repo.record_login_attempt(client_ip)
    repo.log_audit("login_failed", actor_username=username, ip=client_ip, reason="invalid_credentials")
    logger.warning("login_failed", ip=client_ip, username=username, attempts=len(recent_attempts))
    if len(recent_attempts) == LOGIN_RATE_LIMIT_MAX:
        await _send_security_alert(
            title="Alerta de Segurança: Múltiplas Tentativas de Login Falhas",
            body=(
                f"• *Tentativas Falhas:* `{len(recent_attempts)}` em menos de {LOGIN_RATE_LIMIT_WINDOW // 60} minuto(s)\n"
                f"• *IP de Origem:* `{client_ip}`\n"
                f"• *Usuário Visado:* `{username or 'desconhecido'}`\n"
                f"• *Ação:* IP bloqueado temporariamente por taxa limite.\n"
                f"• *Data/Hora:* `{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}`"
            ),
            level="alert"
        )
    return RedirectResponse(url="/login?error=Usuário+ou+senha+incorretos", status_code=303)

@router.get("/login/2fa", response_class=HTMLResponse)
async def login_2fa_page(request: Request, error: Optional[str] = None):
    pending_token = request.cookies.get("barsi_2fa_pending")
    if not pending_token:
        return RedirectResponse(url="/login?error=Sessão+2FA+não+iniciada.+Faça+login+primeiro.", status_code=303)
    data = decode_2fa_pending_token(pending_token, settings.secret_key)
    if not data:
        return RedirectResponse(url="/login?error=Sessão+2FA+expirada.+Faça+login+novamente.", status_code=303)

    login_2fa_path = os.path.join(TEMPLATES_DIR, "login_2fa.html")
    with open(login_2fa_path, "r", encoding="utf-8") as f:
        html = f.read()
    err_html = f'<div class="err">{error}</div>' if error else ""
    csrf_token = generate_csrf_token(pending_token, settings.secret_key)
    html = html.replace("{{ERROR_MSG}}", err_html).replace("{{CSRF_TOKEN}}", csrf_token)
    res = HTMLResponse(content=html)
    set_barsi_cookie(response=res, key="barsi_csrf", value=csrf_token, request=request, httponly=False, samesite="lax", path="/")
    return res

@router.post("/login/2fa")
async def do_login_2fa(request: Request):
    client_ip = _get_client_ip(request)
    repo = get_user_repo()
    now = time.time()
    _cleanup_login_attempts(now)

    recent_attempts = [t for t in _login_attempts.get(client_ip, []) if now - t < LOGIN_RATE_LIMIT_WINDOW]
    if len(recent_attempts) >= LOGIN_RATE_LIMIT_MAX:
        logger.warning("login_2fa_rate_limited_memory", ip=client_ip, count=len(recent_attempts))
        return RedirectResponse(url="/login/2fa?error=Muitas+tentativas.+Aguarde+1+minuto.", status_code=303)

    db_attempts = repo.get_recent_login_attempts(client_ip, LOGIN_RATE_LIMIT_WINDOW)
    if db_attempts >= LOGIN_RATE_LIMIT_MAX:
        logger.warning("login_2fa_rate_limited_sqlite", ip=client_ip, count=db_attempts)
        return RedirectResponse(url="/login/2fa?error=Muitas+tentativas.+Aguarde+1+minuto.", status_code=303)

    pending_token = request.cookies.get("barsi_2fa_pending")
    if not pending_token:
        recent_attempts.append(now)
        _login_attempts[client_ip] = recent_attempts
        repo.record_login_attempt(client_ip)
        repo.log_audit("login_2fa_no_token", ip=client_ip, reason="missing_2fa_pending_cookie")
        return RedirectResponse(url="/login?error=Sessão+2FA+não+iniciada.+Faça+login+primeiro.", status_code=303)
    data = decode_2fa_pending_token(pending_token, settings.secret_key)
    if not data:
        recent_attempts.append(now)
        _login_attempts[client_ip] = recent_attempts
        repo.record_login_attempt(client_ip)
        repo.log_audit("login_2fa_expired_token", ip=client_ip, reason="invalid_or_expired_2fa_token")
        return RedirectResponse(url="/login?error=Sessão+2FA+expirada.+Faça+login+novamente.", status_code=303)

    body = (await request.body()).decode("utf-8", errors="ignore")
    params = urllib.parse.parse_qs(body)
    totp_code = params.get("totp_code", [""])[0].strip()

    user_id = data["uid"]
    ok, method_used = repo.verify_totp_or_backup_code(user_id, totp_code) if totp_code else (False, "")
    if not ok:
        recent_attempts.append(now)
        _login_attempts[client_ip] = recent_attempts
        repo.record_login_attempt(client_ip)
        repo.log_audit("login_2fa_failed", actor_id=user_id, actor_username=data.get("user"), ip=client_ip, reason="invalid_totp_or_backup")
        logger.warning("login_2fa_invalid_code", user_id=user_id, username=data.get("user"))
        if len(recent_attempts) == LOGIN_RATE_LIMIT_MAX:
            await _send_security_alert(
                title="Alerta de Segurança: Múltiplas Falhas de 2FA",
                body=(
                    f"• *Tentativas Falhas:* `{len(recent_attempts)}`\n"
                    f"• *IP de Origem:* `{client_ip}`\n"
                    f"• *Usuário ID:* `{data.get('user') or user_id}`\n"
                    f"• *Ação:* Bloqueio temporário por taxa limite ativado.\n"
                    f"• *Data/Hora:* `{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}`"
                ),
                level="alert"
            )
        return RedirectResponse(url="/login/2fa?error=Código+2FA+ou+de+recuperação+inválido.+Tente+novamente.", status_code=303)

    user = repo.get_by_id(user_id)
    if not user:
        return RedirectResponse(url="/login?error=Usuário+não+encontrado", status_code=303)

    if method_used == "backup_code":
        repo.log_audit("backup_code_used", actor_id=user_id, actor_username=user["username"], ip=client_ip, reason="login_2fa_recovery")

    _login_attempts.pop(client_ip, None)
    repo.clear_login_attempts(client_ip)
    repo.log_audit("login_2fa_success", actor_id=user_id, actor_username=user["username"], ip=client_ip)
    logger.info("login_2fa_success", user_id=user_id, username=user["username"], role=user["role"])
    jti = secrets.token_hex(16)
    token = create_session_token(
        user["id"],
        user["username"],
        user["role"],
        settings.secret_key,
        jti=jti,
        session_version=user.get("session_version", 1)
    )
    res = RedirectResponse(url="/", status_code=303)
    set_barsi_cookie(response=res, key="barsi_session", value=token, request=request, httponly=True, max_age=86400 * 7, samesite="lax")
    csrf_token = repo.rotate_session_csrf(jti, user_id=user["id"])
    set_barsi_cookie(response=res, key="barsi_csrf", value=csrf_token, request=request, httponly=False, max_age=86400 * 7, samesite="lax", path="/")
    res.delete_cookie(key="barsi_2fa_pending")
    return res

@router.get("/auth/google/login")
async def google_login(request: Request):
    if not settings.google_client_id:
        return RedirectResponse(
            url="/login?error=Google+OAuth+ainda+não+configurado.+Configure+BARSI_GOOGLE_CLIENT_ID+no+ambiente.",
            status_code=303
        )
    state = secrets.token_urlsafe(24)
    redirect_uri = settings.google_redirect_uri
    if not redirect_uri:
        redirect_uri = str(request.base_url).rstrip("/") + "/auth/google/callback"
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    res = RedirectResponse(url=auth_url, status_code=303)
    set_barsi_cookie(response=res, key="barsi_oauth_state", value=state, request=request, httponly=True, max_age=600, samesite="lax")
    return res

@router.get("/auth/google/callback")
async def google_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    if error:
        return RedirectResponse(url=f"/login?error=Autenticação+Google+cancelada:+{urllib.parse.quote(error)}", status_code=303)
    if not code or not state:
        return RedirectResponse(url="/login?error=Parâmetros+OAuth+inválidos", status_code=303)
    cookie_state = request.cookies.get("barsi_oauth_state")
    if not cookie_state or cookie_state != state:
        return RedirectResponse(url="/login?error=Estado+OAuth+inválido+ou+expirado", status_code=303)

    redirect_uri = settings.google_redirect_uri
    if not redirect_uri:
        redirect_uri = str(request.base_url).rstrip("/") + "/auth/google/callback"

    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(token_url, data=token_data)
            if token_resp.status_code != 200:
                logger.error("google_oauth_token_exchange_failed", status=token_resp.status_code, body=token_resp.text)
                return RedirectResponse(url="/login?error=Falha+na+comunicação+com+o+Google", status_code=303)
            tokens = token_resp.json()
            access_token = tokens.get("access_token")
            userinfo_resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            if userinfo_resp.status_code != 200:
                return RedirectResponse(url="/login?error=Falha+ao+obter+dados+do+Google", status_code=303)
            userinfo = userinfo_resp.json()
    except Exception as e:
        logger.error("google_oauth_exception", error=str(e))
        return RedirectResponse(url=f"/login?error=Erro+no+login+com+Google:+{urllib.parse.quote(str(e))}", status_code=303)

    google_id = userinfo.get("sub")
    email = userinfo.get("email")
    if not email:
        return RedirectResponse(url="/login?error=Google+não+retornou+o+email+do+usuário", status_code=303)

    repo = get_user_repo()
    user = repo.get_by_google_id(google_id)
    if not user:
        user = repo.get_by_email(email)
        if user:
            repo.link_google_account(user["id"], google_id, email)
        else:
            users = repo.list_users()
            if len(users) == 1:
                user = repo.get_by_id(users[0]["id"])
                repo.link_google_account(user["id"], google_id, email)
            else:
                return RedirectResponse(
                    url=f"/login?error=Conta+Google+({urllib.parse.quote(email)})+não+vinculada.+Solicite+ao+administrador.",
                    status_code=303
                )

    if user.get("totp_enabled"):
        pending_token = create_2fa_pending_token(user["id"], user["username"], settings.secret_key)
        res = RedirectResponse(url="/login/2fa", status_code=303)
        set_barsi_cookie(response=res, key="barsi_2fa_pending", value=pending_token, request=request, httponly=True, max_age=300, samesite="lax")
        res.delete_cookie(key="barsi_oauth_state")
        return res

    redirect_target = (
        "/settings?warn=Ative+o+2FA+para+garantir+a+seguranca+da+sua+conta#2fa"
        if (user.get("role") == "admin" and not user.get("totp_enabled"))
        else "/"
    )
    token = create_session_token(
        user["id"],
        user["username"],
        user["role"],
        settings.secret_key,
        session_version=user.get("session_version", 1)
    )
    res = RedirectResponse(url=redirect_target, status_code=303)
    set_barsi_cookie(response=res, key="barsi_session", value=token, request=request, httponly=True, max_age=86400 * 7, samesite="lax")
    csrf_token = generate_csrf_token(token, settings.secret_key)
    set_barsi_cookie(response=res, key="barsi_csrf", value=csrf_token, request=request, httponly=False, max_age=86400 * 7, samesite="lax", path="/")
    res.delete_cookie(key="barsi_oauth_state")
    return res

@router.get("/logout")
async def do_logout(request: Request):
    token = request.cookies.get("barsi_session")
    if token:
        data = decode_session_token(token, settings.secret_key)
        if data and data.get("jti"):
            repo = get_user_repo()
            repo.revoke_session(data["jti"], user_id=data.get("uid"), expires_at=data.get("exp"))
            repo.delete_session_csrf(data["jti"])
            repo.log_audit(
                action="auth_logout",
                actor_id=data.get("uid"),
                actor_username=data.get("user"),
                reason="user_logout"
            )
    res = RedirectResponse(url="/login", status_code=303)
    res.delete_cookie(key="barsi_session")
    res.delete_cookie(key="barsi_csrf")
    return res

# ----------------- ROTAS DE PERFIL & SENHA -----------------

@router.get("/api/user/profile")
async def get_user_profile_api(request: Request):
    """Retorna dados do perfil do usuário autenticado para multi-tenancy."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return {
        "user_id": user.get("uid", 1),
        "username": user.get("user", "admin"),
        "role": user.get("role", "leitor")
    }

@router.post("/api/user/change-password")
async def change_user_password_self(req: ChangePasswordRequest, request: Request):
    """Altera a senha do usuário autenticado exigindo a confirmação da senha atual."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    if not req.new_password or len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="A nova senha deve ter no mínimo 6 caracteres")

    client_ip = _get_client_ip(request)
    repo = get_user_repo()
    auth_check = repo.authenticate(user["user"], req.current_password)
    if not auth_check:
        repo.record_login_attempt(client_ip)
        repo.log_audit(
            "user_change_password_failed",
            actor_id=user["uid"],
            actor_username=user["user"],
            ip=client_ip,
            reason="bad_current_password"
        )
        raise HTTPException(status_code=400, detail="Senha atual incorreta.")

    success = repo.update_password(user["uid"], req.new_password)
    if not success:
        raise HTTPException(status_code=500, detail="Falha ao atualizar a senha")

    repo.log_audit(
        "user_change_password_success",
        actor_id=user["uid"],
        actor_username=user["user"],
        ip=client_ip
    )
    await _send_security_alert(
        title="Alerta de Segurança: Senha Mestre Alterada",
        body=(
            f"• *Usuário:* `{user['user']}`\n"
            f"• *IP de Origem:* `{client_ip}`\n"
            f"• *Data/Hora:* `{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}`\n"
            f"• *Ação:* Senha alterada com sucesso e outras sessões ativas foram encerradas."
        ),
        level="warn"
    )
    user_updated = repo.get_by_id(user["uid"])
    if not user_updated:
        raise HTTPException(status_code=401, detail="Usuário não encontrado ou sessão expirada")
    new_jti = secrets.token_hex(16)
    new_token = create_session_token(
        user_updated["id"],
        user_updated["username"],
        user_updated["role"],
        settings.secret_key,
        jti=new_jti,
        session_version=user_updated.get("session_version", 1),
    )
    res = JSONResponse(content={
        "ok": True,
        "message": "Senha atualizada com sucesso! Outras sessões foram encerradas."
    })
    set_barsi_cookie(
        response=res, key="barsi_session", value=new_token,
        request=request, httponly=True, max_age=86400 * 7, samesite="lax"
    )
    new_csrf = repo.rotate_session_csrf(new_jti, user_id=user["uid"])
    set_barsi_cookie(
        response=res, key="barsi_csrf", value=new_csrf,
        request=request, httponly=False, max_age=86400 * 7, samesite="lax", path="/"
    )
    return res

# ----------------- ROTAS 2FA (TOTP) RFC 6238 -----------------

@router.post("/api/auth/2fa/setup")
async def setup_2fa(request: Request):
    """Inicia a configuração de 2FA gerando um segredo TOTP e URI para o usuário atual."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = get_user_repo()
    secret_b32, uri = repo.setup_totp(user["uid"])
    return {
        "ok": True,
        "secret": secret_b32,
        "uri": uri,
        "qr_code_url": f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(uri)}"
    }

@router.post("/api/auth/2fa/enable")
async def enable_2fa(req: TotpConfirmRequest, request: Request):
    """Valida o código de 6 dígitos gerado no app e conclui a ativação do 2FA, gerando códigos de recuperação."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = get_user_repo()
    success = repo.confirm_and_enable_totp(user["uid"], req.code)
    if not success:
        raise HTTPException(status_code=400, detail="Código de 6 dígitos inválido ou expirado.")
    backup_codes = repo.generate_backup_codes(user["uid"])
    repo.log_audit(
        action="2fa_enabled",
        actor_id=user["uid"],
        actor_username=user.get("user") or user.get("username"),
        reason="user_self_service"
    )
    await _send_security_alert(
        title="Segurança da Conta: 2FA Ativado",
        body=(
            f"• *Usuário:* `{user.get('user') or user.get('username')}`\n"
            f"• *IP de Origem:* `{_get_client_ip(request)}`\n"
            f"• *Data/Hora:* `{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}`\n"
            f"• *Status:* Autenticação em dois fatores ativada e códigos de recuperação gerados."
        ),
        level="info"
    )
    return {
        "ok": True,
        "message": "2FA (TOTP) ativado com sucesso!",
        "backup_codes": backup_codes
    }

@router.post("/api/auth/2fa/disable")
async def disable_2fa(req: TotpConfirmRequest, request: Request):
    """Desativa o 2FA para o usuário atual após validar o código TOTP vigente (proof-of-possession)."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = get_user_repo()
    try:
        repo.disable_totp(user["uid"], totp_code=req.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    repo.log_audit(
        action="2fa_disabled",
        actor_id=user["uid"],
        actor_username=user.get("user") or user.get("username"),
        reason="user_self_service"
    )
    await _send_security_alert(
        title="Alerta Crítico: 2FA Desativado",
        body=(
            f"• *Usuário:* `{user.get('user') or user.get('username')}`\n"
            f"• *IP de Origem:* `{_get_client_ip(request)}`\n"
            f"• *Data/Hora:* `{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}`\n\n"
            f"⚠️ _Se você não realizou esta alteração, acesse a conta imediatamente e redefina sua senha mestre._"
        ),
        level="alert"
    )
    user_updated = repo.get_by_id(user["uid"])
    if not user_updated:
        raise HTTPException(status_code=401, detail="Usuário não encontrado ou sessão expirada")
    new_jti = secrets.token_hex(16)
    new_token = create_session_token(
        user_updated["id"],
        user_updated["username"],
        user_updated["role"],
        settings.secret_key,
        jti=new_jti,
        session_version=user_updated.get("session_version", 1),
    )
    res = JSONResponse(content={
        "ok": True,
        "message": "2FA (TOTP) desativado com sucesso! Outras sessões foram encerradas."
    })
    set_barsi_cookie(
        response=res, key="barsi_session", value=new_token,
        request=request, httponly=True, max_age=86400 * 7, samesite="lax"
    )
    new_csrf = repo.rotate_session_csrf(new_jti, user_id=user["uid"])
    set_barsi_cookie(
        response=res, key="barsi_csrf", value=new_csrf,
        request=request, httponly=False, max_age=86400 * 7, samesite="lax", path="/"
    )
    return res


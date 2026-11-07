"""Script de deploy automatizado para TV Box via SSH/SFTP."""
import os
import sys
import getpass
import paramiko
from scp import SCPClient

TVBOX_IP = os.getenv("BARSI_TVBOX_IP", "192.168.100.72")
TVBOX_USER = os.getenv("BARSI_TVBOX_USER", "cleiton")
TVBOX_PASS = os.getenv("BARSI_TVBOX_PASS") or getpass.getpass(f"Senha SSH para {TVBOX_USER}@{TVBOX_IP}: ")
REMOTE_DIR = "/home/cleiton/barsi_quant_ia"
LOCAL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def run_remote(ssh, cmd, sudo=False):
    if sudo:
        cmd = f"echo '{TVBOX_PASS}' | sudo -S {cmd}"
    stdin, stdout, stderr = ssh.exec_command(cmd)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return exit_status, out, err

def main():
    print(f"[*] Conectando via SSH a {TVBOX_USER}@{TVBOX_IP}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(TVBOX_IP, username=TVBOX_USER, password=TVBOX_PASS, timeout=10)
    print("[+] Conectado com sucesso!")

    # 1. Verifica memória e SO
    _, out, _ = run_remote(ssh, "free -m | grep Mem")
    print(f"[i] Memoria na TV Box: {out}")

    # 2. Cria diretório remoto
    run_remote(ssh, f"mkdir -p {REMOTE_DIR}/src {REMOTE_DIR}/memory {REMOTE_DIR}/cache {REMOTE_DIR}/scripts")

    # 3. Copia arquivos via SCP
    print(f"[*] Sincronizando arquivos de {LOCAL_DIR} para a TV Box...")
    with SCPClient(ssh.get_transport()) as scp:
        # Arquivos raiz do projeto
        for f in ["pyproject.toml", "README.md", ".env.example", "Makefile", "carteira_barsi_quant_ia.xlsx"]:
            local_path = os.path.join(LOCAL_DIR, f)
            if os.path.exists(local_path):
                scp.put(local_path, f"{REMOTE_DIR}/{f}")
        
        # Copia src e scripts
        scp.put(os.path.join(LOCAL_DIR, "src"), REMOTE_DIR, recursive=True)
        scp.put(os.path.join(LOCAL_DIR, "scripts"), REMOTE_DIR, recursive=True)

    print("[+] Arquivos transferidos com sucesso!")

    # 4. Configura .env na TV Box se não existir
    _, _, _ = run_remote(ssh, f"test -f {REMOTE_DIR}/.env || cp {REMOTE_DIR}/.env.example {REMOTE_DIR}/.env")

    # 5. Configura venv
    print("[*] Configurando Python Virtualenv na TV Box...")
    run_remote(ssh, f"python3 -m venv {REMOTE_DIR}/.venv")
    
    # 6. Instala dependências leves
    print("[*] Instalando dependências (pydantic, yfinance, openpyxl, structlog, tenacity)...")
    status, out, err = run_remote(ssh, f"{REMOTE_DIR}/.venv/bin/pip install --upgrade pip")
    status, out, err = run_remote(ssh, f"{REMOTE_DIR}/.venv/bin/pip install -e {REMOTE_DIR}")
    print(f"[+] Dependencias instaladas (status: {status})")

    # 7. Executa teste rápido
    print("[*] Executando teste do pipeline na TV Box...")
    status, out, err = run_remote(ssh, f"{REMOTE_DIR}/.venv/bin/python -m barsi run --dry-run")
    print("\n--- RESULTADO NA TV BOX ---")
    print(out)
    if err:
        print("Avisos/Stderr:", err)
    print("---------------------------")

    ssh.close()
    print("[OK] Deploy e validacao concluidos na TV Box!")

if __name__ == "__main__":
    main()

"""Sincroniza credentials.json e gspread_adapter para a TV Box."""
import os
import getpass
import paramiko
from scp import SCPClient

TVBOX_IP = os.getenv("BARSI_TVBOX_IP", "")
TVBOX_USER = os.getenv("BARSI_TVBOX_USER", "")
TVBOX_HOME = os.getenv("BARSI_TVBOX_HOME", "/opt/barsi_quant_ia")
if not TVBOX_IP or not TVBOX_USER:
    print("[!] Configure BARSI_TVBOX_IP e BARSI_TVBOX_USER no .env")
    raise SystemExit(1)
TVBOX_PASS = os.getenv("BARSI_TVBOX_PASS") or getpass.getpass(f"Senha SSH para {TVBOX_USER}@{TVBOX_IP}: ")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(TVBOX_IP, username=TVBOX_USER, password=TVBOX_PASS, timeout=5)

with SCPClient(ssh.get_transport()) as scp:
    scp.put("barsi_quant_ia/credentials.json", f"{TVBOX_HOME}/credentials.json")
    scp.put("barsi_quant_ia/src/barsi/adapters/sheets/gspread_adapter.py", f"{TVBOX_HOME}/src/barsi/adapters/sheets/gspread_adapter.py")

cmd = f"sed -i 's/BARSI_SHEETS_PROVIDER=.*/BARSI_SHEETS_PROVIDER=gspread/' {TVBOX_HOME}/.env && grep BARSI_SHEETS {TVBOX_HOME}/.env"
stdin, stdout, stderr = ssh.exec_command(cmd)
print("TV BOX ENV:")
print(stdout.read().decode())
ssh.close()
print("[OK] Sincronizado para a TV Box com sucesso!")


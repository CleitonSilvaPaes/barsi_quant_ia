"""Sincroniza credentials.json e gspread_adapter para a TV Box."""
import os
import getpass
import paramiko
from scp import SCPClient

TVBOX_IP = os.getenv("BARSI_TVBOX_IP", "192.168.100.72")
TVBOX_USER = os.getenv("BARSI_TVBOX_USER", "cleiton")
TVBOX_PASS = os.getenv("BARSI_TVBOX_PASS") or getpass.getpass(f"Senha SSH para {TVBOX_USER}@{TVBOX_IP}: ")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(TVBOX_IP, username=TVBOX_USER, password=TVBOX_PASS, timeout=5)

with SCPClient(ssh.get_transport()) as scp:
    scp.put("barsi_quant_ia/credentials.json", "/home/cleiton/barsi_quant_ia/credentials.json")
    scp.put("barsi_quant_ia/src/barsi/adapters/sheets/gspread_adapter.py", "/home/cleiton/barsi_quant_ia/src/barsi/adapters/sheets/gspread_adapter.py")

cmd = "sed -i 's/BARSI_SHEETS_PROVIDER=.*/BARSI_SHEETS_PROVIDER=gspread/' /home/cleiton/barsi_quant_ia/.env && grep BARSI_SHEETS /home/cleiton/barsi_quant_ia/.env"
stdin, stdout, stderr = ssh.exec_command(cmd)
print("TV BOX ENV:")
print(stdout.read().decode())
ssh.close()
print("[OK] Sincronizado para a TV Box com sucesso!")


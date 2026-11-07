"""Testa a execução do serviço systemd e lê o journalctl."""
import os
import getpass
import paramiko
import time

TVBOX_IP = os.getenv("BARSI_TVBOX_IP", "192.168.100.72")
TVBOX_USER = os.getenv("BARSI_TVBOX_USER", "cleiton")
TVBOX_PASS = os.getenv("BARSI_TVBOX_PASS") or getpass.getpass(f"Senha SSH para {TVBOX_USER}@{TVBOX_IP}: ")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(TVBOX_IP, username=TVBOX_USER, password=TVBOX_PASS, timeout=5)

def run_sudo(cmd):
    full_cmd = f"echo '{TVBOX_PASS}' | sudo -S {cmd}"
    stdin, stdout, stderr = ssh.exec_command(full_cmd)
    stdout.channel.recv_exit_status()
    return stdout.read().decode().strip(), stderr.read().decode().strip()

print("[*] Disparando barsi-sync.service...")
run_sudo("systemctl start barsi-sync.service")

print("[*] Aguardando conclusao do ciclo...")
time.sleep(12)

out, _ = run_sudo("journalctl -u barsi-sync.service -n 25 --no-pager")
print("\n--- JOURNALCTL DA TV BOX ---")
print(out)
print("----------------------------")
ssh.close()


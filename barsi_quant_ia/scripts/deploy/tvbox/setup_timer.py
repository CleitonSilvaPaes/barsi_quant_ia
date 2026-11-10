"""Configura o Systemd Timer na TV Box via SSH."""
import os
import getpass
import paramiko

TVBOX_IP = os.getenv("BARSI_TVBOX_IP", "")
TVBOX_USER = os.getenv("BARSI_TVBOX_USER", "")
TVBOX_HOME = os.getenv("BARSI_TVBOX_HOME", "/opt/barsi_quant_ia")
if not TVBOX_IP or not TVBOX_USER:
    print("[!] Configure BARSI_TVBOX_IP e BARSI_TVBOX_USER no .env")
    raise SystemExit(1)
TVBOX_PASS = os.getenv("BARSI_TVBOX_PASS") or getpass.getpass(f"Senha SSH para {TVBOX_USER}@{TVBOX_IP}: ")

service_content = f"""[Unit]
Description=Execucao Barsi Sync Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User={TVBOX_USER}
WorkingDirectory={TVBOX_HOME}
ExecStart={TVBOX_HOME}/.venv/bin/python -m barsi run
StandardOutput=journal
StandardError=journal
"""

timer_content = """[Unit]
Description=Timer para Execucao Barsi em Horario de Pregao (09h as 18h)

[Timer]
OnCalendar=Mon..Fri *-*-* 09,10,11,12,13,14,15,16,17,18:00:00
Persistent=true
RandomizedDelaySec=30

[Install]
WantedBy=timers.target
"""

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(TVBOX_IP, username=TVBOX_USER, password=TVBOX_PASS, timeout=5)

    def run_sudo(cmd):
        full_cmd = f"echo '{TVBOX_PASS}' | sudo -S {cmd}"
        stdin, stdout, stderr = ssh.exec_command(full_cmd)
        stdout.channel.recv_exit_status()
        return stdout.read().decode().strip(), stderr.read().decode().strip()

    # Grava service e timer
    s_cmd = f"bash -c \"cat << 'EOF' > /etc/systemd/system/barsi-sync.service\n{service_content}\nEOF\""
    t_cmd = f"bash -c \"cat << 'EOF' > /etc/systemd/system/barsi-sync.timer\n{timer_content}\nEOF\""
    
    run_sudo(s_cmd)
    run_sudo(t_cmd)
    run_sudo("systemctl daemon-reload")
    run_sudo("systemctl enable --now barsi-sync.timer")
    out, _ = run_sudo("systemctl list-timers barsi-sync.timer")
    print("STATUS SYSTEMD TIMER:")
    print(out)
    ssh.close()

if __name__ == "__main__":
    main()


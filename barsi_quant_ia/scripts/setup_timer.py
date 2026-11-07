"""Configura o Systemd Timer na TV Box via SSH."""
import os
import getpass
import paramiko

TVBOX_IP = os.getenv("BARSI_TVBOX_IP", "192.168.100.72")
TVBOX_USER = os.getenv("BARSI_TVBOX_USER", "cleiton")
TVBOX_PASS = os.getenv("BARSI_TVBOX_PASS") or getpass.getpass(f"Senha SSH para {TVBOX_USER}@{TVBOX_IP}: ")

service_content = """[Unit]
Description=Execucao Barsi Sync Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=cleiton
WorkingDirectory=/home/cleiton/barsi_quant_ia
ExecStart=/home/cleiton/barsi_quant_ia/.venv/bin/python -m barsi run
StandardOutput=journal
StandardError=journal
"""

timer_content = """[Unit]
Description=Timer para Execucao Barsi em Horario de Pregao (09h as 18h)

[Timer]
OnCalendar=Mon..Fri 09..18:00:00
Persistent=true

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


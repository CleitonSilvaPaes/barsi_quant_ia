import os
import getpass
import paramiko
from scp import SCPClient

tv_ip = os.getenv("BARSI_TVBOX_IP", "")
tv_user = os.getenv("BARSI_TVBOX_USER", "")
tv_home = os.getenv("BARSI_TVBOX_HOME", "/opt/barsi_quant_ia")
if not tv_ip or not tv_user:
    print("[!] Configure BARSI_TVBOX_IP e BARSI_TVBOX_USER no .env")
    raise SystemExit(1)
tv_pass = os.getenv("BARSI_TVBOX_PASS") or getpass.getpass(f"Senha SSH para {tv_user}@{tv_ip}: ")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(tv_ip, username=tv_user, password=tv_pass, timeout=5)

with SCPClient(ssh.get_transport()) as scp:
    scp.put('barsi_quant_ia/src/barsi/api/http.py', f'{tv_home}/src/barsi/api/http.py')
    scp.put('barsi_quant_ia/src/barsi/domain/quant_ai.py', f'{tv_home}/src/barsi/domain/quant_ai.py')

service_content = f"""[Unit]
Description=API HTTP Telemetria Barsi Quant IA
After=network.target

[Service]
Type=simple
User={tv_user}
WorkingDirectory={tv_home}
ExecStart={tv_home}/.venv/bin/python -m barsi api
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

stdin, stdout, stderr = ssh.exec_command("sudo tee /etc/systemd/system/barsi-api.service", get_pty=True)
stdin.write(f"{tv_pass}\n")
stdin.flush()
stdin.write(service_content)
stdin.channel.shutdown_write()
stdout.read()

stdin, stdout, stderr = ssh.exec_command("sudo systemctl daemon-reload && sudo systemctl enable --now barsi-api.service && sleep 2 && systemctl status barsi-api.service", get_pty=True)
stdin.write(f"{tv_pass}\n")
stdin.flush()
print("STATUS DO SERVIÇO WEB NA TV BOX:")
print(stdout.read().decode())
ssh.close()


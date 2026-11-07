import os
import getpass
import paramiko
from scp import SCPClient

tv_ip = os.getenv("BARSI_TVBOX_IP", "192.168.100.72")
tv_user = os.getenv("BARSI_TVBOX_USER", "cleiton")
tv_pass = os.getenv("BARSI_TVBOX_PASS") or getpass.getpass(f"Senha SSH para {tv_user}@{tv_ip}: ")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(tv_ip, username=tv_user, password=tv_pass, timeout=5)

with SCPClient(ssh.get_transport()) as scp:
    scp.put('barsi_quant_ia/src/barsi/api/http.py', '/home/cleiton/barsi_quant_ia/src/barsi/api/http.py')
    scp.put('barsi_quant_ia/src/barsi/domain/quant_ai.py', '/home/cleiton/barsi_quant_ia/src/barsi/domain/quant_ai.py')

service_content = """[Unit]
Description=API HTTP Telemetria Barsi Quant IA
After=network.target

[Service]
Type=simple
User=cleiton
WorkingDirectory=/home/cleiton/barsi_quant_ia
ExecStart=/home/cleiton/barsi_quant_ia/.venv/bin/python -m barsi api
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


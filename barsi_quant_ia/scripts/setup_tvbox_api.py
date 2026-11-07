import paramiko
from scp import SCPClient

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.100.72', username='cleiton', password='compstream', timeout=5)

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

stdin, stdout, stderr = ssh.exec_command("echo compstream | sudo -S tee /etc/systemd/system/barsi-api.service")
stdin.write(service_content)
stdin.channel.shutdown_write()
stdout.read()

cmd = "echo compstream | sudo -S systemctl daemon-reload && echo compstream | sudo -S systemctl enable --now barsi-api.service && sleep 2 && echo compstream | sudo -S systemctl status barsi-api.service"
stdin, stdout, stderr = ssh.exec_command(cmd)
print("STATUS DO SERVIÇO WEB NA TV BOX:")
print(stdout.read().decode())
ssh.close()


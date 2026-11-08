import paramiko, os
from dotenv import load_dotenv
load_dotenv('.env')

ip = os.getenv('BARSI_TVBOX_IP', '192.168.100.72')
user = os.getenv('BARSI_TVBOX_USER')
pwd = os.getenv('BARSI_TVBOX_PASS')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(ip, username=user, password=pwd, timeout=5)

stdin, stdout, stderr = ssh.exec_command('git -C /home/cleiton/barsi_quant_ia log -1 --oneline')
print("TV Box git commit:", stdout.read().decode())

stdin, stdout, stderr = ssh.exec_command('journalctl -u barsi-api.service -n 25 --no-pager')
print("TV Box journalctl:")
print(stdout.read().decode())
ssh.close()


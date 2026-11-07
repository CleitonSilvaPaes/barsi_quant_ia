import os
import paramiko
import json

tv_ip = os.getenv("BARSI_TVBOX_IP", "192.168.100.72")
tv_user = os.getenv("BARSI_TVBOX_USER", "cleiton")
tv_pass = os.getenv("BARSI_TVBOX_PASS", "")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(tv_ip, username=tv_user, password=tv_pass, timeout=5)

payload = json.dumps({"model": "gemma2:2b", "prompt": "Diga apenas: 'Conexao IA TV Box para PC estabelecida com sucesso!'", "stream": False})
cmd = f"curl -s -X POST http://192.168.100.74:11434/api/generate -d '{payload}'"

stdin, stdout, stderr = ssh.exec_command(cmd)
res = stdout.read().decode()
try:
    data = json.loads(res)
    print("RESPOSTA DA IA (gemma2:2b no PC) VIA TV BOX:")
    print(data.get("response"))
except Exception as e:
    print("Erro json:", e, "Raw:", res)
ssh.close()


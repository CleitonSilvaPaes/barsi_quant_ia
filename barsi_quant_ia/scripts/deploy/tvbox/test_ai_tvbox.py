import os
import paramiko
import json

def main():
    tv_ip = os.getenv("BARSI_TVBOX_IP", "")
    tv_user = os.getenv("BARSI_TVBOX_USER", "")
    tv_pass = os.getenv("BARSI_TVBOX_PASS", "")
    ollama_url = os.getenv("BARSI_OLLAMA_URL", "http://127.0.0.1:11434")

    if not tv_ip or not tv_user:
        print("[!] Configure BARSI_TVBOX_IP e BARSI_TVBOX_USER no .env")
        raise SystemExit(1)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(tv_ip, username=tv_user, password=tv_pass, timeout=5)

    payload = json.dumps({"model": "gemma2:2b", "prompt": "Diga apenas: 'Conexao IA TV Box para PC estabelecida com sucesso!'", "stream": False})
    cmd = f"curl -s -X POST {ollama_url}/api/generate -d '{payload}'"

    stdin, stdout, stderr = ssh.exec_command(cmd)
    res = stdout.read().decode()
    try:
        data = json.loads(res)
        print("RESPOSTA DA IA (gemma2:2b no PC) VIA TV BOX:")
        print(data.get("response"))
    except Exception as e:
        print("Erro json:", e, "Raw:", res)
    ssh.close()

if __name__ == "__main__":
    main()



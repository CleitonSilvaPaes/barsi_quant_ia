import os
import sys
import requests

api_url = os.getenv("BARSI_API_URL", "")
if not api_url:
    tvbox_ip = os.getenv("BARSI_TVBOX_IP", "")
    api_url = f"http://{tvbox_ip}:8000" if tvbox_ip else "http://localhost:8000"

if len(sys.argv) > 1 and sys.argv[1].startswith("http"):
    api_url = sys.argv[1]

username = os.getenv("BARSI_INITIAL_ADMIN_USER", "")
password = os.getenv("BARSI_INITIAL_ADMIN_PASS", "")

if not username or not password:
    print("[!] Configure BARSI_INITIAL_ADMIN_USER e BARSI_INITIAL_ADMIN_PASS no .env")
    raise SystemExit(1)

print(f"[+] Conectando na API em: {api_url}")
s = requests.Session()
res = s.post(f"{api_url}/login", data={"username": username, "password": password}, allow_redirects=False)
print("Login Status:", res.status_code)
print("Cookies:", s.cookies.get_dict())
print("Headers:", dict(res.headers))

if "barsi_session" in s.cookies:
    res_c = s.get(f"{api_url}/api/carteira")
    print("Carteira status:", res_c.status_code)
    data = res_c.json()
    print("Total ativos:", len(data))
    for item in data[:3]:
        print("  Asset:", item["ticker"], "Qtd Atual:", item["qtd_total"], "Preco:", item["preco"], "Teto:", item["teto"], "Margem:", item["margem"])
    
    res_s = s.get(f"{api_url}/api/simulador")
    print("Simulador status:", res_s.status_code)
    sim = res_s.json()
    print("Total ordens:", len(sim))
    for o in sim:
        print("  Order:", o["rank"], o["ticker"], "Qtd Frac:", o["qtd_fracionaria"], "Gasto:", o["gasto_efetivo"])


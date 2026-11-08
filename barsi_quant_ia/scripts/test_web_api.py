import os
import requests

TVBOX_IP = os.getenv("BARSI_TVBOX_IP", "")
username = os.getenv("BARSI_INITIAL_ADMIN_USER", "")
password = os.getenv("BARSI_INITIAL_ADMIN_PASS", "")

if not TVBOX_IP or not username:
    print("[!] Configure BARSI_TVBOX_IP e BARSI_INITIAL_ADMIN_USER no .env")
    raise SystemExit(1)

BASE_URL = f"http://{TVBOX_IP}:8000"
s = requests.Session()
res = s.post(f'{BASE_URL}/login', data={'username': username, 'password': password}, allow_redirects=False)
print('Login Status:', res.status_code)
print('Cookies:', s.cookies.get_dict())
print('Headers:', dict(res.headers))

if 'barsi_session' in s.cookies:
    res_c = s.get(f'{BASE_URL}/api/carteira')
    print('Carteira status:', res_c.status_code)
    data = res_c.json()
    print('Total ativos:', len(data))
    for item in data[:3]:
        print('  Asset:', item['ticker'], 'Qtd Atual:', item['qtd_total'], 'Aporte Qtd:', item['qtd_aporte'], 'Preco:', item['preco'], 'Teto:', item['teto'], 'Margem:', item['margem'])
    
    res_s = s.get(f'{BASE_URL}/api/simulador')
    print('Simulador status:', res_s.status_code)
    sim = res_s.json()
    print('Total ordens:', len(sim))
    for o in sim:
        print('  Order:', o['rank'], o['ticker'], 'Qtd Frac:', o['qtd_fracionaria'], 'Gasto:', o['gasto_efetivo'])

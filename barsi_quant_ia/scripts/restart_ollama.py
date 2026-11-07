import os
import subprocess
import time

os.system('taskkill /f /im ollama.exe 2>nul')
os.system('taskkill /f /im "ollama app.exe" 2>nul')
time.sleep(1)

my_env = os.environ.copy()
my_env['OLLAMA_HOST'] = '0.0.0.0:11434'
app_path = os.path.expanduser('~\\AppData\\Local\\Programs\\Ollama\\ollama app.exe')
subprocess.Popen([app_path], env=my_env)
time.sleep(3)

import urllib.request, json
try:
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=3) as r:
        print("Ollama 127.0.0.1 OK:", json.loads(r.read().decode()).get("models"))
except Exception as e:
    print("Ollama 127.0.0.1:", e)


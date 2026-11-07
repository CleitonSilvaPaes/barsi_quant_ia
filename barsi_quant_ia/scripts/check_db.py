import sqlite3
import os

for p in ["memory/barsi_memory.db", "barsi_quant_ia/memory/barsi_memory.db"]:
    if os.path.exists(p):
        conn = sqlite3.connect(p)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        print(f"Banco em {p}:", cur.fetchall())
        conn.close()


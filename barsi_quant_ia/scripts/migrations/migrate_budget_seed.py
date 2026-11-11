"""Migra os valores monetários para recurring_expenses no SQLite. Roda de forma 100% idempotente."""
import os
import sys

# Garante path para importação do módulo barsi
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from barsi.adapters.history.budget_repository import BudgetRepository, DEFAULT_BUDGET_SEEDS

SEEDS = DEFAULT_BUDGET_SEEDS

def run_seed(db_path: str = "./memory/barsi_memory.db") -> int:
    repo = BudgetRepository(db_path)
    return repo.seed_defaults()

if __name__ == "__main__":
    count = run_seed()
    print(f"[OK] {count} seeds aplicados em recurring_expenses no banco local.")

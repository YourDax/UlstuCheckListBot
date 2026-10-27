import os

# Тесты никогда не должны попасть в боевую Postgres-базу, даже если DATABASE_URL лежит в .env.
# Пустое значение в окружении выигрывает у .env (config использует setdefault).
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

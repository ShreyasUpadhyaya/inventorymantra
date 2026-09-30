import psycopg2
from psycopg2 import sql
import csv
import os
import sys

# One-off export of the source database to CSV. Credentials come from the
# environment only (SOURCE_DATABASE_URL in .env); never hardcode them here.
source_url = os.environ.get("SOURCE_DATABASE_URL")
if not source_url:
    sys.exit("SOURCE_DATABASE_URL is not set (see .env.example).")

conn = psycopg2.connect(source_url, options="-c timezone=UTC")
cur = conn.cursor()

# 1. Find every user table in every schema (skips system schemas)
cur.execute("""
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_type = 'BASE TABLE'
      AND table_schema NOT IN ('pg_catalog', 'information_schema')
    ORDER BY table_schema, table_name
""")
tables = cur.fetchall()

if not tables:
    print("No tables found in this database.")
else:
    print(f"Found {len(tables)} table(s):\n")

os.makedirs("database_export", exist_ok=True)
summary_lines = []

for schema, table in tables:
    # 2. Column structure
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """, (schema, table))
    columns = cur.fetchall()

    # 3. Row count
    cur.execute(sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
        sql.Identifier(schema), sql.Identifier(table)))
    count = cur.fetchone()[0]

    header = f"{schema}.{table}  ({count} rows)"
    print(header)
    summary_lines.append(header)
    for name, dtype in columns:
        line = f"    {name}: {dtype}"
        print(line)
        summary_lines.append(line)
    print()
    summary_lines.append("")

    # 4. Export data to CSV
    cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
        sql.Identifier(schema), sql.Identifier(table)))
    rows = cur.fetchall()
    col_names = [d[0] for d in cur.description]

    with open(f"database_export/{schema}.{table}.csv", "w",
              newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(col_names)
        writer.writerows(rows)

# 5. Save the overview of all tables
with open("database_export/_schema_summary.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(summary_lines))

cur.close()
conn.close()
print("Done. Check the 'database_export' folder.")
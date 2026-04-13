import sqlite3
from pathlib import Path

DB_PATH = Path("/Users/little_claw/.openclaw/workspace/job-pipeline/data/jobs.db")
content = Path("/Users/little_claw/.openclaw/workspace/job-pipeline/digest_report.md").read_text()
conn = sqlite3.connect(str(DB_PATH))
conn.execute("DELETE FROM summaries WHERE date = '2026-04-10'")
conn.execute("INSERT INTO summaries (date, content) VALUES (?, ?)", ("2026-04-10", content))
conn.commit()
conn.close()
print(f"Saved digest summary ({len(content)} chars)")

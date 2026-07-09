from sqlalchemy import text

from app import create_app
from app.extensions import db
import os
import sys

sys.path.append(
    os.path.dirname(
        os.path.dirname(__file__)
    )
)

app = create_app()


COLUMNS = [
    "ALTER TABLE document ADD COLUMN ai_status VARCHAR(50) DEFAULT 'not_analyzed'",
    "ALTER TABLE document ADD COLUMN ai_confidence FLOAT",
    "ALTER TABLE document ADD COLUMN ai_extracted_data JSON",
    "ALTER TABLE document ADD COLUMN ai_compliance_result JSON",
    "ALTER TABLE document ADD COLUMN ai_error TEXT",
    "ALTER TABLE document ADD COLUMN ai_analyzed_at DATETIME",
]


with app.app_context():

    for sql in COLUMNS:

        try:
            db.session.execute(text(sql))
            db.session.commit()
            print("Executed:", sql)

        except Exception as e:
            db.session.rollback()
            print("Skipped:", e)

print("Done.")
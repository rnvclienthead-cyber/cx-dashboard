"""Заявки, которые убрали из учёта и нельзя снова подтянуть с WB."""
from sqlalchemy import text

PURGE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cx_purged_claims (
    srid TEXT PRIMARY KEY,
    claim_id TEXT,
    supplier_article TEXT,
    note TEXT,
    purged_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
)
"""


def init_purged_claims_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(PURGE_TABLE_SQL))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_cx_purged_claims_claim_id "
            "ON cx_purged_claims (claim_id)"
        ))


def is_purged_claim(db, *, srid: str = "", claim_id: str = "") -> bool:
    srid = str(srid or "").strip()
    cid = str(claim_id or "").strip()
    if not srid and not cid:
        return False
    row = db.execute(text("""
        SELECT 1 FROM cx_purged_claims
        WHERE (:srid <> '' AND srid = :srid)
           OR (:cid <> '' AND claim_id = :cid)
        LIMIT 1
    """), {"srid": srid, "cid": cid}).first()
    return bool(row)


def purged_srid_sql(alias: str = "c") -> str:
    return (
        f"NOT EXISTS (SELECT 1 FROM cx_purged_claims p "
        f"WHERE p.srid = {alias}.srid)"
    )

from sqlalchemy import create_engine, text

URL_SUPABASE = "postgresql://postgres.wdcrihtjabrkzgsxezjb:RDB[r6o&BA0qSlVVGjb-@aws-1-eu-central-1.pooler.supabase.com:6543/postgres"
URL_LOCAL_VPS = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"

for url in [URL_SUPABASE, URL_LOCAL_VPS]:
    try:
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS wb_ratings_raw;"))
        print(f"✅ Успешно удалено!")
    except Exception as e:
        pass

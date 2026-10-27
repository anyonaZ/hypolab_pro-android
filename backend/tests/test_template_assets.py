from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.generation import service


def test_template_and_nugget_are_saved_together(monkeypatch, tmp_path):
    db_engine = create_engine(f"sqlite:///{tmp_path / 'templates.db'}")
    Base.metadata.create_all(db_engine)
    sessions = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(service, "SessionLocal", sessions)
    saved = service.save_template_asset({"name": "Revision persistence", "template_text": "ts_rank({field}, 20)",
        "nugget": "Use for slowly updated estimates.", "context": {"datasets": ["analyst1"], "variants": ["rank"]}})
    rows = service.list_template_assets()
    assert rows[0]["id"] == saved["id"]
    assert rows[0]["nugget"] == "Use for slowly updated estimates."
    assert rows[0]["context"]["datasets"] == ["analyst1"]
    assert service.delete_template_asset(saved["id"])
    assert service.list_template_assets() == []

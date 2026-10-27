from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.catalog import service
from app.db.base import Base


def isolated_catalog(monkeypatch, tmp_path):
    db_engine = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(db_engine)
    sessions = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(service, "SessionLocal", sessions)


def test_catalog_preserves_full_descriptions_and_context(monkeypatch, tmp_path):
    isolated_catalog(monkeypatch, tmp_path)
    long_description = "earnings revisions " * 100
    service.ingest_datasets("EQUITY", "USA", 1, "TOP3000", [{"id": "ds1", "name": "Analyst", "description": "estimates", "category_id": "analyst", "coverage": .9}])
    service.ingest_fields("EQUITY", "USA", 1, "TOP3000", "ds1", [{"id": "f1", "description": long_description, "type": "VECTOR", "alphaCount": 2}])
    fields = service.local_fields(["ds1"], "EQUITY", "USA", 1, "TOP3000")
    assert fields[0]["description"] == long_description
    assert fields[0]["type"] == "VECTOR"
    assert fields[0]["category"] == "analyst"


def test_catalog_ingestion_updates_existing_rows_without_duplicates(monkeypatch, tmp_path):
    isolated_catalog(monkeypatch, tmp_path)
    rows = [{"id": "ds1", "name": "Analyst", "category_id": "analyst", "coverage": .5}]
    service.ingest_datasets("EQUITY", "USA", 1, "TOP3000", rows)
    rows[0].update({"coverage": .95, "valueScore": 7})
    service.ingest_datasets("EQUITY", "USA", 1, "TOP3000", rows)
    saved = service.local_datasets("EQUITY", "USA", 1, "TOP3000")
    assert len(saved) == 1
    assert saved[0]["coverage"] == .95
    assert saved[0]["valueScore"] == 7


def test_catalog_backup_round_trip(monkeypatch, tmp_path):
    isolated_catalog(monkeypatch, tmp_path)
    payload = {"format": "ace-catalog/v1", "datasets": {"concepts": [{"dataset_id": "ds1", "name": "News", "category": "news"}], "availability": [{"dataset_id": "ds1", "instrument": "EQUITY", "region": "EUR", "delay": 1, "universe": "TOP2500", "coverage": .8}]}, "fields": {"concepts": [{"field_id": "news_score", "dataset_id": "ds1", "description": "sentiment", "type": "MATRIX"}], "availability": [{"field_id": "news_score", "instrument": "EQUITY", "region": "EUR", "delay": 1, "universe": "TOP2500"}]}}
    result = service.import_catalog(payload)
    assert result["health"]["dataset_concepts"] == 1
    assert service.export_catalog()["fields"]["concepts"][0]["field_id"] == "news_score"
    graph = service.graph()
    assert {e["kind"] for e in graph["edges"]} == {"contains"}


def test_catalog_reset_removes_local_metadata(monkeypatch, tmp_path):
    isolated_catalog(monkeypatch, tmp_path)
    service.ingest_datasets("EQUITY", "USA", 1, "LARGE", [{"id": "ds1", "name": "News"}])
    service.ingest_fields("EQUITY", "USA", 1, "LARGE", "ds1", [{"id": "f1", "type": "MATRIX"}])
    out = service.reset_catalog()
    assert out["health"]["dataset_concepts"] == 0
    assert out["health"]["field_concepts"] == 0

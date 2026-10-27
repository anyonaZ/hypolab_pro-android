import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base

from app.modules.regions import CommonReq, TransferContext, _common_research


def test_common_research_keeps_exact_dataset_and_field_intersection(monkeypatch):
    datasets = {
        "USA": [{"id": "shared", "name": "Shared"}, {"id": "usa_only"}],
        "EUR": [{"id": "shared", "name": "Shared"}, {"id": "eur_only"}],
    }
    fields = {
        "USA": pd.DataFrame([{"id": "common_field", "dataset_id": "shared", "type": "MATRIX"}, {"id": "usa_field", "dataset_id": "shared"}]),
        "EUR": pd.DataFrame([{"id": "common_field", "dataset_id": "shared", "type": "MATRIX"}, {"id": "eur_field", "dataset_id": "shared"}]),
    }
    monkeypatch.setattr("app.modules.regions.engine.valid_combo", lambda instrument, region, delay, universe: (region, delay, universe))
    monkeypatch.setattr("app.modules.regions.engine.get_datasets", lambda region, **kwargs: datasets[region])
    monkeypatch.setattr("app.modules.regions.engine.fetch_fields", lambda ids, region, **kwargs: (fields[region], len(fields[region])))
    monkeypatch.setattr("app.modules.regions.catalog.ingest_datasets", lambda *args: None)
    monkeypatch.setattr("app.modules.regions.catalog.ingest_fields", lambda *args: None)
    req = CommonReq(instrument="EQUITY", stage="fields", dataset_ids=["shared"], contexts=[
        TransferContext(region="USA", delay=1, universe="TOP3000"),
        TransferContext(region="EUR", delay=1, universe="TOP2500"),
    ])
    result = _common_research(req, lambda **kwargs: None, lambda: False)
    assert [x["id"] for x in result["datasets"]] == ["shared"]
    assert [x["id"] for x in result["fields"]] == ["common_field"]


def test_study_and_context_checkpoints_persist(monkeypatch, tmp_path):
    from app.modules import regions
    eng=create_engine(f"sqlite:///{tmp_path/'transfer.db'}")
    Base.metadata.create_all(eng)
    monkeypatch.setattr(regions,"SessionLocal",sessionmaker(bind=eng,expire_on_commit=False))
    monkeypatch.setattr(regions.engine,"valid_combo",lambda instrument,region,delay,universe:(region,delay,universe))
    monkeypatch.setattr(regions.engine,"get_datasets",lambda region,**kwargs:[{"id":"shared"},{"id":region.lower()}])
    monkeypatch.setattr(regions.catalog,"ingest_datasets",lambda *args:None)
    saved=regions._save_study(regions.StudySave(mode="regions",contexts=[
        TransferContext(region="USA",delay=1,universe="TOP3000"),
        TransferContext(region="EUR",delay=1,universe="TOP2500")]))
    result=regions._run_study(saved["id"],"datasets",lambda **kwargs:None,lambda:False)
    assert result["status"]=="datasets_ready"
    assert [x["id"] for x in result["datasets"]]==["shared"]
    assert len(result["checkpoints"]["datasets"])==2


def test_transfer_mounts_complete_catalog_without_brain_requests(monkeypatch):
    from app.modules import regions
    local = {
        "USA": [{"id": "shared"}, {"id": "usa_only"}],
        "EUR": [{"id": "shared"}, {"id": "eur_only"}],
    }
    monkeypatch.setattr(regions.catalog, "context_status", lambda *a, **k: {
        "datasets_ready": True, "fields_ready": True})
    monkeypatch.setattr(regions.catalog, "local_datasets",
                        lambda instrument, region, delay, universe: local[region])
    monkeypatch.setattr(regions.catalog, "local_fields", lambda ids, instrument, region, delay, universe: [
        {"id": "common_field", "dataset_id": "shared", "type": "MATRIX"},
        {"id": f"{region.lower()}_only", "dataset_id": "shared", "type": "MATRIX"},
    ])
    monkeypatch.setattr(regions.engine, "valid_combo", lambda instrument, region, delay, universe: (region, delay, universe))
    monkeypatch.setattr(regions.engine, "get_datasets", lambda **kwargs: (_ for _ in ()).throw(AssertionError("BRAIN dataset request")))
    monkeypatch.setattr(regions.engine, "fetch_fields", lambda *a, **k: (_ for _ in ()).throw(AssertionError("BRAIN field request")))
    req = CommonReq(instrument="EQUITY", stage="fields", dataset_ids=["shared"], contexts=[
        TransferContext(region="USA", delay=1, universe="TOP3000"),
        TransferContext(region="EUR", delay=1, universe="TOP2500"),
    ])
    result = _common_research(req, lambda **kwargs: None, lambda: False)
    assert [x["id"] for x in result["datasets"]] == ["shared"]
    assert [x["id"] for x in result["fields"]] == ["common_field"]

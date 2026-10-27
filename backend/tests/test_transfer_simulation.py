from app.simulation import service
from app.modules.simulate import _transfer_contexts


def test_transfer_study_runs_every_verified_context(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append((kwargs["region"], kwargs["delay"], kwargs["universes"][0], kwargs["neutralizations"], kwargs["transfer_study_id"]))
        return {"simulated": 1, "passed": 1, "errors": [], "results": [{
            "expr": "rank(shared_field)", "region": kwargs["region"], "delay": kwargs["delay"],
            "sharpe": 2.0, "passed": True,
        }]}

    monkeypatch.setattr(service, "run_simulation", fake_run)
    monkeypatch.setattr(service, "_valid_neuts", lambda instrument, region, values: values)
    out = service.run_transfer_study(
        study_id=7, expressions=["rank(shared_field)"], instrument="EQUITY",
        contexts=[{"region": "USA", "delay": 1, "universe": "TOP3000", "neutralizations": ["SECTOR", "INDUSTRY"]},
                  {"region": "EUR", "delay": 1, "universe": "TOP2500", "neutralizations": ["MARKET"]}],
        neutralizations=["INDUSTRY"], decay=4, truncation=.08, test_period="P1Y0M0D",
        concurrency=2, limit_of_multi=10, tag="", winner_tag="", check_submission=False,
        progress=lambda **kwargs: None, should_cancel=lambda: False,
    )
    assert calls == [("USA", 1, "TOP3000", ["SECTOR", "INDUSTRY"], 7),
                     ("EUR", 1, "TOP2500", ["MARKET"], 7)]
    assert out["simulated"] == 2
    assert out["transfer_evidence"][0]["contexts_passed"] == 2
    assert out["transfer_evidence"][0]["direction_consistent"] is True


def test_transfer_settings_expand_each_market_independently(monkeypatch):
    monkeypatch.setattr("app.modules.simulate.engine.valid_combo",lambda instrument,region,delay,universe:(region,delay,universe))
    contexts=_transfer_contexts("EQUITY",[
        {"region":"EUR","delay":1,"universe":"TOP2500"},
        {"region":"USA","delay":1,"universe":"TOP3000"},
    ],[
        {"region":"EUR","delay":1,"universes":["TOP2500","TOP1200"],"neutralizations":["MARKET","SECTOR"]},
        {"region":"USA","delay":1,"universes":["TOP3000"],"neutralizations":["INDUSTRY"]},
    ],["INDUSTRY"])
    assert [(x["region"],x["universe"],x["neutralizations"]) for x in contexts]==[
        ("EUR","TOP2500",["MARKET","SECTOR"]),("EUR","TOP1200",["MARKET","SECTOR"]),
        ("USA","TOP3000",["INDUSTRY"]),
    ]


def test_transfer_progress_reports_global_configuration_total(monkeypatch):
    updates=[]
    def fake_run(**kwargs):
        local=len(kwargs["expressions"])*len(kwargs["universes"])*len(kwargs["neutralizations"])
        kwargs["progress"](done=local,total=local,message="local")
        return {"simulated":local,"passed":0,"errors":[],"results":[]}
    monkeypatch.setattr(service,"run_simulation",fake_run)
    monkeypatch.setattr(service,"_valid_neuts",lambda instrument,region,values:values)
    service.run_transfer_study(
        study_id=8,expressions=["rank(a)","rank(b)"],instrument="EQUITY",
        contexts=[{"region":"EUR","delay":1,"universe":"TOP2500","neutralizations":["MARKET","SECTOR"]},
                  {"region":"USA","delay":1,"universe":"TOP3000","neutralizations":["INDUSTRY"]}],
        neutralizations=["INDUSTRY"],decay=4,truncation=.08,test_period="P0Y0M0D",
        concurrency=2,limit_of_multi=10,tag="",winner_tag="",check_submission=False,
        progress=lambda **kw:updates.append(kw),should_cancel=lambda:False)
    assert {u.get("total") for u in updates}=={6}
    assert updates[-1]["done"]==6

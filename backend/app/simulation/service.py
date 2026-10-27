"""Simulation engine + the success gate.

Simulates generated alphas (regular FastExpr) across the chosen universes and
neutralizations, then judges each against ONE explicit gate. An alpha is a verified
success only if EVERY metric passes with a positive expected direction:

  Sharpe >= threshold   (delay 0: 2.69, else 1.58)
  Fitness >= threshold  (delay 0: 1.5,  else 1.0)
  |Turnover| < 0.70
  in-sample tests: no FAIL
  self / prod / powerpool correlation < 0.70   (only when the submission check is run)

Winners (|fitness| over a user threshold) and every kept alpha are tagged with the user's
OWN chosen tags — nothing hardcoded. Passing/strong alphas also credit the diversity
engine's usage table by |fitness|, so what actually WORKED steers future generation.

Reuses the studio engine (ace_lib) via the brain adapter.
"""

from __future__ import annotations

import json
import math
import re
import time
from functools import partial
from multiprocessing.pool import ThreadPool

from app.brain import engine
import ace_lib as ace  # noqa: E402

from app.db.base import SessionLocal
from app.db import models as M
from app.generation.service import _operators_in, _fields_in, _leaf_idents  # reuse AST walkers

# Fields that exist in every equity region — never worth a per-region lookup (price/volume + the
# standard group fields). Anything else in an expression is verified against the region's data.
_UNIVERSAL = {
    "open", "close", "high", "low", "volume", "vwap", "returns", "cap", "adv20", "sharesout",
    "dividend", "split", "assets", "liabilities", "sales", "ebit", "ebitda",
    "industry", "subindustry", "sector", "market", "country", "exchange", "currency",
}
from sqlalchemy import select, desc, func


def normalize_test_period(v) -> str:
    """Accept flexible in-sample test-period inputs and return BRAIN's canonical P<Y>Y<M>M<D>D:
      • "" / None                 -> "P0Y0M0D" (no IS test period)
      • 1 / "1" / "2"             -> that many YEARS -> "P1Y0M0D"
      • "P1Y"                     -> "P1Y0M0D"
      • "P0Y6M"  ("6 months")     -> "P0Y6M0D"
      • "6M" / "18m"              -> "P0Y18M0D"
      • "P0Y0M0D"                 -> unchanged
    Anything unparseable falls back to the safe "P0Y0M0D"."""
    if v is None:
        return "P0Y0M0D"
    s = str(v).strip().upper()
    if not s:
        return "P0Y0M0D"
    if re.fullmatch(r"\d+", s):              # bare number => years
        return f"P{int(s)}Y0M0D"
    if re.fullmatch(r"\d+[YMD]", s):         # "6M" => "P6M" for the parser below
        s = "P" + s
    m = re.fullmatch(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?", s)
    if not m or not any(m.groups()):
        return "P0Y0M0D"
    y, mo, d = (int(g) if g else 0 for g in m.groups())
    return f"P{y}Y{mo}M{d}D"


def _dedup_expressions(exprs: list) -> tuple[list, int]:
    """Drop near-duplicate expressions (identical after whitespace/case is normalized) so we don't
    spend simulations on the same alpha twice within a batch. Order-preserving."""
    seen, kept = set(), []
    for e in exprs:
        key = re.sub(r"\s+", "", str(e)).lower()
        if key and key not in seen:
            seen.add(key)
            kept.append(e)
    return kept, len(exprs) - len(kept)


def _valid_neuts(instrument: str, region: str, neutralizations: list, progress=None) -> list:
    """Drop any neutralization the region/instrument doesn't offer (e.g. a stale COUNTRY choice on
    USA) so it never becomes an alpha config BRAIN rejects with a 400. If none survive, fall back to
    a valid one; if the option set is unknown, leave the list untouched."""
    try:
        valid = engine.valid_neutralizations(instrument, region)
    except Exception:
        valid = set()
    if not valid:
        return neutralizations
    kept = [n for n in neutralizations if n in valid]
    dropped = [n for n in neutralizations if n not in valid]
    if dropped and progress:
        try:
            progress(log=f"skipping neutralization(s) not available for {region}: {', '.join(sorted(set(dropped)))}")
        except Exception:
            pass
    if kept:
        return kept
    return ["INDUSTRY"] if "INDUSTRY" in valid else [sorted(valid)[0]]


def gate_thresholds(delay: int) -> dict:
    """Delay-0 alphas are judged far more strictly on Sharpe (and Fitness)."""
    if int(delay) == 0:
        return {"sharpe": 2.69, "fitness": 1.5}
    return {"sharpe": 1.58, "fitness": 1.0}


def _num(v):
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _norm_turnover(t):
    if t is None:
        return None
    t = abs(t)
    return t / 100.0 if t > 1.5 else t   # BRAIN may give 0.42 or 42


def _corr_from_submission(df, *needles) -> float | None:
    """Pull a correlation value out of the submission-check DataFrame by fuzzy name match."""
    if df is None or getattr(df, "empty", True) or "name" not in df.columns:
        return None
    for _, row in df.iterrows():
        nm = str(row.get("name", "")).lower()
        if all(n in nm for n in needles):
            return _num(row.get("value"))
    return None


def _credit_usage(region: str, operators: list, fields: list, fitness: float) -> None:
    now = time.time()
    with SessionLocal() as db:
        for kind, keys in (("operator", operators), ("field", fields)):
            for k in keys:
                row = db.scalar(select(M.Usage).where(
                    M.Usage.kind == kind, M.Usage.key == k, M.Usage.region == region))
                if row:
                    row.count += 1
                    row.sum_abs_fitness += abs(fitness or 0)
                    row.last_at = now
                else:
                    db.add(M.Usage(kind=kind, key=k, region=region, count=1,
                                   sum_abs_fitness=abs(fitness or 0), last_at=now))
        db.commit()


def run_simulation(*, expressions, region, delay, universes, neutralizations, decay, truncation,
                   test_period, pasteurization, unit_handling, nan_handling, max_trade, visualization,
                   concurrency, limit_of_multi, max_turnover, min_sharpe, min_fitness, max_corr,
                   tag, winner_tag, winner_color, tag_winners_above,
                   check_submission, get_pnl, get_stats, progress, should_cancel, instrument="EQUITY",
                   randomize=False, range_from=0, range_to=0, transfer_study_id=0) -> dict:
    import multiprocessing
    s = engine.require_session()
    test_period = normalize_test_period(test_period)
    neutralizations = _valid_neuts(instrument, region, neutralizations, progress)
    exprs = [e.strip() for e in expressions if e.strip()]
    exprs, _pruned = _dedup_expressions(exprs)
    if _pruned:
        try:
            progress(log=f"skipped {_pruned} duplicate expression(s)")
        except Exception:
            pass
    if not exprs:
        raise RuntimeError("No expressions to simulate.")

    alpha_list = [
        ace.generate_alpha(expr, alpha_type="REGULAR", region=region, universe=u, delay=delay,
                           decay=decay, neutralization=n, truncation=truncation,
                           pasteurization=pasteurization, test_period=test_period,
                           unit_handling=unit_handling, nan_handling=nan_handling,
                           max_trade=max_trade, visualization=visualization)
        for expr in exprs for u in universes for n in neutralizations
    ]
    # Randomize ALL configs (expr × universe × neutralization) FIRST, then take the requested range,
    # so a range is a RANDOM SAMPLE of the whole config space rather than the first N in order.
    if randomize:
        import random
        random.shuffle(alpha_list)
    lo = max(0, int(range_from or 0))
    hi = int(range_to) if (range_to and int(range_to) > 0) else len(alpha_list)
    if lo > 0 or hi < len(alpha_list):
        before = len(alpha_list)
        alpha_list = alpha_list[lo:hi]
        try:
            progress(log=f"config range: {len(alpha_list)} of {before} configs (indices [{lo}, {hi}){' , randomized' if randomize else ''})")
        except Exception:
            pass
    if not alpha_list:
        raise RuntimeError("The selected config range is empty — widen From/To.")
    conc = max(1, min(8, concurrency))
    multi = max(2, min(10, limit_of_multi))
    batches = [alpha_list[i:i + multi] for i in range(0, len(alpha_list), multi)]
    progress(total=len(alpha_list), message=f"{len(alpha_list)} configs · {len(batches)} batches ×{multi} · conc {conc}",
             log=f"{len(exprs)} expr × {len(universes)} universe × {len(neutralizations)} neut = {len(alpha_list)} configs")

    flat, done, cancelled, errors = [], 0, False, []
    with ThreadPool(conc) as pool:
        it = pool.imap_unordered(partial(ace.simulate_multi_alpha, s), batches)
        while True:
            if should_cancel():
                cancelled = True
                pool.terminate()
                progress(log="STOP requested")
                break
            try:
                res = it.next(timeout=1)
            except StopIteration:
                break
            except multiprocessing.TimeoutError:
                continue
            except Exception as e:  # noqa: BLE001
                msg = str(e).strip().splitlines()[0][:180] if str(e).strip() else type(e).__name__
                if msg not in errors:
                    errors.append(msg)
                progress(log=f"BATCH ERROR: {msg}")
                continue
            # Salvaged batches now return real alphas AND error rows (alpha_id None, error set).
            # Keep real ones; collect BRAIN's distinct error messages so the UI can show WHY.
            real = [x for x in res if x.get("alpha_id")]
            for x in res:
                e = x.get("error")
                if e and e not in errors:
                    errors.append(e)
            flat.extend(real)
            done += len(real)
            progress(done=done, log=f"+{len(real)} simulated ({done}/{len(alpha_list)})")

    ok = [x for x in flat if x.get("alpha_id")]
    if not ok:
        return {"configs": len(alpha_list), "simulated": 0, "passed": 0, "cancelled": cancelled,
                "errors": errors[:20], "results": []}

    sim_cfg = {"get_pnl": get_pnl, "get_stats": get_stats, "check_submission": check_submission,
               "check_self_corr": check_submission, "check_prod_corr": check_submission}
    progress(message=f"fetching stats for {len(ok)} alpha(s)…", log=f"fetching stats ({len(ok)})")

    def fetch(x):
        try:
            return ace.get_specified_alpha_stats(s, x["alpha_id"], x["simulate_data"], **sim_cfg)
        except Exception:
            return {"alpha_id": x.get("alpha_id"), "simulate_data": x.get("simulate_data"),
                    "is_stats": None, "is_tests": None}

    stats_list = []
    with ThreadPool(3) as pool:
        for r in pool.imap(fetch, ok):
            stats_list.append(r)
    result = ace._delete_duplicates_from_result(stats_list)

    thr_sharpe, thr_fit = min_sharpe, min_fitness
    rows, passed, tagged = [], 0, 0
    for r in result:
        aid = r.get("alpha_id")
        cfg = r.get("simulate_data", {}) or {}
        settings = cfg.get("settings", {}) if isinstance(cfg.get("settings"), dict) else {}
        expr = cfg.get("regular") or ""
        st = r.get("is_stats")
        sharpe = fitness = turnover = returns = margin = drawdown = None
        d = {}
        if hasattr(st, "empty") and not st.empty:
            d = st.iloc[0].to_dict()
            sharpe, fitness, turnover, returns = _num(d.get("sharpe")), _num(d.get("fitness")), _num(d.get("turnover")), _num(d.get("returns"))
            margin, drawdown = _num(d.get("margin")), _num(d.get("drawdown"))
        tests = r.get("is_tests")
        tests_failed = int((tests["result"] == "FAIL").sum()) if (hasattr(tests, "empty") and not tests.empty and "result" in tests.columns) else 0

        # ace_lib returns GET /alphas/{id}/check in is_tests.  There is no
        # separate `check_submission` member on this result object.
        sub = tests if check_submission else None
        self_c = _corr_from_submission(sub, "self") if check_submission else None
        prod_c = _corr_from_submission(sub, "prod") if check_submission else None
        pool_c = _corr_from_submission(sub, "pool") or _corr_from_submission(sub, "power") if check_submission else None
        sub_fail = bool(hasattr(sub, "empty") and not sub.empty and "result" in sub.columns and (sub["result"] == "FAIL").any())

        turn = _norm_turnover(turnover)
        reasons = []
        # A strong negative result is research evidence even though it cannot pass the
        # positive-direction gate.  Fitness is not consistently signed by every BRAIN
        # response, so assess its magnitude and require the Sharpe itself to be negative.
        inversion_candidate = bool(sharpe is not None and fitness is not None
                                   and sharpe < 0 and abs(sharpe) >= 1.0 and abs(fitness) >= 0.8)
        if sharpe is None or sharpe < thr_sharpe:
            reasons.append(f"Sharpe {sharpe:.2f}<{thr_sharpe}" if sharpe is not None else "no Sharpe")
        if fitness is None or fitness < thr_fit:
            reasons.append(f"Fitness {fitness:.2f}<{thr_fit}" if fitness is not None else "no Fitness")
        if inversion_candidate:
            reasons.append("promising negative direction — simulate the negated expression")
        if turn is not None and turn >= max_turnover:
            reasons.append(f"turnover {turn:.0%}≥{max_turnover:.0%}")
        if tests_failed:
            reasons.append(f"{tests_failed} IS test fail")
        if check_submission:
            if sub_fail:
                reasons.append("submission check FAIL")
            for label, val in (("self", self_c), ("prod", prod_c), ("powerpool", pool_c)):
                if val is not None and abs(val) >= max_corr:
                    reasons.append(f"{label}-corr {abs(val):.2f}≥{max_corr}")
        ok_gate = not reasons

        n_ops = len(_operators_in(expr))

        # tag FIRST (winner tag if strong & passing, else the plain tag — both user-chosen),
        # so the applied tag is recorded on the stored row.
        tag_used = ""
        if aid:
            try:
                if ok_gate and fitness is not None and fitness >= tag_winners_above and winner_tag:
                    ace.set_alpha_properties(s, aid, color=winner_color or "GREEN", tags=[winner_tag])
                    tag_used = winner_tag; tagged += 1
                elif tag:
                    ace.set_alpha_properties(s, aid, tags=[tag])
                    tag_used = tag
            except Exception:
                pass

        check_records = tests.to_dict(orient="records") if (hasattr(tests, "empty") and not tests.empty) else []
        evidence = ({"submission_tests": check_records,
                     "self": {"focus": self_c}, "production": {"focus": prod_c},
                     "powerpool": {"focus": pool_c}} if check_submission else {})
        with SessionLocal() as db:
            db.add(M.SimResult(alpha_id=aid or "", expression=expr, region=region, delay=delay,
                               universe=settings.get("universe", ""), neutralization=settings.get("neutralization", ""),
                               sharpe=sharpe, fitness=fitness, turnover=turn, returns=returns,
                               margin=margin, drawdown=drawdown,
                               self_corr=self_c, prod_corr=prod_c, powerpool_corr=pool_c,
                               tests_failed=tests_failed, passed_gate=ok_gate,
                               gate_reasons=json.dumps(reasons), n_ops=n_ops, tagged=tag_used,
                               test_period=test_period, inversion_candidate=inversion_candidate,
                               transfer_study_id=int(transfer_study_id or 0),
                               evidence_json=json.dumps(evidence, default=str),
                               metrics_json=json.dumps({k: (_num(v) if hasattr(v, "item") else v)
                                                        for k, v in d.items()}, default=str)))
            db.commit()

        if fitness is not None and fitness > 0:
            try:
                # Credit the operators this alpha used, weighted by |fitness|, so the
                # diversity engine learns what actually WORKED (not just what was tried).
                _credit_usage(region, list(set(_operators_in(expr))), [], fitness)
            except Exception:
                pass

        if ok_gate:
            passed += 1
        rows.append({"alpha_id": aid, "expr": expr, "region":region,"delay":delay,"universe": settings.get("universe"),
                     "neutralization": settings.get("neutralization"), "sharpe": sharpe,
                     "fitness": fitness, "turnover": turn, "returns": returns, "margin": margin,
                     "drawdown": drawdown, "self_corr": self_c, "prod_corr": prod_c,
                     "powerpool_corr": pool_c, "tests_failed": tests_failed, "passed": ok_gate,
                     "reasons": reasons, "tag": tag_used, "test_period": test_period,
                     "inversion_candidate": inversion_candidate,
                     "suggested_expression": f"-({expr})" if inversion_candidate else ""})

    rows.sort(key=lambda x: (-(x["passed"]), -(x["fitness"] if x["fitness"] is not None else -999)))
    progress(log=f"done: {len(rows)} simulated · {passed} passed the gate · {tagged} tagged")
    return {"configs": len(alpha_list), "simulated": len(rows), "passed": passed, "tagged": tagged,
            "cancelled": cancelled, "errors": errors[:20], "thresholds": {"sharpe": thr_sharpe, "fitness": thr_fit},
            "results": rows}


def run_transfer_study(*, study_id, expressions, contexts, instrument, neutralizations, decay,
                       truncation, test_period, concurrency, limit_of_multi, tag, winner_tag,
                       check_submission, pasteurization="ON", unit_handling="VERIFY", nan_handling="OFF",
                       max_trade="OFF", visualization=False, max_turnover=.70, max_corr=.70,
                       min_sharpe=None, min_fitness=None, randomize=False, range_from=0, range_to=0,
                       progress, should_cancel) -> dict:
    """Evaluate one immutable expression set under every verified study context."""
    groups=[]; all_results=[]
    config_counts=[len(dict.fromkeys(x.strip() for x in expressions if x.strip()))*max(1,len(ctx.get("neutralizations") or neutralizations)) for ctx in contexts]
    global_total=sum(config_counts)
    global_lo=min(global_total,max(0,int(range_from or 0)))
    global_hi=min(global_total,max(global_lo,int(range_to))) if range_to and int(range_to)>0 else global_total
    selected_total=max(0,global_hi-global_lo)
    if selected_total <= 0:
        raise RuntimeError("The selected transfer config range is empty — set To above From.")
    offset=0; overall_done=0
    for i,ctx in enumerate(contexts):
        if should_cancel(): break
        region,delay,universe=ctx["region"],int(ctx["delay"]),ctx["universe"]
        context_neuts=ctx.get("neutralizations") or neutralizations
        context_count=config_counts[i]; local_lo=max(0,global_lo-offset); local_hi=min(context_count,global_hi-offset)
        offset+=context_count
        if local_hi<=local_lo:
            groups.append({"context":ctx,"simulated":0,"passed":0,"skipped_by_range":True,"results":[]})
            continue
        context_selected=local_hi-local_lo
        progress(total=selected_total,done=overall_done,
                 message=f"{region} D{delay} · {universe} · {context_selected} configs · 0/{context_selected}")
        thresholds=gate_thresholds(delay)
        try:
            valid=_valid_neuts(instrument,region,context_neuts)
            result=run_simulation(expressions=expressions,region=region,delay=delay,instrument=instrument,
                universes=[universe],neutralizations=valid,decay=decay,truncation=truncation,
                test_period=test_period,pasteurization=pasteurization,unit_handling=unit_handling,nan_handling=nan_handling,
                max_trade=max_trade,visualization=visualization,concurrency=concurrency,limit_of_multi=limit_of_multi,
                max_turnover=max_turnover,min_sharpe=min_sharpe if min_sharpe is not None else thresholds["sharpe"],
                min_fitness=min_fitness if min_fitness is not None else thresholds["fitness"],max_corr=max_corr,
                tag=tag,winner_tag=winner_tag,winner_color="GREEN",tag_winners_above=1,
                check_submission=check_submission,get_pnl=False,get_stats=False,
                progress=lambda **kw: progress(
                    total=selected_total,
                    done=min(selected_total,overall_done+min(context_selected,int(kw.get('done') or 0))),
                    message=f"{region} D{delay} · {universe} · {context_selected} configs · "
                            f"{min(context_selected,int(kw.get('done') or 0))}/{context_selected}"),
                should_cancel=should_cancel,transfer_study_id=study_id,randomize=randomize,
                range_from=local_lo,range_to=0 if local_hi>=context_count else local_hi)
            group={"context":ctx,"simulated":result.get("simulated",0),"passed":result.get("passed",0),
                   "errors":result.get("errors",[]),"results":result.get("results",[])}
            all_results.extend(group["results"]); groups.append(group)
        except Exception as exc:
            groups.append({"context":ctx,"simulated":0,"passed":0,"error":str(exc).splitlines()[0][:200]})
        overall_done+=context_selected
        progress(total=selected_total,done=overall_done,
                 message=f"transfer · {overall_done}/{selected_total} configurations complete")
    # Cross-context evidence per expression makes transferability explicit.
    evidence=[]
    for expr in dict.fromkeys(expressions):
        rows=[x for x in all_results if x.get("expr")==expr]
        sharpes=[x["sharpe"] for x in rows if x.get("sharpe") is not None]
        evidence.append({"expression":expr,"contexts_tested":len(rows),"contexts_passed":sum(bool(x.get("passed")) for x in rows),
            "direction_consistent":not sharpes or all(x>=0 for x in sharpes) or all(x<=0 for x in sharpes),
            "min_sharpe":min(sharpes) if sharpes else None,"max_sharpe":max(sharpes) if sharpes else None,
            "mean_sharpe":sum(sharpes)/len(sharpes) if sharpes else None})
    return {"study_id":study_id,"contexts":groups,"results":all_results,"transfer_evidence":evidence,
            "simulated":len(all_results),"passed":sum(bool(x.get("passed")) for x in all_results)}


def run_cross_region_sweep(*, expressions, regions, delay, instrument, neutralizations, decay,
                           truncation, concurrency, limit_of_multi, tag, winner_tag,
                           home_region="", progress, should_cancel) -> dict:
    """Run the same expressions across several regions to diversify fast. Each region uses a VALID
    universe/delay for it and that delay's gate thresholds.

    PRE-FLIGHT: before simulating, verify (against BRAIN's region-specific data-fields) that the
    dataset-specific datafields the expressions use actually EXIST in each region's universe. A
    region missing any required field is SKIPPED with the missing field names — so we never waste
    a simulation on a region that can't support the alpha, and the user sees exactly why."""
    out = []
    total = len(regions)

    # Which dataset-specific fields must exist everywhere? (Universal price/volume/group fields are
    # assumed present and not looked up.) Confirm them in the HOME region first — that's the ground
    # truth for "this identifier is a real datafield" vs a constant the search doesn't index.
    candidates = set()
    for e in expressions:
        candidates.update(_leaf_idents(e))
    to_check = {c for c in candidates if c.lower() not in _UNIVERSAL}
    required: set = set()
    if to_check and home_region:
        try:
            progress(message=f"verifying {len(to_check)} datafield(s) in {home_region}…")
            required = engine.datafields_present(home_region, delay, "TOP3000", instrument, to_check)
        except Exception:
            required = set()   # can't determine -> don't block, fall back to simulate-and-report

    for i, region in enumerate(regions):
        if should_cancel():
            break
        progress(total=total, done=i, message=f"sweeping {region} ({i + 1}/{total})…")
        _, d, universe = engine.valid_combo(instrument, region, delay, "TOP3000")
        try: region_valid_neuts = engine.valid_neutralizations(instrument, region)
        except Exception: region_valid_neuts = set(neutralizations)
        region_neuts = [n for n in neutralizations if n in region_valid_neuts]
        if not region_neuts:
            preferred = ["INDUSTRY", "SECTOR", "SUBINDUSTRY", "MARKET"]
            region_neuts = [next((n for n in preferred if n in region_valid_neuts),
                                 sorted(region_valid_neuts)[0] if region_valid_neuts else "INDUSTRY")]

        if required:
            progress(message=f"checking data in {region}…")
            try:
                present = engine.datafields_present(region, d, universe, instrument, required)
            except Exception:
                present = set(required)   # lookup failed -> don't block this region
            missing = sorted(required - present)
            if missing:
                out.append({"region": region, "delay": d, "universe": universe,
                            "skipped": "fields not available in this region", "missing": missing})
                continue

        t = gate_thresholds(d)
        try:
            res = run_simulation(
                expressions=expressions, region=region, delay=d, universes=[universe],
                neutralizations=region_neuts, decay=decay, truncation=truncation,
                test_period="P0Y", pasteurization="ON", unit_handling="VERIFY", nan_handling="OFF",
                max_trade="OFF", visualization=False, concurrency=concurrency, limit_of_multi=limit_of_multi,
                max_turnover=0.70, min_sharpe=t["sharpe"], min_fitness=t["fitness"], max_corr=0.70,
                tag=tag, winner_tag=winner_tag, winner_color="GREEN", tag_winners_above=1.0,
                check_submission=False, get_pnl=False, get_stats=False,
                progress=lambda **k: None, should_cancel=should_cancel)
            out.append({"region": region, "delay": d, "universe": universe, "neutralizations": region_neuts,
                        "passed": res.get("passed", 0), "simulated": res.get("simulated", 0),
                        "results": (res.get("results") or [])[:15]})
        except Exception as e:  # noqa: BLE001
            out.append({"region": region, "error": str(e).splitlines()[0][:140]})
    progress(total=total, done=total, message="sweep complete")
    return {"regions": out, "checked_fields": sorted(required)}


def run_batch(*, batches, region, delay, universes, neutralizations, decay, truncation,
              concurrency, limit_of_multi, min_sharpe, min_fitness, tag, winner_tag,
              progress, should_cancel) -> dict:
    """Run several expression batches sequentially in one background job — queue them and let it
    work unattended. Each batch reports its own pass count."""
    out = []
    for i, b in enumerate(batches):
        if should_cancel():
            break
        exprs = [e.strip() for e in b.get("expressions", []) if e.strip()]
        progress(total=len(batches), done=i, message=f"batch {i + 1}/{len(batches)} · {b.get('label', '')} ({len(exprs)})")
        if not exprs:
            out.append({"label": b.get("label", ""), "skipped": "empty"})
            continue
        try:
            res = run_simulation(
                expressions=exprs, region=region, delay=delay, universes=universes or ["TOP3000"],
                neutralizations=neutralizations or ["INDUSTRY"], decay=decay, truncation=truncation,
                test_period="P0Y", pasteurization="ON", unit_handling="VERIFY", nan_handling="OFF",
                max_trade="OFF", visualization=False, concurrency=concurrency, limit_of_multi=limit_of_multi,
                max_turnover=0.70, min_sharpe=min_sharpe, min_fitness=min_fitness, max_corr=0.70,
                tag=tag, winner_tag=winner_tag, winner_color="GREEN", tag_winners_above=1.0,
                check_submission=False, get_pnl=False, get_stats=False,
                progress=lambda **k: None, should_cancel=should_cancel)
            out.append({"label": b.get("label", ""), "passed": res.get("passed", 0), "simulated": res.get("simulated", 0)})
        except Exception as e:  # noqa: BLE001
            out.append({"label": b.get("label", ""), "error": str(e).splitlines()[0][:140]})
    progress(total=len(batches), done=len(batches), message="queue complete")
    return {"batches": out}


def run_walk_forward(*, expressions, region, instrument, neutralizations, decay, truncation,
                     concurrency, limit_of_multi, tag, winner_tag, progress, should_cancel) -> dict:
    """Regime/robustness check: run the same expressions at BOTH delay 1 and delay 0 (each with a
    valid universe + that delay's gate). An alpha that holds up at both delays is far more likely
    to be real than overfit to one."""
    out = []
    delays = [1, 0]
    for i, d in enumerate(delays):
        if should_cancel():
            break
        progress(total=len(delays), done=i, message=f"walk-forward · delay {d}…")
        _, dd, universe = engine.valid_combo(instrument, region, d, "TOP3000")
        if dd != d:
            out.append({"delay": d, "skipped": f"delay {d} not available in {region}"})
            continue
        t = gate_thresholds(d)
        try:
            res = run_simulation(
                expressions=expressions, region=region, delay=d, universes=[universe],
                neutralizations=neutralizations or ["INDUSTRY"], decay=decay, truncation=truncation,
                test_period="P0Y", pasteurization="ON", unit_handling="VERIFY", nan_handling="OFF",
                max_trade="OFF", visualization=False, concurrency=concurrency, limit_of_multi=limit_of_multi,
                max_turnover=0.70, min_sharpe=t["sharpe"], min_fitness=t["fitness"], max_corr=0.70,
                tag=tag, winner_tag=winner_tag, winner_color="GREEN", tag_winners_above=1.0,
                check_submission=False, get_pnl=False, get_stats=False,
                progress=lambda **k: None, should_cancel=should_cancel)
            out.append({"delay": d, "universe": universe, "passed": res.get("passed", 0),
                        "simulated": res.get("simulated", 0)})
        except Exception as e:  # noqa: BLE001
            out.append({"delay": d, "error": str(e).splitlines()[0][:140]})
    return {"delays": out}


# ── stored results + success rate ────────────────────────────────────────────────────

def list_results(limit: int = 200) -> dict:
    with SessionLocal() as db:
        rows = db.scalars(select(M.SimResult).order_by(desc(M.SimResult.created_at)).limit(limit)).all()
        out = [{"alpha_id": r.alpha_id, "expr": r.expression, "region": r.region, "delay": r.delay,
                "universe": r.universe, "neutralization": r.neutralization, "sharpe": r.sharpe,
                "fitness": r.fitness, "turnover": r.turnover, "returns": r.returns,
                "margin": r.margin, "drawdown": r.drawdown, "self_corr": r.self_corr,
                "prod_corr": r.prod_corr, "powerpool_corr": r.powerpool_corr,
                "tests_failed": r.tests_failed, "passed": r.passed_gate,
                "reasons": json.loads(r.gate_reasons or "[]"), "tag": r.tagged,
                "test_period": r.test_period or "P0Y0M0D",
                "inversion_candidate": bool(r.inversion_candidate),
                "transfer_study_id": int(r.transfer_study_id or 0),
                "suggested_expression": f"-({r.expression})" if r.inversion_candidate else "",
                "evidence": json.loads(r.evidence_json or "{}"),
                "metrics": json.loads(r.metrics_json or "{}")} for r in rows]
        n_all = db.scalar(select(func.count()).select_from(M.SimResult)) or 0
        n_pass = db.scalar(select(func.count()).select_from(M.SimResult).where(M.SimResult.passed_gate.is_(True))) or 0
    return {"results": out, "total": n_all, "passed": n_pass,
            "success_rate": round(n_pass / n_all, 3) if n_all else 0.0}

"""ORM models for the knowledge database.

Kept intentionally small but expressive: datasets and fields are stored per (id, region,
delay) so the same concept seen in different regions accumulates as separate rows the
similarity layer can link. JSON columns hold flexible payloads (selected ids, hypotheses)
without a table per shape.
"""

from __future__ import annotations

import time

from sqlalchemy import Boolean, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> float:
    return time.time()


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("dataset_id", "region", "delay", name="uq_dataset_region_delay"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, index=True)
    region: Mapped[str] = mapped_column(String, index=True)
    delay: Mapped[int] = mapped_column(Integer, index=True)
    instrument: Mapped[str] = mapped_column(String, default="EQUITY")
    universe: Mapped[str] = mapped_column(String, default="")
    category: Mapped[str] = mapped_column(String, default="", index=True)
    name: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    value_score: Mapped[float] = mapped_column(Float, default=0.0)
    alpha_count: Mapped[int] = mapped_column(Integer, default=0)
    fetch_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[float] = mapped_column(Float, default=_now)
    last_seen: Mapped[float] = mapped_column(Float, default=_now)


class Field(Base):
    __tablename__ = "fields"
    __table_args__ = (UniqueConstraint("field_id", "region", "delay", name="uq_field_region_delay"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[str] = mapped_column(String, index=True)
    dataset_id: Mapped[str] = mapped_column(String, index=True)
    region: Mapped[str] = mapped_column(String, index=True)
    delay: Mapped[int] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String, default="MATRIX")
    prefix: Mapped[str] = mapped_column(String, default="", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    alpha_count: Mapped[int] = mapped_column(Integer, default=0)
    is_virgin: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[float] = mapped_column(Float, default=_now)
    last_seen: Mapped[float] = mapped_column(Float, default=_now)


class DatasetConcept(Base):
    __tablename__ = "catalog_dataset_concepts"
    dataset_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String, default="", index=True)
    subcategory: Mapped[str] = mapped_column(String, default="", index=True)
    first_seen: Mapped[float] = mapped_column(Float, default=_now)
    last_seen: Mapped[float] = mapped_column(Float, default=_now)


class DatasetAvailability(Base):
    __tablename__ = "catalog_dataset_availability"
    __table_args__ = (UniqueConstraint("dataset_id", "instrument", "region", "delay", "universe",
                                      name="uq_catalog_dataset_context"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, index=True)
    instrument: Mapped[str] = mapped_column(String, default="EQUITY", index=True)
    region: Mapped[str] = mapped_column(String, index=True)
    delay: Mapped[int] = mapped_column(Integer, index=True)
    universe: Mapped[str] = mapped_column(String, index=True)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    value_score: Mapped[float] = mapped_column(Float, default=0.0)
    alpha_count: Mapped[int] = mapped_column(Integer, default=0)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    last_verified: Mapped[float] = mapped_column(Float, default=_now, index=True)


class FieldConcept(Base):
    __tablename__ = "catalog_field_concepts"
    field_id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String, default="MATRIX", index=True)
    prefix: Mapped[str] = mapped_column(String, default="", index=True)
    nature: Mapped[str] = mapped_column(String, default="", index=True)
    first_seen: Mapped[float] = mapped_column(Float, default=_now)
    last_seen: Mapped[float] = mapped_column(Float, default=_now)


class FieldAvailability(Base):
    __tablename__ = "catalog_field_availability"
    __table_args__ = (UniqueConstraint("field_id", "instrument", "region", "delay", "universe",
                                      name="uq_catalog_field_context"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[str] = mapped_column(String, index=True)
    instrument: Mapped[str] = mapped_column(String, default="EQUITY", index=True)
    region: Mapped[str] = mapped_column(String, index=True)
    delay: Mapped[int] = mapped_column(Integer, index=True)
    universe: Mapped[str] = mapped_column(String, index=True)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    date_coverage: Mapped[float] = mapped_column(Float, default=0.0)
    alpha_count: Mapped[int] = mapped_column(Integer, default=0)
    is_virgin: Mapped[bool] = mapped_column(Boolean, default=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    last_verified: Mapped[float] = mapped_column(Float, default=_now, index=True)


class CatalogSyncRun(Base):
    __tablename__ = "catalog_sync_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)
    status: Mapped[str] = mapped_column(String, default="planned", index=True)
    scope_json: Mapped[str] = mapped_column(Text, default="{}")
    request_budget: Mapped[int] = mapped_column(Integer, default=100)
    requests_used: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class CatalogSyncTask(Base):
    __tablename__ = "catalog_sync_tasks"
    __table_args__ = (UniqueConstraint("run_id", "kind", "context_key", "target_id",
                                      name="uq_catalog_sync_task"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String, index=True)  # datasets|fields
    context_key: Mapped[str] = mapped_column(String, index=True)
    target_id: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    records: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[float] = mapped_column(Float, default=_now)


class Usage(Base):
    """Per-user usage counters (operators / fields / categories / datasets / regions), the
    substrate for the diversity engine. Populated as generation and simulation land."""
    __tablename__ = "usage"
    __table_args__ = (UniqueConstraint("kind", "key", "region", name="uq_usage"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)     # operator|field|category|dataset|region
    key: Mapped[str] = mapped_column(String, index=True)
    region: Mapped[str] = mapped_column(String, default="", index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    sum_abs_fitness: Mapped[float] = mapped_column(Float, default=0.0)
    last_at: Mapped[float] = mapped_column(Float, default=_now)


class ResearchSession(Base):
    __tablename__ = "research_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    category: Mapped[str] = mapped_column(String, default="")
    region: Mapped[str] = mapped_column(String, default="")
    delay: Mapped[int] = mapped_column(Integer, default=1)
    goal: Mapped[str] = mapped_column(Text, default="")
    datasets_json: Mapped[str] = mapped_column(Text, default="[]")
    fields_json: Mapped[str] = mapped_column(Text, default="[]")
    provider: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    paper_name: Mapped[str] = mapped_column(String, default="")
    hypotheses_json: Mapped[str] = mapped_column(Text, default="[]")
    expressions_json: Mapped[str] = mapped_column(Text, default="[]")
    templates_json: Mapped[str] = mapped_column(Text, default="[]")


class SavedHypothesis(Base):
    """A reusable, deduplicated research thesis, independent of any one expression."""
    __tablename__ = "saved_hypotheses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)
    title: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    use_count: Mapped[int] = mapped_column(Integer, default=0)


class EvolutionRun(Base):
    __tablename__ = "evolution_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)
    status: Mapped[str] = mapped_column(String, default="running", index=True)
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    protocol_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")


class EvolutionIndividual(Base):
    __tablename__ = "evolution_individuals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    expression: Mapped[str] = mapped_column(Text)
    parent_expression: Mapped[str] = mapped_column(Text, default="")
    mutation: Mapped[str] = mapped_column(String, default="seed")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    pareto_rank: Mapped[int] = mapped_column(Integer, default=999)
    survived: Mapped[bool] = mapped_column(Boolean, default=False)


class AccountAlphaSnapshot(Base):
    """Read-only local snapshot of one alpha owned by the authenticated BRAIN user."""
    __tablename__ = "account_alpha_snapshots"
    alpha_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=0, index=True)
    fetched_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    status: Mapped[str] = mapped_column(String, default="", index=True)
    region: Mapped[str] = mapped_column(String, default="", index=True)
    delay: Mapped[int] = mapped_column(Integer, default=1)
    universe: Mapped[str] = mapped_column(String, default="")
    expression: Mapped[str] = mapped_column(Text, default="")
    operators_json: Mapped[str] = mapped_column(Text, default="[]")
    fields_json: Mapped[str] = mapped_column(Text, default="[]")
    datasets_json: Mapped[str] = mapped_column(Text, default="[]")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class AccountResearchSync(Base):
    __tablename__ = "account_research_syncs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quarter: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="running", index=True)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[float] = mapped_column(Float, default=_now)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)


class SimResult(Base):
    """A simulated alpha and its verdict against the success gate. `passed_gate` and
    `gate_reasons` capture exactly which metrics passed/failed, so the success-rate engine
    and reporting can reason over real, per-metric outcomes."""
    __tablename__ = "sim_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    alpha_id: Mapped[str] = mapped_column(String, default="", index=True)
    expression: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str] = mapped_column(String, default="", index=True)
    delay: Mapped[int] = mapped_column(Integer, default=1)
    universe: Mapped[str] = mapped_column(String, default="")
    neutralization: Mapped[str] = mapped_column(String, default="")
    sharpe: Mapped[float] = mapped_column(Float, nullable=True)
    fitness: Mapped[float] = mapped_column(Float, nullable=True)
    turnover: Mapped[float] = mapped_column(Float, nullable=True)
    returns: Mapped[float] = mapped_column(Float, nullable=True)
    margin: Mapped[float] = mapped_column(Float, nullable=True)
    drawdown: Mapped[float] = mapped_column(Float, nullable=True)
    self_corr: Mapped[float] = mapped_column(Float, nullable=True)
    prod_corr: Mapped[float] = mapped_column(Float, nullable=True)
    powerpool_corr: Mapped[float] = mapped_column(Float, nullable=True)
    tests_failed: Mapped[int] = mapped_column(Integer, default=0)
    passed_gate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    gate_reasons: Mapped[str] = mapped_column(Text, default="[]")
    n_ops: Mapped[int] = mapped_column(Integer, default=0)
    tagged: Mapped[str] = mapped_column(String, default="")
    test_period: Mapped[str] = mapped_column(String, default="P0Y0M0D", index=True)
    inversion_candidate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    transfer_study_id: Mapped[int] = mapped_column(Integer, default=0, index=True)


class TransferStudy(Base):
    """Persistent cross-market or D0/D1 research protocol and its exact shared data."""
    __tablename__ = "transfer_studies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)
    name: Mapped[str] = mapped_column(String, default="Transfer study")
    mode: Mapped[str] = mapped_column(String, default="regions")
    instrument: Mapped[str] = mapped_column(String, default="EQUITY")
    status: Mapped[str] = mapped_column(String, default="draft", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    current_stage: Mapped[str] = mapped_column(String, default="setup")
    active_job_id: Mapped[str] = mapped_column(String, default="")
    contexts_json: Mapped[str] = mapped_column(Text, default="[]")
    datasets_json: Mapped[str] = mapped_column(Text, default="[]")
    selected_datasets_json: Mapped[str] = mapped_column(Text, default="[]")
    fields_json: Mapped[str] = mapped_column(Text, default="[]")
    selected_fields_json: Mapped[str] = mapped_column(Text, default="[]")
    checkpoints_json: Mapped[str] = mapped_column(Text, default="{}")
    settings_json: Mapped[str] = mapped_column(Text, default="{}")


class TransferCandidate(Base):
    __tablename__ = "transfer_candidates"
    __table_args__ = (UniqueConstraint("study_id", "fingerprint", name="uq_transfer_candidate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    expression: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String, index=True)
    origin: Mapped[str] = mapped_column(String, default="research")
    lineage_json: Mapped[str] = mapped_column(Text, default="{}")


class AccountOptionCache(Base):
    __tablename__ = "account_option_cache"
    key: Mapped[str] = mapped_column(String, primary_key=True, default="active")
    updated_at: Mapped[float] = mapped_column(Float, default=_now)
    payload_json: Mapped[str] = mapped_column(Text, default="[]")


class OperatorRef(Base):
    """The Operator Lab's single source of truth for how each operator is used correctly:
    signature, parameter kinds (positional vs keyword), a canonical example the LLM copies,
    and notes. Seeded from the account's operator definitions + curated examples; the user can
    edit any row (user_edited=True protects it from re-seeding)."""
    __tablename__ = "operator_ref"

    # Composite key: the same operator name (add, multiply, …) exists under REGULAR/SELECTION/COMBO.
    name: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[str] = mapped_column(String, primary_key=True, default="REGULAR")   # REGULAR|SELECTION|COMBO
    signature: Mapped[str] = mapped_column(String, default="")
    params_json: Mapped[str] = mapped_column(Text, default="[]")   # [{name, kind, required, default}]
    example: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)


class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    scope: Mapped[str] = mapped_column(String, default="generate")   # generate|template
    category: Mapped[str] = mapped_column(String, default="")
    region: Mapped[str] = mapped_column(String, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    datasets_json: Mapped[str] = mapped_column(Text, default="[]")
    source_research_id: Mapped[int] = mapped_column(Integer, default=0)


class PromptRevision(Base):
    __tablename__ = "prompt_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt_id: Mapped[int] = mapped_column(Integer, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    protocol_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")


class ResearchCollection(Base):
    """Reusable fields/data nugget with its economic context and applicability."""
    __tablename__ = "research_collections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)
    name: Mapped[str] = mapped_column(String, default="")
    theme: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    fields_json: Mapped[str] = mapped_column(Text, default="[]")
    context_json: Mapped[str] = mapped_column(Text, default="{}")


class TemplateAsset(Base):
    __tablename__ = "template_assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)
    name: Mapped[str] = mapped_column(String, default="")
    template_text: Mapped[str] = mapped_column(Text, default="")
    nugget: Mapped[str] = mapped_column(Text, default="")
    context_json: Mapped[str] = mapped_column(Text, default="{}")


class TemplateRevision(Base):
    __tablename__ = "template_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(Integer, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    template_text: Mapped[str] = mapped_column(Text, default="")
    nugget: Mapped[str] = mapped_column(Text, default="")
    context_json: Mapped[str] = mapped_column(Text, default="{}")


class ResearchAudit(Base):
    """Immutable provenance for generation, simulation, evaluation and maintenance actions."""
    __tablename__ = "research_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    stage: Mapped[str] = mapped_column(String, index=True)
    experiment_id: Mapped[str] = mapped_column(String, default="", index=True)
    fingerprint: Mapped[str] = mapped_column(String, default="", index=True)
    provider: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class CatalogSnapshot(Base):
    __tablename__ = "catalog_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")

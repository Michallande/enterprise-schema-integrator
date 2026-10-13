"""Integration & Readiness Agent.

Pipeline:
  a. Schema mapping (LLM)         -> List[FieldMapping]
  b. Data quality engine (Python) -> List[DataIssue]
  c. Health score (Python)        -> int 0-100
  d. Executive synthesis (LLM)    -> summary + action items
  e. Assemble                     -> ReadinessReport

The LLM only does what needs judgment (fuzzy column matching, prose).
Every validation rule and the score are deterministic and reproducible.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Callable, Dict, List, Optional, Tuple

from dotenv import load_dotenv

# Load env vars before importing langsmith so tracing config is picked up.
load_dotenv()

import anthropic  # noqa: E402
import instructor  # noqa: E402
import pandas as pd  # noqa: E402
from langsmith import traceable  # noqa: E402
from langsmith.wrappers import wrap_anthropic, wrap_openai  # noqa: E402
from openai import OpenAI  # noqa: E402
from pydantic import BaseModel, Field, StringConstraints, field_validator  # noqa: E402

from schemas import CanonicalCustomer, DataIssue, FieldMapping, ReadinessReport  # noqa: E402



def has_real_key(name: str) -> bool:
    """True if the env var is set to something other than a .env.example placeholder."""
    value = os.getenv(name, "").strip()
    return bool(value) and not value.startswith("your_")


# Anthropic is preferred when its key is present; OpenAI is the fallback.
PROVIDER = "anthropic" if has_real_key("ANTHROPIC_API_KEY") else "openai"
if PROVIDER == "anthropic":
    MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    # Current Claude models reject non-default temperature; max_tokens is required.
    PROVIDER_KWARGS: Dict[str, Any] = {"max_tokens": 16000}
else:
    MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    PROVIDER_KWARGS = {"temperature": 0}

SAMPLE_ROWS = 5  # Only a small sample of records is sent to the LLM.
MIN_MAPPING_CONFIDENCE = 0.7

TARGET_FIELDS = list(CanonicalCustomer.model_fields)
VALID_STATUSES = {"active", "inactive", "churned"}

# Share of the score each severity can remove, applied to the fraction of
# records affected. Scales with dataset size, unlike a flat per-issue penalty.
SEVERITY_WEIGHTS = {"CRITICAL": 70, "WARNING": 20, "INFO": 10}

UNAMBIGUOUS_DATE_FORMATS = ["%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"]
SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
ROW_REFERENCE = re.compile(r"\brows?\s*#?\d", re.IGNORECASE)


# ---------------------------------------------------------------------------
# LLM response models
# ---------------------------------------------------------------------------


class SchemaMappingResult(BaseModel):
    mappings: List[FieldMapping]

    @field_validator("mappings")
    @classmethod
    def targets_must_exist(cls, mappings: List[FieldMapping]) -> List[FieldMapping]:
        # A failed validation makes instructor re-ask the LLM with the error.
        for m in mappings:
            if m.target_field_name not in TARGET_FIELDS:
                raise ValueError(
                    f"'{m.target_field_name}' is not a target field. "
                    f"Valid targets: {TARGET_FIELDS}"
                )
        targets = [m.target_field_name for m in mappings]
        dupes = {t for t in targets if targets.count(t) > 1}
        if dupes:
            raise ValueError(f"Each target field may be mapped only once: {sorted(dupes)}")
        return mappings


class ExecutiveSynthesis(BaseModel):
    # Length limits make instructor re-ask the LLM if it gets wordy.
    executive_summary: str = Field(
        ..., max_length=280,
        description="At most 2 short sentences: the readiness verdict, then the main blocker(s)",
    )
    key_action_items: List[Annotated[str, StringConstraints(max_length=100)]] = Field(
        ..., min_length=1, max_length=2,
        description="1-2 strategic next steps for leadership, one line each, no row references "
                    "(e.g. 'Request an updated data extract with mandatory email fields before ingestion')",
    )

    @field_validator("key_action_items")
    @classmethod
    def no_row_level_fixes(cls, items: List[str]) -> List[str]:
        # Row-level detail is already in the issues table; actions stay strategic.
        for item in items:
            if ROW_REFERENCE.search(item):
                raise ValueError(f"Action item must not reference specific rows: '{item}'")
        return items


def build_client() -> instructor.Instructor:
    # The LangSmith wrappers record each raw LLM call as a child run.
    if PROVIDER == "anthropic":
        # JSON mode: the schema goes in the prompt and the reply is validated
        # by Pydantic. Tool mode relies on forced tool_choice, which current
        # Claude models with thinking on can reject.
        return instructor.from_anthropic(wrap_anthropic(anthropic.Anthropic()), mode=instructor.Mode.ANTHROPIC_JSON)
    return instructor.from_openai(wrap_openai(OpenAI()))


# ---------------------------------------------------------------------------
# a. Schema mapping (LLM)
# ---------------------------------------------------------------------------


@traceable(name="schema_mapping")
def map_schema(client: instructor.Instructor, df: pd.DataFrame) -> List[FieldMapping]:
    sample = df.head(SAMPLE_ROWS).to_dict(orient="records")
    target_schema = json.dumps(CanonicalCustomer.model_json_schema()["properties"], indent=2)

    result = client.chat.completions.create(
        model=MODEL,
        response_model=SchemaMappingResult,
        max_retries=2,
        **PROVIDER_KWARGS,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a data integration engineer mapping a customer's legacy "
                    "export onto a canonical schema. Map each source column to at most "
                    "one target field. Omit source columns with no reasonable target. "
                    "Base confidence on both the column name and the sample values."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Target schema:\n{target_schema}\n\n"
                    f"Source columns: {json.dumps(list(df.columns))}\n\n"
                    f"Sample rows:\n{json.dumps(sample, indent=2, default=str)}"
                ),
            },
        ],
    )
    return result.mappings


# ---------------------------------------------------------------------------
# b. Data quality engine (deterministic)
# ---------------------------------------------------------------------------


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == ""


def _parse_number(value: Any) -> Tuple[Optional[float], bool]:
    """Return (number, was_text_formatted). number is None if unparseable."""
    if isinstance(value, (int, float)):
        return float(value), False
    try:
        return float(str(value).replace("$", "").replace(",", "").strip()), True
    except ValueError:
        return None, True


def _classify_date(value: str) -> str:
    """Return 'ok', 'ambiguous', or 'invalid'."""
    value = value.strip()
    match = SLASH_DATE.match(value)
    if match:
        first, second, year = (int(g) for g in match.groups())
        valid_mdy = _valid_date(year, first, second)
        valid_dmy = _valid_date(year, second, first)
        if valid_mdy and valid_dmy and first != second:
            return "ambiguous"
        return "ok" if valid_mdy or valid_dmy else "invalid"
    for fmt in UNAMBIGUOUS_DATE_FORMATS:
        try:
            datetime.strptime(value, fmt)
            return "ok"
        except ValueError:
            continue
    return "invalid"


def _valid_date(year: int, month: int, day: int) -> bool:
    try:
        datetime(year, month, day)
        return True
    except ValueError:
        return False


@traceable(name="data_quality_engine")
def check_data_quality(df: pd.DataFrame, mappings: List[FieldMapping]) -> List[DataIssue]:
    issues: List[DataIssue] = []

    # Schema-level issues: unmapped or weakly mapped target fields.
    trusted = {m.target_field_name: m for m in mappings if m.confidence_score >= MIN_MAPPING_CONFIDENCE}
    for m in mappings:
        if m.confidence_score < MIN_MAPPING_CONFIDENCE:
            issues.append(DataIssue(
                row_index=None, field_name=m.target_field_name, issue_type="low_confidence_mapping",
                description=f"'{m.source_field_name}' -> '{m.target_field_name}' has confidence "
                            f"{m.confidence_score:.2f}; needs human review before load.",
                severity="WARNING",
            ))
    for target in TARGET_FIELDS:
        if target not in trusted:
            issues.append(DataIssue(
                row_index=None, field_name=target, issue_type="unmapped_field",
                description=f"No source column confidently maps to required field '{target}'.",
                severity="CRITICAL",
            ))

    canonical = df.rename(columns={m.source_field_name: t for t, m in trusted.items()})

    def add(row: int, field: str, issue_type: str, description: str, severity: str) -> None:
        issues.append(DataIssue(row_index=row, field_name=field, issue_type=issue_type,
                                description=description, severity=severity))

    for row, record in canonical.iterrows():
        # Missing values in any mapped required field.
        for field in trusted:
            if _is_missing(record.get(field)):
                add(row, field, "missing_value", f"Required field '{field}' is empty.", "CRITICAL")

        email = record.get("email")
        if "email" in trusted and not _is_missing(email) and "@" not in str(email):
            add(row, "email", "invalid_format", f"Email '{email}' is missing '@'.", "CRITICAL")

        name = record.get("full_name")
        if "full_name" in trusted and not _is_missing(name) and str(name) != str(name).strip():
            add(row, "full_name", "whitespace", f"Name '{name}' has leading/trailing whitespace.", "INFO")

        ltv = record.get("lifetime_value")
        if "lifetime_value" in trusted and not _is_missing(ltv):
            number, was_text = _parse_number(ltv)
            if number is None:
                add(row, "lifetime_value", "invalid_format", f"Value '{ltv}' is not a number.", "CRITICAL")
            elif number < 0:
                add(row, "lifetime_value", "invalid_value",
                    f"Lifetime value is negative ({number:,.2f}).", "CRITICAL")
            elif was_text:
                add(row, "lifetime_value", "non_numeric_format",
                    f"Value '{ltv}' is stored as text; normalize to {number}.", "INFO")

        status = record.get("account_status")
        if "account_status" in trusted and not _is_missing(status):
            if str(status).strip().lower() not in VALID_STATUSES:
                add(row, "account_status", "unmapped_value",
                    f"Status '{status}' is not one of {sorted(VALID_STATUSES)}; "
                    "needs a business rule before load.", "CRITICAL")

        signup = record.get("signup_date")
        if "signup_date" in trusted and not _is_missing(signup):
            kind = _classify_date(str(signup))
            if kind == "ambiguous":
                add(row, "signup_date", "ambiguous_date",
                    f"Date '{signup}' could be MM/DD or DD/MM; confirm source locale.", "WARNING")
            elif kind == "invalid":
                add(row, "signup_date", "invalid_format", f"Date '{signup}' could not be parsed.", "CRITICAL")

    # Duplicate customer IDs.
    if "customer_id" in trusted:
        ids = canonical["customer_id"].dropna()
        for row in ids[ids.duplicated(keep="first")].index:
            add(row, "customer_id", "duplicate",
                f"Customer ID '{canonical.at[row, 'customer_id']}' appears more than once.", "CRITICAL")

    return issues


# ---------------------------------------------------------------------------
# c. Health score (deterministic)
# ---------------------------------------------------------------------------


@traceable(name="health_score")
def calculate_health_score(issues: List[DataIssue], total_records: int) -> int:
    if total_records == 0:
        return 0
    # Schema-level issues affect every record.
    if any(i.row_index is None and i.severity == "CRITICAL" for i in issues):
        return 0

    score = 100.0
    for severity, weight in SEVERITY_WEIGHTS.items():
        affected = {i.row_index for i in issues if i.severity == severity and i.row_index is not None}
        has_schema_issue = any(i.row_index is None and i.severity == severity for i in issues)
        fraction = 1.0 if has_schema_issue else len(affected) / total_records
        score -= weight * fraction
    return max(0, min(100, round(score)))


# ---------------------------------------------------------------------------
# d. Executive synthesis (LLM)
# ---------------------------------------------------------------------------


@traceable(name="executive_synthesis")
def synthesize(
    client: instructor.Instructor,
    score: int,
    total_records: int,
    mappings: List[FieldMapping],
    issues: List[DataIssue],
) -> ExecutiveSynthesis:
    counts = {s: sum(1 for i in issues if i.severity == s) for s in SEVERITY_WEIGHTS}
    payload = {
        "data_health_score": score,
        "total_records": total_records,
        "issue_counts": counts,
        "field_mappings": [m.model_dump() for m in mappings],
        "issues": [i.model_dump() for i in issues],
    }
    return client.chat.completions.create(
        model=MODEL,
        response_model=ExecutiveSynthesis,
        max_retries=2,
        **PROVIDER_KWARGS,
        messages=[
            {
                "role": "system",
                "content": (
                    "You write data migration readiness briefs for busy executives. "
                    "Use only the facts provided; do not invent issues or numbers. "
                    "Row indices are 0-based.\n"
                    "executive_summary: at most 2 short sentences. Open with whether the "
                    "data is ready to load, then name the main blocker.\n"
                    "key_action_items: only 1-2 strategic next steps for leadership, one "
                    "line each (under 15 words), starting with a verb. Address root causes in the source "
                    "process or data contract, not individual records: never mention row "
                    "numbers or repeat fixes already listed in the issues table. Example: "
                    "'Request an updated data extract with mandatory email fields before "
                    "ingestion'."
                ),
            },
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
    )


# ---------------------------------------------------------------------------
# e. Pipeline
# ---------------------------------------------------------------------------


@traceable(name="readiness_pipeline")
def run_pipeline(
    data_path: str,
    client: Optional[instructor.Instructor] = None,
    on_stage: Callable[[str], None] = lambda stage: None,
) -> ReadinessReport:
    client = client or build_client()

    on_stage("Parsing source data")
    records: List[Dict[str, Any]] = json.loads(Path(data_path).read_text())
    df = pd.DataFrame(records)

    on_stage("Mapping source schema to canonical model")
    mappings = map_schema(client, df)
    on_stage("Running data quality checks")
    issues = check_data_quality(df, mappings)
    score = calculate_health_score(issues, len(df))
    on_stage("Writing executive summary")
    synthesis = synthesize(client, score, len(df), mappings, issues)

    return ReadinessReport(
        data_health_score=score,
        total_records_processed=len(df),
        mapped_fields=mappings,
        data_issues=issues,
        executive_summary=synthesis.executive_summary,
        key_action_items=synthesis.key_action_items,
    )


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "messy_customer_data.json"
    report = run_pipeline(path)
    print(report.model_dump_json(indent=2))

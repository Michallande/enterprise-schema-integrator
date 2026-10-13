"""Offline stand-in for the instructor client, used when no OpenAI key is set.

Exposes the same `client.chat.completions.create(response_model=...)` interface,
so the agent pipeline runs unchanged. Mapping uses column-name keywords and the
summary is templated from the deterministic findings.
"""

import json
from types import SimpleNamespace
from typing import Any, Dict, List

from agent import ExecutiveSynthesis, SchemaMappingResult
from schemas import FieldMapping

# Checked in order; the first target whose keyword appears in the column name wins.
KEYWORDS = [
    ("email", ["email", "mail"]),
    ("account_status", ["status", "state"]),
    ("signup_date", ["date", "signup", "reg", "created"]),
    ("lifetime_value", ["revenue", "ltv", "lifetime", "value", "spend"]),
    ("full_name", ["name"]),
    ("customer_id", ["id", "ref"]),
]


def _map_columns(columns: List[str]) -> SchemaMappingResult:
    mappings, used = [], set()
    for column in columns:
        lowered = column.lower()
        for target, words in KEYWORDS:
            hit = next((w for w in words if w in lowered), None)
            if hit and target not in used:
                used.add(target)
                mappings.append(FieldMapping(
                    source_field_name=column,
                    target_field_name=target,
                    confidence_score=0.9,
                    reasoning=f"[mock] Column name contains '{hit}'.",
                ))
                break
    return SchemaMappingResult(mappings=mappings)


def _rows(rows: List[int], limit: int = 4) -> str:
    shown = ", ".join(map(str, rows[:limit]))
    return f"{shown} +{len(rows) - limit} more" if len(rows) > limit else shown


def _summarize(payload: Dict[str, Any]) -> ExecutiveSynthesis:
    score, total = payload["data_health_score"], payload["total_records"]
    issues = payload["issues"]
    critical = [i for i in issues if i["severity"] == "CRITICAL"]
    blocked_rows = sorted({i["row_index"] for i in critical if i["row_index"] is not None})

    if critical:
        summary = (f"[mock] Not ready to load: {len(critical)} critical issue(s) block "
                   f"rows {_rows(blocked_rows)}. Health score is {score}/100.")
    else:
        summary = f"[mock] Ready to load with minor cleanup. Health score is {score}/100."

    actions = ["[mock] Request a corrected source extract with validation on required fields"]
    if any(i["severity"] == "WARNING" for i in issues):
        actions.append("[mock] Agree a data contract with the source team on formats and status values")
    return ExecutiveSynthesis(executive_summary=summary, key_action_items=actions)


def _create(response_model, messages, **_: Any):
    user_content = messages[-1]["content"]
    if response_model is SchemaMappingResult:
        columns_line = next(line for line in user_content.splitlines() if line.startswith("Source columns:"))
        columns = json.loads(columns_line.split(":", 1)[1])
        return _map_columns(columns)
    if response_model is ExecutiveSynthesis:
        return _summarize(json.loads(user_content))
    raise ValueError(f"Mock client does not support {response_model.__name__}")


def build_mock_client() -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))

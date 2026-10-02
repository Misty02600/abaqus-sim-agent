"""两组Skill路由共用的原生评分与汇总，不负责Agent运行或状态恢复。"""

from dataclasses import dataclass

from pydantic_evals.evaluators import (
    EvaluationReason,
    Evaluator,
    EvaluatorContext,
    ReportEvaluator,
    ReportEvaluatorContext,
)
from pydantic_evals.reporting import ScalarResult
from pydantic_evals.reporting.analyses import ReportAnalysis

from abaqus_sim_agent.evaluation.skill_observation import required_int


@dataclass
class SkillRouteEvaluator[InputsT](Evaluator[InputsT, dict[str, object], None]):
    def evaluate(self, ctx: EvaluatorContext[InputsT, dict[str, object], None]):
        observed = ctx.output
        passed = observed["exact_route_pass"]
        if not isinstance(passed, bool):
            raise ValueError("routing verdict must be boolean")
        return {
            "exact_route_pass": EvaluationReason(
                passed,
                reason="Skill loading constraints only; engineering answer remains ungraded.",
            ),
            "required_load_recall": float(str(observed["required_load_recall"])),
            "infrastructure_error": str(
                observed.get("infrastructure_error_type") or "none"
            ),
            "review_status": str(observed["review_status"]),
        }


@dataclass
class SkillRoutingSummary[InputsT](ReportEvaluator[InputsT, dict[str, object], None]):
    def evaluate(
        self, ctx: ReportEvaluatorContext[InputsT, dict[str, object], None]
    ) -> list[ReportAnalysis]:
        cases = ctx.report.cases
        rows = [case.output for case in cases]
        summary = {
            "case_count": len(cases) + len(ctx.report.failures),
            "case_errors": len(ctx.report.failures),
            "grading_errors": sum(bool(case.evaluator_failures) for case in cases),
            "exact_route_passes": sum(
                bool(case.assertions["exact_route_pass"].value)
                for case in cases
                if not case.evaluator_failures and "exact_route_pass" in case.assertions
            ),
        }
        for source, target in (
            ("forbidden_load_count", "forbidden_loads_total"),
            ("load_call_count", "load_calls_total"),
            ("duplicate_load_call_count", "duplicate_load_calls_total"),
        ):
            summary[target] = sum(required_int(row, source) for row in rows)
        # Body size exists only in the structural-topic observer; do not invent it for the other suite.
        if rows and all("loaded_skill_body_bytes" in row for row in rows):
            summary["loaded_skill_body_bytes_total"] = sum(
                required_int(row, "loaded_skill_body_bytes") for row in rows
            )
        summary["runtime_requested_cases"] = sum(
            bool(row["runtime_requested"]) for row in rows
        )
        return [
            ScalarResult(title=name, value=value) for name, value in summary.items()
        ]

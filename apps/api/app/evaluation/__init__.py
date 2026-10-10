"""M8 golden-set evaluation and prompt comparison primitives."""

from app.evaluation.golden import GoldenCase, golden_set
from app.evaluation.pipeline import EvaluationReport, compare_prompts

__all__ = ["EvaluationReport", "GoldenCase", "compare_prompts", "golden_set"]

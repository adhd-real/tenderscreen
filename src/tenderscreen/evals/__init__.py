"""Score extraction results against hand-labelled answers, field by field."""

from tenderscreen.evals.compare import FieldResult, FieldStats, Outcome
from tenderscreen.evals.run import Evaluation, LabelsError, evaluate, main

__all__ = ["Evaluation", "FieldResult", "FieldStats", "LabelsError", "Outcome", "evaluate", "main"]

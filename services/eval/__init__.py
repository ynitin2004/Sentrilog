"""Evaluation harness (improvements.md P-2, roadmap Phase 13).

Every threshold in the pipeline was a guess until this package existed. Each suite here scores
one component against a versioned, hashed dataset and reports the number a buyer or regulator
would ask for: screening recall and false-positive rate, extraction field F1, face match TAR at a
fixed FAR, and auto-clear precision. Results go to the eval_runs table and to a Markdown report,
and CI runs the suites on every push.

    uv run python -m services.eval screening [--compare] [--record]
    uv run python -m services.eval face | extraction | risk | all
"""

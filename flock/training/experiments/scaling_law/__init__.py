"""Scaling-law sweeps: token-budget runs launched many at a time in one allocation.

`config` resolves a sweep file into runs, `trainer` saves at token counts and
stops at a budget, `train` is the per-run entry point and `sweep` plans,
prepares and launches runs.
"""

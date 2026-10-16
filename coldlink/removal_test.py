"""Test of graph dependence: run the core workflow with FalkorDB unreachable.

It should fail. Coldlink has no fallback store: similarity, series, paths,
decision dependencies and custody chains are all graph queries.

Usage: python -m coldlink.removal_test
"""
import os
import subprocess
import sys

env = dict(os.environ, FALKORDB_PORT="1")  # nothing listens here
p = subprocess.run([sys.executable, "-m", "coldlink.agents.lead", "scenarios/scenario_a.json", "--mode", "offline"],
                   env=env, capture_output=True, text=True)
last = (p.stderr.strip().splitlines() or ["(no stderr)"])[-1]
print(f"exit code {p.returncode}: {last}")
print("PASS: workflow fails without the graph" if p.returncode != 0 else "FAIL: workflow ran without the graph")
sys.exit(0 if p.returncode != 0 else 1)

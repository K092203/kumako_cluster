"""A solver that prints plain lines (no #TUNE) — measured via regex."""

import argparse
import random
import time

ap = argparse.ArgumentParser()
ap.add_argument("--alpha", type=float, default=1.0)
ap.add_argument("--budget", type=float, default=1.0)
a = ap.parse_args()

t0 = time.time()
score = 0.0
while time.time() - t0 < a.budget:
    score = max(score, random.random() * a.alpha)

print(f"time: {time.time() - t0:.6f}")
print(f"best={score:.8f}")
print("OK")

"""A tiny stochastic 'solver' that emits a #TUNE line (the default protocol)."""

import argparse
import random
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--alpha", type=float, default=1.0)
ap.add_argument("--budget", type=float, default=1.0)
ap.add_argument("--seed", type=int, default=None)
a = ap.parse_args()

random.seed(a.seed)
t0 = time.time()
score = 0.0
while time.time() - t0 < a.budget:
    score = max(score, random.random() * a.alpha)

print(f"best={score}")
print(
    f"#TUNE elapsed={time.time() - t0:.6f} score={score:.8f} correct=1",
    file=sys.stderr,
)

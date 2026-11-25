import argparse
from datetime import date
from pathlib import Path

from .core import generate_dataset


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a deterministic SYNTHETIC Azure cost dataset")
    ap.add_argument("--out", type=Path, required=True, help="output directory (outside the repo)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--months", type=int, default=13)
    ap.add_argument("--scale", type=float, default=1.0, help="1.0 ~ 20k rows per actual export; ~540 ~ 10M rows")
    ap.add_argument("--start", type=date.fromisoformat, default=date(2025, 10, 1))
    a = ap.parse_args()
    m = generate_dataset(a.out, seed=a.seed, months=a.months, scale=a.scale, start=a.start)
    print(f"wrote {m['actual_rows']} actual + {m['amortized_rows']} amortized rows to {a.out}")


if __name__ == "__main__":
    main()

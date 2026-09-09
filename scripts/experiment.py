"""Identity-split sensitivity experiment.

Question: the paper notes that contributors using multiple GitHub accounts corrupt
its contributor-history features, but never measures how much. This measures it.

Method: take contributors we believe use a single account, deal their pull requests
across two artificial accounts, rebuild the history features exactly as the authors'
code does, retrain, and compare against the untouched baseline. The target variable
never changes -- only the model's view of who wrote what.
"""
from __future__ import annotations

import json
import pickle
import random
import statistics
import sys

import numpy as np
from catboost import CatBoostClassifier
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import label_binarize

import build_features as bf

SEED = 20260909
N_SPLITS = 5
CLASSES = [0, 1, 2]


def matrix(feats):
    X = np.array([[f[c] for c in bf.FEATURES] for f in feats], dtype=float)
    y = np.array([f["target"] for f in feats], dtype=int)
    return X, y


def score(y_true, proba):
    """Macro one-vs-rest AUC-ROC and AUC-PR, as the paper reports them."""
    present = sorted(set(y_true))
    Y = label_binarize(y_true, classes=CLASSES)
    roc = roc_auc_score(y_true, proba, multi_class="ovr", average="macro", labels=CLASSES)
    aps = [average_precision_score(Y[:, k], proba[:, k]) for k in present]
    return roc, float(np.mean(aps))


def evaluate(feats, seed=SEED, warmup=0.15):
    """Time-ordered cross-validation, mirroring the paper's setup."""
    feats = sorted(feats, key=lambda f: f["opened_at"])
    feats = feats[int(len(feats) * warmup):]          # drop cold-start rows
    X, y = matrix(feats)

    rocs, prs, dummy_rocs, dummy_prs, importances = [], [], [], [], []
    for tr, te in TimeSeriesSplit(n_splits=N_SPLITS).split(X):
        model = CatBoostClassifier(
            iterations=400, depth=6, learning_rate=0.05,
            loss_function="MultiClass", random_seed=seed,
            verbose=False, allow_writing_files=False,
        )
        model.fit(X[tr], y[tr])
        proba = model.predict_proba(X[te])
        roc, pr = score(y[te], proba)
        rocs.append(roc); prs.append(pr)
        importances.append(model.get_feature_importance())

        # majority-class dummy: predict the training prior for every row
        prior = np.bincount(y[tr], minlength=3) / len(tr)
        droc, dpr = score(y[te], np.tile(prior, (len(te), 1)))
        dummy_rocs.append(droc); dummy_prs.append(dpr)

    imp = np.mean(importances, axis=0)
    return {
        "n": len(feats),
        "auc_roc": float(np.mean(rocs)),
        "auc_pr": float(np.mean(prs)),
        "dummy_roc": float(np.mean(dummy_rocs)),
        "dummy_pr": float(np.mean(dummy_prs)),
        "improve_roc": 100 * (np.mean(rocs) / np.mean(dummy_rocs) - 1),
        "improve_pr": 100 * (np.mean(prs) / np.mean(dummy_prs) - 1),
        "importance": {c: float(v) for c, v in zip(bf.FEATURES, imp)},
    }


def split_identities(rows, fraction, seed):
    """Deal each selected contributor's PRs across two artificial accounts."""
    rng = random.Random(seed)
    by_author: dict[str, list[int]] = {}
    for r in rows:
        by_author.setdefault(r["login"], []).append(r["number"])

    eligible = [a for a, prs in by_author.items() if len(prs) >= 2]
    chosen = set(rng.sample(eligible, int(round(len(eligible) * fraction))))

    identity = {}
    for author in chosen:
        for number in by_author[author]:
            identity[number] = f"{author}#{rng.randint(0, 1)}"
    return identity, len(chosen), len(eligible)


def main():
    rows, base_feats, _ = pickle.load(open("data/stage1.pkl", "rb"))

    results = {}
    print("=== baseline (real identities) ===", flush=True)
    base = evaluate(base_feats)
    results["baseline"] = base
    print(f"n={base['n']}  AUC-ROC {base['auc_roc']:.3f} (+{base['improve_roc']:.0f}% vs dummy)"
          f"  AUC-PR {base['auc_pr']:.3f} (+{base['improve_pr']:.0f}%)", flush=True)

    ranked = sorted(base["importance"].items(), key=lambda kv: -kv[1])
    print("top features:", ", ".join(f"{k} {v:.1f}" for k, v in ranked[:6]), flush=True)

    for fraction in (0.10, 0.20, 0.40):
        runs = []
        for rep in range(3):
            identity, n_split, n_elig = split_identities(rows, fraction, SEED + rep)
            feats = bf.build(rows, identity)
            runs.append(evaluate(feats, seed=SEED + rep))
        agg = {
            "fraction": fraction,
            "contributors_split": n_split,
            "contributors_eligible": n_elig,
            "auc_roc": statistics.mean(r["auc_roc"] for r in runs),
            "auc_roc_sd": statistics.pstdev([r["auc_roc"] for r in runs]),
            "auc_pr": statistics.mean(r["auc_pr"] for r in runs),
            "auc_pr_sd": statistics.pstdev([r["auc_pr"] for r in runs]),
            "importance": {
                c: statistics.mean(r["importance"][c] for r in runs) for c in bf.FEATURES
            },
        }
        agg["delta_roc"] = agg["auc_roc"] - base["auc_roc"]
        agg["delta_pr"] = agg["auc_pr"] - base["auc_pr"]
        results[f"split_{int(fraction*100)}"] = agg
        print(f"\n=== {int(fraction*100)}% of contributors split "
              f"({n_split}/{n_elig} with >=2 PRs) ===", flush=True)
        print(f"AUC-ROC {agg['auc_roc']:.3f} (sd {agg['auc_roc_sd']:.3f}) "
              f"delta {agg['delta_roc']:+.3f}", flush=True)
        print(f"AUC-PR  {agg['auc_pr']:.3f} (sd {agg['auc_pr_sd']:.3f}) "
              f"delta {agg['delta_pr']:+.3f}", flush=True)

    with open("results/results.json", "w") as fh:
        json.dump(results, fh, indent=2, default=float)
    print("\nwrote results.json", flush=True)


if __name__ == "__main__":
    sys.exit(main())

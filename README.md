# Does the multi-account problem actually affect the PR latency models?

A small sensitivity study on **Khatoonabadi et al., "Predicting the First Response
Latency of Maintainers and Contributors in Pull Requests"** (IEEE TSE, 2024).

## The question

The paper builds models that predict how long a maintainer takes to respond to a new
pull request. Two of its most important features describe the contributor's past
behaviour in the project:

- **Contributor Performance** — the share of that contributor's earlier PRs that were merged
- **Contributor Responsiveness** — the median time that contributor took to reply in earlier PRs

In the authors' own code (`measure_features_maintainers.py`), both are computed by
filtering earlier pull requests on `actor == @contributor`, which is the **GitHub
username**. When a contributor has no history, both fall back to `0`.

So if one person uses two GitHub accounts, the project sees two people, each with half
a history — and the first PR under each account looks exactly like a first-time
contributor. The paper acknowledges this in its threats to validity and recommends
that *future work* investigate identity correction, but never measures how much it
actually costs.

This repository measures it.

## What was done

1. Collected the 5,800 most recent Flutter pull requests from the GitHub GraphQL API
   (13 March 2026 – 9 September 2026), 5,758 of them authored by humans.
2. Identified 203 maintainer accounts from privileged actions — merging or closing a
   PR — plus reviewers who reviewed at least 3 pull requests written by others.
3. Rebuilt the paper's maintainer-side features, following the authors' definitions,
   and classified the first response latency into their three buckets
   (within 1 day / 1 day to 1 week / more than 1 week).
4. Trained CatBoost with time-ordered cross-validation, as the paper does.
5. Split a share of contributors into two artificial accounts, rebuilt the history
   features under those split identities, retrained, and compared.

The model uses 15 features across the project, contributor and pull request
dimensions — the same set the paper's maintainer-side model uses. The six
review-process features in the paper's Table 2 are marked contributor-only, because
they describe events that occur after the maintainer has already responded; one of
them *is* the maintainer's first response latency.

The target variable never changes between runs. The only difference is the model's
view of who wrote what.

## Result 1 — the baseline reproduces

| | This study | Paper (Flutter) |
|---|---|---|
| AUC-ROC | 0.695 | 0.71 |
| AUC-PR | 0.498 | 0.47 |

2,251 evaluation rows after dropping a 15% cold-start warm-up. Class balance is
50.5% / 30.0% / 19.6%. Improvement over a majority-class dummy is +39% AUC-ROC and
+49% AUC-PR, close to the paper's reported +42% and +76% for Flutter.

Contributor Performance is the single most important feature, matching the paper's
finding that it sits in the top five.

## Result 2 — the newcomer effect replicates strongly

The paper's most socially significant claim is that inexperienced contributors wait
longer for feedback. On completely independent 2026 data:

| | Median wait | Share waiting > 1 week |
|---|---|---|
| No prior history in the project | 124.4 h | 39.1% |
| Five or more prior PRs | 15.6 h | 14.9% |

Newcomers wait roughly **eight times longer** for a first response. This is an
independent confirmation on data the authors never saw.

## Result 3 — but the identity threat is small

| Contributors split into two accounts | AUC-ROC | Change | AUC-PR | Change |
|---|---|---|---|---|
| none (baseline) | 0.695 | — | 0.498 | — |
| 10% | 0.691 | −0.004 | 0.496 | −0.003 |
| 20% | 0.692 | −0.003 | 0.496 | −0.002 |
| 40% | 0.685 | −0.010 | 0.489 | −0.010 |

Three random repetitions per level; standard deviations 0.002–0.006. The 10% and 20%
changes sit inside that noise, so the only level that clearly separates from the
baseline is 40%.

The newcomer gap barely moves either: **+108.7 h** at baseline versus **+108.1 h**
with 40% of contributors split.

To bound it from above, wiping *all* contributor history — every PR treated as a
first-timer — costs only:

| | AUC-ROC | AUC-PR |
|---|---|---|
| history features intact | 0.695 | 0.498 |
| history destroyed entirely | 0.643 | 0.454 |
| worst possible cost | **−0.052** | **−0.044** |

Total identity failure costs about 0.05 AUC. Any realistic level of multi-account
error is a fraction of that.

## Conclusion

The criticism was worth testing and the answer is **no**. The multi-account problem is
real and the paper is right to name it, but on this data it does not materially change
either the model's accuracy or the newcomer finding. Splitting 40% of contributors
across two accounts costs about 1.4% of relative AUC.

The reason is arithmetic rather than luck. Splitting one contributor's PRs across two
accounts creates only one extra zero-history pull request per account; it does not
erase the rest. The share of PRs with no contributor history rises from 11.4% to
13.1% even when 40% of contributors are split.

So the honest revision to the critique is this: the authors were right to leave it in
future work, and a reader should treat their newcomer finding as solid rather than
fragile.

## Deviations from the paper

Stated plainly, because they matter for how far these numbers travel.

- **Different time window.** The paper's data ends December 2022; this covers March to
  September 2026. Flutter's review process has changed, and it now merges through an
  `auto-submit` bot.
- **Maintainer identification.** The paper used privileged timeline events. This uses
  merge and close actors plus frequent reviewers, because Flutter's bot performs most
  merges and GitHub's `authorAssociation` labels most Flutter reviewers as
  `CONTRIBUTOR`.
- **First response detection.** Comments and reviews only, not the full event timeline,
  so only 46% of PRs have a detectable first response and can be used. The paper's
  broader event definition covers more. The risk is not sample size but selection: PRs
  that attract a written response may not be representative of all PRs.
- **`pr_commits`** uses the PR's total commit count rather than commits at submission time.
- **One project.** Flutter only, so nothing here generalises to the other 19.
- **Split model.** Each selected contributor's PRs are dealt randomly between two
  accounts. A real second account might instead take over at a point in time. Random
  dealing fragments history more aggressively, so this is the harsher test.
- **Unvalidated thresholds.** The 3-review maintainer rule, the 15% warm-up and the
  5-PR definition of an experienced contributor are judgement calls, not results of a
  sensitivity check.

## Running it

```bash
# put a GitHub token (classic, no scopes needed) in github_token.txt
python3 scripts/collect_flutter.py     # raw PR data
python3 scripts/collect_mergers.py     # who has write access
python3 scripts/experiment.py          # baseline + identity splits
```

Requires `catboost`, `scikit-learn` and `numpy`. Collection uses the standard library only.

## Credit

All credit for the original study, its design, and its feature definitions belongs to
Khatoonabadi, Costa, Abdellatif and Shihab. Their paper is at
[arXiv:2311.07786](https://arxiv.org/abs/2311.07786) and their replication package at
[doi:10.5281/zenodo.10119283](https://doi.org/10.5281/zenodo.10119283).
This repository is an independent robustness check by a reader, not by the authors,
and uses freshly collected data rather than theirs.

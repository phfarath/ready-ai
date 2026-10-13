# Mutation Drill — Healer Scorecard Playbook

Monthly drill that injects declared mutations into a test/staging app and
measures whether the drift-detection stack (visual diff × DOM fingerprint,
under the US5 multi-causal gate) catches real changes without firing on
cosmetic noise. Output is a healer scorecard (`healer_scorecard.json` +
`healer_scorecard.html`) with detection rate and false-positive rate per
regime: `single_visual`, `single_dom`, `gated`.

## Why

Auto-healing rewrites documentation baselines. Two failure modes:

- **Missed drift (FN)**: product changed, detector stayed silent → docs rot.
- **False positive (FP)**: cosmetic churn (timestamps, rotating avatars,
  promo banners) flagged as drift → healer churns baselines on noise.

The drill quantifies both per channel, so regressions in the detector are
caught by measurement instead of production surprises.

## Running a drill

```bash
python -m tools.mutation_harness --config tools/mutation_harness/examples/drill.yaml \
    --url http://staging.internal/app --out drills/2026-10/
```

Requirements: Chrome available (same launch path as the engine), the app
reachable via CDP. `--headed` and `-v` help debug a new mutation set.

Artifacts in the output dir: `shots/` (baseline + per-mutation), `diffs/`,
`healer_scorecard.json`, `healer_scorecard.html`.

## Declaring mutations

```yaml
app_url: http://127.0.0.1:8000/inner   # or pass --url
threshold: 0.95                        # visual similarity threshold
settle_ms: 150                         # wait between apply and measure
mutations:
  - id: rename-cta                     # semantic: MUST be detected
    kind: semantic
    op: set_text
    selector: "#cta"
    value: "Confirm order"
  - id: drop-panel
    kind: semantic
    op: remove
    selector: "#promo-panel"
  - id: clock-churn                    # noise: should NOT be flagged
    kind: noise
    op: set_text
    selector: "#clock"
    value: "{{now}}"                   # placeholders: {{now}}, {{rand}}
  - id: promo-banner
    kind: noise
    op: insert_banner                  # selector optional (default: body)
    value: "Sale ends soon"
```

Ops: `set_text`, `set_attribute` (needs `attr`), `remove`, `move_after`
(needs `to`), `insert_banner`, `js` (raw expression — must return truthy
when applied). A mutation whose JS returns falsy (stale selector) is
recorded as `applied: false` and excluded from the confusion matrix —
fix the declaration, don't let it masquerade as a detection miss.

## Reading the scorecard

- **detection_rate / mutation_score** — fraction of semantic mutations
  flagged. Drops mean the detector is going blind.
- **fpr** — fraction of noise mutations flagged. Rises mean the healer
  is churning baselines on cosmetics.
- **single-channel vs gated** — the US5 gate requires both channels to
  agree before auto-heal actuates; a single flag alone yields
  DRIFT_SUSPECTED. Expect the gate to trade some detection rate for a
  much lower FPR. If `gated` FPR > 0, noise is crossing both channels —
  investigate before trusting auto-heal.

## Monthly cadence

1. Run the drill against the staging app (schedule or manual).
2. Diff the scorecard against last month's JSON — committed history of
   `healer_scorecard.json` gives the trend line.
3. **Correlate with escaped production drift**: any drift discovered in
   production that the drill's semantic set would have caught (per its
   channel profile) marks a detector regression — add a mutation covering
   that change shape and re-run.
4. Review `skipped_mutations`: stale selectors mean the fixture set
   rotted, not that detection improved.
5. Regime deltas to act on:
   - `single_visual` FPR climbing → raise threshold or add noise
     exclusions before the gate has to absorb them.
   - `gated` detection falling below `single_dom` → visual channel is
     under-contributing; check threshold and screenshot stability.

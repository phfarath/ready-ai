"""E2E: mutation drill against the local fixture server with real Chrome.

Exercises the full harness path — BrowserSession, Runtime.evaluate
mutation injection, screenshot similarity, DOM fingerprint — and asserts
the scorecard the drill produces. Auto-skipped without a Chrome binary
(see conftest require_chrome).

Expected channel behaviour on the ``/inner`` fixture page
(``#inner-btn`` is fingerprinted interactive; ``#inner-status`` is not):

- ``promo-with-cta`` (semantic js): big banner + new button → visual AND
  dom flag → gated TP.
- ``rename-inner-btn`` (semantic set_text): button text lives in the DOM
  fingerprint but barely moves pixels → single_dom TP, gated FN. This is
  the documented gate blindspot the scorecard exists to measure.
- ``promo-banner`` (noise insert_banner): big visual block, no
  interactive elements → single_visual FP, gated TN. This is the
  false-positive the gate exists to suppress.
- ``clock-churn`` (noise set_text on non-fingerprinted span): TN.
"""

import json

import yaml

from tools.mutation_harness.runner import run_mutation_drill

_DRILL_MUTATIONS = [
    {
        "id": "promo-with-cta",
        "kind": "semantic",
        "op": "js",
        "js": (
            "(()=>{const b=document.createElement('div');"
            "b.style.cssText='padding:60px;background:#7c3aed;color:#fff;"
            "font-size:24px;text-align:center';"
            "b.innerHTML='<button id=\"new-cta\">Buy now</button> BIG PROMO';"
            "document.body.prepend(b);return true})()"
        ),
    },
    {
        "id": "rename-inner-btn",
        "kind": "semantic",
        "op": "set_text",
        "selector": "#inner-btn",
        "value": "Confirm action now",
    },
    {
        "id": "promo-banner",
        "kind": "noise",
        "op": "insert_banner",
        "value": "Limited offer — {{rand}}",
    },
    {
        "id": "clock-churn",
        "kind": "noise",
        "op": "set_text",
        "selector": "#inner-status",
        "value": "{{now}}",
    },
]


async def test_mutation_drill_end_to_end(e2e_server, cdp_port, tmp_path):
    config_path = tmp_path / "drill.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "app_url": f"{e2e_server}/inner",
                "threshold": 0.95,
                "settle_ms": 100,
                "mutations": _DRILL_MUTATIONS,
            }
        ),
        encoding="utf-8",
    )

    scorecard = await run_mutation_drill(
        config_path,
        output_dir=tmp_path / "drill_out",
        port=cdp_port,
        headless=True,
    )

    # All declared mutations must actually apply — a falsy apply is a
    # stale declaration, not a detection miss.
    assert scorecard["applied_mutations"] == scorecard["total_mutations"] == 4

    by_id = {m["id"]: m for m in scorecard["mutations"]}

    # Semantic: banner + injected button flags BOTH channels → gated TP.
    promo = by_id["promo-with-cta"]
    assert promo["visual_flagged"] is True
    assert promo["dom_flagged"] is True
    assert promo["verdicts"]["gated"] == "TP"

    # Semantic: button rename flips the fingerprint (text is part of the
    # interactive-element state) — dom channel must flag it.
    assert by_id["rename-inner-btn"]["dom_flagged"] is True
    assert by_id["rename-inner-btn"]["verdicts"]["single_dom"] == "TP"

    # Noise banner: visual flags it, dom stays clean → single-channel FP,
    # gate suppresses it (TN). This is the tradeoff the card measures.
    banner = by_id["promo-banner"]
    assert banner["visual_flagged"] is True
    assert banner["dom_flagged"] is False
    assert banner["verdicts"]["single_visual"] == "FP"
    assert banner["verdicts"]["gated"] == "TN"

    # Noise timestamp on a non-fingerprinted span: dom never flags.
    assert by_id["clock-churn"]["dom_flagged"] is False

    gated = scorecard["regimes"]["gated"]
    assert gated["fp"] == 0
    assert gated["tp"] >= 1

    # Artifacts: JSON scorecard + rendered HTML + per-mutation shots.
    out = tmp_path / "drill_out"
    saved = json.loads((out / "healer_scorecard.json").read_text())
    assert saved["regimes"]["gated"]["tp"] == gated["tp"]
    html = (out / "healer_scorecard.html").read_text(encoding="utf-8")
    assert "Healer Scorecard" in html
    assert (out / "shots" / "_baseline.png").exists()
    assert (out / "shots" / "m_promo-with-cta.png").exists()

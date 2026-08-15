# AI/ML Security Capabilities — Roadmap Notes

> Brainstorm 2026-07-25. Ranked by value-for-effort given what Duar already has
> (structured AI-ready logs, silentLogin/step-up, three-tier authz, refresh-reuse detection).

## Principle

**AI never sits in the authz decision path.** Allow/deny stays deterministic
(JWT, RBAC, ACL). AI belongs in detection, scoring, and recommendation —
feeding signals into deterministic policy.

## Tier 1 — AI-adjacent, no model needed (do first)

| Capability | How |
|---|---|
| Geo-velocity / impossible travel | Haversine + timestamp math on login/refresh events from existing structured logs. A rule, not ML — but the #1 signal vendors sell as AI. |
| New device / ASN / country flags | Hash of UA + IP-ASN per user in Redis; first-seen triggers step-up or notification email. |
| Credential-stuffing detection | Per-IP and per-ASN failure-ratio counters on `/auth` endpoints. slowapi already in the stack — this is smarter thresholds. |

~A week of work on top of existing logs; 80% of the story.

## Tier 2 — real ML, cheap, high value

- **Risk-based adaptive authentication** — score each login/refresh
  (features: geo delta, hour-of-day deviation, device novelty, ASN reputation,
  failure history) with an isolation forest or plain logistic model.
  Score → action: low = silent, medium = force interactive re-auth
  (`silentLogin`/`autoReauth` is the step-up lever), high = deny + audit.
  Industry standard (Entra Conditional Access risk, Google risk-based flows).
  First thing that justifies an actual model.
- **Service-key behavioral baselines** — service apps have very regular
  traffic; anomaly detection on call pattern/volume/source per `service_name`
  catches stolen keys far better than it does for humans.

## Tier 3 — LLM-based

- **Log triage / incident narration** — pipe anomaly clusters to Claude for
  "what happened, is it an attack, blast radius" summaries into admin panel or
  alerts. Detection stays deterministic; the LLM only explains. The logging
  envelope was explicitly designed for this.
- **Role mining / privilege-creep reports** — LLM (or plain clustering) over
  RBAC grants vs. actual action usage: dormant grants, duplicate roles,
  least-privilege recommendations. Advisory output = good LLM fit.
- **Access-review copilot (admin)** — "explain why user X can edit document Y"
  by walking the three tiers and narrating the grant chain. UX over existing
  `/check` logic.
- **AI-driven adversarial testing** — `duar-pentest` Layer 2 is already
  positioned for this: LLM-generated attack sequences against staging,
  replayed as regression suites.

## Skip / later

- ML WAF — buy, don't build.
- UEBA platforms — overkill at current scale.
- Any "AI firewall" in the token path — violates the principle above.

## Next step (when picked up)

Sketch the event schema + risk-score seam for Tier 2 adaptive auth.

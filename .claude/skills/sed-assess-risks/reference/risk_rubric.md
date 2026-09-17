# Risk rubric (sed-assess-risks)

## What to report
- **Combinations the rules cannot see:** a vendor whose SLA is falling (`vendor.<id>.sla_delta_pp` below zero) while a
  contract of theirs renews within the horizon; an approved issue cluster on a vendor-run service; a renewal whose
  comments mention an uplift, a dispute or an exit plan.
- **Context for a rule finding:** why an auto-renewing contract matters (value, criticality, alternatives mentioned in
  the comments), or why a utilisation outlier is seasonal. Use `annotates_rule_stable_key`.
- **Nothing** for a subject that is fine. Do not restate rule findings without judgement.

## Kinds
| Kind | Subject | Typical signals |
|---|---|---|
| `renewal_risk` | contract | days to notice or end, auto-renew, annual value, comments |
| `license_risk` | license | utilisation, idle cost, over-assignment |
| `vendor_risk` | vendor | SLA delta and series, contract value, approved clusters |
| `cost_risk` | app | cost variance against budget |
| `rationalization` | app | quiet application with cost |

## Severity
- `critical`: an auto-renewal or a large spend decision with a deadline within about 3 weeks, or a vendor service
  failing on a business-critical process.
- `high`: a decision needed this quarter, or a vendor SLA clearly degrading (several points) with money at stake.
- `medium`: a dated item worth preparing (renewal in the horizon, moderate decline).
- `low`: minor or already handled (for example the contract is marked non-renewing).

## Decision and date
- `recommendation`: the concrete decision (renegotiate, give notice, right-size N licences, open a service review,
  decommission) in one or two sentences.
- `decision_due`: the notice deadline when there is one; otherwise the date by which the decision must be taken; null
  when there is no date.

## Evidence
- `signals`: the fact keys you rely on, copied from the packet's `facts`.
- `body_md`: two or three sentences; numbers only as `{{f:<fact_key>}}` tokens of those signals.
- `confidence`: your calibrated probability that the risk is real and correctly rated.

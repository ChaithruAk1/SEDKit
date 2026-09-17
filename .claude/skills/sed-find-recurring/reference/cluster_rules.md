# Cluster rules (sed-find-recurring)

## What makes a cluster
Report a cluster when incidents share a cause and the pattern deserves attention:
- **Episode after a change:** groups of one application that start within days of each other, right after a change
  listed in `changes_before_onset` (a close code `successful_issues` or `unsuccessful` is a strong hint). Merge every
  phrasing and language of the same symptom into one cluster and name the change in `suspected_change`.
- **Periodic failure:** a `candidate_group` with `periodicity: monthly` (or the same weekday every week) whose
  `day_of_month` concentrates on the same days: month-end or month-start jobs. Periodic issues without a problem record
  usually deserve `raise_problem`.
- **Cross-application outage:** `burst` groups on several applications on the same day with the same symptom (SSO,
  network, a shared platform). Make one cluster with every such group and severity `high` or `critical`.
- **Growth:** a `label_group` whose `growth_pct` is high with enough `tickets_last_4w` to matter.
- **Large steady symptom** with an obvious action (users who need a how-to page, a recurring access request that
  belongs in a catalogue item).

Do not report ordinary background noise: steady low-volume groups with no growth, no periodicity and no change hint.
Leave a group out rather than invent a cause. Ten precise clusters beat forty vague ones.

## Merging groups
- Groups of the same application with the same symptom in other words or another language belong together.
- A `label_group` and a `candidate_group` describing the same symptom belong together (the label group gives the AI
  category and growth, the candidate group the longer history).
- Groups of different applications belong together only for one shared outage (same day, same symptom).

## Action
| Situation | `recommended_action` |
|---|---|
| Recurring or growing, cause unknown, no problem record (`problem_exists` false) | `raise_problem` |
| Users can solve or work around it themselves and no KB or runbook page exists (`kb` empty) | `kb_article` |
| A vendor-run service, vendor product defect or hosted platform | `vendor_escalation` |
| Known defect or configuration, fix identified (after a change, a failing job) | `fix` |
| Real but small, or already handled (a problem exists and volume is falling) | `monitor` |

## Severity
- `critical`: business stopped, many users or several applications (an SSO outage).
- `high`: a business process disrupted (postings, month-end close) or fast growth.
- `medium`: steady volume with a clear action.
- `low`: minor inconvenience.

## Key merges
Merge two symptom keys of one application only when they name the same symptom (`queue_stuck` and
`queue_messages_stuck`); keep keys that differ in what fails (`login_failure` and `password_reset`). Fold the key with
fewer tickets into the one with more.

## Confidence and wording
- `confidence`: your calibrated probability that the cluster is real and correctly grouped.
- `title`: what goes wrong, in plain words, without numbers, names or ticket ids.
- `body_md`: two or three sentences for the reviewer: what happens, since when, why you think so, what to do. Numbers
  only as `{{f:<ref>.<fact>}}` tokens listed in `evidence`.

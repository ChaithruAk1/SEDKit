# Test design (sed-draft-test-plan)

## Coverage
- Every story has at least one case; every acceptance criterion is checked by at least one case.
- Add a `negative` case for each validation rule or error behaviour a criterion mentions.
- Add an `integration` case when the story crosses systems (an interface, a file exchange, a notification).
- Add `security` cases for access rules (who may and may not do the action) and `accessibility` cases for user-facing
  screens when the story is about a screen.
- `uat` cases describe the business scenario end to end in the user's words; one or two per story set is enough.
- `performance` only when a criterion names a response time or volume.

## Writing a case
- Title: what is checked, e.g. "Invalid tax identifier is flagged on intake".
- Preconditions: the state before the steps, with placeholders (<test supplier>, <approver role>).
- Steps: short imperative actions a tester can follow without guessing; no more than twelve.
- Expected: one observable result per case. Two results mean two cases.
- Never use real people, credentials, hosts, customer or supplier data.

## not_covered
List what the plan deliberately leaves out and why (for example load tests owned elsewhere, data migration checks),
so the reviewer sees the gaps.

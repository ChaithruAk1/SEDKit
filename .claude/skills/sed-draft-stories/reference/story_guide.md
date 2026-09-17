# Story guide (sed-draft-stories)

## A good story
- **Independent and small:** one role, one goal, deliverable in a sprint. Split by workflow step, business rule, data
  variation or interface, never by technical layer (no "build the database table" stories).
- **Valuable:** `so_that` names the business benefit the page gives, not a restatement of `i_want`.
- **Covered:** together, the stories of a page cover every requirement on it. Anything the page states vaguely goes to
  `open_questions` instead of an invented rule.
- **Not a duplicate:** skip what context.md lists as already in Jira, unless the page clearly asks for more.

## Acceptance criteria
- Testable and observable: "Given <state>, when <action>, then <result>".
- Cover the main path, the validation rules and the error behaviour the page mentions.
- No implementation details (tables, endpoints) unless the page requires them.
- One to eight per story; more means the story should be split.

## Priority and estimate
- `highest`: blocks go-live or a legal or compliance obligation. `high`: core flow of the page. `medium`: needed but
  not core. `low` / `lowest`: convenience.
- `estimate_points` is relative (1 trivial, 3 a typical story, 8 large, 13 should be split). Use null when the page
  gives too little to judge.

## Confidence
The probability that the set covers the page correctly. Lower it for short or ambiguous pages, and say why in
`open_questions`.

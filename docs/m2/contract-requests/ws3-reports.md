# Contract requests: ws3-reports

## 1. XLSX conditional formats highlight empty cells (low priority, not blocking)

- **Status:** done after the I9 review: each rule is preceded by a `blanks` rule with `stop_if_true`.

- **File:** `src/sed/reports/xlsx_builder.py` (frozen), the `for cond in sheet_spec.conditional:` loop in `build_xlsx`.
- **Change:** skip empty cells before applying a spec rule, for example by adding a
  `{"type": "blanks", "stop_if_true": True}` conditional format on the same range just before each
  `{"type": "cell", ...}` rule (or by writing the rule as a formula with `NOT(ISBLANK(...))`).
- **Reason:** Excel evaluates an empty cell as 0 in a "cell value" rule, so `<` and `<=` rules colour cells whose
  snapshot value is `None`. Examples: `sla_pct < 90` on a family or application month with no resolved incidents
  (monthly `trend_6m_by_family`, `sla_6m_by_family`; vendor `incidents_by_app` for unattributed incidents) and
  `days_to_notice <= 30` on a contract without a notice deadline (weekly `renewals_90d`, quarterly `renewals_2q`,
  vendor `renewal_timeline`). The numbers are correct; only the highlighting is misleading.
- **Workaround in ws3 files:** none that keeps the plan's "SLA below target red" and "notice within 30 days red"
  formats, so the specs keep those rules. Nothing else in ws3-reports depends on this change.

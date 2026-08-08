# Income modeled as a recurring Expense, not a separate mechanism

**Status**: accepted

Income needs to roll forward monthly and be comparable against actuals the same way spending is, and it needs to appear in the same Rollover walk-forward logic without a special case.

We modeled income as a normal recurring Expense (e.g. "Salary") under a Category with `system_type = income`, rather than adding a dedicated Income table, an auto-generated-per-transaction mechanism, or a field on User/Budget. Income Transactions still always reference an Expense, same as every other transaction type.

## Consequences

Planned-vs-actual comparisons, rollover, and budget-total queries all work uniformly across income and spending — but any code path that wants to treat "income" specially must filter by `Category.system_type == income` rather than by table or transaction type alone.

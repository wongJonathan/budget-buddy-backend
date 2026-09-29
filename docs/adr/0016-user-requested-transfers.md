# Users can request Transfers, written and withdrawn as groups

**Status**: accepted (amends ADR-0011 and ADR-0014)

ADR-0011 made `TRANSFER` a server-only type, written by `close_savings` to drain a closed fund.
Users need it directly: the motivating case is emptying an emergency fund into the Pool to cover
the month, then allocating that money with ordinary Saves and Spends. We decided that **a User
may request a Transfer, the server writes its rows as one group, and deleting an Expense
withdraws its own side of each Transfer out of it and the whole of each Transfer into it**.

## Shape

`POST /transactions` with `type: transfer`, `expense_id` naming the source and an optional
`to_expense_id` naming the destination (null means the Pool).

- **Fund to Pool** is the ADR-0011 pair: a `TRANSFER` row on the source Expense (the *anchor*),
  and a `TRANSFER` row with no Expense for the Pool side.
- **Fund to fund** is the same pair plus a `SAVE` into the destination: three rows. The Pool-side
  row is required, not decoration. Without it the Save debits a Pool that was never credited.
  `SPEND_SAVED` + `SAVE` looks equivalent and destroys money (ADR-0011). Because the destination
  gets an ordinary Save, its Met moves by the ordinary rule: at that instant the money really is
  in the Pool being allocated.

Every row, anchor included, carries `transfer_id` = the anchor's id. Those rows together are one
*Transfer group*: membership is `transfer_id IS NOT NULL`, and the anchor is the row pointing at
itself. `close_savings`'s drain pairs have the same shape.

The anchor points at itself so that the one column marks every member. A null on the anchor
would look the same as an ordinary row's, leaving membership to be inferred from the type. The
cost is a flush and then an update of the anchor, because its id comes from the database. A
separate group-id column would avoid that, but it would be a second column for a fact
`transfer_id` can already hold.

The source must have a fund holding at least the amount (read with the Savings row locked, since
the balance is derived and "never negative" cannot be a CHECK). The amount must be positive,
because a negative Transfer would be the Pool-to-fund movement ADR-0011 made unrepresentable.
Source and destination must differ, must be in the current Period, and must not be deleted. The
destination's fund opens lazily, as for any Save.

## A group moves as one

A group is edited and deleted as a unit. That is what "editing a Transfer cannot lose money"
requires. With independent rows, halving the pair leaves a full-size Save, or deleting one row
leaves money on one side of a move and not the other.

- `name`, `note`, `date` and `amount` apply to every row. An amount is re-checked against the
  source's balance plus the group's current amount, since that amount is being handed back.
- The source can change, and is re-checked as on create. The destination can change, which
  re-points the Save and so its fund and Met (ADR-0015). Changing it to the Pool removes the
  Save, and from the Pool adds one.
- `type` cannot change on a group row, and no row can be changed into a `TRANSFER`. This is the
  same hole ADR-0011 closed for `SPEND_SAVED`. The Pool side's `expense_id` cannot be set.
- Deleting any row deletes the whole group with one `deleted_at`.
- A group that an Expense deletion has taken part of is read-only (409) until that Expense is
  Restored. Otherwise an edit to the rows still live would leave Restore bringing back a row that
  no longer matches them. The drain pair a deletion writes sits on the deleted Expense, and is
  read-only for the same reason.

## What deleting an Expense withdraws from its Transfers

ADR-0014 withdraws every Transaction on the deleted Expense. That still holds, Transfer rows
included, and it now also withdraws:

- the **Pool side of each Transfer out of the Expense**, which has no `expense_id` to be found
  by. The destination's Save on another Expense is not withdrawn: deleting the source leaves the
  destination exactly as it was;
- **the whole of each Transfer into the Expense**, source side included. Deleting the destination
  undoes the move, and the source gets its money back. Only Transfers whose Save is still live
  count. A Save removed when its Transfer was re-pointed elsewhere still carries the
  `transfer_id`, and must not reach a Transfer that now belongs to someone else.

Restore brings back the same set, matched as before by `deleted_at`. It is refused (409) when
that set includes a row on another Expense deleted since: deleting B takes A's side of A → B,
then deleting A drains A without it, and restoring B alone would take the same money out of A's
closed fund a second time. Restore A first.

The rule is "everything that moved money in or out of this fund this Period leaves together".
That is what makes it impossible for the withdrawal to leave the fund negative, so deleting an
Expense is never refused on account of a Transfer. The cases:

- **Transfer out, funded by an earlier Period.** A holds £100 and sends £60 to B, then A is
  deleted. The Transfer out of A and its Pool side are withdrawn, and the drain returns all £100.
  B keeps its £60 Save. The Pool gains £40, the same totals as if the Transfer had stood and the
  drain had returned only A's remaining £40.
- **Transfer out, funded by this Period's Save.** £100 of Income is Saved into A and sent on to
  B, then A is deleted. A's Save and the Transfer out both go. A is £0, B keeps £100, and the Pool
  is £0: as if the Income had gone straight to B, which is what the User ended up doing.
- **Transfer in.** A sends £60 to B, then B is deleted. All three rows are withdrawn, A is back to
  £100, and the Pool is unchanged.
- **Transfer in, then out to the Pool.** A → B, B → Pool, then B is deleted. Both Transfers are
  undone: A has its £100 back and the Pool loses the £100 B had sent it.
- **A chain.** A → B → C, then B is deleted. A gets its £100 back, and C keeps its £100 because
  deleting a source leaves the destination alone. The Pool is −£100. This is the same
  over-allocation ADR-0014 already accepts for Restore: an accurate picture, not an error.
- **Source deleted first.** A → B, A deleted, then B deleted. A's side went with A and A was
  drained, so B's deletion takes only B's Save and the money lands in the Pool. There is no live
  A to refund.

Deleting an Expense still checks that no fund went below zero, but only as a safety net. The
withdrawal leaves just earlier Periods' money, which is negative only if an earlier Period's rows
were edited afterwards.

## Considered Options

- **Pool side as `INCOME`, or a new type.** Rejected for the reasons in ADR-0011 and ADR-0014:
  Income asserts money arrived from outside.
- **Two-row fund-to-fund that bypasses the Pool.** It needs a directional Transfer and a new
  balance formula, and it funds the destination without moving its Met.
- **Skip Transfer groups when deleting an Expense.** This was the first version of this ADR.
  It kept a User's Transfer showing as standing ("it no longer belongs to the Expense"). But when
  this Period's Save paid for the Transfer, withdrawing the Save alone leaves the fund negative,
  so the delete had to be refused until the User deleted the Transfer first. The totals are the
  same in every case the skip allowed. The only difference is whether the ledger shows the
  Transfer as withdrawn.
- **Skip groups, and withdraw Transfers only when the fund would otherwise go negative.** The
  same action would do different things depending on the numbers, and Restore would have to know
  which Transfers were taken.
- **Replace the withdrawn Save with a smaller server-written one covering the Transfer.** This
  writes a Save the User never made.
- **When the source is deleted, withdraw the whole group, including the destination's Save.** It
  empties a fund the User deliberately filled, and it still has to refuse when the destination
  has spent that money.
- **When the destination is deleted, withdraw only its Save and send the money to the Pool.**
  An earlier version of this ADR did this, on the grounds that A had given the money away on
  purpose. It was reversed because deleting the destination reads as "this move was a mistake",
  and undoing the move is what a User expects.
- **Withdraw the Transfer out of the Expense but not its Pool side.** This was the original gap.
  The Pool side keeps counting the money the drain then returns a second time.

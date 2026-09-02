# Accounts are provisioned by a script, not self-registered

**Status**: accepted

There is no signup route. `POST /users` has been removed, and `UserCreate` with it. The only way to create a `User` is `app.scripts.create_user`, an operator-run CLI that calls `provision_user` in `app/services/provisioning.py`.

Budget Buddy is a personal budgeting backend for a known, small set of people, deployed on a single VPS behind a Cloudflare Tunnel. A public registration endpoint on that deployment means anyone who discovers the tunnel hostname can create accounts inside a personal-finance database, which in turn brings along everything registration implies: rate limiting, bot defence, email verification, and an abuse story. None of that buys anything when the set of legitimate account holders is "the people who own this server".

Provisioning also settles what a new account looks like. `provision_user` creates the `User` and a blank `Budget` in one transaction and points `User.active_budget_id` at it, so an account never exists in the state of having no Budget at all — the client can assume there is always an active Budget to render.

The write ordering is forced by the schema: `budgets.user_id` and `users.active_budget_id` reference each other, so the User is inserted with a null pointer, flushed to obtain its id, the Budget is inserted against it, and only then is the pointer filled in. Keeping that in a dedicated `provisioning.py` — rather than splitting it across `services/user.py` and `services/budget.py` — means the ordering lives in one file, which is where you want it when provisioning misbehaves.

## Considered Options

- **Public `POST /auth/register`** (rejected): the conventional shape, and the one a frontend expects. Rejected because open registration on an internet-reachable personal finance app is a liability with no upside here, and because it drags in rate limiting and verification work that serves no real user.
- **Registration gated by a shared signup secret** (rejected): keeps the self-service UX and closes the door, but it is still a public endpoint with a credential to distribute, rotate, and leak. Worth revisiting only if accounts ever need to be created by someone without shell access.
- **Operator-run provisioning script** (chosen): no public surface at all, and account creation is a deliberate act by someone who already has server access.

## Consequences

Creating an account requires shell access to the deployment, which is fine while the account holders are the operators and becomes the reason to revisit this if that ever stops being true.

The provisioned password is passed as a command-line argument, so it is exposed in shell history and briefly in `ps`. That was accepted on the basis that the credential is temporary and rotated by its owner on first sign-in — **but nothing enforces that rotation today**. There is no `must_change_password` flag and `POST /auth/change-password` is a stub. Until that flow exists, a provisioned password remains valid indefinitely, and this ADR's premise is weaker than it reads. Closing that gap is the natural follow-up.

`audit.record(USER_CREATED)` is called *after* the transaction commits, not before. The audit module deliberately writes on its own session and commits independently, so recording first and then failing the commit would leave a `USER_CREATED` event describing a user that does not exist. This is the opposite of the ordering used on the login failure paths, where the separate session exists precisely so the event outlives the request's rollback.

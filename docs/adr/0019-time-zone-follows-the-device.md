# A User's Time Zone follows the device

**Status**: accepted (amends the intended fix in `docs/user-time-zones.md`)

`User.time_zone` is the IANA zone the User's device last reported, not a home zone the User sets. The client sends it as an `X-Time-Zone` header on `POST /auth/login` and `GET /auth/me`, and the server overwrites the stored value each time. It is an observation, so `PATCH /users` cannot set it: a manual edit would be overwritten by the next `/me`. A missing header keeps the stored zone, and so does one `zoneinfo` can't resolve, since a client bug shouldn't block sign-in. The column is `NOT NULL DEFAULT 'UTC'`, which is what every "today" and Period already uses.

For now it is only stored. `current_period()` and `today()` still read UTC.

## Consequences

- When the zone is wired into `current_period()`, the Period boundary moves with the device. A User who flies from Sydney to New York on 1 October finds September open again until New York reaches October, and can write to a Period they had treated as closed (ADR-0017). Per-User Rollover would also read whichever zone was stored when it ran. The wiring change has to decide whether to accept that or guard against it, for example by never letting the open Period move backwards.

## Considered Options

- **Home zone, captured once and then editable only by the User.** This keeps the Period boundary stable while the User travels. It was rejected in favour of always matching what the device shows the User.
- **Storing both a home zone and the last zone the device reported.** Rejected as more state than anything needs today.

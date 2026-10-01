# Security and deployment notes

## Vercel and Supabase boundary

Production requests require a server-side `SUPABASE_DB_URL`; database credentials are never sent to browser code. Keep `SUPABASE_SERVICE_ROLE_KEY` server-side only. Supabase Row-Level Security is enabled on backend tables, and the browser is not intended to query those tables directly. Use the transaction-pooler connection mode recommended for serverless deployments and store credentials only as Vercel environment variables.

The app creates durable officer sessions in Supabase, storing only a hash of the session cookie and a server-side CSRF token. This avoids relying on one Vercel function instance's memory. Officer application details and review actions require an authenticated officer with the appropriate jurisdiction/permission. Disabling or rotating an officer account revokes its stored sessions.

## Applicant-facing data

An applicant can look up a single application using its application number and entered email. The public response is intentionally minimized to status, jurisdiction office, instrument type/serial, document-review states, event history, risk summary, notifications, and saved preference flags. It omits applicant name, email, phone, address, organization, internal IDs, officer identity, filenames, and private officer notes. Full application details and document filenames remain officer-only.

**Preview limitation:** this lookup matches an entered email; it does not prove ownership of that email via OTP or a magic link. Application numbers are not a strong authentication factor. Use only synthetic/test data in public friend testing. Before production use, add an unpredictable tracking secret or email verification and rate limiting.

Applicant notification preferences and read receipts are stored in the shared database. The app does not currently send email or SMS; provider integration is required for outbound delivery. Document uploads must match both the application number and entered email and use the private Supabase Storage bucket when storage credentials are configured.

## Preview deployment

- Use a Preview-only, unique officer password. A password was previously posted in chat; treat it as exposed and rotate it before friend testing.
- The included demo officer is an `ADMIN` account. Do not use that demo credential in production.
- The local SQLite database and real applicant records are not migrated to Supabase. The app's sample seed records are synthetic.
- If the GitHub repository was made public while a database or credential was committed, switch it back to private and rotate exposed credentials. `.gitignore` does not remove files already tracked or erase them from Git history.

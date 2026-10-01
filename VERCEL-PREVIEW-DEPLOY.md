# VeriMetrix Preview: friend-testing checklist

Use the **new** Vercel project imported from the private `Naren1411/veri-metrix` GitHub repository. Do not use or change the old Vercel project named `veri-metrix`. Friend testing should use a Preview deployment, not a public production release.

## Before sharing

- In the new Vercel project, set the environment variables from `VERCEL-UPDATE-STEPS.md` for **Preview** and the branch being tested. Keep database URLs/passwords and service-role keys server-side.
- Connect Preview to the new Supabase project `verimetrix-preview` (project ref `mzhofgakaezwhzysrdkd`). The VeriMetrix tables and private `verimetrix-documents` storage bucket are already created.
- Use a newly generated, unique Preview officer password. A previous demo password was shared in chat and must be treated as exposed. The demo officer is an `ADMIN`; never use that account in production.
- The first valid API request initializes backend tables and synthetic demo rows. It does not import records from local SQLite.
- Use synthetic applicant information and documents only. Public tracking matches application number plus entered email but does not prove email ownership with OTP or a magic link. Public responses are intentionally limited; full application details require officer authentication.

## Verify the deployed workflow

1. In the **new** Vercel project's Deployments page, wait for the intended Preview-branch deployment to show **Ready**. Copy that deployment's URL.
2. Request `https://<new-preview-host>/api/public-stats`. Expected: HTTP 200 JSON, not an empty `{}` response or an authentication redirect.
3. Submit one synthetic application using an email the tester controls; save its application number and email.
4. Open applicant tracking and confirm the submission and limited status view appear.
5. Sign in to the Preview officer portal as `officer@verimetrix.local` with the new Preview-only password supplied privately by the owner. Confirm the application appears in the authenticated officer queue.
6. Start document review and confirm that the applicant tracker reflects the status/event change.
7. Try to approve documents before required files are verified; the backend should reject premature approval. Verify the files, approve the document stage, and confirm the applicant tracker reflects the status, timeline, notification, and document-state changes.
8. Optional: test inspection scheduling/completion and certificate issuance with synthetic data only.

Officer sessions, applicant preferences, read receipts, application statuses, and workflow events are stored in the shared Supabase database. Email/SMS preferences persist, but outbound email/SMS sending needs a provider integration.

Share only the Preview URL and, if necessary, the demo officer login through a private channel. Never share database credentials or API keys.

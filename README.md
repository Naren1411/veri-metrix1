# VeriMetrix

This is the existing VeriMetrix application, with the Vercel API routing and applicant/officer synchronization fixes applied. It keeps the project's original structure: vanilla-JS frontend, Python WSGI API, local SQLite development, and Supabase PostgreSQL persistence in deployment.

## Package safety

This clean package excludes `.git` history, `.env.local`, Vercel project metadata, local SQLite databases, uploads, Python bytecode, and temporary patch files. `.env.example` contains placeholders only. Keep all real credentials in environment variables, never in source control.

## Local checks

From the project root:

```bash
python -m unittest discover -s tests -v
python -m py_compile server.py api/index.py
```

The regression suite covers API routing, officer login/session handling, applicant submission, officer review, and shared status/preference synchronization. Use synthetic test data only.

## New deployment

- Push this package to the new **private** GitHub repository `Naren1411/veri-metrix`.
- Create a **new** Vercel project from that repository; leave the old Vercel project `veri-metrix` untouched. The suggested new Vercel name is `default-naren`.
- Connect the new Vercel project's Preview environment to the already-created Supabase project `verimetrix-preview` (ref `mzhofgakaezwhzysrdkd`). The app schema and private `verimetrix-documents` bucket are already set up there.
- Configure Preview secrets using [`VERCEL-UPDATE-STEPS.md`](VERCEL-UPDATE-STEPS.md), then follow [`VERCEL-PREVIEW-DEPLOY.md`](VERCEL-PREVIEW-DEPLOY.md) for live checks.

If the Vercel account denies project creation, an account owner with project-creation permission must create it in the dashboard. Do not redirect this code to the old Vercel project.

## Security

Never commit `.env.local`, database URLs/passwords, Supabase service-role keys, Vercel credentials, or real applicant data. `SUPABASE_DB_URL` and `SUPABASE_SERVICE_ROLE_KEY` are server-side secrets only. Read [`SECURITY.md`](SECURITY.md) before inviting a friend to test.

# First deployment: new VeriMetrix GitHub, Vercel, and Supabase

This is a clean package of the existing application source. It does not include Git history, local database files, `.env.local`, or Vercel metadata. The setup below creates a **new** GitHub repository and a **new** Vercel project; it does not touch the old Vercel project `veri-metrix`.

## 1. Push the complete project to new GitHub

1. In GitHub, open `Naren1411/veri-metrix` and make it **private** before uploading.
2. Extract this package and open its root folder in VS Code. Confirm it contains `server.py`, `index.html`, `api/index.py`, `vercel.json`, `supabase/`, and `tests/`.
3. In the VS Code terminal, run the checks:

   ```bash
   python -m unittest discover -s tests -v
   python -m py_compile server.py api/index.py
   ```

   Expected: 12 tests pass and Python compilation exits without an error.

4. Initialize a fresh Git repository and push all application files to `main`:

   ```bash
   git init
   git branch -M main
   git status --short
   git add .gitignore .env.example README.md SECURITY.md VERCEL-PREVIEW-DEPLOY.md VERCEL-UPDATE-STEPS.md api/index.py html5-qrcode.min.js index.html manifest.webmanifest national.js officer-admin.js officer-auth-ui.js offline-status.js ops-plus.js qrcode.min.js requirements.txt server.py supabase/schema.sql supabase/storage.sql tests/test_server.py tests/test_vercel_api.py theme.css vercel.json
   git --no-pager diff --cached --check
   git --no-pager diff --cached --name-only
   git commit -m "Initial VeriMetrix project"
   git remote add origin https://github.com/Naren1411/veri-metrix.git
   git push -u origin main
   ```

5. Verify the full file list is on GitHub and the repository remains private. Do not upload `.env.local`, database files, uploads, passwords, or API keys.

6. Create and push the separate friend-testing branch:

   ```bash
   git switch -c fix/vercel-api-route-preview
   git push -u origin fix/vercel-api-route-preview
   ```

## 2. Create a separate Vercel project

1. In the Vercel account, choose **Add New → Project** and import the private repository `Naren1411/veri-metrix`.
2. Choose a new project name (suggested: `default-naren`) and set Root Directory to the repository root. Leave framework/build detection on auto-detect or `Other`; do not override the project's `vercel.json` routing.
3. Set `main` as the Production branch for this new Vercel project. Leave the old `veri-metrix` project and its Production configuration unchanged.
4. If Vercel denies project creation, an account owner with project-creation permission must create this new project. Do not connect the code to the old project as a workaround.

## 3. Configure the new Supabase project in Vercel

The already-created database is the Supabase project `verimetrix-preview`, ref `mzhofgakaezwhzysrdkd`, region Mumbai (`ap-south-1`). Its application schema is applied, RLS is enabled on the app tables, and the private bucket `verimetrix-documents` exists. Do not rerun the schema unless intentionally resetting the database.

In the **new** Vercel project → **Settings → Environment Variables**, add the following for **Preview** and scope to `fix/vercel-api-route-preview` if branch scoping is available:

| Variable | Value/source |
|---|---|
| `SUPABASE_DB_URL` | New project's server-side PostgreSQL connection string from Supabase; use the transaction pooler for serverless where available. Secret. |
| `SUPABASE_URL` | `https://mzhofgakaezwhzysrdkd.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | New project's server-side service-role/secret key. Secret; never expose in browser code. |
| `SUPABASE_DOCUMENT_BUCKET` | `verimetrix-documents` |
| `VERIMETRIX_SECURE_COOKIES` | `1` |
| `VERIMETRIX_DEMO_OFFICER_EMAIL` | `officer@verimetrix.local` |
| `VERIMETRIX_DEMO_OFFICER_PASSWORD` | A fresh, unique Preview-only password. Secret. Do not reuse the password previously shared in chat. |

Never put secret values in GitHub, source files, screenshots, or chat. Do not configure Production secrets until the owner intentionally prepares a production release. After changing environment variables, redeploy the Preview branch so the function receives them.

## 4. Wait for Preview and test

In the new Vercel project's **Deployments** page, find `fix/vercel-api-route-preview` and wait until its deployment status is **Ready**. Use that deployment URL for friend testing. Follow [`VERCEL-PREVIEW-DEPLOY.md`](VERCEL-PREVIEW-DEPLOY.md) to validate the API, applicant submission, officer login, document review, and shared status updates.

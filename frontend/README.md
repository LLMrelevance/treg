# Dashboard

This app lives in the public treg repository and is served by the existing Python Web service.
There is no separate production frontend server.

- `src/App.vue`: application shell and conditional page/dialog mounts.
- `src/pages/`: catalog, getting started, activity, tools, team, referrals and help.
- `src/components/` and `src/dialogs/`: shared navigation and overlays.
- `src/state/`: existing Options API use cases, grouped by feature, plus initial state and boot.
- `src/api.ts`: same-origin JSON transport, session expiry and edge-encoding behavior.
- `src/styles/`: base styling. Redesign styles and artwork are shared from `src/treg/web/media/redesign/`.

This is an incremental extraction. The old use cases still share per-application state through
`state/context.ts`; their JavaScript and the shared onboarding widgets are not fully typed.
New isolated components should use typed props and events. Existing hash navigation and deep links
remain in the navigation/catalog/details modules; this change does not replace their URL contract.
There is no duplicated legacy Dashboard HTML implementation.

## Develop

Install Node 22.12+ and npm, then run `scripts/dev-local.sh up` from the repository root.
Open `http://localhost:18790/app`; the Python response loads Vite modules from :5173 for hot updates.
The local-only `TREG_FRONTEND_DEV` switch cannot be used with PostgreSQL or a public hostname.

## Validate and package

From the repository root:

```sh
bash scripts/build-dashboard.sh
npm --prefix frontend test
cd frontend && npx playwright install chromium && cd ..
npm --prefix frontend run test:e2e
uv build
```

Browser tests start their own server on :18791 with a disposable database and no dotenv file.
`PLAYWRIGHT_CHANNEL=chrome` can use an installed Chrome for local checks.

Builds generate `src/treg/web/dashboard/`, which is ignored by Git and included in wheels/sdists.
Do not edit generated files. Distributable package builds fail if these assets are absent; editable
Python installs and background workers do not require Node. The Web build script is
`scripts/build-web.sh`, which compiles the app and retains the locked Python installation.

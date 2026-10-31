# Tera2Nano ERP – frontend

React 18 + Vite + react-router. All company data is fetched from and saved to the Django API
(`/api/v1/...`); nothing is kept only in browser state. The auth token is held in `sessionStorage`
and sent as an `Authorization: Token …` header (never in a URL).

## Run

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to http://localhost:8000
npm run build      # production build into dist/
```

Start the backend first (`python manage.py runserver` in `backend/`) and sign in with an ERP user.
A company administrator is sent to the setup wizard (`/setup`) automatically when no company exists.

## Layout

- `src/api.js` – fetch wrapper, DRF error parsing, authenticated blob/download helpers
- `src/forms.js`, `src/specs.js` – field specs, client-side validation, payload building
- `src/ui.jsx` – toasts, dialogs, confirm / reason prompts (`useReasonSubmit` resubmits with `change_reason` when the server asks), spec-driven forms
- `src/components/` – `CrudSection` (list + add/edit/deactivate), `ProfileEditor`, `LogoUploader`
- `src/pages/` – the 14 Company Management pages; `src/Wizard.jsx` – setup wizard (reuses the page components)

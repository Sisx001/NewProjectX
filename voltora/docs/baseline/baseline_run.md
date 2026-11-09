# VOLTORA legacy runtime baseline (2026-11-09)
Copy: /tmp/legacy-baseline (from /tmp/handoff/src, which was not modified). Ubuntu 24.04, Python 3.12.3, Node 24.22.2.

## 1. Backend deps
`cd backend && uv venv .venv --python 3.12 && uv pip install -p .venv/bin/python -r requirements-deploy.txt pytest pytest-xdist pytest-asyncio` -> OK (~1s, cached). requirements-deploy.txt has no non-PyPI URLs (nothing skipped there). backend/requirements.txt (needs the emergent cloudfront extra-index) was NOT used.
Extra needed: `filelock` (imported by tests/test_mfa_lock.py, absent from requirements) - installed.

## 2. MongoDB
Downloaded https://fastdl.mongodb.org/linux/mongodb-linux-x86_64-ubuntu2204-7.0.14.tgz (85MB) -> /tmp/legacy-baseline/mongo; `mongod --dbpath mdata --bind_ip 127.0.0.1 --port 27017 --fork` OK. Real mongod used; mongomock not needed.

## 3. API boot
Env (/tmp/legacy-baseline/testenv.sh): MONGO_URL=mongodb://127.0.0.1:27017, DB_NAME=voltora_legacy_test, REBUILD_COLLECTION_PREFIX=v1_, APP_ORIGIN=http://127.0.0.1:8001, APP_SECRET, OWNER_SETUP_KEY (both >=32 chars, validated by runtime_config), RUNTIME_MODE=sandbox, EXTERNAL_ACTIONS_ENABLED=false, REACT_APP_BACKEND_URL/BACKEND_URL=http://127.0.0.1:8001.
My first 31-char key gave at import of core.py: `ConfigurationError: ... OWNER_SETUP_KEY must be a fresh private secret of at least 32 characters` (87 passed / 273 errors on that run - my error).
`uvicorn server:app --host 127.0.0.1 --port 8001` -> "Application startup complete" in <10s, no startup errors. `GET /api/health` -> {"status":"ok","database":"connected"}; `/api/store` returns seeded VOLTORA settings (BDT, BD, Asia/Dhaka). DB auto-seeds on boot.
Docs mention backend/.env.example; only frontend/.env.example exists in the archive.

## 4. Backend pytest (`python -m pytest tests -q -p no:cacheprovider`; pytest.ini forces -n 2 --dist loadscope; 147 files in tests/)
Needs a live API at REACT_APP_BACKEND_URL (test_voltora_e2e, test_origin_aware_auth) and an OWNER_SETUP_KEY matching the server's.
- Run A (DB unseeded; API started with different random keys): 1 failed, 972 passed, 10 skipped, 5 errors, 3.8s.
  * FAILED tests/test_auth_public_user.py::test_public_user_staff_includes_workspace_path - core.py:67 `settings()` -> `TypeError: 'NoneType' object is not subscriptable` (test assumes seeded `settings` doc; state-dependent; passed once DB was seeded by API boot).
  * 5 ERRORs in tests/test_voltora_e2e.py session fixture: `POST /api/auth/setup` -> 403 {"detail":"Invalid owner setup key"} (my key mismatch).
- Run B (API restarted on same env/DB): **973 passed, 10 skipped, 5 errors**, 3.0s. The 5 e2e errors persisted (same 403 invalid setup key) even though server and shell keys were identical. Hypothesis, NOT confirmed: another test module mutates OWNER_SETUP_KEY/env in the shared pytest process. Not investigated.
- Run C (e2e alone, `pytest tests/test_voltora_e2e.py -n 0`, fresh DB): **3 passed, 2 failed**, 2.7s:
  * test_auth_security_and_route_guards: `assert 'Secure' in 'voltora_session=...; HttpOnly; Max-Age=3599; Path=/; SameSite=lax'` (e2e.py:269). Probably caused by my http:// origin (needs HTTPS), not a pure code verdict.
  * test_external_actions_lock_and_basic_commerce: checkout -> 400 {"detail":"This delivery option does not cover the selected address"} (e2e.py:453). Reproduced on every run (on a second run with an existing owner, auth test passed and 4/5 passed). Looks like a real legacy defect or seed/delivery-zone mismatch in checkout.
- The 10 skips were not itemised.
Caveat: mostly unit-style tests; passing says nothing about live provider integrations (disabled).

## 5. Frontend (/tmp/legacy-baseline/frontend)
- `npm install --no-audit --no-fund` FAILED: ERESOLVE (react-day-picker@8.10.1 peer date-fns ^2||^3). Removed in the COPY the two non-registry deps `@emergentbase/overlay` and `@emergentbase/visual-edits` (assets.emergent.sh tarballs; never fetched), then `--legacy-peer-deps` -> OK, 1492 packages, 32s. Archive has no yarn.lock/package-lock, so versions are unpinned.
- `npx tsc --noEmit -p .`: 50 errors, **0 in src/**; all in node_modules/@types/node (TS1005/1109/1128..., @types/node too new for pinned TypeScript 4.9.5). Command exits non-zero in this env but app source shows no type errors.
- `craco build` FAILED immediately: "craco: Config file not found" - **craco.config.js is missing from the archive**, yet src uses the `@/` alias (tsconfig has only baseUrl: src). I wrote a baseline-only craco.config.js in the copy (alias @ -> src).
- Next failure: MODULE_NOT_FOUND in ajv-keywords (ajv hoisting) -> `npm i --save-dev ajv@8` in the copy.
- Next failure: "Failed to compile" from ESLint plugin: 6x `Definition for rule 'react-hooks/exhaustive-deps' was not found` (eslint 9 vs react-scripts 5 mismatch). With `CI=false DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL=http://127.0.0.1:8001 npx craco build`: **Compiled successfully**, 14s, main.js 376.65 kB. prebuild/postbuild (scripts/inject_site_graph.py) were not run.
- Unit tests `CI=true npx craco test --watchAll=false`: **43 suites, 283 tests, all passed**, 3.4s (with the stand-in craco config).

## 6. Screenshots (/tmp/handoff/notes/legacy-shots/)
Python SPA static server on build (port 3000) + API on 8001 with ADDITIONAL_TRUSTED_ORIGINS=http://127.0.0.1:3000; Chrome headless 1440px.
- home.png: storefront renders with live API data (hero, nav, categories, "DEMO STORE" announcement, cookie banner, chat bubble).
- admin.png: `/admin` shows the storefront 404 page. The admin is not at /admin; default workspace path is `/w-k4q9v2mx` (schemas.py:477) and requires owner setup + MFA. Admin UI was NOT exercised; this screenshot is of the 404 only.

## 7. Cleanup
mongod (clean shutdown), uvicorn and static server stopped; verified no processes remain. Logs in /tmp/legacy-baseline (pytest.log, pytest2.log, tsc.log, build.log, build2.log, jest.log, uvicorn.log).

## Not done / unverified
Admin UI and owner login/MFA in a browser; payments/couriers/AI (disabled); a fully green e2e run; itemising the 10 skips; root cause of the 5 e2e setup-key errors in the full run; scripts/*.py and deploy/ files.

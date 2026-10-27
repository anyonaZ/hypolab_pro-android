# ACE Studio (primary application)

> If you are using this suite for the first time, start with ACE Studio.

A local-first research workspace for WorldQuant BRAIN consultants: data discovery, hypothesis formation, FastExpr generation, templates, BRAIN simulation, result review and portfolio correlation.

- Run: `chmod +x run.sh && ./run.sh` → open http://localhost:5173
- Setup: [SETUP.md](SETUP.md) · Run details: [RUN.md](RUN.md)
- Guides: `../docs/ace-studio/` (DATA_EXPLORATION, ALPHA_WORKFLOW, SIMULATIONS, RESULTS)
- Original developer README: [docs/DEVELOPER_README.md](docs/DEVELOPER_README.md)
- Database: `data/ace_studio_v2.db` (shipped; catalog of 137 datasets / 27,114 fields)

Layout: `backend/` (FastAPI), `frontend/` (React+Vite), `data/`, `docs/`, `support-worker/` (optional Cloudflare worker source), `build/`, plus Windows/packaging helpers (`run.bat`, `package.py`, `build_pyinstaller.py`, `*.ps1`, `*.bat`) kept for completeness.

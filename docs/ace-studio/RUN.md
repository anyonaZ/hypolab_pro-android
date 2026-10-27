# Run ACE Studio

```bash
cd 01-ace-studio
chmod +x run.sh      # if needed
./run.sh
```
Backend `http://127.0.0.1:8766` (uvicorn `--reload`), frontend `http://localhost:5173` (`npm run dev`). Stop with `Ctrl-C`.

## First use
1. Start ACE Studio, open http://localhost:5173
2. Sign in to BRAIN (login dialog, then complete biometrics and "Finish login"); the workspace is locked until a session exists
3. Settings: optionally add an LLM key/provider and run the test
4. Set the research context (region, universe, delay) and open **Data Explorer**
5. Select dataset(s), inspect fields (MATRIX / VECTOR / GROUP)
6. Write a hypothesis in **Alpha Lab** (or **Strategy Lab** for directions)
7. Generate or write a candidate FastExpr; the app validates it
8. **Simulation**: choose settings and run; 9. **Results & Analytics**: review gates, PnL, test-period retention, checks
See `../docs/ace-studio/` for details.

Backup: `cp data/ace_studio_v2.db data/ace_studio_v2.backup.db` (app stopped).

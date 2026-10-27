# ACE Studio setup (Arch Linux)

```bash
sudo pacman -S --needed python nodejs npm curl unzip
cd 01-ace-studio
python -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
deactivate
cd frontend && npm install
```
(`run.sh` performs all of this automatically on first start.) Credentials: see `../SECURITY.md`.

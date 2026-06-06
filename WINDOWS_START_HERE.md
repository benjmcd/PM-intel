# Windows Start Here

Primary environment: Windows local directory.

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts\verify.py
```

If PowerShell script execution is restricted:

```cmd
py -3.11 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts\verify.py
```

## Local-only boundary

This project is not preparing for a hosted product. Keep work local-first: local Postgres, local config, local files/reports, local CLI, localhost-only HTTP receiver tests, and opt-in read-only live venue access when needed.

## Database

Start Docker Desktop, then:

```powershell
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
```

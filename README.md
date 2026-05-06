# KsccAgent_New

`KsccAgent_New` is a PyQt6 desktop client for Kscc/OpenAI-style coding workflows.

## Features

- Solo / IDE dual-mode workflow
- Monaco editor integration
- File tree and workspace switching
- Session sidebar with grouped conversations
- Light / dark theme customization
- Per-model `Context Limit` and `Max Output` settings
- Chat attachments, pasted screenshots, and image preview

## Project Structure

- `app.py`: main desktop application
- `agent.py`: agent runtime and model interaction
- `config.py`: configuration and model defaults
- `context.py`: token / context window utilities
- `session_store.py`: session persistence
- `theme/`: theme, icon, markdown, and Monaco styling resources
- `monaco.html`: Monaco editor host page

## Requirements

- Python 3.11+
- Node.js + npm
- Windows recommended

## Install

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Install Monaco frontend dependency:

```bash
npm install
```

## Run

```bash
python app.py
```

Or on Windows:

```bat
run.bat
```

## Runtime Data

These files/directories are local-only and are not committed:

- `config.json`
- `sessions/`
- `backup/`
- `node_modules/`
- `__pycache__/`

## Notes

- Monaco editor assets are installed through `package.json`.
- Kscc built-in models and external models support separate default token limits.
- Local runtime configuration is created automatically after first launch.

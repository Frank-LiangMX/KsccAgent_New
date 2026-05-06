# KsccAgent_New

PyQt6 desktop client for Kscc/OpenAI coding workflows, with:

- Solo / IDE modes
- Monaco editor integration
- Session sidebar and workspace browser
- Theme customization
- Model-specific context and max-output settings
- Attachment support in chat input

## Requirements

- Python 3.11+
- Node.js / npm

## Install

```bash
pip install -r requirements.txt
npm install
```

## Run

```bash
python app.py
```

or on Windows:

```bat
run.bat
```

## Notes

- `config.json`, `sessions/`, and other local runtime data are excluded from version control.
- Monaco editor frontend assets are installed through `package.json`.

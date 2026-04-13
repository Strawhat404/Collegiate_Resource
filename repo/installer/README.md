# Windows Installer (.msi)

Files in this directory:

| File | Purpose |
| --- | --- |
| `CRHGC.wxs` | WiX 3.x source for the MSI |
| `build_msi.ps1` | PowerShell build script (PyInstaller + WiX) |
| `update_pubkey.pem.example` | Documentation stub describing how to generate the production signing key. **Not a valid PEM.** The repo deliberately ships no real key. |
| `update_pubkey.pem` | (Operator-supplied) Real RSA-3072 SPKI public key for offline update-package signature verification. Place this file here before running `build_msi.ps1`; the build refuses to bundle a placeholder. If absent, the MSI is built without a bundled key and the deployed app rejects every update until a real key is dropped into `%LOCALAPPDATA%\CRHGC\update_pubkey.pem`. |
| `app.ico` | Application icon (provide your own; not committed) |

## Build

On a Windows 11 host:

```powershell
cd repo
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller
powershell -ExecutionPolicy Bypass -File installer\build_msi.ps1
```

Output: `installer\CRHGC.msi`. Per-machine install into
`C:\Program Files\CRHGC`. Per-user data lives in `%LOCALAPPDATA%\CRHGC`
and is preserved across upgrades.

## Update package format

```
update.zip
├── update.json        # {"version": "1.2.0", "files": [...], "notes": "..."}
├── update.json.sig    # RSA-PSS signature over update.json (raw bytes)
└── payload/...        # files copied into the install dir
```

Sign with the matching private key:

```bash
openssl dgst -sha256 -sign priv.pem -sigopt rsa_padding_mode:pss \
    -sigopt rsa_pss_saltlen:-1 -out update.json.sig update.json
zip -j update.zip update.json update.json.sig
zip -r update.zip payload/
```

The `Updates` tab in the running app accepts the `.zip` (or `.crpkg`)
file via *Import update package…*. After applying, the previous database
state is preserved as a snapshot under `%LOCALAPPDATA%\CRHGC\snapshots\`,
and *Rollback selected* restores it.

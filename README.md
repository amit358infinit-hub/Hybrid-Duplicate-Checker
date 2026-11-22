# Hybrid Duplicate Checker

An offline Windows desktop app for finding exact duplicates and reviewing similar documents. Powered by the upstream Czkawka CLI for BLAKE3 matches.

## Run from source on Windows x64

1. Install Python 3.12 and add it to PATH.
2. Run `python -m pip install -r sources/desktop_app/requirements.txt`.
3. Download [Czkawka CLI 12.0.2 for Windows](https://github.com/qarmin/czkawka/releases/download/12.0.2/windows_czkawka_cli.exe) into `tools/czkawka-12.0.2/`. Verify SHA-256 `eb7c2009d2dd49cf5202acbd65370caece0a3f819b9515e727cedb1ec088ed1a` with `Get-FileHash`.
4. Double-click `run.bat` or run `python sources/desktop_app/main.py`.

For a first scan, choose one test folder and use Safe Match. Name, size, page/title, and visual matches are candidates for review. Test quarantine and restore on disposable copies before using file actions on important documents.

## License

Hybrid Duplicate Checker is distributed under GNU AGPL v3. See [LICENSE](LICENSE). The separately downloaded Czkawka CLI retains its [upstream license](tools/czkawka-12.0.2/LICENSE_MIT). Python, Qt for Python, PyMuPDF and the other dependencies retain their respective licenses.

"""Verify application imports before launching the desktop UI.

The check imports feature modules without starting workers, sending messages,
opening browsers, or processing stories. It also works in the Windows bundle.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

FEATURE_MODULES = (
    'modules.browser_installer',
    'modules.threading_manager',
    'modules.sheet_parser',
    'modules.drive_downloader',
    'modules.docx_parser',
    'modules.ai_groq_parser',
    'modules.progress',
    'modules.readora_client',
    'modules.review_service',
    'modules.google_sheet_updater',
    'modules.google_auth',
    'modules.chrome_launcher',
    'modules.updater',
    'modules.telegram_service',
    'modules.workers',
    'modules.gui_drive_file_picker',
    'modules.gui_review_dialog',
    'gui',
)


def check_startup():
    ready, failed = [], {}
    for name in FEATURE_MODULES:
        try:
            importlib.import_module(name)
            ready.append(name)
        except Exception as exc:
            # Exception messages can contain configuration values. Report types only.
            failed[name] = type(exc).__name__
    return {'ready': ready, 'failed': failed, 'platform': sys.platform}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-startup', action='store_true')
    parser.add_argument('--startup-report', type=Path)
    args = parser.parse_args(argv)
    report = check_startup()
    if args.startup_report:
        args.startup_report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    if args.check_startup:
        if sys.stdout is not None:
            print(json.dumps(report, indent=2))
        return 1 if report['failed'] else 0
    if report['failed']:
        from tkinter import messagebox
        names = '\n'.join(f'{name}: {kind}' for name, kind in report['failed'].items())
        messagebox.showerror('Mohra startup failed',
                             'Some application modules could not load. Reinstall Mohra.\n\n' + names)
        return 1
    importlib.import_module('gui').main()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

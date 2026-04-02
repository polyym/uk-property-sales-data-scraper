#!/usr/bin/env python3
"""Pre-flight check: validates that the environment is ready to scrape."""

import importlib
import os
import sys


def check(label: str, passed: bool, fix: str = '') -> bool:
    status = 'OK' if passed else 'FAIL'
    print(f'  [{status:>4}] {label}')
    if not passed and fix:
        print(f'         Fix: {fix}')
    return passed


def main() -> None:
    print()
    print('  Property Scraper, Environment Check')
    print('  ' + '=' * 40)
    print()

    all_ok = True

    # Python version.
    v = sys.version_info
    all_ok &= check(
        f'Python {v.major}.{v.minor}.{v.micro}',
        v >= (3, 13),
        'Install Python 3.13+ from https://www.python.org/downloads/',
    )

    # Required packages.
    packages = {
        'polars': 'pip install polars',
        'curl_cffi': 'pip install curl_cffi',
        'bs4': 'pip install beautifulsoup4',
        'playwright': 'pip install playwright',
        'playwright_stealth': 'pip install playwright-stealth',
    }
    for pkg, install_cmd in packages.items():
        try:
            importlib.import_module(pkg)
            all_ok &= check(f'{pkg} installed', True)
        except ImportError:
            all_ok &= check(f'{pkg} installed', False, install_cmd)

    # Config file.
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.json')
    all_ok &= check(
        'config.json exists',
        os.path.exists(config_path),
        'Copy config.example.json to config.json and fill in your settings',
    )

    # Proxies (informational only).
    proxy_path = os.path.join(os.path.dirname(__file__), '..', 'proxies.txt')
    if os.path.exists(proxy_path):
        with open(proxy_path, encoding='utf-8') as f:
            count = sum(1 for line in f if line.strip())
        print(f'  [INFO] proxies.txt found ({count} proxies)')
    else:
        print(f'  [INFO] No proxies.txt; will use browser-only mode')

    # Memory (stdlib only, no psutil dependency).
    try:
        if sys.platform == 'win32':
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ('dwLength', ctypes.c_ulong),
                    ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            avail_gb = stat.ullAvailPhys / (1024 ** 3)
        else:
            avail_gb = (
                os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_AVPHYS_PAGES')
                / (1024 ** 3)
            )
        all_ok &= check(
            f'Available RAM ({avail_gb:.1f} GB)',
            avail_gb >= 2.0,
            'Close other applications. 4 GB+ free recommended.',
        )
    except Exception:
        print('  [INFO] Could not check available memory')

    print()
    if all_ok:
        print('  All checks passed. Ready to scrape.')
    else:
        print('  Some checks failed. Fix the issues above and retry.')
    print()


if __name__ == '__main__':
    main()

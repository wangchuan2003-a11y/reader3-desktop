"""Write reproducible app metadata with plistlib, preserving spaces and non-ASCII paths."""
from pathlib import Path
import plistlib
import sys


def metadata(project: Path, bundled=True) -> dict:
    info = {
        'CFBundleIdentifier': 'local.reader3.desktop',
        'CFBundleName': 'Reader3',
        'CFBundleDisplayName': 'Reader3 阅读器',
        'CFBundleExecutable': 'Reader3',
        'CFBundlePackageType': 'APPL',
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleShortVersionString': '0.1.0',
        'CFBundleVersion': '1',
        'CFBundleIconFile': 'Reader3',
        'LSMinimumSystemVersion': '13.0',
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'Based on reader3 by Andrej Karpathy. Local reading edition.',
        'ReaderBundledRuntime': bundled,
        'NSAppTransportSecurity': {
            'NSAllowsLocalNetworking': True,
            'NSExceptionDomains': {'127.0.0.1': {'NSExceptionAllowsInsecureHTTPLoads': True}},
        },
    }
    if not bundled:
        info['ReaderProjectPath'] = str(project.resolve())
    return info


if __name__ == '__main__':
    project, app = map(Path, sys.argv[1:3])
    (app / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(metadata(project, '--development' not in sys.argv)))

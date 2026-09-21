#!/bin/bash
# Build a local macOS app. No network, package install, or publication is performed.
set -euo pipefail
reader_project="$(cd "$(dirname "$0")/.." && pwd -P)"
reader_mode="${1:---bundled}"
if [[ "$reader_mode" != "--bundled" && "$reader_mode" != "--development" ]]; then
  printf 'Usage: desktop/build.sh [--bundled | --development]\n' >&2
  exit 2
fi
mkdir -p "$reader_project/dist"
reader_stage="$(mktemp -d "$reader_project/dist/.reader3-build.XXXXXX")"
trap 'rm -rf "$reader_stage"' EXIT
reader_app="$reader_stage/Reader3.app"
mkdir -p "$reader_app/Contents/MacOS" "$reader_app/Contents/Resources"

/usr/bin/xcrun swiftc -O -swift-version 5 -target arm64-apple-macosx13.0 \
  "$reader_project/desktop/ReaderPolicy.swift" "$reader_project/desktop/main.swift" \
  -o "$reader_app/Contents/MacOS/Reader3"
"$reader_app/Contents/MacOS/Reader3" --self-test

"$reader_project/.venv/bin/python" "$reader_project/desktop/write_info.py" "$reader_project" "$reader_app" "$reader_mode"
if [[ "$reader_mode" == "--bundled" ]]; then
  "$reader_project/.venv/bin/python" "$reader_project/desktop/package_runtime.py" "$reader_project" "$reader_app"
fi
/usr/bin/xcrun swift "$reader_project/desktop/make_icon.swift" "$reader_stage/Reader3.iconset"
/usr/bin/iconutil -c icns "$reader_stage/Reader3.iconset" -o "$reader_app/Contents/Resources/Reader3.icns"
/usr/bin/plutil -lint "$reader_app/Contents/Info.plist"
/usr/bin/codesign --force --sign - --timestamp=none "$reader_app"
/usr/bin/codesign --verify --deep --strict "$reader_app"

# Replacement is confined to this generated app. Books and source code never enter the bundle.
if [ -e "$reader_project/dist/Reader3.app" ]; then
  mv "$reader_project/dist/Reader3.app" "$reader_stage/previous.app"
fi
mv "$reader_app" "$reader_project/dist/Reader3.app"
printf 'Built: %s\n' "$reader_project/dist/Reader3.app"

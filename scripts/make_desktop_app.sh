#!/usr/bin/env bash
# 构建当前 React 写作工作台的 macOS .app 包。
# 默认输出到 dist/DraftLoop.app；可用 DRAFTLOOP_APP_OUTPUT 指定目录。
# 复现：bash scripts/make_desktop_app.sh
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
ICON="$PROJ/app/resources/app.icns"
APP_NAME="DraftLoop"
OUTPUT_DIR="${DRAFTLOOP_APP_OUTPUT:-$PROJ/dist}"
APP="$OUTPUT_DIR/$APP_NAME.app"
PYTHON="$PROJ/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "缺少 .venv，请先运行 ./run.sh 或安装 requirements-lock.txt。" >&2
  exit 1
fi

echo "==> 构建前端资源"
npm run build --prefix "$PROJ/frontend"

if [[ ! -f "$ICON" ]]; then
  echo "==> 生成应用图标"
  "$PYTHON" "$PROJ/scripts/make_icon.py"
fi

echo "==> 构建 $APP"
mkdir -p "$OUTPUT_DIR"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$ICON" "$APP/Contents/Resources/app.icns"

cat > "$APP/Contents/MacOS/launcher" <<EOF
#!/bin/bash
cd "$PROJ"
exec /usr/bin/arch -arm64 "$PYTHON" -m app.desktop_main
EOF
chmod +x "$APP/Contents/MacOS/launcher"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>DraftLoop</string>
  <key>CFBundleDisplayName</key><string>DraftLoop · IELTS Writing Coach</string>
  <key>CFBundleIdentifier</key><string>com.draftloop.ielts-writing</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>app.icns</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

touch "$APP"
echo "✓ 已生成应用包：$APP"
echo "  双击即可启动；首次打开若提示未验证开发者，右键 → 打开。"

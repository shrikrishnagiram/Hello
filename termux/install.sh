#!/data/data/com.termux/files/usr/bin/bash
# One-time setup of the Bank Nifty Strangle Scanner inside Termux (Android).
# Safe to re-run: it updates the code and rewrites the commands/shortcuts.
# Everything is inside main() so bash reads the whole script before running it
# (the script updates its own file via git).
main() {
set -e
REPO_URL="https://github.com/shrikrishnagiram/Hello.git"
BRANCH="${BRANCH:-ccr-e3df3d29-cy21k0}"
APP_DIR="$HOME/Hello"
BIN="${PREFIX:-/data/data/com.termux/files/usr}/bin"

echo "==> Installing Python, git and helpers (first time: 5-10 minutes)..."
# Never stop to ask about config files: keep the existing ones.
export DEBIAN_FRONTEND=noninteractive
APT_OPTS="-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"
pkg update -y || true
apt-get install -y $APT_OPTS python git procps termux-tools termux-api
echo "==> Installing the requests library..."
pip install --disable-pip-version-check requests

echo "==> Getting the scanner code..."
OLD_SUM=$(cksum "$APP_DIR/termux/install.sh" 2>/dev/null || true)
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" reset --quiet --hard "origin/$BRANCH"   # data/ is gitignored, so logs are kept
else
  git clone --quiet -b "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
mkdir -p "$APP_DIR/data"
# If this update brought a newer installer, run the new one instead of this old copy.
NEW_SUM=$(cksum "$APP_DIR/termux/install.sh" 2>/dev/null || true)
if [ -n "$OLD_SUM" ] && [ "$OLD_SUM" != "$NEW_SUM" ] && [ -z "$INSTALLER_REEXEC" ]; then
  echo "==> Installer was updated; continuing with the new version..."
  INSTALLER_REEXEC=1 exec bash "$APP_DIR/termux/install.sh"
fi

echo "==> Creating commands and shortcuts..."
# Real commands on PATH -- work immediately, no need to reopen Termux.
make_cmd() { printf '#!/data/data/com.termux/files/usr/bin/bash\n%s\n' "$2" > "$BIN/$1"; chmod +x "$BIN/$1"; }
make_cmd scanner        'exec bash "$HOME/Hello/termux/start.sh" "$@"'
make_cmd scanner-demo   'SCANNER_DEMO=1 exec bash "$HOME/Hello/termux/start.sh" "$@"'
make_cmd scanner-stop   'exec bash "$HOME/Hello/termux/stop.sh"'
make_cmd scanner-update 'exec bash "$HOME/Hello/termux/install.sh"'
# Market data collector (Nifty 200 prices, volume, F&O positions, history)
make_cmd market-setup    'exec bash "$HOME/Hello/termux/market-setup.sh"'
make_cmd market-start    'exec bash "$HOME/Hello/termux/market-start.sh"'
make_cmd market-stop     'exec bash "$HOME/Hello/termux/market-stop.sh"'
make_cmd market-status   'exec bash "$HOME/Hello/termux/market-status.sh"'
make_cmd market-selftest 'cd "$HOME/Hello" && exec python -m market selftest'
make_cmd market-backfill 'cd "$HOME/Hello" && exec python -m market backfill "$@"'
make_cmd market-snapshot 'cd "$HOME/Hello" && exec python -m market snapshot'
# Swing stocks app
make_cmd stocks          'exec bash "$HOME/Hello/termux/stocks-start.sh"'
make_cmd stocks-demo     'pkill -f "python stocks_app.py"; STOCKS_DEMO=1 exec bash "$HOME/Hello/termux/stocks-start.sh"'
make_cmd stocks-stop     'pkill -f "python stocks_app.py" && echo "Swing stopped." || echo "Swing was not running."'
# Remove aliases written by older versions of this installer.
[ -f "$HOME/.bashrc" ] && sed -i '/# strangle-scanner$/d' "$HOME/.bashrc"

# Termux:Widget home-screen shortcuts (optional app)
mkdir -p "$HOME/.shortcuts"
printf '#!/data/data/com.termux/files/usr/bin/bash\nexec bash "$HOME/Hello/termux/start.sh"\n' > "$HOME/.shortcuts/Strangle Scanner"
printf '#!/data/data/com.termux/files/usr/bin/bash\nexec bash "$HOME/Hello/termux/stop.sh"\n'  > "$HOME/.shortcuts/Stop Scanner"
chmod +x "$HOME/.shortcuts/Strangle Scanner" "$HOME/.shortcuts/Stop Scanner"

echo
echo "Done. Now type:   scanner"
}
main "$@"

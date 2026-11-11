#!/data/data/com.termux/files/usr/bin/bash
# One-time setup of the Bank Nifty Strangle Scanner inside Termux (Android).
# Safe to re-run: it updates the code and rewrites the shortcuts.
set -e
REPO_URL="https://github.com/shrikrishnagiram/Hello.git"
BRANCH="${BRANCH:-ccr-e3df3d29-cy21k0}"
APP_DIR="$HOME/Hello"

echo "==> Installing Python, git and helpers (this can take 5-10 minutes)..."
# Never stop to ask about config files: keep the existing ones.
export DEBIAN_FRONTEND=noninteractive
APT_OPTS="-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"
pkg update -y || true
apt-get install -y $APT_OPTS python git procps termux-tools
echo "==> Installing the requests library..."
pip install --disable-pip-version-check requests

echo "==> Getting the scanner code..."
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" reset --quiet --hard "origin/$BRANCH"   # data/ is gitignored, so logs are kept
else
  git clone --quiet -b "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
mkdir -p "$APP_DIR/data"

echo "==> Creating shortcuts..."
mkdir -p "$HOME/.shortcuts"
cp "$APP_DIR/termux/start.sh" "$HOME/.shortcuts/Strangle Scanner"
cp "$APP_DIR/termux/stop.sh"  "$HOME/.shortcuts/Stop Scanner"
chmod +x "$HOME/.shortcuts/Strangle Scanner" "$HOME/.shortcuts/Stop Scanner"

# Commands you can type in Termux
BASHRC="$HOME/.bashrc"
touch "$BASHRC"
sed -i '/# strangle-scanner$/d' "$BASHRC"
cat >> "$BASHRC" <<'RC'
alias scanner='bash "$HOME/.shortcuts/Strangle Scanner"' # strangle-scanner
alias scanner-stop='bash "$HOME/.shortcuts/Stop Scanner"' # strangle-scanner
alias scanner-demo='SCANNER_DEMO=1 bash "$HOME/.shortcuts/Strangle Scanner"' # strangle-scanner
alias scanner-update='bash "$HOME/Hello/termux/install.sh"' # strangle-scanner
RC

echo
echo "Done. Close and reopen Termux, then type:   scanner"
echo "(Or add the Termux:Widget to your home screen and tap 'Strangle Scanner'.)"

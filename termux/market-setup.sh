#!/data/data/com.termux/files/usr/bin/bash
# Connect this phone to your PRIVATE GitHub data repository (one time).
# The GitHub key is typed here, on the phone only. It is never shown or sent anywhere else.
set -e
DATA_DIR="${MARKET_DATA_DIR:-$HOME/market-data}"
echo "Connect your phone to your private data repository on GitHub."
echo
read -r -p "Your GitHub username: " GHUSER
read -r -p "Private repository name [market-data]: " GHREPO
GHREPO="${GHREPO:-market-data}"
echo
echo "Now paste your GitHub key (it starts with github_pat_)."
echo "Nothing will show on screen while you paste. That's normal. Then press Enter."
read -r -s GHTOKEN
echo
GHTOKEN="$(printf '%s' "$GHTOKEN" | tr -d '[:space:]')"   # drop stray spaces/newlines from pasting
if [ -z "$GHTOKEN" ]; then echo "No key received (the paste didn't go through). Run market-setup again."; exit 1; fi
case "$GHTOKEN" in
  github_pat_*) echo "Key received: starts with github_pat_, ${#GHTOKEN} characters long. (Not shown for safety.)" ;;
  ghp_*)        echo "Key received: a 'classic' key (ghp_...), ${#GHTOKEN} characters long." ;;
  *)            echo "That doesn't look like a GitHub key (it should start with github_pat_)."
                echo "It is ${#GHTOKEN} characters long. Copy the key again and run market-setup again."; exit 1 ;;
esac

# Save the key for git only, readable only by Termux.
git config --global credential.helper store
touch "$HOME/.git-credentials"; chmod 600 "$HOME/.git-credentials"
grep -v "@github.com" "$HOME/.git-credentials" > "$HOME/.git-credentials.tmp" || true
printf 'https://%s:%s@github.com\n' "$GHUSER" "$GHTOKEN" >> "$HOME/.git-credentials.tmp"
mv "$HOME/.git-credentials.tmp" "$HOME/.git-credentials"; chmod 600 "$HOME/.git-credentials"
unset GHTOKEN

URL="https://github.com/$GHUSER/$GHREPO.git"
echo "Checking access to $GHUSER/$GHREPO ..."
if ERR="$(GIT_TERMINAL_PROMPT=0 git ls-remote "$URL" 2>&1 >/dev/null)"; then ACCESS=ok; else ACCESS=fail; fi
if [ "$ACCESS" = fail ]; then
  ERR="$(echo "$ERR" | sed -E 's#https://[^@/ ]+@#https://#g')"   # never print credentials
  echo
  echo "GitHub said: $ERR"
  echo
  if echo "$ERR" | grep -qi "not found"; then
    echo "This usually means the KEY CAN'T SEE the repository. Fix it in the browser:"
    echo "  github.com/settings/personal-access-tokens -> tap your key -> Edit"
    echo "  - Repository access: 'Only select repositories' -> add market-data"
    echo "  - Permissions -> Contents: 'Read and write'"
    echo "  - Save (Update). The key itself stays the same; just run market-setup again."
  elif echo "$ERR" | grep -qiE "authentication|invalid|denied|403|401"; then
    echo "GitHub rejected the key itself. It may be expired, deleted, or copied incompletely."
    echo "Create a new key (see the steps) and run market-setup again."
  else
    echo "Check your internet connection and run market-setup again."
  fi
  exit 1
fi
echo "Access OK."

if [ -d "$DATA_DIR/.git" ]; then
  echo "Already connected; updating..."
  git -C "$DATA_DIR" pull -q --rebase || true
else
  git clone -q "$URL" "$DATA_DIR" 2>/dev/null || git clone "$URL" "$DATA_DIR"
fi
cd "$DATA_DIR"
git config user.name "phone-collector"
git config user.email "collector@localhost"
if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
  git symbolic-ref HEAD refs/heads/main          # brand-new empty repository
fi
mkdir -p meta
[ -f meta/watchlist.csv ] || printf 'symbol,note\nKALYANKJIL,starting example\n' > meta/watchlist.csv
[ -f meta/radar.csv ] || printf 'symbol,direction,reason,added,expires\n' > meta/radar.csv
[ -f README.md ] || printf '# Market data (private)\n\nCollected by the phone (Nifty 200 prices, volume, F&O positions, daily files).\nRead by Claude for reviews. Never contains broker passwords or keys.\n' > README.md
git add -A
git commit -q -m "Set up data repository" || true
BR=$(git rev-parse --abbrev-ref HEAD)
git push -q -u origin "$BR"
echo
echo "Connected! Data will be saved to $GHUSER/$GHREPO."
echo "Next: run   market-selftest"

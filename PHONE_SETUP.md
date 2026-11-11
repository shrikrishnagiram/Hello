# Run the scanner on your Android phone (no laptop needed)

The scanner runs **on your phone** inside Termux, a free app, and you use it in Chrome like
a normal app. Prices come straight from NSE over your phone's internet connection.
It never places orders.

## 1. Install two free apps (one time)
From **F-Droid** (https://f-droid.org). F-Droid's Termux is more up to date than the Play Store's:
- **Termux**
- **Termux:Widget** (optional: lets you start the scanner with one tap from the home screen)

## 2. Install the scanner (one time, about 3–5 minutes)
Open Termux, paste this line, and press Enter:

```
pkg install -y git && git clone -b ccr-e3df3d29-cy21k0 https://github.com/shrikrishnagiram/Hello.git ~/Hello && bash ~/Hello/termux/install.sh
```

When it says **Done**, the scanner is installed.

## 3. Start it
In Termux type:

```
scanner
```

Chrome opens the scanner. If Termux says `No command scanner found`, run
`git -C ~/Hello pull && bash ~/Hello/termux/install.sh` once and try again. To try it outside market hours with made-up prices, type `scanner-demo`
instead.

## 4. Make it an app icon (one time)
In Chrome, while the scanner page is open, tap **⋮ → Add to Home screen → Install**.
A "Strangle" icon appears on your home screen and opens full-screen.

Optional one-tap start: long-press the home screen → **Widgets** → **Termux:Widget**, and
place it. It shows **Strangle Scanner** (start) and **Stop Scanner**.

## Every day
1. Tap the **Strangle Scanner** widget, or open Termux and type `scanner`.
2. Use the **Strangle** app icon. If the scanner isn't running, the app tells you.
3. When you're done: `scanner-stop` (or the **Stop Scanner** widget) to save battery.

## Commands
| Type in Termux | What it does |
|---|---|
| `scanner` | start with live NSE data and open it |
| `scanner-demo` | start with made-up prices (labelled DEMO) |
| `scanner-stop` | stop it |
| `scanner-update` | download the latest version (your scan history is kept) |

## If something goes wrong
- **"Couldn't load data … NSE request failed"**: NSE is busy or blocking. Wait a minute and
  press Scan again, or switch between Wi-Fi and mobile data.
- **The app says the scanner isn't running**: Android may have stopped Termux. Run `scanner`
  again. To stop this from happening, set Termux's battery use to **Unrestricted** (Settings →
  Apps → Termux → Battery).
- **Nothing works after an update**: `scanner-stop`, then `scanner-update`, then `scanner`.
- **Server log**: `tail -n 30 ~/Hello/data/server.log`

Your scan history is saved at `~/Hello/data/scan_log.csv`.

# FitWaze Android app (free)

The FitWaze web app inside a real Android app. Same screens, plus what a
website can't do:

- **Walks keep tracking with the screen off.** Android shows a "Walk in
  progress" notification while FitWaze follows the route.
- **Turn-back and "back at your start" alerts arrive as notifications**,
  with sound, even when the phone is in a pocket.
- Its own icon on the home screen and in the app drawer.

Everything here is free. The app opens your FitWaze server, so the server
needs a permanent https address (see `docs/DEPLOY.md`, free option).

## One-time setup on Windows

1. Install **Node.js LTS**:  `winget install OpenJS.NodeJS.LTS`
2. Install **Android Studio** (free): https://developer.android.com/studio
   Open it once and let it download the Android SDK it offers.
3. Close and reopen PowerShell, then:
   ```
   cd C:\Users\<you>\Fitwaze\mobile
   npm install
   ```

## Build the app

1. Point the app at your server (use your own address):
   ```
   npm run set-server -- https://fitwaze.onrender.com
   ```
2. Open the Android project:
   ```
   npm run open
   ```
3. In Android Studio: wait for "Gradle sync" to finish, then
   **Build > Build App Bundle(s) / APK(s) > Build APK(s)**.
   When it finishes, click **locate**: the file is `app-debug.apk`.

## Put it on an Android phone (no Play Store)

- **With a cable:** on the phone, turn on Developer options and USB
  debugging (Settings > About phone > tap "Build number" 7 times), plug it
  in, and press the green **Run** button in Android Studio.
- **Or send the file:** email or share `app-debug.apk` to the phone, tap it,
  and allow "Install unknown apps" for that app when Android asks.

The first walk asks for location access: choose **While using the app**.
Allow notifications too, for the turn-back alerts.

## Changing the server address

Run `npm run set-server -- <new https address>` and build again. A
temporary cloudflared link works for a quick test, but it changes every
time cloudflared restarts, so the app would need rebuilding each time.

## iPhone (later, needs a Mac and a $99/year Apple account)

`npx cap add ios`, add the location keys from the background-geolocation
plugin's README to `Info.plist`, then build in Xcode. Until then, iPhone
users can add the web app to the home screen (Share > Add to Home Screen).

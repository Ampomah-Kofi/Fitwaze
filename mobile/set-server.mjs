// Point the app at your FitWaze server, then copy the change into the
// Android project:   npm run set-server -- https://fitwaze.onrender.com
import { readFileSync, writeFileSync } from "node:fs";
import { execSync } from "node:child_process";

const url = (process.argv[2] || "").trim().replace(/\/+$/, "");
if (!/^https:\/\/[^\s/]+/.test(url)) {
  console.error("Give the server's https address, e.g.  npm run set-server -- https://fitwaze.onrender.com");
  process.exit(1);
}
const config = JSON.parse(readFileSync("capacitor.config.json", "utf8"));
config.server.url = url;
writeFileSync("capacitor.config.json", JSON.stringify(config, null, 2) + "\n");
console.log("FitWaze app now opens " + url);
execSync("npx cap sync android", { stdio: "inherit" });

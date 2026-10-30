# Offline laptop build

The whole demo on one laptop, with no internet at the meeting. Images are prebuilt for Intel/AMD and Apple Silicon and
pulled from GitHub Container Registry (GHCR). Agents replay recorded Nemotron runs; no model is ever called and no
NVIDIA key is on the laptop. The top bar says "Offline demo · recorded agent runs" on every screen.

What changes offline:
- **Ask the agent**: the suggested questions are buttons that send at once; there is no free-text box.
- **Request changes**: the reason is picked from the recorded reasons, not typed.
- **Field notes and Research again** are hidden (their agent runs answer free text, which cannot be recorded).
- **Overview map**: basemap tiles around the plume are cached in the image; the observation-day scene toggle is off.

## On the laptop

**Once, with internet (about 30 minutes):**
1. Install Docker Desktop (Windows: the AMD64 build with WSL 2; Mac: Apple chip or Intel build). Open it once and
   wait for "Engine running".
2. Copy the demo folder to the laptop:
   - Windows: `atelicxdell.cmd`, `atelicxdell.ps1`, `ghcr-token.txt`
   - Mac: `atelicxdell.sh`, `ghcr-token.txt`
3. Run it once (Windows: double-click `atelicxdell.cmd`; Mac: `./atelicxdell.sh` in Terminal). The first run downloads
   about 3 GB, loads the data and opens the browser at http://localhost:8080 (sign in: `atelic` / `atelic-demo`).

**At the meeting (no internet needed):** open Docker Desktop, then run the launcher again. It notices it is offline,
starts the version already on the laptop and opens the browser. Use the rail's **Reset demo** between run-throughs.

| Command | What it does |
|---|---|
| `atelicxdell` | Update if online, start, load the data once per version, open the browser |
| `atelicxdell stop` | Stop the demo; data is kept |
| `atelicxdell reset` | Wipe this demo's data and start fresh |
| `atelicxdell status` / `logs` | Containers / follow the logs |

Data and settings live in `%LOCALAPPDATA%\Atelic\atelic-dell` (Windows) or `~/.atelic/atelic-dell` (Mac).
Only port 8080 on localhost is opened.

## Several demos on one laptop

Each demo has its own project name (`DEMO_NAME`), so its containers, networks and volumes (its own Neo4j, InfluxDB,
PostgreSQL, Qdrant) never mix with another demo's. Only the web port must differ. For another demo, copy the
launchers and change the settings at the top: `DEMO_NAME`, `HTTP_PORT` (8081, 8082, …) and the image names.
Running two demos at once needs about 6 GB of memory each; stop one before starting the next on smaller laptops.

## Building and updating (us)

1. **Record** every agent step the offline story can reach, once, against the live model:
   ```
   docker compose -p ddl-rec --env-file <env with NVIDIA_API_KEY, HTTP_PORT=8099> up -d --build
   docker compose -p ddl-rec --env-file <same> --profile seed run --rm seed
   python3 deploy/offline/record_story.py http://localhost:8099 <login-password>
   docker compose -p ddl-rec --env-file <same> cp engine:/srv/recordings/. engine/data/recorded_runs/
   ```
   It plays INC-001 and INC-002 through the UI API with the same scripted texts the offline buttons send (read from
   `frontend/public/js/story.js`). Recordings are keyed by those texts: change a text and record again.
2. **Commit**, then **build and push**: `deploy/offline/build_push.sh` (needs `gh auth token` with `write:packages`).
   It refuses uncommitted changes and missing recordings, caches the map tiles and pushes
   `ghcr.io/atelicai/dell-demo-{engine,backend,web}:offline` plus a tag per commit.
3. The laptop picks the update up the next time the launcher runs online, including a newer launcher and compose file
   (both ship inside the engine image).

**Registry token for the laptop:** a classic GitHub personal access token with only `read:packages`, from an account
that can read the AtelicAI packages. Save it as `ghcr-token.txt`. It can pull images and nothing else; revoke it after
the meeting.

**Map tiles:** `scripts/cache_basemap.py` caches Esri World Imagery tiles around the plume into the image (not into
git). Check Esri's terms allow this offline use for the meeting.

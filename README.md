# KCSB Cyberjaya — YOLO Annotation Dataset Dashboard

[**Open the dashboard**](https://kcsb-cyberjaya.github.io/) · [**GitHub repository**](https://github.com/KCSB-Cyberjaya/kcsb-cyberjaya.github.io)

**Windows + TrueNAS CORE (SMB) → static GitHub Pages dashboard.**

Tracks **finished YOLO Detection exports**, with automatic category statistics for `parts/` and `abnormal/`, image decoding, YOLO label validation, per-class box totals, and a searchable dashboard. It never needs a CVAT account or a connection to your CVAT server. The Windows computer reads the NAS; GitHub only hosts published statistics.

> **Public website warning:** GitHub Pages is public for normal personal/team use. The site publishes image/label *counts* and QC metrics. By default this starter replaces nested dataset paths and class names with anonymous aliases, and removes file-level error messages. It still reveals aggregate dataset information and the category names. Do not deploy it for confidential statistics. Never upload raw images, annotations or `.reports/` to your public repository.

## Open the Pages dashboard

This is the KCSB Cyberjaya organization-level Pages site:

**https://kcsb-cyberjaya.github.io/**

The dashboard includes:

- Five live summary cards: datasets, exported images, valid YOLO boxes, invalid label lines, corrupted images.
- Images-by-category and bounding-boxes-by-class charts.
- Search, category filter, QC-only filter and column-based sort.
- Clickable dataset details, QC totals and CSV export.
- A README summary updated at the same time as the site.

The dashboard starts with **Awaiting first scan**. Run the Windows publisher below to show real numbers.

## Architecture

```text
CVAT PCs (different local logins) -> finished exports on TrueNAS CORE
                                          |
                               SMB share (read only)
                                          |
                          Windows computer + Python scanner
                             |                    |
              private .reports/ files    public-safe docs/data.json + README.md
                                                  |
                                   git push to public GitHub repo
                                                  |
                             GitHub-hosted Actions: deploy-pages.yml
                                                  |
                               GitHub Pages interactive website
```

**Important:** GitHub-hosted runners cannot normally read a private LAN SMB share. To avoid exposing your NAS and avoid using a self-hosted runner on a public repository, scans run on your Windows computer; **GitHub Actions only deploys** the static Pages site.

## Expected TrueNAS folder layout

```text
\\TRUENAS\datasets\annotation_exports\
├── parts\any\nested\folders\data\
│   ├── images\
│   ├── labels\
│   ├── data.yaml
│   └── Train.txt
└── abnormal\any\nested\folders\data\
    ├── images\
    ├── labels\
    ├── data.yaml
    └── Train.txt
```

The scanner recursively finds directories containing sibling `images/` and `labels/`, regardless of the number of intervening folders. YOLO ZIP exports are also supported. `Train.txt` contains image references, **not** YOLO object labels. A missing/empty label `.txt` may be a valid background image.

## Step 1 — Publish this starter to your existing KCSB GitHub Pages repository

1. Your GitHub repository is already created: **https://github.com/KCSB-Cyberjaya/kcsb-cyberjaya.github.io**. It should be **public** for Pages on GitHub Free.
2. See **[KCSB_SETUP.md](KCSB_SETUP.md)** for copy-pasteable Windows Git commands for its initial upload.
3. On GitHub, open **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. The initial push will trigger **Actions → Deploy YOLO dashboard to GitHub Pages**. You can also run it manually with **Run workflow**.
5. Open **https://kcsb-cyberjaya.github.io/**. The site will initially show **Awaiting first scan** until you scan the NAS.

The workflow `.github/workflows/deploy-pages.yml` deploys the `docs/` folder whenever it changes on `main`, and on manual `workflow_dispatch`. It uses the standard Pages configure/upload/deploy actions with `pages: write` and `id-token: write` permissions.

## Step 2 — Set up Windows

On a Windows PC with read-only SMB access to TrueNAS:

1. Install **Python 3.10+** and **Git for Windows**.
2. Configure your Git credentials (Git Credential Manager or SSH) so `git push origin main` succeeds **without a prompt** when run by the intended Windows account.
3. **Clone `https://github.com/KCSB-Cyberjaya/kcsb-cyberjaya.github.io.git`** onto that PC and open PowerShell in its local working folder (keep the checked-out branch named `main`).
4. Install dependencies:

   ```powershell
   py -3 -m pip install -r requirements.txt
   ```

5. Verify SMB access using an actual folder UNC path:

   ```powershell
   Test-Path '\\TRUENAS\datasets\annotation_exports'
   ```

   It should return `True`. **Do not use mapped drive letters** in unattended Windows tasks; the drive mapping may not exist for the task's account.

6. Run a local-only test before publishing:

   ```powershell
   .\run-scan.ps1 -NasRoot '\\TRUENAS\datasets\annotation_exports'
   ```

   Examine local private reports in `.reports/`, and preview the generated website:

   ```powershell
   py -3 -m http.server 8000 --directory docs
   ```

   Open `http://localhost:8000` (stop the server with Ctrl+C). The site loads `docs/data.json` asynchronously and may not load correctly as a direct `file://` URL.

## Step 3 — Publish to GitHub Pages

After inspecting the report, run:

```powershell
.\publish-pages.ps1 -NasRoot '\\TRUENAS\datasets\annotation_exports'
```

This command:

1. Scans your nested `parts` and `abnormal` exports on Windows.
2. Writes full filenames, per-dataset errors and private name mappings to ignored `.reports/` files.
3. Writes **anonymized** `docs/data.json` and a README statistics section.
4. Commits **only** `docs/data.json` and `README.md`, and pushes them to `main` with your existing Git authentication.
5. The GitHub-hosted workflow automatically deploys the updated `docs/` website.

The script auto-detects the standard Pages URL from your GitHub `origin` remote and inserts it in the README. For a custom domain or unusual remote use `-SiteUrl`:

```powershell
.\publish-pages.ps1 -NasRoot '\\TRUENAS\datasets\annotation_exports' -SiteUrl 'https://dashboard.example.com/'
```

For a local test without pushing, add `-NoPush`. To skip expensive full image decoding, add `-SkipImageDecode`; the dashboard displays **Not scanned** rather than incorrectly showing zero corrupted images.

### Optional — show real dataset and class names on Pages

The default public site uses IDs such as `parts/dataset-001` and `Class 001`. The true relative paths and class names are stored **only on Windows** in `.reports/`.

If all those names and file-level QC warnings are definitely safe to publish, opt in:

```powershell
.\publish-pages.ps1 -NasRoot '\\TRUENAS\datasets\annotation_exports' -ExposeNames
```

**Once committed to a public Git repository, previously published names can remain in Git history even after you turn this off.** Review this option carefully.

## Step 4 — Schedule automatic scanning on Windows

Use **Windows Task Scheduler** on the Windows computer (not GitHub Actions):

1. **Create Task** → General: run using a Windows account that can read TrueNAS over SMB and can push Git commits. For a first setup, choose *Run only when user is logged on* and confirm it works; the Task Scheduler execution identity controls NAS and Git access.
2. **Triggers:** Daily, e.g. 9:00 AM (your Windows local time).
3. **Actions:** Start a program:

   **Program:** `powershell.exe`

   **Arguments:**

   ```text
   -NoProfile -File "C:\path\to\kcsb-cyberjaya.github.io\publish-pages.ps1" -NasRoot "\\TRUENAS\datasets\annotation_exports"
   ```

   **Start in:** `C:\path\to\kcsb-cyberjaya.github.io`

4. Test with **Run** in Task Scheduler and verify a new `data: refresh YOLO dashboard` commit and green **Deploy YOLO dashboard to GitHub Pages** action on GitHub.

If your PowerShell execution policy blocks `.ps1` files, use a signed script or adjust the execution policy under your organization's security rules; do not disable those protections indiscriminately.

**Transfer integrity:** For ongoing exports, it is safest to write an `_EXPORT_COMPLETE` marker **after** a dataset transfer finishes, then set `REQUIRE_COMPLETE_MARKER=1` in the Windows environment. Without it, the scanner may observe partially copied datasets. ZIP exports use a neighboring `<filename>.zip.complete` marker.

## Troubleshooting

| Problem | What to check |
|---|---|
| Blank or 404 Pages link | `Settings → Pages` source set to GitHub Actions; first deployment completed; correct URL |
| Dashboard says Awaiting first scan | Run `publish-pages.ps1` on Windows; check that `docs/data.json` was pushed |
| Windows says NAS_ROOT inaccessible | SMB share permissions and UNC path under the actual Windows account running the script |
| No YOLO datasets found | NAS_ROOT must point to a parent containing `parts/` and `abnormal/` |
| Scheduled scan works manually but not in Task Scheduler | Scheduled user lacks SMB/Git access, Python PATH, or saved credentials |
| Git push rejected | Run `git pull --ff-only origin main`, then retry; check authentication and branch rules |
| Class names and paths look anonymous | Expected public-safe behavior; see `-ExposeNames` and local `.reports/private-name-mapping.json` |
| Corrupted = Not scanned | Image full-decoding was disabled; this is deliberately **not** zero |
| Scan fails on one dataset | Check `.reports/failures.json` and the Windows scanner console; no new page is pushed |

## Test and customize

```powershell
py -3 -m pip install pytest
py -3 -m pytest -q
```

- `scripts/sync.py`: finds and validates nested YOLO Detection exports.
- `scripts/dashboard.py`: builds privacy-filtered static Pages JSON and README summaries.
- `run-scan.ps1`: scans and writes files locally.
- `publish-pages.ps1`: scans, commits only safe output files, and pushes.
- `docs/index.html`, `docs/styles.css`, `docs/app.js`: self-contained dashboard, no external JavaScript/CDN.
- `.github/workflows/deploy-pages.yml`: GitHub-hosted Pages publication.

The site contains **no image thumbnails or raw annotation text**. Metrics reflect exported datasets on TrueNAS, not CVAT jobs, annotator progress or unexported images.

---

<!-- AUTO_STATS_START -->
*No scan data published yet. Run `publish-pages.ps1` from Windows to populate this section.*
<!-- AUTO_STATS_END -->

# KCSB Cyberjaya — first deployment checklist

Repository: https://github.com/KCSB-Cyberjaya/kcsb-cyberjaya.github.io  
GitHub Pages: https://kcsb-cyberjaya.github.io/

These steps assume the repository is **empty** and you extracted this ZIP on a Windows PC. Use PowerShell in the extracted folder. Do **not** copy dataset images, `labels/*.txt`, `.reports/`, or NAS credentials into the public repository.

## A. Upload once to the empty repository

Install [Git for Windows](https://git-scm.com/downloads/win) if needed, then in PowerShell:

```powershell
# In the folder where you extracted the ZIP:
git init -b main
git remote add origin https://github.com/KCSB-Cyberjaya/kcsb-cyberjaya.github.io.git
git add .
git commit -m "Initial KCSB YOLO dashboard"
git push -u origin main
```

Git may ask you to sign in. Your account must have push access to the KCSB-Cyberjaya organization repository. If the repository already contains commits, **clone** it and copy the starter files into the clone instead of running `git init` in another folder.

## B. Activate GitHub Pages

1. Open https://github.com/KCSB-Cyberjaya/kcsb-cyberjaya.github.io/settings/pages (requires repository admin access).
2. Under **Build and deployment**, set **Source** to **GitHub Actions**.
3. Open **Actions**. Run or re-run **Deploy YOLO dashboard to GitHub Pages** if required.
4. Open https://kcsb-cyberjaya.github.io/. Until the NAS scan runs, the dashboard displays **Awaiting first scan**.

If Pages settings are missing or blocked, ask an organization owner to check whether Pages publishing is permitted for members.

## C. First Windows/TrueNAS scan

Install Python 3.10+ on the same Windows PC and give that Windows account read-only access to the TrueNAS CORE SMB export share.

```powershell
py -3 -m pip install -r requirements.txt
Test-Path '\\TRUENAS\datasets\annotation_exports'
.\run-scan.ps1 -NasRoot '\\TRUENAS\datasets\annotation_exports'
```

Replace the UNC path with your real SMB share. **`Test-Path` must return `True`**. This dry run writes public-safe `docs/data.json` and local private reports to `.reports/`, but does not push.

Review the local output and publish:

```powershell
.\publish-pages.ps1 -NasRoot '\\TRUENAS\datasets\annotation_exports'
```

The publisher commits **only** `README.md` and `docs/data.json`. The push causes GitHub Actions to redeploy the website. By default dataset paths, class names, and filenames are not made public; counts and category labels are still visible to everyone.

## D. Schedule later

Use Windows Task Scheduler to run `publish-pages.ps1` daily under the same Windows account with SMB and Git access. See the [README](README.md) for detailed Task Scheduler steps.

## Verify

- GitHub **Actions** shows a successful Pages deployment.
- The public website loads without 404 and shows charts, category filters, and dataset rows after the first scan.
- GitHub **Code** displays only dashboard source and statistics, not original images or labels.
- Full private QC logs stay locally in `.reports/`.

**Note:** Counts are calculated from datasets already exported to TrueNAS. This does not reflect still-unexported CVAT tasks, and public Pages must not be used for confidential metrics.

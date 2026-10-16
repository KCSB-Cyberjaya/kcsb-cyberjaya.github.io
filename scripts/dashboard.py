"""Write a public-safe GitHub Pages dashboard and a repository README summary.

The private, filename-level scanner reports stay in .reports/ on Windows.
For public sites, anonymize dataset paths, class names, file paths and exception details.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path

START = '<!-- AUTO_STATS_START -->'
END = '<!-- AUTO_STATS_END -->'


def esc_md(value: object) -> str:
    return (str(value).replace('|', '&#124;').replace('\n', ' ')
            .replace('<', '&lt;').replace('>', '&gt;').replace('`', "'"))


def truthy(value: str) -> bool:
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def public_view(rows: list[dict], failures: list[str], expose_names: bool) -> tuple[list[dict], list[str], dict]:
    """Return rows suitable for a publicly accessible GitHub Pages deployment."""
    if expose_names:
        # Explicit opt in: people visiting the public Pages URL can see these names.
        return [dict(row) for row in rows], list(failures), {}

    counters = defaultdict(int)
    class_names = sorted({str(name) for row in rows for name in row.get('class_counts', {})}, key=str.casefold)
    class_aliases = {name: f'Class {index:03d}' for index, name in enumerate(class_names, start=1)}
    safe_rows = []
    private_id_map = {}
    for row in sorted(rows, key=lambda item: (str(item['category']).casefold(), str(item['dataset_id']).casefold())):
        category = str(row['category'])
        counters[category] += 1
        alias = f'{category}/dataset-{counters[category]:03d}'
        private_id_map[alias] = row['dataset_id']
        safe = dict(row)
        safe['dataset_id'] = alias
        safe['class_counts'] = {class_aliases[str(name)]: count
                                for name, count in row.get('class_counts', {}).items()}
        safe['qc_samples'] = []  # QC strings may include image filenames, subdirectories, etc.
        safe['qc_redacted'] = True
        safe_rows.append(safe)
    failure_messages = ([f'{len(failures)} scan error(s); file-level details are retained on the Windows scanner only.']
                        if failures else [])
    return safe_rows, failure_messages, {'dataset_aliases': private_id_map,
                                         'class_aliases': class_aliases}


def write_dashboard(rows: list[dict], groups: dict, failures: list[str], report_dir: Path) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    expose_names = truthy(os.environ.get('DASHBOARD_EXPOSE_NAMES', 'false'))
    safe_rows, safe_failures, mapping = public_view(rows, failures, expose_names)
    # Never publish the mapping; it is useful to identify a failed export in TrueNAS.
    report_dir.mkdir(parents=True, exist_ok=True)
    if mapping:
        (report_dir / 'private-name-mapping.json').write_text(
            json.dumps(mapping, indent=2, ensure_ascii=False), encoding='utf-8')

    totals = {
        'datasets': len(safe_rows),
        'images': sum(int(x['images']) for x in safe_rows),
        'label_files': sum(int(x['label_files']) for x in safe_rows),
        'boxes': sum(int(x['boxes']) for x in safe_rows),
        'invalid_yolo_lines': sum(int(x['invalid_yolo_lines']) for x in safe_rows),
        'corrupted_images': (sum(int(x['corrupted_images']) for x in safe_rows)
                             if all(x['corrupted_images'] is not None for x in safe_rows) else None),
        'images_without_label_file': sum(int(x['images_without_label_file']) for x in safe_rows),
        'orphan_label_files': sum(int(x['orphan_label_files']) for x in safe_rows),
        'image_list_missing': sum(int(x['image_list_missing']) for x in safe_rows),
        'dataset_size_mb': round(sum(float(x['dataset_size_mb']) for x in safe_rows), 2),
    }
    class_counts = Counter()
    categories = {}
    for row in safe_rows:
        class_counts.update(row.get('class_counts', {}))
        group = categories.setdefault(row['category'], {
            'datasets': 0, 'images': 0, 'boxes': 0, 'invalid_yolo_lines': 0,
            'corrupted_images': 0 if totals['corrupted_images'] is not None else None,
            'class_counts': {},
        })
        group['datasets'] += 1
        group['images'] += row['images']
        group['boxes'] += row['boxes']
        group['invalid_yolo_lines'] += row['invalid_yolo_lines']
        if group['corrupted_images'] is not None:
            group['corrupted_images'] += row['corrupted_images']
        for name, count in row.get('class_counts', {}).items():
            group['class_counts'][name] = group['class_counts'].get(name, 0) + count

    payload = {
        'scanned_at': timestamp,
        'public_names_enabled': expose_names,
        'totals': totals,
        'categories': categories,
        'classes': dict(class_counts.most_common()),
        'datasets': safe_rows,
        'failures': safe_failures,
    }
    site_dir = Path('docs')
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / 'data.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    write_readme_summary(payload, Path('README.md'))


def write_readme_summary(payload: dict, readme: Path) -> None:
    totals = payload['totals']
    categories = payload['categories']
    classes = payload['classes']
    rows = payload['datasets']
    bad = 'Not scanned' if totals['corrupted_images'] is None else totals['corrupted_images']
    url = os.environ.get('DASHBOARD_URL', '').strip()
    link = f'[**Open interactive GitHub Pages dashboard ↗**]({url})' if url else (
        'Interactive dashboard: enable **Settings → Pages → GitHub Actions** and use the Pages URL ' 
        '(or preview locally with `py -3 -m http.server 8000 --directory docs`).'
    )
    summary = [
        START,
        '> Auto-generated from **exported TrueNAS datasets only**; not CVAT task completion. '
        'This summary contains neither source images nor YOLO `.txt` annotations.',
        '',
        f'**Last scan:** {payload["scanned_at"]}  ',
        f'**Datasets:** {totals["datasets"]:,} · **Images:** {totals["images"]:,} · '
        f'**YOLO boxes:** {totals["boxes"]:,} · **Invalid YOLO lines:** {totals["invalid_yolo_lines"]:,} · '
        f'**Corrupted images:** {bad}',
        '',
        link,
        '',
        '### By category',
        '| Category | Datasets | Images | Boxes | Invalid lines | Corrupted |',
        '|---|---:|---:|---:|---:|---:|',
    ]
    for name, group in sorted(categories.items()):
        corrupt = group['corrupted_images'] if group['corrupted_images'] is not None else 'Not scanned'
        summary.append(f'| {esc_md(name)} | {group["datasets"]:,} | {group["images"]:,} | '
                       f'{group["boxes"]:,} | {group["invalid_yolo_lines"]:,} | {corrupt} |')
    if not categories:
        summary.append('| (none yet) | 0 | 0 | 0 | 0 | — |')
    summary += ['', '### Top classes', '| Class | YOLO boxes |', '|---|---:|']
    for name, count in list(classes.items())[:12]:
        summary.append(f'| {esc_md(name)} | {count:,} |')
    if not classes:
        summary.append('| (none yet) | 0 |')
    summary += ['', '<details>', '<summary>All exported datasets (expand)</summary>', '',
                '| Category / dataset ID | Images | Labels | Boxes | Invalid | Corrupted |',
                '|---|---:|---:|---:|---:|---:|']
    for row in sorted(rows, key=lambda r: r['dataset_id'].casefold()):
        corrupted = row['corrupted_images'] if row['corrupted_images'] is not None else '—'
        summary.append(f'| {esc_md(row["dataset_id"])} | {row["images"]:,} | {row["label_files"]:,} | '
                       f'{row["boxes"]:,} | {row["invalid_yolo_lines"]:,} | {corrupted} |')
    summary += ['', '</details>', '',
                '**Privacy:** ' + ('Relative dataset IDs and YOLO class names are public (explicitly enabled).' 
                                  if payload.get('public_names_enabled') else
                                  'Dataset paths, class names, and file-level QC warnings are anonymized for public Pages.'),
                '',
                '**Notes:** Missing `.txt` files may be valid background images. '
                'Class counts should be combined only when class naming is consistent across datasets.', END]
    body = '\n'.join(summary)
    if readme.exists():
        original = readme.read_text(encoding='utf-8')
        if START in original and END in original:
            before, remainder = original.split(START, 1)
            _, after = remainder.split(END, 1)
            readme.write_text(before + body + after, encoding='utf-8')
        else:
            readme.write_text(original.rstrip() + '\n\n' + body + '\n', encoding='utf-8')
    else:
        readme.write_text('# TrueNAS YOLO GitHub Pages dashboard\n\n' + body + '\n', encoding='utf-8')

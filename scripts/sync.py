#!/usr/bin/env python3
"""Read completed YOLO Detection exports from an NAS share, then sync GitHub.

No CVAT API access, CVAT accounts or CVAT export calls are required.
Supported datasets: discover any nested images/ and labels/ sibling directories
within parts/ and abnormal/ (or configured categories), with optional nested ZIPs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import logging
import csv
import math
import os
from pathlib import Path, PurePosixPath
import re
from typing import Iterator
import zipfile

from PIL import Image, ImageFile
import requests
import yaml

LOG = logging.getLogger('nas-yolo-tracker')
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp'}
SKIP_FOLDERS = {'.git', '.trash', '@eaDir', '__MACOSX', '.snapshot', '.zfs'}
MAX_QC_SAMPLES = 20


def setting(name: str, default: str = '') -> str:
    return (os.environ.get(name) or default).strip()


def key_for_file(name: str) -> str:
    """Canonical split + base name for matching YOLO image and text files."""
    parts = list(PurePosixPath(name.replace('\\', '/')).parts)
    if parts and parts[0].lower() in {'dataset', 'export'}:
        parts.pop(0)
    normalized = []
    for part in parts[:-1]:
        lowered = part.lower()
        if lowered in {'images', 'image', 'labels', 'label'}:
            continue
        if lowered in {'obj_train_data', 'obj_valid_data', 'obj_test_data'}:
            normalized.append({'obj_train_data': 'train', 'obj_valid_data': 'val', 'obj_test_data': 'test'}[lowered])
        elif lowered in {'valid', 'validation'}:
            normalized.append('val')
        else:
            normalized.append(part)
    normalized.append(PurePosixPath(parts[-1]).stem)
    return '/'.join(normalized).casefold()


def is_image(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in IMAGE_EXTS


def is_label_candidate(name: str, image_keys: set[str]) -> bool:
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).suffix.lower() != '.txt':
        return False
    if PurePosixPath(name).name.lower() in {'obj.names', 'train.txt', 'test.txt', 'val.txt', 'valid.txt', 'classes.txt'}:
        return False
    return ('labels' in (p.lower() for p in parts[:-1]) or key_for_file(name) in image_keys
            or any(p.lower().startswith('obj_') and p.lower().endswith('_data') for p in parts[:-1]))


def parse_classes(file_names: list[str], read_bytes) -> dict[int, str]:
    for name in sorted(file_names, key=lambda x: len(PurePosixPath(x).parts)):
        if PurePosixPath(name).name.lower() == 'data.yaml':
            data = yaml.safe_load(read_bytes(name).decode('utf-8-sig')) or {}
            names = data.get('names', [])
            if isinstance(names, list):
                return {i: str(v) for i, v in enumerate(names)}
            if isinstance(names, dict):
                return {int(k): str(v) for k, v in names.items()}
    for name in sorted(file_names):
        if PurePosixPath(name).name.lower() == 'obj.names':
            return {i: v.strip() for i, v in enumerate(read_bytes(name).decode('utf-8-sig').splitlines()) if v.strip()}
    return {}


def parse_box_line(line: str, classes: dict[int, str]) -> tuple[int | None, str | None]:
    values = line.split()
    if len(values) != 5:
        return None, 'expected 5 values for YOLO Detection'
    if not re.fullmatch(r'\d+', values[0]):
        return None, 'class ID is not a nonnegative integer'
    class_id = int(values[0])
    if classes and class_id not in classes:
        return None, 'class ID is absent from data.yaml/obj.names'
    try:
        x, y, w, h = (float(v) for v in values[1:])
    except ValueError:
        return None, 'invalid coordinate'
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None, 'nonfinite coordinate'
    tol = 1e-5
    if not (0 - tol <= x <= 1 + tol and 0 - tol <= y <= 1 + tol):
        return None, 'center outside [0,1]'
    if w <= 0 or h <= 0 or w > 1 + tol or h > 1 + tol:
        return None, 'invalid bounding box dimensions'
    if x - w / 2 < -tol or x + w / 2 > 1 + tol or y - h / 2 < -tol or y + h / 2 > 1 + tol:
        return None, 'bounding box extends outside image'
    return class_id, None


class Export:
    def __init__(self, path: Path):
        self.path = path
        self.archive: zipfile.ZipFile | None = None
        self.files: dict[str, str | Path] = {}

    def __enter__(self) -> 'Export':
        if self.path.is_file():
            self.archive = zipfile.ZipFile(self.path)
            names = [name for name in self.archive.namelist() if not name.endswith('/')
                     and not any(p in SKIP_FOLDERS for p in PurePosixPath(name).parts)]
            common_root = names and all(name.startswith(names[0].split('/')[0] + '/') for name in names)
            prefix = names[0].split('/')[0] + '/' if common_root else ''
            self.files = {name[len(prefix):]: name for name in names if not name.endswith('/')}
        else:
            self.files = {p.relative_to(self.path).as_posix(): p for p in self.path.rglob('*')
                          if p.is_file() and not p.is_symlink()
                          and not any(part in SKIP_FOLDERS for part in p.relative_to(self.path).parts)}
        return self

    def __exit__(self, *args) -> None:
        if self.archive:
            self.archive.close()

    @contextmanager
    def open(self, name: str) -> Iterator:
        src = self.files[name]
        if self.archive:
            with self.archive.open(src) as fd:
                yield fd
        else:
            with open(src, 'rb') as fd:
                yield fd

    def read_bytes(self, name: str) -> bytes:
        with self.open(name) as fd:
            return fd.read()

    def file_size(self, name: str) -> int:
        if self.archive:
            return self.archive.getinfo(self.files[name]).file_size
        return Path(self.files[name]).stat().st_size


def scan_export(path: Path, scan_images: bool = True) -> dict:
    """A 0 corrupt count means the selected image files were fully decoded."""
    with Export(path) as exp:
        names = sorted(exp.files)
        images = [name for name in names if is_image(name)]
        image_by_key: dict[str, list[str]] = {}
        for image in images:
            image_by_key.setdefault(key_for_file(image), []).append(image)
        labels = [name for name in names if is_label_candidate(name, set(image_by_key))]
        labels_by_key: dict[str, list[str]] = {}
        for label in labels:
            labels_by_key.setdefault(key_for_file(label), []).append(label)
        classes = parse_classes(names, exp.read_bytes)
        per_class: Counter[str] = Counter()
        annotated_keys: set[str] = set()
        valid_boxes = 0
        invalid_lines = 0
        qc = []

        def warn(message: str) -> None:
            if len(qc) < MAX_QC_SAMPLES:
                qc.append(message)

        for label in labels:
            label_key = key_for_file(label)
            try:
                raw = exp.read_bytes(label).decode('utf-8-sig')
            except (UnicodeError, OSError, RuntimeError) as exc:
                invalid_lines += 1
                warn(f'{label}: unreadable label file ({type(exc).__name__})')
                continue
            for line_num, line in enumerate(raw.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                class_id, err = parse_box_line(line, classes)
                if err:
                    invalid_lines += 1
                    warn(f'{label}:{line_num}: {err}')
                else:
                    valid_boxes += 1
                    annotated_keys.add(label_key)
                    per_class[classes.get(class_id, f'class_{class_id}')] += 1

        corrupted = 0
        if scan_images:
            for image in images:
                try:
                    with exp.open(image) as fd:
                        with Image.open(fd) as obj:
                            obj.load()
                except (Exception) as exc:
                    corrupted += 1
                    warn(f'{image}: unable to decode image ({type(exc).__name__})')
        duplicate_images = sum(max(0, len(v) - 1) for v in image_by_key.values())
        duplicate_labels = sum(max(0, len(v) - 1) for v in labels_by_key.values())
        orphan_labels = len(set(labels_by_key) - set(image_by_key))
        without_labels = len(set(image_by_key) - set(labels_by_key))
        for key, found in image_by_key.items():
            if len(found) > 1:
                warn(f'Image key {key} is duplicated: {", ".join(found[:3])}')
        # A CVAT/Ultralytics Train.txt lists images; it is never a YOLO label.
        # Validate only references explicitly relative to this export's images/.
        manifest_entries = 0
        manifest_missing = 0
        manifest_external = 0
        available_image_paths = {name.casefold() for name in images}
        for manifest in names:
            if PurePosixPath(manifest).name.lower() not in {'train.txt', 'val.txt', 'valid.txt', 'test.txt'}:
                continue
            for raw_line in exp.read_bytes(manifest).decode('utf-8-sig').splitlines():
                reference = raw_line.strip().replace('\\', '/')
                if not reference or reference.startswith('#'):
                    continue
                manifest_entries += 1
                if reference.startswith('/') or re.match(r'^[A-Za-z]:/', reference):
                    manifest_external += 1
                    continue
                reference = re.sub(r'^(?:\./)+', '', reference)
                # File lists may start with images/ or a relative data/images/.
                parts = PurePosixPath(reference).parts
                if 'images' not in [x.lower() for x in parts]:
                    manifest_external += 1
                    continue
                idx = [x.lower() for x in parts].index('images')
                relative = '/'.join(parts[idx:]).casefold()
                if relative not in available_image_paths:
                    manifest_missing += 1
                    warn(f'{manifest}: referenced image not found: {raw_line.strip()}')
        total_size = sum(exp.file_size(n) for n in names)
        return {
            'dataset_id': path.name,
            'kind': 'ZIP' if path.is_file() else 'folder',
            'images': len(images),
            'label_files': len(labels),
            'images_with_boxes': len(annotated_keys & set(image_by_key)),
            'boxes': valid_boxes,
            'invalid_yolo_lines': invalid_lines,
            'corrupted_images': corrupted if scan_images else None,
            'images_without_label_file': without_labels,
            'orphan_label_files': orphan_labels,
            'duplicate_image_keys': duplicate_images,
            'duplicate_label_keys': duplicate_labels,
            'dataset_size_mb': round(total_size / (1024 * 1024), 2),
            'image_list_entries': manifest_entries,
            'image_list_missing': manifest_missing,
            'image_list_external': manifest_external,
            'class_counts': dict(sorted(per_class.items())),
            'qc_samples': qc,
            'classes_known': bool(classes),
            'image_integrity_checked': scan_images,
            'synced_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
        }


def discover_exports(root: Path, categories: list[str], require_marker: bool = False) -> list[Path]:
    """Discover each YOLO export by sibling images/ and labels/ directories.

    The stable identifier is the full path relative to NAS_ROOT, so two
    categories may contain folders with identical names safely.
    """
    matches: list[Path] = []
    scan_roots = [root / category for category in categories if (root / category).is_dir()]
    if not scan_roots:
        raise RuntimeError(
            f'No category folders found beneath {root}. Expected: {", ".join(categories)}. '
            'Set NAS_ROOT to the parent directory containing the category folders.'
        )

    for category_dir in scan_roots:
        for walk_root, dirs, files in os.walk(category_dir, topdown=True, followlinks=False):
            base = Path(walk_root)
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIP_FOLDERS
                             and not d.lower().endswith(('.partial', '.uploading', '.tmp'))
                             and not (base / d).is_symlink())
            lower_to_dir = {d.lower(): d for d in dirs}
            if 'images' in lower_to_dir and 'labels' in lower_to_dir:
                if not require_marker or (base / '_EXPORT_COMPLETE').is_file():
                    matches.append(base)
                # Don't discover data folders inside an already discovered export.
                dirs[:] = []
                continue
            for file in files:
                archive = base / file
                if file.lower().endswith('.zip') and not archive.is_symlink():
                    if not require_marker or (base / (file + '.complete')).is_file():
                        matches.append(archive)
    return sorted(matches, key=lambda path: path.relative_to(root).as_posix())


def gh_rest(session: requests.Session, method: str, endpoint: str, **kwargs):
    result = session.request(method, 'https://api.github.com' + endpoint, timeout=60, **kwargs)
    result.raise_for_status()
    return result.json() if result.content else None


def gh_graphql(token: str, query: str, variables: dict) -> dict:
    result = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables},
                           headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'},
                           timeout=60)
    result.raise_for_status()
    body = result.json()
    if body.get('errors'):
        raise RuntimeError(f'GraphQL error: {body["errors"]}')
    return body['data']


def project_fields(token: str, owner: str, owner_type: str, number: int) -> tuple[str, dict[str, tuple[str, str]]]:
    if owner_type not in ('org', 'user'):
        raise ValueError('PROJECT_OWNER_TYPE must be org or user')
    owner_field = 'organization' if owner_type == 'org' else 'user'
    q = '''query($login: String!, $number: Int!) {
      OWNER(login: $login) { projectV2(number: $number) {
        id fields(first: 100) { nodes { ... on ProjectV2Field { id name dataType } } }
      } }
    }'''.replace('OWNER', owner_field)
    data = gh_graphql(token, q, {'login': owner, 'number': number})
    project = (data.get(owner_field) or {}).get('projectV2')
    if not project:
        raise ValueError('Cannot access GitHub Project; check owner, number and PROJECT_TOKEN')
    fields = {f['name']: (f['id'], f['dataType']) for f in project['fields']['nodes']
              if f and f.get('dataType') in {'NUMBER', 'TEXT'}}
    return project['id'], fields


def issue_key(dataset: str) -> str:
    return sha256(dataset.encode('utf-8')).hexdigest()[:24]


def find_existing_issues(session: requests.Session, repository: str) -> dict[str, dict]:
    issues = {}
    page = 1
    while True:
        result = gh_rest(session, 'GET', f'/repos/{repository}/issues', params={'per_page': 100, 'page': page, 'state': 'all'})
        for row in result:
            if 'pull_request' in row:
                continue
            match = re.search(r'<!-- nas-dataset:([a-f0-9]{24}) -->', row.get('body') or '')
            if match:
                issues[match.group(1)] = row
        if len(result) < 100:
            return issues
        page += 1


def markdown_safe(value: str) -> str:
    return str(value).replace('|', '&#124;').replace('\n', ' ').replace('`', "'").replace('<', '&lt;')


def issue_markdown(row: dict) -> str:
    class_lines = '\n'.join(f'| {markdown_safe(name)} | {value} |' for name, value in row['class_counts'].items()) or '| (none) | 0 |'
    samples = '\n'.join(f'- `{markdown_safe(sample)}`' for sample in row['qc_samples']) or '- None'
    corrupt = row['corrupted_images'] if row['corrupted_images'] is not None else 'Not scanned'
    return f'''<!-- nas-dataset:{issue_key(row['dataset_id'])} -->
## TrueNAS YOLO export: {markdown_safe(row['dataset_id'])}

| Metric | Value |
|---|---:|
| Category | {markdown_safe(row['category'])} |
| Export type | {row['kind']} |
| Images | {row['images']} |
| Label files | {row['label_files']} |
| Valid bounding boxes | {row['boxes']} |
| Images with one or more valid boxes | {row['images_with_boxes']} |
| Invalid YOLO lines | {row['invalid_yolo_lines']} |
| Corrupted images | {corrupt} |
| Images without label files | {row['images_without_label_file']} |
| Orphan label keys | {row['orphan_label_files']} |
| Duplicate image / label keys | {row['duplicate_image_keys']} / {row['duplicate_label_keys']} |
| Dataset size | {row['dataset_size_mb']} MiB |
| Image-list entries | {row['image_list_entries']} |
| Missing image-list references | {row['image_list_missing']} |
| External/unverified image-list references | {row['image_list_external']} |
| Last scanned | {row['synced_at']} |

**Bounding boxes by class**

| Class | Boxes |
|---|---:|
{class_lines}

**QC examples (up to {MAX_QC_SAMPLES})**
{samples}

**Interpretation:** An absent or empty YOLO `.txt` may legitimately mean no objects. The share only
shows exported datasets; it cannot determine CVAT account activity, unfinished tasks, or review approval.
Class IDs without a `data.yaml` or `obj.names` are displayed as `class_N`.

_Auto-generated from a read-only TrueNAS share. Do not edit the body directly._
'''


def upsert_issue(session: requests.Session, repository: str, existing: dict, row: dict) -> dict:
    key = issue_key(row['dataset_id'])
    title = '[NAS YOLO] ' + row['dataset_id'][:160]
    body = issue_markdown(row)
    if key in existing:
        issue = existing[key]
        if issue.get('state') != 'open' or issue.get('title') != title or issue.get('body') != body:
            issue = gh_rest(session, 'PATCH', f'/repos/{repository}/issues/{issue["number"]}',
                            json={'state': 'open', 'title': title, 'body': body})
        return issue
    issue = gh_rest(session, 'POST', f'/repos/{repository}/issues', json={'title': title, 'body': body})
    existing[key] = issue
    return issue


def write_project_fields(token: str, project_id: str, fields: dict[str, tuple[str, str]], issue_node: str, row: dict) -> None:
    q = '''mutation($input: AddProjectV2ItemByIdInput!) {
       addProjectV2ItemById(input: $input) { item { id } }
    }'''
    item = gh_graphql(token, q, {'input': {'projectId': project_id, 'contentId': issue_node}})['addProjectV2ItemById']['item']['id']
    metrics = {
        'Images': row['images'],
        'Label Files': row['label_files'],
        'Boxes': row['boxes'],
        'Images With Boxes': row['images_with_boxes'],
        'Invalid YOLO Lines': row['invalid_yolo_lines'],
        'Images Without Label File': row['images_without_label_file'],
        'Orphan Label Files': row['orphan_label_files'],
        'Duplicate Keys': row['duplicate_image_keys'] + row['duplicate_label_keys'],
        'Dataset Size MB': row['dataset_size_mb'],
        'Image List Missing': row['image_list_missing'],
        'Image List Entries': row['image_list_entries'],
        'Category': row['category'],
        'Dataset Path': row['dataset_id'],
    }
    if row['corrupted_images'] is not None:
        metrics['Corrupted Images'] = row['corrupted_images']
    selected = [(name, value) for name, value in metrics.items() if name in fields]
    if not selected:
        LOG.warning('No matching fields created in GitHub Project')
        return
    # Keep GraphQL calls reasonably small.
    for offset in range(0, len(selected), 8):
        chunk = selected[offset:offset + 8]
        args = {'project': project_id, 'item': item}
        declared = ['$project: ID!', '$item: ID!']
        operations = []
        for i, (name, value) in enumerate(chunk):
            field_id, field_type = fields[name]
            type_arg = 'String!' if field_type == 'TEXT' else 'Float!'
            value_field = 'text' if field_type == 'TEXT' else 'number'
            declared.extend((f'$f{i}: ID!', f'$v{i}: {type_arg}'))
            args[f'f{i}'] = field_id
            args[f'v{i}'] = str(value) if field_type == 'TEXT' else float(value)
            operations.append(f'''x{i}: updateProjectV2ItemFieldValue(input: {{
                projectId: $project, itemId: $item, fieldId: $f{i}, value: {{{value_field}: $v{i}}}
              }}) {{ projectV2Item {{ id }} }}''')
        mutation = 'mutation(' + ', '.join(declared) + ') {\n' + '\n'.join(operations) + '\n}'
        gh_graphql(token, mutation, args)


def run() -> int:
    parser = argparse.ArgumentParser(description='Audit NAS YOLO exports and update GitHub Projects')
    parser.add_argument('--root', default=setting('NAS_ROOT'), help='Mounted TrueNAS export root')
    parser.add_argument('--dry-run', action='store_true', help='Legacy alias for scan-only (default)')
    parser.add_argument('--sync-project', action='store_true', help='Additionally sync Issues and GitHub Projects (requires tokens)')
    parser.add_argument('--skip-image-decode', action='store_true', help='Skip expensive corruption checks')
    parser.add_argument('--categories', default=setting('NAS_CATEGORIES', 'parts,abnormal'), help='Comma-separated category folders')
    args = parser.parse_args()
    root = Path(args.root).expanduser()
    if not args.root or not root.is_dir():
        raise RuntimeError('NAS_ROOT must point to an existing directory')
    # On Windows, a UNC SMB share or mapped directory does not reliably report
    # is_mount(). Verify it is actually readable and require discovered datasets.
    try:
        next(root.iterdir(), None)
    except OSError as exc:
        raise RuntimeError(f'Cannot read NAS_ROOT {root}: {exc}') from exc
    if args.dry_run and args.sync_project:
        parser.error('--dry-run and --sync-project cannot be used together')
    require_marker = setting('REQUIRE_COMPLETE_MARKER', '0').lower() in {'1', 'true', 'yes'}
    categories = [s.strip() for s in args.categories.split(',') if s.strip()]
    if not categories:
        raise RuntimeError('At least one category is required')
    exports = discover_exports(root, categories, require_marker)
    if not exports:
        raise RuntimeError('No nested YOLO datasets found under selected category folders')

    reports = []
    failures = []
    for export in exports:
        try:
            row = scan_export(export, not args.skip_image_decode)
            row['dataset_id'] = export.relative_to(root).as_posix()
            row['category'] = row['dataset_id'].split('/')[0]
            if row['images'] == 0 and row['label_files'] == 0:
                LOG.warning('Skipping %s: no images or YOLO labels found', export)
                continue
            reports.append(row)
            LOG.info('%s: %d images, %d boxes, %d invalid lines', row['dataset_id'], row['images'], row['boxes'], row['invalid_yolo_lines'])
        except Exception as exc:
            failures.append(f'{export.name}: {type(exc).__name__}: {exc}')
            LOG.exception('Error scanning %s', export)

    report_dir = Path('.reports')
    report_dir.mkdir(exist_ok=True)
    (report_dir / 'datasets.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
    summaries = {}
    for row in reports:
        group = summaries.setdefault(row['category'], {'datasets': 0, 'images': 0,
                                    'label_files': 0, 'boxes': 0, 'invalid_yolo_lines': 0,
                                    'corrupted_images': 0 if not args.skip_image_decode else None,
                                    'class_counts': {}})
        group['datasets'] += 1
        for key in ('images', 'label_files', 'boxes', 'invalid_yolo_lines'):
            group[key] += row[key]
        if group['corrupted_images'] is not None:
            group['corrupted_images'] += row['corrupted_images']
        for name, count in row['class_counts'].items():
            group['class_counts'][name] = group['class_counts'].get(name, 0) + count
    (report_dir / 'categories.json').write_text(json.dumps(summaries, indent=2), encoding='utf-8')
    with (report_dir / 'datasets.csv').open('w', newline='', encoding='utf-8') as csvfile:
        columns = ['category', 'dataset_id', 'images', 'label_files', 'boxes',
                   'images_with_boxes', 'corrupted_images', 'invalid_yolo_lines',
                   'images_without_label_file', 'orphan_label_files', 'image_list_entries',
                   'image_list_missing', 'dataset_size_mb']
        writer = csv.DictWriter(csvfile, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(reports)
    (report_dir / 'failures.json').write_text(json.dumps(failures, indent=2), encoding='utf-8')
    # Always generate a GitHub-readable README plus an interactive static web site.
    from dashboard import write_dashboard
    write_dashboard(reports, summaries, failures, report_dir)

    if args.sync_project:
        repo = setting('GH_REPOSITORY')
        token = setting('GITHUB_TOKEN')
        project_token = setting('PROJECT_TOKEN')
        owner = setting('PROJECT_OWNER')
        number = setting('PROJECT_NUMBER')
        if not all([repo, token, project_token, owner, number]):
            raise RuntimeError('GH_REPOSITORY, GITHUB_TOKEN, PROJECT_TOKEN, PROJECT_OWNER and PROJECT_NUMBER are required')
        session = requests.Session()
        session.headers.update({'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})
        project_id, fields = project_fields(project_token, owner, setting('PROJECT_OWNER_TYPE', 'org'), int(number))
        existing = find_existing_issues(session, repo)
        for report in reports:
            try:
                issue = upsert_issue(session, repo, existing, report)
                write_project_fields(project_token, project_id, fields, issue['node_id'], report)
            except Exception as exc:
                LOG.exception('Failed to sync GitHub for %s', report['dataset_id'])
                failures.append(f'{report["dataset_id"]}: GitHub sync failed: {type(exc).__name__}: {exc}')
        (report_dir / 'failures.json').write_text(json.dumps(failures, indent=2), encoding='utf-8')
        write_dashboard(reports, summaries, failures, report_dir)
    return 1 if failures or not reports else 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        raise SystemExit(run())
    except Exception:
        LOG.exception('Fatal tracker error')
        raise SystemExit(1)

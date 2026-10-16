import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from PIL import Image

module_path = Path(__file__).resolve().parents[1] / 'scripts' / 'sync.py'
spec = importlib.util.spec_from_file_location('nas_nested_sync', module_path)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def image(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (15, 15)).save(path)


def make_export(path: Path, boxes: int = 1):
    image(path / 'images' / 'train' / 'a.jpg')
    labels = path / 'labels' / 'train'
    labels.mkdir(parents=True, exist_ok=True)
    (labels / 'a.txt').write_text('0 0.5 0.5 0.2 0.2\n' * boxes)
    (path / 'data.yaml').write_text('names:\n  0: scratch\n')
    (path / 'Train.txt').write_text('images/train/a.jpg\n')


def test_nested_discovery_and_distinct_ids(tmp_path):
    left = tmp_path / 'parts' / 'anything' / 'run' / 'data'
    right = tmp_path / 'abnormal' / 'anything' / 'run' / 'data'
    make_export(left, 2)
    make_export(right, 1)
    found = sync.discover_exports(tmp_path, ['parts', 'abnormal'])
    assert [x.relative_to(tmp_path).as_posix() for x in found] == [
        'abnormal/anything/run/data', 'parts/anything/run/data'
    ]
    left_result = sync.scan_export(left)
    assert left_result['boxes'] == 2
    assert left_result['images'] == 1
    assert left_result['label_files'] == 1  # Train.txt must not count
    assert left_result['image_list_entries'] == 1
    assert left_result['image_list_missing'] == 0
    assert left_result['class_counts'] == {'scratch': 2}


def test_manifest_missing_and_external(tmp_path):
    export = tmp_path / 'parts' / 'some' / 'data'
    make_export(export)
    (export / 'Train.txt').write_text('images/train/a.jpg\nimages/train/missing.jpg\nC:\\external\\x.jpg\n')
    row = sync.scan_export(export)
    assert row['image_list_entries'] == 3
    assert row['image_list_missing'] == 1
    assert row['image_list_external'] == 1


def test_marker_only_on_actual_data_directory(tmp_path):
    data = tmp_path / 'parts' / 'customer' / 'data'
    make_export(data)
    assert sync.discover_exports(tmp_path, ['parts'], require_marker=True) == []
    (data / '_EXPORT_COMPLETE').touch()
    assert sync.discover_exports(tmp_path, ['parts'], require_marker=True) == [data]


def test_dry_run_writes_group_summaries(tmp_path):
    root = tmp_path / 'nas'
    make_export(root / 'parts' / 'a' / 'data', 2)
    make_export(root / 'abnormal' / 'b' / 'data', 3)
    work = tmp_path / 'work'
    work.mkdir()
    result = subprocess.run([
        sys.executable, str(module_path), '--dry-run', '--root', str(root),
        '--categories', 'parts,abnormal', '--skip-image-decode',
    ], cwd=work, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    rows = json.loads((work / '.reports' / 'datasets.json').read_text())
    groups = json.loads((work / '.reports' / 'categories.json').read_text())
    assert len(rows) == 2
    assert groups['parts']['boxes'] == 2
    assert groups['parts']['class_counts'] == {'scratch': 2}
    assert groups['abnormal']['boxes'] == 3
    assert groups['parts']['corrupted_images'] is None


def test_no_category_fails_fast(tmp_path):
    try:
        sync.discover_exports(tmp_path, ['parts', 'abnormal'])
    except RuntimeError as exc:
        assert 'No category folders' in str(exc)
    else:
        raise AssertionError('Expected failure for incorrect NAS_ROOT')

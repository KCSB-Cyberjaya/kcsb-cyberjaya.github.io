"""Static GitHub Pages privacy/report smoke tests."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from dashboard import write_dashboard


def row(dataset_id, category, count):
    return {
        'dataset_id': dataset_id, 'category': category, 'images': 2,
        'label_files': 1, 'boxes': count, 'invalid_yolo_lines': 0,
        'corrupted_images': None, 'images_without_label_file': 1,
        'orphan_label_files': 0, 'image_list_missing': 0,
        'dataset_size_mb': 1.5, 'class_counts': {'secretScratchName': count},
        'duplicate_image_keys': 0, 'duplicate_label_keys': 0,
        'kind': 'folder', 'qc_samples': ['labels/private-file.txt: invalid class'],
    }


def test_public_dashboard_anonymizes_names(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('DASHBOARD_EXPOSE_NAMES', raising=False)
    readme = Path('README.md')
    readme.write_text('# Main project\n\n<!-- AUTO_STATS_START -->\nold\n<!-- AUTO_STATS_END -->\n\nKeep me.\n', encoding='utf-8')
    rows = [row('parts/customer/private/data', 'parts', 3), row('abnormal/x/data', 'abnormal', 5)]
    write_dashboard(rows, {}, ['private/path/data: OSError: denied'], Path('.reports'))
    page_text = Path('docs/data.json').read_text(encoding='utf-8')
    page = json.loads(page_text)
    md = readme.read_text(encoding='utf-8')
    assert page['totals']['boxes'] == 8
    assert page['totals']['corrupted_images'] is None
    assert page['datasets'][0]['dataset_id'] == 'abnormal/dataset-001'
    assert page['datasets'][1]['dataset_id'] == 'parts/dataset-001'
    assert page['classes'] == {'Class 001': 8}
    for content in (md, page_text):
        for secret in ('private', 'secretScratchName', 'customer', 'private-file.txt', 'OSError'):
            assert secret not in content
    assert 'Keep me.' in md
    assert md.count('<!-- AUTO_STATS_START -->') == 1
    name_map = json.loads(Path('.reports/private-name-mapping.json').read_text(encoding='utf-8'))
    assert name_map['dataset_aliases']['parts/dataset-001'] == 'parts/customer/private/data'
    write_dashboard(rows, {}, [], Path('.reports'))
    assert Path('README.md').read_text(encoding='utf-8').count('<!-- AUTO_STATS_START -->') == 1


def test_explicit_name_exposure_opt_in(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DASHBOARD_EXPOSE_NAMES', 'true')
    write_dashboard([row('parts/customer/private/data', 'parts', 3)], {}, [], Path('.reports'))
    site = json.loads(Path('docs/data.json').read_text(encoding='utf-8'))
    assert site['public_names_enabled'] is True
    assert site['datasets'][0]['dataset_id'] == 'parts/customer/private/data'
    assert site['datasets'][0]['qc_samples'][0].startswith('labels/private-file')
    assert site['classes']['secretScratchName'] == 3

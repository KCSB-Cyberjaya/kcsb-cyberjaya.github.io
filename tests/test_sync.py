import importlib.util
from pathlib import Path
import zipfile

from PIL import Image

module_path = Path(__file__).resolve().parents[1] / 'scripts' / 'sync.py'
spec = importlib.util.spec_from_file_location('nas_sync', module_path)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def create_img(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (24, 24), 'white').save(path)


def test_image_and_label_counts(tmp_path):
    root = tmp_path / 'batch-A'
    create_img(root / 'images' / 'train' / 'a.jpg')
    create_img(root / 'images' / 'train' / 'b.png')
    (root / 'labels' / 'train').mkdir(parents=True)
    (root / 'labels' / 'train' / 'a.txt').write_text('0 0.5 0.5 0.2 0.2\n1 0.5 0.5 0.4 0.4\n')
    (root / 'labels' / 'train' / 'orphan.txt').write_text('0 0.5 0.5 0.2 0.2\n')
    (root / 'data.yaml').write_text('names: [person, car]\n')
    result = sync.scan_export(root)
    assert result['images'] == 2
    assert result['boxes'] == 3
    assert result['images_with_boxes'] == 1
    assert result['class_counts'] == {'car': 1, 'person': 2}
    assert result['orphan_label_files'] == 1
    assert result['images_without_label_file'] == 1
    assert result['corrupted_images'] == 0


def test_bad_box_and_corrupted_file(tmp_path):
    root = tmp_path / 'batch-B'
    (root / 'images').mkdir(parents=True)
    (root / 'labels').mkdir(parents=True)
    (root / 'images' / 'broken.jpg').write_bytes(b'not jpeg content')
    (root / 'labels' / 'broken.txt').write_text('0 0.1 0.1 0.5 0.5\n0 0.5 0.5 0.4\n')
    result = sync.scan_export(root)
    assert result['boxes'] == 0
    assert result['invalid_yolo_lines'] == 2
    assert result['corrupted_images'] == 1


def test_zip_without_extracting(tmp_path):
    root = tmp_path / 'batch-C.zip'
    image = tmp_path / 'image.jpg'
    create_img(image)
    with zipfile.ZipFile(root, 'w') as z:
        z.write(image, 'folder/images/val/x.jpg')
        z.writestr('folder/labels/val/x.txt', '0 0.5 0.5 0.1 0.1\n')
        z.writestr('folder/data.yaml', 'names: [cat]\n')
    result = sync.scan_export(root)
    assert result['images'] == 1
    assert result['boxes'] == 1
    assert result['class_counts'] == {'cat': 1}
    assert result['kind'] == 'ZIP'
    assert result['corrupted_images'] == 0


def test_yolo11_adjacent(tmp_path):
    root = tmp_path / 'batch-D'
    create_img(root / 'obj_train_data' / 'img01.png')
    (root / 'obj_train_data' / 'img01.txt').write_text('0 0.5 0.5 0.1 0.1\n')
    (root / 'obj.names').write_text('truck\n')
    result = sync.scan_export(root)
    assert result['boxes'] == 1
    assert result['class_counts'] == {'truck': 1}


def test_marker_filters_partial_dataset(tmp_path):
    # The nested edition uses a marker on the actual image/label-pair folder.
    source = tmp_path / 'parts'
    complete = source / 'nested' / 'job2'
    incomplete = source / 'nested' / 'job1'
    for path in (complete, incomplete):
        (path / 'images').mkdir(parents=True)
        (path / 'labels').mkdir()
    (complete / '_EXPORT_COMPLETE').touch()
    assert sync.discover_exports(tmp_path, ['parts'], require_marker=True) == [complete]


def test_detection_validity():
    assert sync.parse_box_line('2 0.5 0.5 0.3 0.2', {}) == (2, None)
    assert sync.parse_box_line('3 0.5 0.5 0.3 0.2', {0: 'a'})[1]
    assert sync.parse_box_line('0 nan 0.5 0.3 0.2', {})[1]
    assert sync.parse_box_line('0 0.1 0.1 0.8 0.8', {})[1]
    assert sync.parse_box_line('0 0.5 0.5 0.0 0.2', {})[1]


def test_duplicate_image_keys(tmp_path):
    root = tmp_path / 'batch-E'
    create_img(root / 'images' / 'train' / 'a.png')
    create_img(root / 'images' / 'train' / 'a.jpg')
    result = sync.scan_export(root)
    assert result['duplicate_image_keys'] == 1

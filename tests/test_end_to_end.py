from PIL import Image

from seal_generator.batch import BatchProcessor
from seal_generator.renderer import render_seal
from seal_generator.resources import font_path, load_supported_codepoints
from seal_generator.validation import prepare_records


def test_nine_lengths_batch_to_real_png_files(tmp_path):
    names = ["印" * length for length in range(8, 17)]
    font_file = font_path()
    records = prepare_records(names, load_supported_codepoints(font_file))

    summary = BatchProcessor(lambda name: render_seal(name, font_file)).run(
        records, tmp_path
    )

    assert all(record.actual_filename for record in summary.records)
    paths = sorted(tmp_path.glob("*.png"))
    assert len(paths) == 9
    for path in paths:
        with Image.open(path) as image:
            assert image.mode == "RGBA"
            assert image.size == (1024, 1024)
            assert image.getextrema()[3][0] == 0

from pathlib import Path

from PIL import Image, ImageDraw

from seal_generator.renderer import render_seal
from seal_generator.resources import font_path


PREFIX = "北京华夏印章科技发展股份"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts" / "reference-correction"


def checkerboard(size: tuple[int, int], cell: int = 24) -> Image.Image:
    image = Image.new("RGBA", size, "#343434")
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle(
                    (x, y, x + cell - 1, y + cell - 1), fill="#4a4a4a"
                )
    return image


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGB", (1500, 1620), "#202124")
    draw = ImageDraw.Draw(sheet)
    for index, length in enumerate(range(8, 17)):
        name = PREFIX[: length - 4] + "有限公司"
        assert len(name) == length
        seal = render_seal(name, font_path())
        seal.save(OUTPUT / f"{length:02d}-{name}.png")

        tile = checkerboard((440, 440))
        tile.alpha_composite(
            seal.resize((420, 420), Image.Resampling.LANCZOS), (10, 10)
        )
        x = 24 + (index % 3) * 500
        y = 24 + (index // 3) * 530
        sheet.paste(tile.convert("RGB"), (x, y))
        draw.text((x, y + 450), f"{length}", fill="white")

    sheet.save(ROOT / "artifacts" / "reference-correction-contact-sheet.png")


if __name__ == "__main__":
    main()

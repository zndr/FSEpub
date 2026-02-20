"""Generate a multi-resolution .ico file for FSE Processor."""

from PIL import Image, ImageDraw, ImageFont

SIZES = [16, 32, 48, 64, 128, 256]


def create_icon_image(size: int) -> Image.Image:
    """Create a single icon image at the given size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background: rounded rectangle with blue gradient feel
    margin = max(1, size // 16)
    radius = max(2, size // 6)
    draw.rounded_rectangle(
        [margin, margin, size - margin - 1, size - margin - 1],
        radius=radius,
        fill=(0, 102, 179),  # FSE blue
    )

    # Draw "FSE" text centered
    font_size = max(6, size // 3)
    try:
        font = ImageFont.truetype("arialbd.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    text = "FSE"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), text, fill=(255, 255, 255), font=font)

    return img


def main() -> None:
    images = [create_icon_image(s) for s in SIZES]
    # Save the largest image as .ico - PIL will auto-create sub-sizes
    largest = images[-1]  # 256x256
    largest.save(
        "assets/icon.ico",
        format="ICO",
        append_images=images[:-1],
    )
    import os
    size = os.path.getsize("assets/icon.ico")
    print(f"Icon saved to assets/icon.ico ({size:,} bytes) with sizes {SIZES}")


if __name__ == "__main__":
    main()

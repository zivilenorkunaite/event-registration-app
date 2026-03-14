import base64
import json
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SERVER = "http://localhost:5050"
TRANSPORT = "ble"
ADDRESS = "B3S_P-HA15010590"
OUT_DIR = ROOT / "app" / "backend" / "data" / "images"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def connect_printer():
    req = Request(
        SERVER + "/connect",
        data=json.dumps({"transport": TRANSPORT, "address": ADDRESS}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=20) as response:
            print("connect", response.status, response.read().decode("utf-8"))
    except Exception as exc:
        print("connect", str(exc))


def send_png(path: Path, label_width=640, label_height=400, direction="top"):
    with path.open("rb") as file_handle:
        b64 = base64.b64encode(file_handle.read()).decode("ascii")

    payload = {
        "imageBase64": b64,
        "labelWidth": label_width,
        "labelHeight": label_height,
        "printTask": "B1",
        "printDirection": direction,
        "quantity": 1,
    }

    req = Request(
        SERVER + "/print",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=180) as response:
            print(path.name, "->", response.status, response.read().decode("utf-8"))
    except Exception as exc:
        print(path.name, "->", str(exc))


def build_tests():
    base = Image.new("RGB", (640, 400), "white")
    draw = ImageDraw.Draw(base)
    draw.rectangle([0, 0, 639, 399], outline="black", width=8)
    draw.rectangle([30, 40, 610, 110], fill="black")
    draw.rectangle([30, 290, 610, 360], fill="black")
    draw.rectangle([30, 150, 300, 250], fill="black")
    draw.rectangle([340, 150, 610, 250], fill="black")
    font = ImageFont.load_default()
    draw.text((252, 186), "RGB", fill="white", font=font)

    path_a = OUT_DIR / "print_test_A_rgb_640x400.png"
    base.save(path_a)

    rotated = base.rotate(180)
    path_b = OUT_DIR / "print_test_B_rgb_rot180_640x400.png"
    rotated.save(path_b)

    threshold = base.convert("L").point(lambda value: 0 if value < 200 else 255, mode="L").convert("RGB")
    path_c = OUT_DIR / "print_test_C_threshold_rgb_640x400.png"
    threshold.save(path_c)

    return path_a, path_b, path_c


def main():
    connect_printer()
    test_paths = build_tests()
    for test_path in test_paths:
        send_png(test_path)
        print("saved", test_path)


if __name__ == "__main__":
    main()

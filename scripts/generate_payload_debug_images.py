from pathlib import Path
from io import BytesIO
import base64

from dotenv import load_dotenv
from PIL import Image

from backend.services.nametag import generate_nametag_image
from backend.main import _build_nametag_artifacts


def main() -> None:
    load_dotenv('.env', override=True)

    out = Path('app/backend/data/images')
    out.mkdir(parents=True, exist_ok=True)

    raw = generate_nametag_image(
        {
            'name': 'ZIVILE',
            'company': 'DATABRICKS',
            'groupName': 'Energy & Utilities Data Connect',
            'location': 'Sydney',
        }
    )

    raw_path = out / 'debug_original_target.png'
    raw_path.write_bytes(raw)

    with Image.open(BytesIO(raw)) as im:
        im.rotate(-90, expand=True).save(out / 'debug_rotated_cw_target.png', format='PNG')
        im.rotate(90, expand=True).save(out / 'debug_rotated_ccw_target.png', format='PNG')

    artifacts = _build_nametag_artifacts(
        first_name='ZIVILE',
        company_val='DATABRICKS',
        group='Energy & Utilities Data Connect',
        location='Sydney',
        registration_id=None,
    )
    payload_bytes = base64.b64decode(artifacts['printPayload']['imageBase64'])
    payload_path = out / 'debug_payload_sent_target.png'
    payload_path.write_bytes(payload_bytes)

    with Image.open(BytesIO(raw)) as img_raw:
        raw_size = img_raw.size
    with Image.open(BytesIO(payload_bytes)) as img_payload:
        payload_size = img_payload.size

    print('raw_size', raw_size)
    print('payload_size', payload_size)
    print('payload_label', artifacts['printPayload']['labelWidth'], artifacts['printPayload']['labelHeight'])
    print('payload_direction', artifacts['printPayload']['printDirection'])
    print('payload_task', artifacts['printPayload']['printTask'])
    print('saved', raw_path)
    print('saved', out / 'debug_rotated_cw_target.png')
    print('saved', out / 'debug_rotated_ccw_target.png')
    print('saved', payload_path)


if __name__ == '__main__':
    main()

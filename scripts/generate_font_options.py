from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv('.env', override=True)
os.environ['NIIMBOT_ROTATE_TEXT_90'] = 'true'

import backend.services.nametag as n


def main() -> None:
    test_name = os.getenv('FONT_TEST_NAME', 'ŻIVILĖ').strip() or 'ŻIVILĖ'
    out_dir = Path('app/backend/data/images/font_options')
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = {
        'verdana': (
            '/System/Library/Fonts/Supplemental/Verdana.ttf',
            '/System/Library/Fonts/Supplemental/Verdana Bold.ttf',
        ),
        'helvetica': (
            '/System/Library/Fonts/Helvetica.ttc',
            '/System/Library/Fonts/Helvetica.ttc',
        ),
        'arial': (
            '/System/Library/Fonts/Supplemental/Arial.ttf',
            '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        ),
        'avenir_next': (
            '/System/Library/Fonts/Supplemental/Avenir Next.ttc',
            '/System/Library/Fonts/Supplemental/Avenir Next Bold.ttf',
        ),
        'trebuchet': (
            '/System/Library/Fonts/Supplemental/Trebuchet MS.ttf',
            '/System/Library/Fonts/Supplemental/Trebuchet MS Bold.ttf',
        ),
        'gill_sans': (
            '/System/Library/Fonts/Supplemental/GillSans.ttc',
            '/System/Library/Fonts/Supplemental/GillSans.ttc',
        ),
        'optima': (
            '/System/Library/Fonts/Supplemental/Optima.ttc',
            '/System/Library/Fonts/Supplemental/Optima ExtraBlack.ttf',
        ),
        'inter': (
            str(Path('app/backend/services/fonts/Inter-Regular.ttf').resolve()),
            str(Path('app/backend/services/fonts/Inter-Bold.ttf').resolve()),
        ),
        'dm_sans': (
            str(Path('app/backend/services/fonts/DMSans-Variable.ttf').resolve()),
            str(Path('app/backend/services/fonts/DMSans-Variable.ttf').resolve()),
        ),
    }

    original_system = n._SYSTEM_FONT_PATHS
    original_bundled = n._BUNDLED_FONT_PATHS

    made: list[Path] = []
    try:
        for name, (regular_path, bold_path) in candidates.items():
            regular = Path(regular_path)
            bold = Path(bold_path)
            if not regular.exists():
                continue
            if not bold.exists():
                bold = regular

            n._SYSTEM_FONT_PATHS = {
                'regular': [str(regular)],
                'bold': [str(bold), str(regular)],
            }
            n._BUNDLED_FONT_PATHS = {'regular': [], 'bold': []}

            img_bytes = n.generate_nametag_image(
                {
                    'name': test_name,
                    'company': 'DATABRICKS',
                    'groupName': 'Energy & Utilities Data Connect',
                    'location': 'Sydney',
                }
            )
            out_path = out_dir / f'font_option_{name}.png'
            out_path.write_bytes(img_bytes)
            made.append(out_path)
    finally:
        n._SYSTEM_FONT_PATHS = original_system
        n._BUNDLED_FONT_PATHS = original_bundled

    for path in made:
        print('saved', path)
    print('count', len(made))
    print('name', test_name)


if __name__ == '__main__':
    main()

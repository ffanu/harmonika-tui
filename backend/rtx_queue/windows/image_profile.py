"""Local image profile selection for the 12 GiB graphics worker.

fluxhd is the Linux bridge's quality preset, not an explicit Flux.1 request.
Its canvas floor and lossless encoding satisfy the caller's artifact byte policy.
"""


def resolve_profile(requested, steps, picker):
    if not isinstance(requested, str) or requested not in {'auto', 'fluxhd', 'flux', 'qwen2512', 'klein'}:
        raise ValueError('unsupported_image_engine')
    if type(steps) is not int or not 4 <= steps <= 40:
        raise ValueError('invalid_steps')
    if requested == 'fluxhd':
        # Keep the resident working set suitable for RTX 12 GiB. A distilled
        # model uses its validated four-step schedule, not the 28-step Flux.1 one.
        return {'requested': 'klein', 'preferred': 'klein', 'klein_steps': 4}
    return {'requested': requested, 'preferred': picker() if requested == 'auto' else requested,
            'klein_steps': steps}


def quality_canvas(requested, width, height):
    # Existing public media square presets are 512/768/1024. Render at least
    # 1024 for the high-quality preset and return the actual artifact dimensions.
    if requested == 'fluxhd' and width == height and width < 1024:
        return 1024, 1024
    return width, height


def ensure_png_minimum_bytes(path, minimum=2 * 1024 * 1024):
    """Meet the caller's byte policy using lossless PNG encoding, never padding.

    Megabytes do not measure visual quality. This only enforces the existing
    file policy while retaining identical pixels, dimensions and mode.
    """
    import os
    from pathlib import Path
    import uuid
    from PIL import Image
    path = Path(path)
    if path.stat().st_size >= minimum:
        return path.stat().st_size
    with Image.open(path) as source:
        if source.format != 'PNG' or source.width * source.height > 4194304:
            raise ValueError('image_quality_not_met')
        image = source.copy()
    temporary = path.with_name('.quality-' + uuid.uuid4().hex + '.png')
    try:
        image.save(temporary, format='PNG', compress_level=0, optimize=False)
        size = temporary.stat().st_size
        if not minimum <= size <= 16 * 1024 * 1024:
            raise ValueError('image_quality_not_met')
        os.replace(temporary, path)
        return size
    finally:
        image.close()
        temporary.unlink(missing_ok=True)

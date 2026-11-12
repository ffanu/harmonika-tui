"""Full Wan 2.2 A14B text and reference workflows with per-family qualification."""
import hashlib
import json
from pathlib import Path
import importlib.util

CONTROL = Path(__file__).resolve().parent
base_path = CONTROL.parent/'harmonika-high-video/workflow.py'
if not base_path.exists():
    base_path = CONTROL.parent/'high_video/workflow.py'
spec = importlib.util.spec_from_file_location('wan14_frozen_base', base_path)
base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)
Failure = base.Failure
MODELS = base.MODELS
MODEL_FILES = {
    'wan22_14b': base.MODEL_FILES['wan22_14b'],
    'wan22_i2v_14b': ['diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors',
                     'diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors',
                     'text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'vae/wan_2.1_vae.safetensors'],
}
for _kind in ('t2v', 'i2v'):
    _version = 'v1.1' if _kind == 't2v' else 'v1'
    _source = 'wan22_14b' if _kind == 't2v' else 'wan22_i2v_14b'
    MODEL_FILES[f'wan22_{_kind}_14b_lightx2v'] = MODEL_FILES[_source] + [
        f'loras/wan2.2_{_kind}_lightx2v_4steps_lora_{_version}_{noise}_noise.safetensors'
        for noise in ('high', 'low')]


def workflow_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def workflow_hash_for_family(family):
    if family not in MODEL_FILES:
        raise Failure('unsupported_quality_model')
    # The text graph is exactly the frozen, already rendered base graph.
    return base.workflow_hash() if family == 'wan22_14b' else hashlib.sha256((base.workflow_hash()+workflow_hash()).encode()).hexdigest()


def profile(family):
    if family not in MODEL_FILES:
        raise Failure('unsupported_quality_model')
    accelerated = family.endswith('_lightx2v')
    kind = 'I2V' if '_i2v_' in family else 'T2V'
    return {**base.profile('wan22_14b'), 'family': family,
            'steps': 4 if accelerated else 20,
            'model': f'Wan 2.2 {kind} A14B FP8' + (' + LightX2V 4-step' if accelerated else '')}


def verify_models(family, control=CONTROL, models=MODELS):
    if family not in MODEL_FILES:
        raise Failure('unsupported_quality_model')
    try:
        manifest = control/'models.json'; rows = {r['relative']: r for r in json.loads(manifest.read_text())}
        receipt = json.loads((control/'models-receipt.json').read_text())
        if receipt['manifest_sha256'] != hashlib.sha256(manifest.read_bytes()).hexdigest():
            raise ValueError()
        verified = {r['relative']: r for r in receipt['verified_files']}
        for name in MODEL_FILES[family]:
            row = rows[name]; path = models/name
            if verified.get(name) != {k: row[k] for k in ('relative', 'bytes', 'sha256')} or not path.is_file() or path.stat().st_size != row['bytes']:
                raise ValueError()
    except (OSError, KeyError, ValueError, TypeError):
        raise Failure('quality_models_unavailable') from None


def active_profile(kind, control=CONTROL, models=MODELS):
    try:
        entry = json.loads((control/'quality-profile.json').read_text())['active'][kind]
        allowed = {'video.generate': {'wan22_14b', 'wan22_t2v_14b_lightx2v'},
                   'video.animate_image': {'wan22_i2v_14b', 'wan22_i2v_14b_lightx2v'}}
        expected = entry['family']
        if expected not in allowed[kind] or entry['workflow_sha256'] != workflow_hash_for_family(expected) or entry['status'] != 'verified':
            raise ValueError()
        p = profile(expected)
        if (any(entry.get(k) != p[k] for k in ('width', 'height', 'fps', 'steps'))
                or type(entry.get('max_segment_seconds')) is not int or not 1 <= entry['max_segment_seconds'] <= 10):
            raise ValueError()
        verify_models(expected, control, models)
        return {**p, 'max_segment_seconds': entry['max_segment_seconds']}
    except (OSError, KeyError, ValueError, TypeError):
        raise Failure('quality_profile_not_qualified') from None


def build(family, prompt, seconds, jid, image_name=None):
    profile(family)
    if family.endswith('_lightx2v'):
        image = '_i2v_' in family
        nodes = build('wan22_i2v_14b' if image else 'wan22_14b', prompt, seconds, jid, image_name)
        kind, version = ('i2v', 'v1') if image else ('t2v', 'v1.1')
        for key, noise in [('5', 'high'), ('6', 'low')]:
            nodes[key+'l'] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {
                'model': [key, 0], 'strength_model': 1.0,
                'lora_name': base.PREFIX+f'wan2.2_{kind}_lightx2v_4steps_lora_{version}_{noise}_noise.safetensors'}}
            nodes[key+'s']['inputs']['model'] = [key+'l', 0]
        nodes['8']['inputs'].update(steps=4, cfg=1.0, start_at_step=0, end_at_step=2)
        nodes['9']['inputs'].update(steps=4, cfg=1.0, start_at_step=2, end_at_step=4)
        return nodes
    if family == 'wan22_14b':
        return base.build(family, prompt, seconds, jid, image_name)
    if not isinstance(image_name, str) or not image_name:
        raise Failure('reference_image_required')
    nodes = base.build('wan22_14b', prompt, seconds, jid)
    for key, part in [('5', 'high'), ('6', 'low')]:
        nodes[key]['inputs']['unet_name'] = base.PREFIX+f'wan2.2_i2v_{part}_noise_14B_fp8_scaled.safetensors'
    nodes['input'] = {'class_type': 'LoadImage', 'inputs': {'image': image_name}}
    nodes['7'] = {'class_type': 'WanImageToVideo', 'inputs': {
        'positive': ['3', 0], 'negative': ['4', 0], 'vae': ['2', 0], 'start_image': ['input', 0],
        'width': 1280, 'height': 720, 'length': nodes['7']['inputs']['length'], 'batch_size': 1}}
    for key in ('8', '9'):
        nodes[key]['inputs'].update(positive=['7', 0], negative=['7', 1])
    nodes['8']['inputs']['latent_image'] = ['7', 2]
    return nodes

"""Wan 2.2 native-resolution workflows; no runtime downloads or preview fallback."""
import hashlib
import json
from pathlib import Path

CONTROL = Path(__file__).resolve().parent
MODELS = Path(r'C:\HarmonikaVideo\wan22-models')
# Windows ComfyUI validates dropdown values using its advertised backslash names.
PREFIX = 'harmonika-quality\\'
MODEL_FILES = {
    'wan22_5b': ['diffusion_models/wan2.2_ti2v_5B_fp16.safetensors',
                 'text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'vae/wan2.2_vae.safetensors'],
    'wan22_14b': ['diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors',
                  'diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors',
                  'text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'vae/wan_2.1_vae.safetensors'],
}


class Failure(Exception):
    pass


def workflow_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def verify_models(family, control=CONTROL, models=MODELS):
    if family not in MODEL_FILES:
        raise Failure('unsupported_quality_model')
    try:
        manifest = control / 'models.json'
        rows = {r['relative']: r for r in json.loads(manifest.read_text())}
        receipt = json.loads((control / 'models-receipt.json').read_text())
        if receipt['manifest_sha256'] != hashlib.sha256(manifest.read_bytes()).hexdigest():
            raise ValueError()
        verified = {r['relative']: r for r in receipt['verified_files']}
        for name in MODEL_FILES[family]:
            row = rows[name]
            if verified.get(name) != {k: row[k] for k in ('relative', 'bytes', 'sha256')}:
                raise ValueError()
            p = models / name
            if not p.is_file() or p.stat().st_size != row['bytes']:
                raise ValueError()
    except (OSError, KeyError, ValueError, TypeError):
        raise Failure('quality_models_unavailable') from None


def profile(family):
    if family not in MODEL_FILES:
        raise Failure('unsupported_quality_model')
    return {'family': family, 'model': 'Wan 2.2 14B FP8' if family == 'wan22_14b' else 'Wan 2.2 TI2V 5B FP16',
            'width': 1280, 'height': 720, 'native_height': 720 if family == 'wan22_14b' else 736,
            'fps': 16 if family == 'wan22_14b' else 24, 'steps': 20,
            'quality': 'high', 'upscaled': False}


def active_profile(kind, control=CONTROL, models=MODELS):
    try:
        value = json.loads((control / 'quality-profile.json').read_text())
        entry = value['active'][kind]
        selected = profile(entry['family'])
        if (value['workflow_sha256'] != workflow_hash() or entry['status'] != 'verified'
                or any(entry.get(k) != selected[k] for k in ('width', 'height', 'fps', 'steps'))
                or type(entry.get('max_segment_seconds')) is not int
                or not 1 <= entry['max_segment_seconds'] <= 10):
            raise ValueError()
        verify_models(entry['family'], control, models)
        return {**selected, 'max_segment_seconds': entry['max_segment_seconds']}
    except (OSError, KeyError, ValueError, TypeError):
        raise Failure('quality_profile_not_qualified') from None


def build(family, prompt, seconds, jid, image_name=None):
    p = profile(family)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 8000:
        raise Failure('invalid_prompt')
    if type(seconds) is not int or not 1 <= seconds <= 10:
        raise Failure('invalid_duration')
    if family == 'wan22_14b' and image_name is not None:
        raise Failure('image_conditioning_not_supported_by_text_model')
    if len(jid) != 32 or any(c not in '0123456789abcdef' for c in jid):
        raise Failure('invalid_job_id')
    length = 4 * ((seconds * p['fps'] + 2) // 4) + 1
    nodes = {
        '1': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': PREFIX+'umt5_xxl_fp8_e4m3fn_scaled.safetensors', 'type': 'wan', 'device': 'default'}},
        '2': {'class_type': 'VAELoader', 'inputs': {'vae_name': PREFIX+('wan_2.1_vae.safetensors' if family == 'wan22_14b' else 'wan2.2_vae.safetensors')}},
        '3': {'class_type': 'CLIPTextEncode', 'inputs': {'text': prompt, 'clip': ['1', 0]}},
        '4': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'blurry, low resolution, distorted architecture, deformed objects, flicker, rainbow artifacts, oversaturated colors, watermark, illegible text', 'clip': ['1', 0]}},
        '10': {'class_type': 'VAEDecodeTiled', 'inputs': {'samples': ['9', 0], 'vae': ['2', 0], 'tile_size': 512, 'overlap': 64, 'temporal_size': 16, 'temporal_overlap': 4}},
        '11': {'class_type': 'CreateVideo', 'inputs': {'images': ['10', 0], 'fps': p['fps'], 'bit_depth': 8, 'color_space': 'sRGB', 'codec': 'h264'}},
        '12': {'class_type': 'SaveVideo', 'inputs': {'video': ['11', 0], 'filename_prefix': 'harmonika_hq_'+jid, 'format': 'mp4', 'format.codec': 'h264'}},
    }
    seed = int(jid[:8], 16)
    if family == 'wan22_5b':
        nodes['5'] = {'class_type': 'UNETLoader', 'inputs': {'unet_name': PREFIX+'wan2.2_ti2v_5B_fp16.safetensors', 'weight_dtype': 'default'}}
        nodes['6'] = {'class_type': 'ModelSamplingSD3', 'inputs': {'model': ['5', 0], 'shift': 8.0}}
        nodes['7'] = {'class_type': 'Wan22ImageToVideoLatent', 'inputs': {'vae': ['2', 0], 'width': p['width'], 'height': p['native_height'], 'length': length, 'batch_size': 1}}
        if image_name is not None:
            nodes['8'] = {'class_type': 'LoadImage', 'inputs': {'image': image_name}}
            nodes['7']['inputs']['start_image'] = ['8', 0]
        nodes['9'] = {'class_type': 'KSampler', 'inputs': {'model': ['6', 0], 'seed': seed, 'steps': p['steps'], 'cfg': 5.0, 'sampler_name': 'uni_pc', 'scheduler': 'simple', 'positive': ['3', 0], 'negative': ['4', 0], 'latent_image': ['7', 0], 'denoise': 1.0}}
    else:
        for key, part in [('5', 'high'), ('6', 'low')]:
            nodes[key] = {'class_type': 'UNETLoader', 'inputs': {'unet_name': PREFIX+f'wan2.2_t2v_{part}_noise_14B_fp8_scaled.safetensors', 'weight_dtype': 'default'}}
            nodes[key+'s'] = {'class_type': 'ModelSamplingSD3', 'inputs': {'model': [key, 0], 'shift': 5.0}}
        nodes['7'] = {'class_type': 'EmptyHunyuanLatentVideo', 'inputs': {'width': p['width'], 'height': p['native_height'], 'length': length, 'batch_size': 1}}
        for key, model, start, end, noise, leftover, latent in [('8', '5s', 0, 10, 'enable', 'enable', '7'), ('9', '6s', 10, 20, 'disable', 'disable', '8')]:
            nodes[key] = {'class_type': 'KSamplerAdvanced', 'inputs': {'model': [model, 0], 'noise_seed': seed, 'steps': p['steps'], 'cfg': 3.5, 'sampler_name': 'euler', 'scheduler': 'simple', 'positive': ['3', 0], 'negative': ['4', 0], 'latent_image': [latent, 0], 'start_at_step': start, 'end_at_step': end, 'add_noise': noise, 'return_with_leftover_noise': leftover}}
    return nodes

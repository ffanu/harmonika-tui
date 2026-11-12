"""Reference fidelity preset with real classifier-free guidance and exclusions."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re

CONTROL = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('wan14_frozen_workflow', CONTROL/'workflow.py')
base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)
Failure = base.Failure
MODELS = base.MODELS
FAMILY = 'wan22_i2v_14b_guided'


def workflow_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def workflow_hash_for_family(family):
    if family != FAMILY:
        return base.workflow_hash_for_family(family)
    return hashlib.sha256((base.workflow_hash_for_family('wan22_i2v_14b')+workflow_hash()).encode()).hexdigest()


def profile(family):
    if family != FAMILY:
        return base.profile(family)
    return {**base.profile('wan22_i2v_14b'), 'family': FAMILY,
            'model': 'Wan 2.2 I2V A14B FP8 + guided 20-step'}


def verify_models(family, control=CONTROL, models=MODELS):
    return base.verify_models('wan22_i2v_14b' if family == FAMILY else family, control, models)


def active_profile(kind, control=CONTROL, models=MODELS):
    try:
        entry = json.loads((control/'quality-profile.json').read_text())['active'][kind]
        if entry['family'] != FAMILY:
            return base.active_profile(kind, control, models)
        p = profile(FAMILY)
        if (kind != 'video.animate_image' or entry.get('status') != 'verified'
                or entry.get('workflow_sha256') != workflow_hash_for_family(FAMILY)
                or any(entry.get(k) != p[k] for k in ('width', 'height', 'fps', 'steps'))
                or type(entry.get('max_segment_seconds')) is not int
                or not 1 <= entry['max_segment_seconds'] <= 10):
            raise ValueError()
        verify_models(FAMILY, control, models)
        return {**p, 'max_segment_seconds': entry['max_segment_seconds']}
    except (OSError, KeyError, ValueError, TypeError):
        raise Failure('quality_profile_not_qualified') from None


def build(family, prompt, seconds, jid, image_name=None):
    if family != FAMILY:
        return base.build(family, prompt, seconds, jid, image_name)
    nodes = base.build('wan22_i2v_14b', prompt, seconds, jid, image_name)
    # CFG=1 bypasses negative conditioning. Keep the full expert schedule/CFG=3.5.
    # Move explicit English exclusions into negative conditioning; do not impose
    # a blanket human/hand ban on user-requested portraits or people animation.
    people = re.compile(r'\b(?:no|without)\s+(?:people|persons?|humans?)\b', re.I)
    hands = re.compile(r'\b(?:no|without)\s+(?:hands?|fingers?)\b', re.I)
    if people.search(prompt):
        nodes['4']['inputs']['text'] += ', people, person, human, hands, fingers, human interaction'
    elif hands.search(prompt):
        nodes['4']['inputs']['text'] += ', hands, fingers, human interaction'
    nodes['3']['inputs']['text'] = hands.sub('', people.sub('', prompt))
    return nodes

#!/usr/bin/env python3
"""Inject an explicit user requirement change only after independent TURN A passes."""
import argparse
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main(root, report):
    result = json.loads(report.read_text())
    if result.get('phase') != 'v1' or result.get('passed') is not True:
        raise RuntimeError('turn_a_must_pass_first')
    project = root / 'project'
    if (project / 'CHANGE.md').exists():
        raise RuntimeError('preserve_existing_requirement_change')
    if not (root / 'sentinel.json').is_file():
        raise RuntimeError('migration_fixture_missing')
    meta = json.loads((root / 'grade.json').read_text())
    for name, digest in meta['protected'].items():
        if hashlib.sha256((project / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('protected_file_changed')
    checkpoint = hashlib.sha256((project / 'CHECKPOINT.json').read_bytes()).hexdigest()
    if checkpoint != meta.get('checkpoint_sha256'):
        raise RuntimeError('checkpoint_changed')
    source = project / 'web.html'
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    marker = '<!-- USER-EDIT-L5: keep this marker -->'
    if marker in source.read_text():
        raise RuntimeError('user_marker_already_present')
    # Explicit synthetic user edit; the candidate must preserve it in TURN B.
    source.write_text(marker + '\n' + source.read_text())
    notes = project / 'USER_NOTES.md'
    notes.write_text(notes.read_text() + '\nTURN B user edit: preserve the exact USER-EDIT-L5 HTML comment and the existing migration sentinel plus its attachment. Follow CHANGE.md and RELEASE-PROTOCOL.md.\n')
    (project / 'CHANGE.md').write_bytes((REPO / 'scripts/level5-lab/CHANGE.md').read_bytes())
    for name in ['USER_NOTES.md', 'CHANGE.md']:
        meta['protected'][name] = hashlib.sha256((project / name).read_bytes()).hexdigest()
    (root / 'grade.json').write_text(json.dumps(meta, indent=2))
    proof = {'injected': True, 'turn_a_report': report.name, 'checkpoint_preserved': True, 'html_before_sha256': before, 'html_after_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'scope': 'synthetic_examiner_user_change'}
    (root / 'turn-b-injection.json').write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--passed-report', required=True, type=Path)
    args = parser.parse_args()
    main(args.root, args.passed_report)

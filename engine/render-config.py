#!/usr/bin/env python3
"""Render the Harmonika provider config template into a strict JSON config.

Only placeholders are substituted; the API key itself is never written here.
The MCP block is dropped when no server path was found so a fresh install never
starts with a broken tool entry.
"""
import argparse
import json
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--mcp-server', default='')
    parser.add_argument('--template', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()

    with open(args.template, encoding='utf-8') as handle:
        text = handle.read()
    text = (text.replace('@HOME@', args.home)
                .replace('@PYTHON@', args.python)
                .replace('@MCP_SERVER@', args.mcp_server))
    config = json.loads(text)

    if not args.mcp_server or not os.path.isfile(args.mcp_server):
        config.pop('mcp', None)

    config.setdefault('provider', {}).setdefault('harmonika', {}).setdefault('options', {})
    options = config['provider']['harmonika']['options']
    if options.get('apiKey') != '{env:HARMONIKA_API_KEY}':
        # Never persist a literal key from a hand-edited template.
        options['apiKey'] = '{env:HARMONIKA_API_KEY}'

    with open(args.out, 'w', encoding='utf-8') as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write('\n')


if __name__ == '__main__':
    main()

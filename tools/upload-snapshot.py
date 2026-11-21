#!/usr/bin/env python3
"""Upload the current worktree snapshot to GitHub via the Git Data API.

Fallback path for when github.com:443 is unreachable but api.github.com is:
builds the whole tree in chunks (POST /git/trees with inline base64 blobs),
then one commit and the refs/heads/main ref. Only tracked files are sent;
CRLF is normalized to LF (the repo pins eol=lf).
"""
import base64
import json
import subprocess
import sys
import urllib.request

REPO = 'zhouzhoublm/ios-unicorn'
EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
CHUNK_BYTES = 1_500_000  # base64 payload budget per tree request

TOKEN = ''
for line in subprocess.check_output(
        ['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
        text=True).splitlines():
    if line.startswith('password='):
        TOKEN = line.split('=', 1)[1]


def api(path, payload=None, method=None):
    url = f'https://api.github.com/{path}'
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ('POST' if data else 'GET'))
    req.add_header('Authorization', f'token {TOKEN}')
    req.add_header('Accept', 'application/vnd.github+json')
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        print(f'API error {e.code} on {path}: {e.read().decode()[:300]}', file=sys.stderr)
        raise


def main():
    files = subprocess.check_output(['git', 'ls-files'], text=True).splitlines()
    modes = {}
    for line in subprocess.check_output(['git', 'ls-files', '-s'], text=True).splitlines():
        meta, path = line.split('\t', 1)
        modes[path] = '100755' if meta.split()[0] == '100755' else '100644'

    entries = []
    total = 0
    for path in files:
        with open(path, 'rb') as f:
            raw = f.read().replace(b'\r\n', b'\n')
        entries.append((path, modes.get(path, '100644'),
                        base64.b64encode(raw).decode()))
        total += len(raw)
    print(f'{len(entries)} files, {total/1048576:.1f} MiB raw')

    # Base: an existing main branch is used as-is; an empty repository is
    # bootstrapped through the Contents API (the Git Data API refuses an
    # empty repo with 409, and the PUT here must not carry a 'branch').
    strays = ['test-bootstrap.md']   # leftover of API probing, deleted below
    try:
        ref = api(f'repos/{REPO}/git/refs/heads/main')
        base_commit = ref['object']['sha']
        base_tree = api(f'repos/{REPO}/git/commits/{base_commit}')['tree']['sha']
        print(f'base: existing main {base_commit[:12]} (tree {base_tree[:12]})')
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        readme = next(e for e in entries if e[0] == 'README.md')
        boot = api(f'repos/{REPO}/contents/README.md', {
            'message': 'bootstrap: README (snapshot upload follows)',
            'content': readme[2],
        }, method='PUT')
        base_commit = boot['commit']['sha']
        base_tree = boot['commit']['tree']['sha']
        strays = []
        print(f'base: bootstrapped {base_commit[:12]} (tree {base_tree[:12]})')

    # chunk by base64 budget, keeping every entry whole
    chunks, cur, size = [], [], 0
    for e in entries:
        if cur and size + len(e[2]) > CHUNK_BYTES:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += len(e[2])
    if cur:
        chunks.append(cur)
    print(f'{len(chunks)} tree requests')

    for i, chunk in enumerate(chunks, 1):
        tree = [{'path': p, 'mode': m, 'type': 'blob',
                 'content': c, 'encoding': 'base64'} for p, m, c in chunk]
        if i == 1:
            # deletions ride along in the first tree request (sha = None)
            tree += [{'path': s, 'mode': '100644', 'type': 'blob', 'sha': None}
                     for s in strays]
        result = api(f'repos/{REPO}/git/trees', {'base_tree': base_tree, 'tree': tree})
        base_tree = result['sha']
        print(f'  tree {i}/{len(chunks)}: {base_tree[:12]} ({len(chunk)} files)')

    commit = api(f'repos/{REPO}/git/commits', {
        'message': 'unicorn: Xiaomi 12S Pro (SM8475) port scaffold on the ios-mondrian baseline\n\n'
                   'Snapshot of the ios-unicorn tree: the ios-mondrian baseline\n'
                   '(xNekom/ios-mondrian, ios-sweet lineage) plus the unicorn\n'
                   'board scaffold — tools/boards.py UNICORN entry, shim\n'
                   'BOARD_UNICORN, xnu/patches-unicorn (393 patches, generated\n'
                   'by tools/port-unicorn-series.py), docs/UNICORN_PORT.md with\n'
                   'the phase plan and the measured unit facts.\n\n'
                   'Uploaded through the Git Data API because github.com:443 was\n'
                   'unreachable; the full upstream history stays in the local\n'
                   'clone and will be pushed over git when the network allows.',
        'tree': base_tree,
        'parents': [base_commit],
        'author': {'name': 'zhouzhoublm', 'email': 'zhouzhoublm@users.noreply.github.com',
                   'date': '2026-11-21T10:25:00Z'},
    })
    print('commit:', commit['sha'])
    ref = api(f'repos/{REPO}/git/refs/heads/main', {'sha': commit['sha'], 'force': True},
              method='PATCH')
    print('ref updated: main ->', ref.get('object', {}).get('sha', commit['sha'])[:12])


if __name__ == '__main__':
    main()

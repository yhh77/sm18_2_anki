#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动化测试脚本: 构建 apkg → AnkiConnect 导入 → 验证卡片数据 → 循环修复

用法: python auto_test.py
"""

import sys, os, json, time, subprocess, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APKG = os.path.join(HERE, 'output', 'sm_import_v4.apkg')
ANKICONNECT = 'http://127.0.0.1:8765'

# ═══════════════════════════════════════════════
# AnkiConnect helpers
# ═══════════════════════════════════════════════

def aik(action, **params):
    body = json.dumps({'action': action, 'version': 6, 'params': params}).encode()
    req = urllib.request.Request(ANKICONNECT, body,
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {'error': str(e)}


# ═══════════════════════════════════════════════
# Steps
# ═══════════════════════════════════════════════

def step_check_connection():
    r = aik('version')
    ok = r.get('result') == 6
    print(f'[1/5] AnkiConnect: {"OK" if ok else "FAIL"}')
    return ok


def step_build():
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, 'src', 'build.py'),
         os.path.join(HERE, 'NodeAsText.txt'), APKG],
        capture_output=True, text=True, timeout=120
    )
    ok = r.returncode == 0
    print(f'[2/5] Build: {"OK" if ok else "FAIL"}')
    if ok:
        for line in r.stdout.strip().split('\n'):
            print(f'  {line}')
    else:
        print(f'  {r.stderr}')
    return ok


def step_import():
    # Delete old SM Import deck first
    r = aik('deckNamesAndIds')
    decks = r.get('result', {})
    for name, did in decks.items():
        if name == 'SM Import':
            aik('deleteDecks', decks=[did], cardsToo=True)
            print(f'  Deleted old deck: {name}')

    apkg_abs = APKG.replace(chr(92), '/')
    r = aik('importPackage', path=apkg_abs)
    print(f'[3/5] Import: result={json.dumps(r, ensure_ascii=False)[:200]}')

    # Check all cards with IDs in our apkg range
    r = aik('findCards', query='')
    all_cards = r.get('result', [])
    card_ids = [c for c in all_cards if c > 1770000000000]
    print(f'  Cards from our import (id > 177...): {len(card_ids)}')

    return card_ids


def step_verify(card_ids):
    if not card_ids:
        print(f'[4/5] Verify: FAIL - no cards')
        return False

    # Check a sample of cards
    sample = card_ids[:10]
    results = []
    for cid in sample:
        info = aik('cardsInfo', cards=[cid])
        if info.get('result'):
            card = info['result'][0]

            results.append({
                'id': cid,
                'type': card['type'],
                'queue': card['queue'],
                'due': card['due'],
                'ivl': card['interval'],
                'reps': card['reps'],
            })

    # Batch check revlog
    sample_ids = [c['id'] for c in results]
    rev_result = aik('getReviewsOfCards', cards=sample_ids)
    rev_data = rev_result.get('result', {})

    for c in results:
        c['rev_list'] = rev_data.get(str(c['id']), [])
        c['rev_count'] = len(c['rev_list'])

    print(f'[4/5] Verify: {len(results)} cards sampled')

    # Checks
    issues = []

    review_cards = [c for c in results if c.get('type') == 2]
    new_cards = [c for c in results if c.get('type') == 0]
    has_revlog = [c for c in results if c.get('rev_count', 0) > 0]

    print(f'  Review cards: {len(review_cards)}/{len(results)}')
    print(f'  New cards: {len(new_cards)}/{len(results)}')
    print(f'  With revlog: {len(has_revlog)}/{len(results)}')

    if len(review_cards) == 0:
        issues.append('No review cards (all type=New)')
    if len(has_revlog) == 0:
        issues.append('No revlog entries imported')
    if len(review_cards) > 0:
        due_vals = [c.get('due', 0) for c in review_cards]
        min_due = min(due_vals)
        print(f'  Due range: {min_due} ~ {max(due_vals)}')
        if min_due > 100:
            issues.append(f'Minimum due is {min_due} days (cards not due soon)')

    # Show table
    print('\n  %15s %4s %5s %6s %4s %4s %4s' % ('ID','type','queue','due','ivl','reps','revs'))
    for c in results[:5]:
        print('  %15s %4s %5s %6s %4s %4s %4s' % (
            c.get('id',0), c.get('type',0), c.get('queue',0),
            c.get('due',0), c.get('ivl',0), c.get('reps',0), c.get('rev_count',0)))

    # Show sample review dates
    if has_revlog:
        sample = has_revlog[0]
        revs = sample.get('rev_list', [])
        if revs:
            from datetime import datetime, timezone
            dates = []
            for r in revs[:3]:
                dt = datetime.fromtimestamp(r['id']/1000.0, tz=timezone.utc)
                dates.append(dt.strftime('%Y-%m-%d'))
            print(f'  Review dates sample: {dates}')

    if issues:
        print(f'\n  ISSUES FOUND:')
        for i in issues:
            print(f'    - {i}')
        return False

    print(f'\n  [OK] All checks passed')
    return True


# ═══════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════

def main():
    print('=' * 70)
    print('Anki APKG Auto Test')
    print('=' * 70)

    for iteration in range(1, 6):
        print(f'\n--- Iteration {iteration} ---')

        if not step_check_connection():
            print('AnkiConnect not available - start Anki')
            sys.exit(1)

        if not step_build():
            print('Build failed')
            sys.exit(1)

        card_ids = step_import()
        ok = step_verify(card_ids)

        if ok:
            print(f'\n[OK] All checks passed after {iteration} iteration(s)')
            break
        else:
            print(f'\n[FAIL] Issues remain, fixing and retrying...')
            # Could add auto-fix logic here
    else:
        print('\n[FAIL] Could not fix after 5 iterations')
        sys.exit(1)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键构建: NodeAsText.txt → .apkg (全内存，零中间文件)

用法: python build.py [NodeAsText.txt] [output.apkg]
"""

import sys, os, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from node2json import parse_node_file
from extract_items import extract_items
from items2apkg import build_apkg


def main():
    txt = sys.argv[1] if len(sys.argv) > 1 else 'NodeAsText.txt'
    apkg = sys.argv[2] if len(sys.argv) > 2 else 'output.apkg'

    if not os.path.exists(txt):
        print(f'File not found: {txt}')
        sys.exit(1)

    t0 = time.time()
    size_mb = os.path.getsize(txt) / 1024 / 1024

    # Step 1: parse TXT → elements (in memory)
    print(f'[1/3] Parsing {txt} ({size_mb:.1f} MB)...')
    elements = parse_node_file(txt)
    print(f'      {len(elements)} elements')

    # Step 2: extract items (in memory)
    print(f'[2/3] Extracting items...')
    items = extract_items(elements)
    print(f'      {len(items)} memorized items')

    # Step 3: build apkg (in memory)
    print(f'[3/3] Building apkg...')
    build_apkg(items, apkg)
    print(f'      {os.path.getsize(apkg) / 1024:.0f} KB')

    print(f'\nDone ({time.time() - t0:.0f}s) → {apkg}')


if __name__ == '__main__':
    main()

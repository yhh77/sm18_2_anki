#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 node2json.py 生成的 JSON 中提取 Item：
  - 正面(Front) / 反面(Back) 内容来源（优先 HTMFile，无则用 Text）
  - RepHist 的 Date + Grade

依赖: 先运行 node2json.py 生成 NodeAsText.json
用法: python extract_items.py [NodeAsText.json] [output.json]

DisplayAt 区分正反面:
  bit 5 (32) = 1 → 答案中显示 → Back
  bit 5 (32) = 0 → 答案中不显示 → Front
"""

import json
import sys
import os
from collections import defaultdict


def extract_items(elements):
    items = []

    for el in elements:
        ei = el.get('ElementInfo', {})
        if ei.get('Type') != 'Item':
            continue
        if ei.get('Status') != 'Memorized':
            continue

        el_no = el.get('ElNo')
        comps = el.get('Components', [])
        rh = el.get('RepHist', [])

        if not comps or not rh:
            continue

        front = []
        back = []

        for c in comps:
            # 优先 HTMFile，无则用 Text
            content = c.get('HTMFile')
            if content is None:
                content = c.get('Text')
            if content is None:
                continue
            content = str(content).strip()
            if not content:
                continue

            da = c.get('DisplayAt', 255)
            if da == 255:
                front.append(content)
            else:
                back.append(content)

        # 必须至少有一面有内容
        if not front and not back:
            continue

        rephist = []
        for r in rh:
            entry = {}
            if 'Date' in r:
                entry['Date'] = r['Date']
            if 'Hour' in r:
                entry['Hour'] = r['Hour']
            if 'Grade' in r:
                entry['Grade'] = r['Grade']
            if entry:
                rephist.append(entry)

        # RepHist 在文件中从新到旧排列，反转为从旧到新
        rephist.reverse()

        items.append({
            'ElNo': el_no,
            'Front': front,
            'Back': back,
            'RepHist': rephist
        })

    return items


def main():
    if len(sys.argv) >= 2:
        input_path = sys.argv[1]
    else:
        input_path = 'NodeAsText.json'

    if not os.path.exists(input_path):
        print(f"错误: 找不到 {input_path}, 请先运行 node2json.py")
        sys.exit(1)

    print(f"正在读取 {input_path} ({os.path.getsize(input_path)/1024/1024:.1f} MB)...")
    with open(input_path, 'r', encoding='utf-8') as f:
        elements = json.load(f)

    print(f"正在提取...")
    items = extract_items(elements)
    print(f"提取完成: {len(items)} 个 Item (Memorized)")

    total_front = sum(len(it['Front']) for it in items)
    total_back = sum(len(it['Back']) for it in items)
    total_rephist = sum(len(it['RepHist']) for it in items)
    front_dist = defaultdict(int)
    back_dist = defaultdict(int)
    for it in items:
        front_dist[len(it['Front'])] += 1
        back_dist[len(it['Back'])] += 1

    print(f"  正面: {total_front}  |  反面: {total_back}  |  RepHist: {total_rephist}")
    print(f"  正面分布: {dict(sorted(front_dist.items()))}")
    print(f"  反面分布: {dict(sorted(back_dist.items()))}")

    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
    else:
        output_path = 'items_extracted.json'

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"已输出: {output_path} ({os.path.getsize(output_path)/1024/1024:.2f} MB)")


if __name__ == '__main__':
    main()

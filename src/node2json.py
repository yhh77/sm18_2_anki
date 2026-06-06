#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 SuperMemo NodeAsText.txt 转换为 JSON 格式。
用法: python node2json.py NodeAsText.txt [output.json]
"""

import json
import re
import sys
import os


def parse_node_file(filepath):
    """解析 NodeAsText 文件，返回元素列表"""
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        raw = f.read()

    elements = []
    # 按 "Begin Element #" 分割
    blocks = re.split(r'\n(?=Begin Element #)', raw)

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        element = parse_element(block)
        if element:
            elements.append(element)

    return elements


def parse_element(block):
    """解析单个 Element 块"""
    lines = block.split('\n')
    element = {}
    current_section = None  # 'element', 'elementinfo', 'component', 'rephist'
    section_data = None
    component_list = []
    rephist_list = []

    for line in lines:
        # 跳过空行
        if not line.strip():
            continue

        # 检测缩进级别
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # 顶层字段 (indent=2)
        if indent == 2 and '=' in stripped and not stripped.startswith('Begin ') and not stripped.startswith('End ') and not stripped.startswith('ElNo='):
            key, _, value = stripped.partition('=')
            element[key.strip()] = value.strip()
            continue

        # Begin / End 块
        if stripped.startswith('Begin ElementInfo'):
            current_section = 'elementinfo'
            section_data = {}
            continue
        elif stripped.startswith('End ElementInfo'):
            if section_data:
                element['ElementInfo'] = section_data
            current_section = None
            section_data = None
            continue

        elif stripped.startswith('Begin Component'):
            current_section = 'component'
            section_data = {}
            continue
        elif stripped.startswith('End Component'):
            if section_data:
                component_list.append(section_data)
            current_section = None
            section_data = None
            continue

        elif stripped.startswith('Begin RepHist'):
            current_section = 'rephist'
            section_data = {}
            rephist_list_temp = []
            continue
        elif stripped.startswith('End RepHist'):
            if section_data:
                rephist_list_temp.append(section_data)
            element['RepHist'] = rephist_list_temp
            current_section = None
            section_data = None
            rephist_list_temp = []
            continue

        # 子块内字段 (indent=4 或更深)
        if current_section == 'elementinfo' and '=' in stripped:
            key, _, value = stripped.partition('=')
            section_data[key.strip()] = value.strip()
            continue

        elif current_section == 'component' and '=' in stripped:
            key, _, value = stripped.partition('=')
            # 解析 Cors=(a,b,c,d)
            v = value.strip()
            if key.strip() == 'Cors':
                m = re.match(r'\((\d+),(\d+),(\d+),(\d+)\)', v)
                if m:
                    section_data['Cors'] = {
                        'Left': int(m.group(1)),
                        'Top': int(m.group(2)),
                        'Width': int(m.group(3)),
                        'Height': int(m.group(4))
                    }
                else:
                    section_data['Cors'] = v
            else:
                # 尝试类型转换
                if v.isdigit():
                    section_data[key.strip()] = int(v)
                else:
                    try:
                        section_data[key.strip()] = float(v)
                    except ValueError:
                        section_data[key.strip()] = v
            continue

        elif current_section == 'rephist' and stripped.startswith('ElNo='):
            # RepHist 中的每条记录以 ElNo= 开头
            parts = parse_rephist_line(stripped)
            rephist_list_temp.append(parts)
            continue

    # 清理
    if component_list:
        element['Components'] = component_list

    # 提取元素编号
    m = re.match(r'Begin Element #(\d+)', block)
    if m:
        element['ElNo'] = int(m.group(1))
    elif 'ElNo' not in element:
        # 从 ElementInfo 中获取
        ei = element.get('ElementInfo', {})
        if 'ElNo' not in element and not ei:
            return None

    # 数值转换顶层字段
    for key in ['Priority', 'ElementColor', 'AutoPlay', 'Scaled', 'ReadPointComponent',
                'ReadPointStart', 'ReadPointLength', 'ReadPointScrollTop', 'ComponentNo']:
        if key in element:
            try:
                if '.' in str(element[key]):
                    element[key] = float(element[key])
                else:
                    element[key] = int(element[key])
            except (ValueError, TypeError):
                pass

    # 转换 ElementInfo 中的数值
    ei = element.get('ElementInfo', {})
    for key in ['Ordinal', 'Repetitions', 'Lapses', 'Interval', 'FirstGrade',
                'ForgettingIndex', 'SourceArticle']:
        if key in ei:
            try:
                if '.' in str(ei[key]):
                    ei[key] = float(ei[key])
                else:
                    ei[key] = int(ei[key])
            except (ValueError, TypeError):
                pass
    for key in ['AFactor', 'UFactor']:
        if key in ei:
            try:
                ei[key] = float(ei[key])
            except (ValueError, TypeError):
                pass

    return element


def parse_rephist_line(line):
    """解析 RepHist 中的一条记录: ElNo=11179 Rep=2 Laps=0 Date=26.03.2026 Hour=8.888 Int=0 Grade=12 Priority=0"""
    result = {}
    # 按空格分割，但注意有些字段可能包含空格值（罕见）
    tokens = line.split()
    for token in tokens:
        if '=' in token:
            key, _, value = token.partition('=')
            key = key.strip()
            value = value.strip()
            # 类型转换
            if key in ('ElNo', 'Rep', 'Laps', 'Int', 'Grade', 'Postpones'):
                try:
                    result[key] = int(value)
                except ValueError:
                    result[key] = value
            elif key in ('Priority', 'Hour', 'Difficulty', 'expFI'):
                try:
                    result[key] = float(value)
                except ValueError:
                    result[key] = value
            else:
                result[key] = value
    return result


def main():
    if len(sys.argv) < 2:
        input_path = 'NodeAsText.txt'
    else:
        input_path = sys.argv[1]

    if not os.path.exists(input_path):
        print(f"错误: 找不到文件 {input_path}")
        sys.exit(1)

    print(f"正在解析 {input_path} ({os.path.getsize(input_path)/1024/1024:.1f} MB)...")
    elements = parse_node_file(input_path)
    print(f"解析完成: {len(elements)} 个元素")

    # 统计
    topics = sum(1 for e in elements if e.get('ElementInfo', {}).get('Type') == 'Topic')
    items = sum(1 for e in elements if e.get('ElementInfo', {}).get('Type') == 'Item')
    with_components = sum(1 for e in elements if e.get('Components'))
    with_rephist = sum(1 for e in elements if e.get('RepHist'))

    print(f"  Topics: {topics}")
    print(f"  Items: {items}")
    print(f"  有组件: {with_components}")
    print(f"  有复习历史: {with_rephist}")

    # 输出
    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
    else:
        base = os.path.splitext(input_path)[0]
        output_path = f"{base}.json"

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(elements, f, ensure_ascii=False, indent=2)

    out_size = os.path.getsize(output_path)
    print(f"已输出: {output_path} ({out_size/1024/1024:.1f} MB)")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""SuperMemo → Anki 转换工具  用法: python main.py [NodeAsText.txt] [output.apkg]"""
import sys, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, 'src', 'build.py')


def main():
    txt = sys.argv[1] if len(sys.argv) > 1 else 'NodeAsText.txt'
    apkg = sys.argv[2] if len(sys.argv) > 2 else os.path.join('output', 'sm_import.apkg')

    if not os.path.exists(txt):
        print(f'File not found: {txt}')
        sys.exit(1)

    os.makedirs(os.path.dirname(apkg) or '.', exist_ok=True)
    subprocess.run([sys.executable, BUILD, txt, apkg], check=True)


if __name__ == '__main__':
    main()

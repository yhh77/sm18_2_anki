# SM18_2_Anki

功能: SuperMemo `NodeAsText.txt` → Anki `.apkg`，保留 FSRS 调度数据和完整复习历史。

将目录下的`sm18_fix.exe`(作用是确保导出内容不会乱码)复制到你的sm18目录下打开, 导出source code, 窗口`Select text export options:`中, 勾选全部三个选项, 导出得到`NodeAsText.txt` .

## 使用

```bash
pip install fsrs
python main.py NodeAsText.txt output/sm_import.apkg
```

然后在 Anki 中 **文件 → 导入**，勾选「导入学习进度」。

## 管道

```
NodeAsText.txt (9 MB)
  → src/node2json.py      解析 SM 文本
  → src/extract_items.py  提取 Item
  → src/items2apkg.py     FSRS 模拟 + apkg 生成 → 写入文件
  → output/sm_import.apkg (1.8 MB)
```

## 规则

### Grade 映射

| SM Grade | FSRS Rating | 说明 |
|----------|------------|------|
| 1, 2 | Again | 重学，间隔重置 |
| 3 | Hard | 间隔缩短 |
| 4, 5 | Good | 正常间隔 |
| >5 (第一条) | Good | FSRS 种子 |
| >5 (后续) | 跳过 | 不生成复习记录 |

### 图片处理

- `file:///` → 嵌入 apkg
- `http(s)://` → 下载后嵌入
- `127.0.0.1:50555/formula?latex=...` → `\(...\)` 内联
  - 这是针对我个人supermemo数据的处理


## 目录

```
├── main.py              ← 入口
├── auto_test.py         ← AnkiConnect 自动测试
├── NodeAsText.txt       ← SM 导出文件
├── output/
│   └── sm_import.apkg   ← Anki 导入
└── src/
    ├── build.py         ← 全内存流水线
    ├── items2apkg.py    ← apkg 生成
    ├── extract_items.py ← 提取 Item
    └── node2json.py     ← 解析文本
```

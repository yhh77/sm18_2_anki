# SM18_2_Anki

功能: SuperMemo `NodeAsText.txt` → Anki `.apkg`，将所有 memorized的item导出到anki,保留 FSRS 调度数据和完整复习历史。

## 使用

#### 导出source code

将目录下的`sm18_fix.exe`(作用是确保导出内容不会乱码)复制到你的sm18目录下打开, 导出source code, 窗口`Select text export options:`中, 勾选全部三个选项, 导出得到`NodeAsText.txt` .

其他非sm18版本可以通过resource hacker, 将`manifest`手动修改为以下

```
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"
 xmlns:asmv3="urn:schemas-microsoft-com:asm.v3">
 <asmv3:application>
   <asmv3:windowsSettings>
     <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>;
     <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>;
     <activeCodePage xmlns="http://schemas.microsoft.com/SMI/2019/WindowsSettings">UTF-8</activeCodePage>;
   </asmv3:windowsSettings>
 </asmv3:application>
 <dependency>
   <dependentAssembly>
     <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" publicKeyToken="6595b64144ccf1df" language="*" processorArchitecture="*"/>
   </dependentAssembly>
 </dependency>
 <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
   <security>
     <requestedPrivileges>
       <requestedExecutionLevel level="asInvoker" uiAccess="false" />
     </requestedPrivileges>
   </security>
 </trustInfo>
 <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
   <application>      <!--The ID below indicates app support for Windows Vista -->
     <supportedOS Id="{e2011457-1546-43c5-a5fe-008deee3d3f0}"/>
     <!--The ID below indicates app support for Windows 7 -->
     <supportedOS Id="{35138b9a-5d96-4fbd-8e2d-a2440225f93a}"/>
     <!--The ID below indicates app support for Windows 8 -->
     <supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}"/>
     <!--The ID below indicates app support for Windows 8.1 -->
     <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"/>
     <!--The ID below indicates app support for Windows 10 -->
     <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
   </application>
 </compatibility>
</assembly>
```

#### 运行

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
- `<img src=127.0.0.1:50555/formula?latex=...>` → `\(...\)` 内联
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
